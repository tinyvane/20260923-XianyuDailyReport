"""Reuse one dedicated Edge profile across launches; user logs in on the official site."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

OFFICIAL = "https://www.goofish.com"
CHALLENGE = re.compile(r"非法访问|安全验证|验证码|滑块|请登录|扫码登录")


class EdgeSession:
    def __init__(self, profile: Path):
        self.profile = profile
        self.context = None
        self.playwright = None
        self.launch_lock = asyncio.Lock()

    async def connect(self):
        async with self.launch_lock:
            if self.context:
                return self.context
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
            from playwright.async_api import async_playwright
            self.profile.parent.mkdir(exist_ok=True)
            self.playwright = await async_playwright().start()
            try:
                self.context = await self.playwright.chromium.launch_persistent_context(
                    str(self.profile), channel="msedge", headless=False,
                    locale="zh-CN", viewport={"width": 1365, "height": 900})
            except Exception:
                await self.playwright.stop()
                self.playwright = None
                raise
            self.context.on("close", lambda: setattr(self, "context", None))
            page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            if page.url == "about:blank":
                try:
                    await page.goto(OFFICIAL, wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    if urlparse(page.url).hostname not in ("www.goofish.com", "goofish.com"):
                        raise
            return self.context

    async def current_page(self):
        context = await self.connect()
        pages = [page for page in context.pages if urlparse(page.url).hostname in ("www.goofish.com", "goofish.com")]
        return pages[-1] if pages else context.pages[0] if context.pages else await context.new_page()

    async def verify_login(self):
        context = await self.connect()
        cookies = {cookie["name"]: cookie["value"] for cookie in await context.cookies(OFFICIAL)}
        if not cookies.get("unb"):
            return False
        try:
            response = await context.request.post(
                "https://passport.goofish.com/newlogin/hasLogin.do",
                params={"appName": "xianyu", "fromSite": "77"},
                form={
                    "hid": cookies["unb"], "ltl": "true", "appName": "xianyu",
                    "appEntrance": "web", "_csrf_token": cookies.get("XSRF-TOKEN", ""),
                    "hsiz": cookies.get("cookie2", ""), "bizParams": "taobaoBizLoginFrom=web",
                    "mainPage": "false", "isMobile": "false", "lang": "zh_CN",
                    "fromSite": "77", "isIframe": "true", "documentReferer": OFFICIAL + "/",
                    "defaultView": "hasLogin", "umidTag": "SERVER", "deviceId": cookies.get("cna", ""),
                }, timeout=12000)
            body = await response.json()
            if not (response.ok and body.get("content", {}).get("success") is True):
                return False
            page = await self.current_page()
            await page.wait_for_timeout(700)
            return not await page.evaluate("""() => [...document.querySelectorAll('a,button,[role=button]')].some(node =>
              node.getClientRects().length && node.textContent?.trim() === '登录')""")
        except Exception:
            return False

    async def following(self):
        if not await self.verify_login():
            raise ValueError("Edge 闲鱼会话未通过验证，请在原窗口重新登录")
        page = await self.current_page()
        if not re.search(r"/(personal|user|follow)", urlparse(page.url).path, re.I):
            raise ValueError("请先在现有 Edge 闲鱼窗口打开自己的关注列表")
        text = (await page.locator("body").inner_text())[:4000]
        if CHALLENGE.search(text) or "关注" not in text:
            raise ValueError("当前页面未显示可读取的关注列表")
        return await page.locator('a[href*="/personal"],a[href*="/user"]').evaluate_all(
            """nodes => nodes.map(a => ({url:a.href,name:(a.innerText || a.getAttribute('title') || '').trim().slice(0,80)}))"""
        )

    async def favorites(self):
        if not await self.verify_login():
            raise ValueError("Edge 闲鱼会话未通过验证，请在原窗口重新登录")
        page = await self.current_page()
        if urlparse(page.url).path != "/collection":
            try:
                await page.goto(OFFICIAL + "/collection", wait_until="domcontentloaded", timeout=30000)
            except Exception as exc:
                raise ValueError("无法打开闲鱼「我的收藏」页面，请检查网络后重试") from exc
        await page.wait_for_timeout(900)
        old_scroll = await page.evaluate("window.scrollY")
        try:
            seen = {}
            stable = 0
            for index in range(24):
                before = len(seen)
                candidates = await page.locator('a[href*="/item?"]').evaluate_all(
                    """nodes => nodes.map(a => {
                      const raw = a.innerText || '';
                      const lines = raw.split(/\\n+/).map(x => x.trim()).filter(Boolean);
                      const title = lines.find(x => x !== '取消收藏' && x !== '我想要' && !/^¥/.test(x)) || a.querySelector('img')?.alt || '';
                      const price = raw.match(/¥\\s*([\\d,.]+(?:[ \\t]*[-–~][ \\t]*[\\d,.]+)?)/)?.[1] || null;
                      return {url:a.href,title:title.slice(0,180),price};
                    })""")
                for candidate in candidates:
                    item_id = parse_qs(urlparse(candidate["url"]).query).get("id", [""])[0]
                    if re.fullmatch(r"\d{8,22}", item_id):
                        seen[item_id] = candidate
                stable = stable + 1 if len(seen) == before else 0
                if len(seen) >= 200 or (index >= 4 and stable >= 4):
                    break
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(700)
            text = (await page.locator("body").inner_text())[:1500]
            if CHALLENGE.search(text):
                raise ValueError("收藏页要求登录或安全验证")
            found = list(seen.values())[:200]
            if not found:
                raise ValueError("收藏页没有找到可识别的商品链接，请确认处于「有效宝贝」且商品卡片已加载")
            return found
        finally:
            await page.evaluate("(y) => window.scrollTo(0, y)", old_scroll)

    async def list_items(self, url: str):
        context = await self.connect()
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1200)
            text = (await page.locator("body").inner_text())[:1500]
            if CHALLENGE.search(text):
                raise ValueError("卖家页面要求登录或安全验证")
            return await page.locator('a[href*="/item?"]').evaluate_all(
                """nodes => [...new Set(nodes.map(a => a.href))].slice(0,80)"""
            )
        finally:
            await page.close()

    async def read_item(self, url: str):
        context = await self.connect()
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(1200)
            data = await page.evaluate(r"""() => {
              const body = document.body?.innerText || '';
              const text = (document.querySelector('main')?.innerText || body.slice(0,14000)).slice(0,14000);
              const read = (...patterns) => {
                for (const pattern of patterns) {
                  const match = text.match(pattern);
                  if (match) {
                    const raw = match[1];
                    return Math.round(parseFloat(raw) * (raw.endsWith('万') ? 10000 : raw.endsWith('千') ? 1000 : 1));
                  }
                }
                return null;
              };
              const seller = document.querySelector('main a[href*="/personal"],main a[href*="/user"]') ||
                document.querySelector('a[href*="/personal"],a[href*="/user"]');
              return {
                blocked: /非法访问|安全验证|验证码|滑块|请登录|扫码登录/.test(body.slice(0,1200)),
                deleted: /糟糕！宝贝被删掉了|宝贝被删掉了|商品已被删除/.test(body.slice(0,1200)),
                title: (document.querySelector('h1')?.innerText || document.querySelector('meta[property="og:title"]')?.content || '').trim().slice(0,180),
                seller_url: seller?.href || '',
                seller_name: (seller?.innerText || '').split(/\r?\n/).map(line => line.trim()).find(Boolean)?.slice(0,80) || '',
                image: document.querySelector('meta[property="og:image"]')?.content || '',
                price: text.match(/¥\s*([\d,.]+(?:[ \t]*[-–~][ \t]*[\d,.]+)?)/)?.[1] || null,
                views: read(/(\d+(?:\.\d+)?[万千]?)\s*(?:次)?浏览/, /浏览[ \t]+(\d+(?:\.\d+)?[万千]?)/),
                wants: read(/(\d+(?:\.\d+)?[万千]?)\s*人想要/, /想要[ \t]+(\d+(?:\.\d+)?[万千]?)/),
                status: /宝贝已下架|商品已下架|已售出/.test(text) ? '已下架' : '未知'
              };
            }""")
            if data["blocked"]:
                raise ValueError("商品页面要求登录或安全验证")
            if data["deleted"]:
                raise ValueError("商品已被删除")
            if not data["title"] or all(data[key] is None for key in ("price", "views", "wants")):
                raise ValueError("商品页面缺少可识别的标题或指标")
            return data
        finally:
            await page.close()

    async def close(self):
        if self.context:
            await self.context.close()
            self.context = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
