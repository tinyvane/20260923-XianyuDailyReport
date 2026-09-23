"""Local report service. Chrome keeps the login; only visible metrics reach this database."""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from pydantic import BaseModel, Field
from edge_adapter import EdgeSession

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "report.sqlite3"
OFFICIAL = "https://www.goofish.com"
last_login = "未检查"
edge_session = EdgeSession(DATA / "browser-profile")
edge_scan_lock = asyncio.Lock()


class LocalOriginGuard(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin not in ("http://127.0.0.1:5055", "http://localhost:5055"):
                return JSONResponse({"detail": "只接受本机页面的操作"}, status_code=403)
        return await call_next(request)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def seller_display_name(value: str, seller_id: str) -> str:
    return next((line.strip()[:80] for line in value.splitlines() if line.strip()), f"用户 {seller_id}")


@contextmanager
def connect():
    DATA.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS sellers (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL,
          selected INTEGER NOT NULL DEFAULT 1, added_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS items (
          id TEXT PRIMARY KEY, seller_id TEXT NOT NULL REFERENCES sellers(id),
          title TEXT NOT NULL, url TEXT NOT NULL, image TEXT, price TEXT,
          last_seen TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS snapshots (
          item_id TEXT NOT NULL REFERENCES items(id), collected_at TEXT NOT NULL,
          views INTEGER, wants INTEGER, price TEXT, status TEXT,
          PRIMARY KEY(item_id, collected_at)
        );
        CREATE INDEX IF NOT EXISTS snapshots_time ON snapshots(collected_at);
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,
          finished_at TEXT, state TEXT NOT NULL, found INTEGER NOT NULL DEFAULT 0,
          valid INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS favorite_items (
          id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
          selected INTEGER NOT NULL DEFAULT 0, added_at TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT '待采集', last_checked TEXT, last_error TEXT,
          card_price TEXT
        );
        """)
        favorite_columns = {row["name"] for row in db.execute("PRAGMA table_info(favorite_items)")}
        for name, definition in (
            ("state", "TEXT NOT NULL DEFAULT '待采集'"),
            ("last_checked", "TEXT"),
            ("last_error", "TEXT"),
            ("card_price", "TEXT"),
        ):
            if name not in favorite_columns:
                db.execute(f"ALTER TABLE favorite_items ADD COLUMN {name} {definition}")
        db.execute("INSERT OR IGNORE INTO settings VALUES ('interval_hours','24')")
        db.execute("INSERT OR IGNORE INTO settings VALUES ('auto_collect','false')")
        db.execute("INSERT OR IGNORE INTO settings VALUES ('schedule_minutes','60')")
        db.execute("INSERT OR IGNORE INTO settings VALUES ('browser_mode','\"edge_extension\"')")
        for row in db.execute("SELECT id,name FROM sellers WHERE instr(name,char(10))>0 OR instr(name,char(13))>0"):
            db.execute("UPDATE sellers SET name=? WHERE id=?",
                       (seller_display_name(row["name"], row["id"]), row["id"]))


def settings():
    with connect() as db:
        return {r["key"]: json.loads(r["value"]) for r in db.execute("SELECT * FROM settings")}


def seller_identity(url: str):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ("www.goofish.com", "goofish.com"):
        raise ValueError("只接受闲鱼官方 HTTPS 个人主页链接")
    if not parsed.path.startswith(("/personal", "/user")):
        raise ValueError("请提供闲鱼个人主页链接")
    query = parse_qs(parsed.query)
    user_id = next((query[key][0] for key in ("userId", "user_id", "id") if query.get(key)), "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,80}", user_id):
        raise ValueError("个人主页缺少可识别的稳定用户 ID")
    return user_id, f"{OFFICIAL}{parsed.path}?userId={user_id}"


def item_identity(url: str):
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ("www.goofish.com", "goofish.com") or parsed.path != "/item":
        return None
    item_id = parse_qs(parsed.query).get("id", [""])[0]
    return item_id if re.fullmatch(r"\d{8,22}", item_id) else None


def metric(text: str, kind: str):
    patterns = {
        "views": [r"(\d+(?:\.\d+)?[万千]?)\s*(?:次)?浏览", r"浏览\s*(\d+(?:\.\d+)?[万千]?)"],
        "wants": [r"(\d+(?:\.\d+)?[万千]?)\s*人想要", r"想要\s*(\d+(?:\.\d+)?[万千]?)"],
    }
    for pattern in patterns[kind]:
        found = re.search(pattern, text)
        if found:
            raw = found.group(1)
            scale = 10000 if raw.endswith("万") else 1000 if raw.endswith("千") else 1
            return round(float(raw.rstrip("万千")) * scale)
    return None


def report(hours: float):
    if not 0.25 <= hours <= 720:
        raise ValueError("对比时长必须在 0.25 到 720 小时之间")
    target = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")
    with connect() as db:
        sellers = [dict(r) for r in db.execute("SELECT * FROM sellers ORDER BY selected DESC,name")]
        favorites = [dict(r) for r in db.execute("""
            SELECT favorite_items.*, items.title AS observed_title, items.seller_id,
                   items.last_seen, items.price
            FROM favorite_items LEFT JOIN items ON items.id=favorite_items.id
            ORDER BY favorite_items.added_at DESC, favorite_items.id DESC
        """)]
        linked_sellers = {f["seller_id"] for f in favorites if f["selected"] and f["seller_id"]}
        for seller in sellers:
            seller["linked"] = seller["id"] in linked_sellers
        items = []
        price_changes = 0
        count_anomalies = 0
        for item in db.execute("""
            SELECT items.* FROM items
            LEFT JOIN sellers ON sellers.id=items.seller_id
            LEFT JOIN favorite_items ON favorite_items.id=items.id
            WHERE (favorite_items.selected=1 AND favorite_items.state='有效')
               OR (sellers.selected=1 AND favorite_items.id IS NULL)
            ORDER BY items.last_seen DESC
        """):
            obj = dict(item)
            latest = db.execute("SELECT * FROM snapshots WHERE item_id=? ORDER BY collected_at DESC LIMIT 1", (item["id"],)).fetchone()
            baseline = db.execute("SELECT * FROM snapshots WHERE item_id=? AND collected_at<=? ORDER BY collected_at DESC LIMIT 1",
                                  (item["id"], target)).fetchone()
            if not latest:
                continue
            obj.update({key: latest[key] for key in ("views", "wants", "status", "collected_at")})
            comparable = bool(baseline and latest["collected_at"] > target and latest["collected_at"] > baseline["collected_at"])
            obj["views_delta"] = latest["views"] - baseline["views"] if comparable and latest["views"] is not None and baseline["views"] is not None else None
            obj["wants_delta"] = latest["wants"] - baseline["wants"] if comparable and latest["wants"] is not None and baseline["wants"] is not None else None
            if any(obj[k] is not None and obj[k] < 0 for k in ("views_delta", "wants_delta")):
                count_anomalies += 1
                obj["views_delta"] = obj["wants_delta"] = None
            obj["baseline_at"] = baseline["collected_at"] if comparable else None
            if comparable and (latest["price"] != baseline["price"] or latest["status"] != baseline["status"]):
                price_changes += 1
            items.append(obj)
        run = db.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    ranked = sorted([x for x in items if x["views_delta"] is not None], key=lambda x: x["views_delta"], reverse=True)[:5]
    seller_rows = []
    for seller in sellers:
        own = [x for x in items if x["seller_id"] == seller["id"]]
        view_changes = [x["views_delta"] for x in own if x["views_delta"] is not None]
        want_changes = [x["wants_delta"] for x in own if x["wants_delta"] is not None]
        seller_rows.append({**seller, "count": len(own), "views_delta": sum(view_changes) if view_changes else None,
                            "wants_delta": sum(want_changes) if want_changes else None})
    comparable_views = [x["views_delta"] for x in items if x["views_delta"] is not None]
    comparable_wants = [x["wants_delta"] for x in items if x["wants_delta"] is not None]
    return {
        "date": datetime.now().astimezone().strftime("%Y-%m-%d"), "hours": hours, "login": last_login,
        "run": dict(run) if run else None, "sellers": seller_rows, "favorites": favorites, "items": items,
        "top": ranked, "valid": run["valid"] if run else 0,
        "found": run["found"] if run else 0,
        "views_delta": sum(comparable_views) if comparable_views else None,
        "wants_delta": sum(comparable_wants) if comparable_wants else None,
        "price_changes": price_changes if any(x["baseline_at"] for x in items) else None,
        "count_anomalies": count_anomalies,
        "uncomparable": sum(x["views_delta"] is None or x["wants_delta"] is None for x in items),
        "settings": settings(), "running": edge_scan_lock.locked(),
    }


class SellerInput(BaseModel):
    url: str
    name: str = Field(default="", max_length=80)


class SellerPatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    selected: bool | None = None


class FavoriteInput(BaseModel):
    url: str
    title: str = Field(default="", max_length=180)
    price: str | None = Field(default=None, max_length=40)


class FavoritePatch(BaseModel):
    selected: bool


class FavoriteFailure(BaseModel):
    state: Literal["失效", "读取失败"]
    reason: str = Field(min_length=1, max_length=180)


class FavoriteSelection(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=500)
    selected: bool


class SettingsInput(BaseModel):
    interval_hours: float = Field(ge=0.25, le=720)
    auto_collect: bool
    schedule_minutes: int = Field(ge=15, le=1440)
    browser_mode: Literal["edge", "edge_extension", "chrome"] = "edge_extension"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
    await edge_session.close()


app = FastAPI(title="闲鱼商品监控日报", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
app.add_middleware(LocalOriginGuard)


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/static/{name}")
def static_file(name: str):
    if name not in ("app.css", "app.js"):
        raise HTTPException(404)
    return FileResponse(ROOT / "static" / name)


@app.get("/api/report")
def get_report(hours: float | None = None):
    try:
        return report(hours if hours is not None else float(settings()["interval_hours"]))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.post("/api/sellers")
def add_seller(body: SellerInput):
    try:
        user_id, url = seller_identity(body.url)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO sellers VALUES (?,?,?,?,?)",
                   (user_id, seller_display_name(body.name, user_id), url, 1, now()))
    return {"id": user_id}


@app.patch("/api/sellers/{seller_id}")
def patch_seller(seller_id: str, body: SellerPatch):
    with connect() as db:
        if not db.execute("SELECT 1 FROM sellers WHERE id=?", (seller_id,)).fetchone():
            raise HTTPException(404)
        if body.name is not None:
            db.execute("UPDATE sellers SET name=? WHERE id=?", (body.name.strip() or seller_id, seller_id))
        if body.selected is not None:
            db.execute("UPDATE sellers SET selected=? WHERE id=?", (int(body.selected), seller_id))
    return {"ok": True}


@app.delete("/api/sellers/{seller_id}")
def delete_seller(seller_id: str):
    with connect() as db:
        db.execute("UPDATE sellers SET selected=0 WHERE id=?", (seller_id,))
    return {"ok": True}


def save_favorites(candidates: list[FavoriteInput]):
    imported = 0
    valid = 0
    with connect() as db:
        for candidate in candidates[:200]:
            item_id = item_identity(candidate.url)
            if not item_id:
                continue
            valid += 1
            exists = db.execute("SELECT 1 FROM favorite_items WHERE id=?", (item_id,)).fetchone()
            db.execute("""INSERT INTO favorite_items (id,url,title,selected,added_at,card_price) VALUES (?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                card_price=COALESCE(excluded.card_price,favorite_items.card_price)""",
                       (item_id, f"{OFFICIAL}/item?id={item_id}",
                        candidate.title.strip() or f"收藏商品 {item_id}", 0, now(), candidate.price))
            imported += not bool(exists)
    return {"imported": imported, "recognized": valid, "limited": len(candidates) >= 200}


@app.patch("/api/favorites/{item_id}")
def patch_favorite(item_id: str, body: FavoritePatch):
    with connect() as db:
        result = db.execute("UPDATE favorite_items SET selected=? WHERE id=?", (int(body.selected), item_id))
        if not result.rowcount:
            raise HTTPException(404, "收藏商品不存在")
    return {"ok": True}


@app.post("/api/favorites/select")
def select_favorites(body: FavoriteSelection):
    ids = list(dict.fromkeys(body.ids))
    if any(not re.fullmatch(r"\d{8,22}", item_id) for item_id in ids):
        raise HTTPException(400, "商品 ID 无效")
    placeholders = ",".join("?" for _ in ids)
    with connect() as db:
        result = db.execute(f"UPDATE favorite_items SET selected=? WHERE id IN ({placeholders})",
                            (int(body.selected), *ids))
    return {"updated": result.rowcount}


@app.post("/api/favorites/{item_id}/failure")
def favorite_failure(item_id: str, body: FavoriteFailure):
    with connect() as db:
        result = db.execute("""UPDATE favorite_items SET state=?,last_checked=?,last_error=? WHERE id=?""",
                            (body.state, now(), body.reason.strip(), item_id))
        if not result.rowcount:
            raise HTTPException(404, "收藏商品不存在")
    return {"ok": True}


@app.put("/api/settings")
def put_settings(body: SettingsInput):
    with connect() as db:
        for key, value in body.model_dump().items():
            db.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, json.dumps(value)))
    return settings()


@app.post("/api/edge/connect")
async def edge_connect():
    try:
        page = await edge_session.current_page()
        await page.bring_to_front()
        return {"url": page.url}
    except Exception as exc:
        raise HTTPException(503, f"无法连接固定 Edge 登录资料：{str(exc).splitlines()[0][:130]}")


@app.post("/api/edge/check")
async def edge_check():
    global last_login
    passed = await edge_session.verify_login()
    last_login = "Edge 已登录" if passed else "Edge 未登录或会话过期"
    return {"ok": passed, "status": last_login}


@app.post("/api/edge/following")
async def edge_following():
    try:
        candidates = await edge_session.following()
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    parsed = [SellerInput(**candidate) for candidate in candidates]
    return chrome_following(parsed)


@app.post("/api/edge/favorites")
async def edge_favorites():
    try:
        candidates = await edge_session.favorites()
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return save_favorites([FavoriteInput(**candidate) for candidate in candidates])


@app.post("/api/edge/favorites/{item_id}/resolve")
async def edge_resolve_favorite(item_id: str):
    global last_login
    async with edge_scan_lock:
        with connect() as db:
            favorite = db.execute("SELECT url FROM favorite_items WHERE id=? AND selected=1", (item_id,)).fetchone()
        if not favorite:
            raise HTTPException(404, "重点商品不存在或已取消")
        if not await edge_session.verify_login():
            last_login = "Edge 未登录或会话过期"
            raise HTTPException(400, "Edge 闲鱼登录未通过验证")
        last_login = "Edge 已登录"
        try:
            data = await edge_session.read_item(favorite["url"])
            owner_id, owner_url = seller_identity(data["seller_url"])
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:
            raise HTTPException(400, f"商品详情读取失败：{str(exc)[:160]}")
        return chrome_run_item(CapturedItem(
            seller_id=owner_id, seller_url=owner_url,
            seller_name=data.get("seller_name", ""), url=favorite["url"],
            title=data["title"], image=data["image"], price=data["price"],
            views=data["views"], wants=data["wants"], status=data["status"]))


@app.post("/api/edge/collect")
async def edge_collect(pending_only: bool = False):
    global last_login
    if edge_scan_lock.locked():
        raise HTTPException(409, "已有 Edge 采集任务运行中")
    async with edge_scan_lock:
        with connect() as db:
            sellers = [] if pending_only else [dict(row) for row in db.execute(
                "SELECT * FROM sellers WHERE selected=1 ORDER BY added_at")]
            favorites = [dict(row) for row in db.execute(
                "SELECT * FROM favorite_items WHERE selected=1 AND (?=0 OR state='待采集') ORDER BY added_at",
                (int(pending_only),))]
        if not sellers and not favorites:
            raise HTTPException(400, "没有待采集的收藏商品" if pending_only else "请先导入收藏商品或勾选重点卖家")
        if not await edge_session.verify_login():
            last_login = "Edge 未登录或会话过期"
            raise HTTPException(400, "Edge 闲鱼登录未通过验证，已停止采集")
        last_login = "Edge 已登录"
        run = chrome_run_start()
        found = valid = 0
        errors = []
        scanned = set()
        try:
            for favorite in favorites:
                found += 1
                scanned.add(favorite["id"])
                try:
                    data = await edge_session.read_item(favorite["url"])
                    owner_id, owner_url = seller_identity(data["seller_url"])
                    chrome_run_item(CapturedItem(
                        seller_id=owner_id, seller_url=owner_url,
                        seller_name=data.get("seller_name", ""),
                        url=favorite["url"], title=data["title"],
                        image=data["image"], price=data["price"], views=data["views"],
                        wants=data["wants"], status=data["status"]))
                    valid += 1
                except Exception as exc:
                    errors.append(f"收藏商品 {favorite['id']}：{str(exc)[:90]}")
                    favorite_failure(favorite["id"], FavoriteFailure(
                        state="失效" if "已被删除" in str(exc) else "读取失败",
                        reason=str(exc)[:180]))
                with connect() as db:
                    db.execute("UPDATE runs SET found=?,valid=? WHERE id=?", (found, valid, run["run_id"]))
                await asyncio.sleep(0.7)
            for seller in sellers:
                try:
                    urls = await edge_session.list_items(seller["url"])
                    urls = [url for url in urls if item_identity(url)][:40]
                    if not urls:
                        errors.append(f"{seller['name']}：主页没有可识别商品")
                        continue
                    for url in urls:
                        if item_identity(url) in scanned:
                            continue
                        found += 1
                        scanned.add(item_identity(url))
                        try:
                            data = await edge_session.read_item(url)
                            owner_id, _ = seller_identity(data["seller_url"])
                            if owner_id != seller["id"]:
                                raise ValueError("商品页未能确认属于所选卖家")
                            chrome_run_item(CapturedItem(
                                seller_id=seller["id"], seller_url=data["seller_url"],
                                seller_name=data.get("seller_name", ""),
                                url=url, title=data["title"],
                                image=data["image"], price=data["price"], views=data["views"],
                                wants=data["wants"], status=data["status"]))
                            valid += 1
                        except Exception as exc:
                            errors.append(f"{seller['name']} / {item_identity(url)}：{str(exc)[:90]}")
                        with connect() as db:
                            db.execute("UPDATE runs SET found=?,valid=? WHERE id=?", (found, valid, run["run_id"]))
                        await asyncio.sleep(0.7)
                except Exception as exc:
                    errors.append(f"{seller['name']}：{str(exc)[:90]}")
        except Exception as exc:
            errors.append(str(exc)[:130])
        return chrome_run_finish(RunFinish(run_id=run["run_id"], found=found, valid=valid, errors=errors))


class ChromeStatus(BaseModel):
    ok: bool


class CapturedItem(BaseModel):
    seller_id: str
    seller_url: str | None = None
    seller_name: str = Field(default="", max_length=80)
    url: str
    title: str = Field(min_length=1, max_length=180)
    image: str = ""
    price: str | None = None
    views: int | None = Field(default=None, ge=0)
    wants: int | None = Field(default=None, ge=0)
    status: str = "未知"


class RunFinish(BaseModel):
    run_id: int
    found: int = Field(ge=0)
    valid: int = Field(ge=0)
    errors: list[str] = Field(default_factory=list)


class RunProgress(BaseModel):
    run_id: int
    found: int = Field(ge=0)
    valid: int = Field(ge=0)


@app.post("/api/chrome/status")
def chrome_status(body: ChromeStatus):
    global last_login
    last_login = "已连接现有浏览器登录页" if body.ok else "现有浏览器登录未确认"
    return {"status": last_login}


@app.post("/api/chrome/following")
def chrome_following(candidates: list[SellerInput]):
    imported = 0
    with connect() as db:
        for candidate in candidates[:300]:
            try:
                user_id, url = seller_identity(candidate.url)
            except ValueError:
                continue
            exists = db.execute("SELECT 1 FROM sellers WHERE id=?", (user_id,)).fetchone()
            db.execute("INSERT OR IGNORE INTO sellers VALUES (?,?,?,?,?)",
                       (user_id, seller_display_name(candidate.name, user_id), url, 0, now()))
            imported += not bool(exists)
    return {"imported": imported}


@app.post("/api/chrome/favorites")
def chrome_favorites(candidates: list[FavoriteInput]):
    return save_favorites(candidates)


@app.post("/api/chrome/run/start")
def chrome_run_start():
    with connect() as db:
        selected_seller = db.execute("SELECT 1 FROM sellers WHERE selected=1").fetchone()
        selected_favorite = db.execute("SELECT 1 FROM favorite_items WHERE selected=1").fetchone()
        if not selected_seller and not selected_favorite:
            raise HTTPException(400, "请先导入收藏商品或勾选重点卖家")
        run_id = db.execute("INSERT INTO runs(started_at,state) VALUES (?,?)", (now(), "运行中")).lastrowid
    return {"run_id": run_id}


@app.post("/api/chrome/run/item")
def chrome_run_item(item: CapturedItem):
    item_id = item_identity(item.url)
    if not item_id:
        raise HTTPException(400, "商品链接不是闲鱼官方商品页")
    if item.views is None and item.wants is None and not item.price:
        raise HTTPException(400, "商品没有可识别指标，已跳过")
    if item.image:
        parsed = urlparse(item.image)
        if parsed.scheme not in ("https", "http"):
            raise HTTPException(400, "商品图片地址无效")
    with connect() as db:
        selected_seller = db.execute("SELECT 1 FROM sellers WHERE id=? AND selected=1", (item.seller_id,)).fetchone()
        selected_favorite = db.execute("SELECT 1 FROM favorite_items WHERE id=? AND selected=1", (item_id,)).fetchone()
        if not selected_seller and not selected_favorite:
            raise HTTPException(400, "商品和卖家均未被选为重点")
        if selected_favorite and not item.seller_url:
            raise HTTPException(400, "收藏商品缺少可确认的卖家主页链接")
        if item.seller_url:
            try:
                confirmed_id, seller_url = seller_identity(item.seller_url)
            except ValueError as exc:
                raise HTTPException(400, str(exc))
            if confirmed_id != item.seller_id:
                raise HTTPException(400, "商品卖家身份不匹配")
            name = seller_display_name(item.seller_name, item.seller_id)
            db.execute("INSERT OR IGNORE INTO sellers VALUES (?,?,?,?,?)",
                       (item.seller_id, name, seller_url, 0, now()))
            if item.seller_name.strip():
                db.execute("UPDATE sellers SET name=? WHERE id=? AND name=?",
                           (name, item.seller_id, f"用户 {item.seller_id}"))
        if not db.execute("SELECT 1 FROM sellers WHERE id=?", (item.seller_id,)).fetchone():
            raise HTTPException(400, "无法确认商品卖家")
        stamp = now()
        db.execute("""INSERT INTO items VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET seller_id=excluded.seller_id,
            title=excluded.title,url=excluded.url,image=excluded.image,
            price=excluded.price,last_seen=excluded.last_seen""",
            (item_id, item.seller_id, item.title, item.url, item.image, item.price, stamp))
        db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?)",
                   (item_id, stamp, item.views, item.wants, item.price, item.status))
        db.execute("""UPDATE favorite_items SET title=?,state='有效',last_checked=?,last_error=NULL WHERE id=?""",
                   (item.title, stamp, item_id))
    return {"id": item_id, "seller_id": item.seller_id,
            "seller_name": seller_display_name(item.seller_name, item.seller_id)}


@app.post("/api/chrome/run/finish")
def chrome_run_finish(body: RunFinish):
    state = "完成" if body.valid and not body.errors else "部分完成" if body.valid else "失败"
    message = "；".join(body.errors[:5]) if body.errors else "采集完成"
    with connect() as db:
        result = db.execute("UPDATE runs SET finished_at=?,state=?,found=?,valid=?,message=? WHERE id=? AND state='运行中'",
                            (now(), state, body.found, body.valid, message, body.run_id))
        if not result.rowcount:
            raise HTTPException(404, "采集任务不存在或已完成")
    return {"state": state, "found": body.found, "valid": body.valid, "message": message}


@app.post("/api/chrome/run/progress")
def chrome_run_progress(body: RunProgress):
    if body.valid > body.found:
        raise HTTPException(400, "有效商品数不能超过发现数")
    with connect() as db:
        result = db.execute("UPDATE runs SET found=?, valid=? WHERE id=? AND state='运行中'",
                            (body.found, body.valid, body.run_id))
        if not result.rowcount:
            raise HTTPException(404, "采集任务不存在或已结束")
    return {"ok": True}
