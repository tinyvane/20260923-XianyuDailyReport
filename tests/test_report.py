import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import monitor
from edge_adapter import EdgeSession


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_db = monitor.DATA, monitor.DB
        monitor.DATA = Path(self.temp.name)
        monitor.DB = monitor.DATA / "report.sqlite3"
        monitor.init_db()

    def tearDown(self):
        monitor.DATA, monitor.DB = self.old_data, self.old_db
        self.temp.cleanup()

    def seed(self, first_age=25, last_age=1):
        current = datetime.now(timezone.utc)
        old = (current - timedelta(hours=first_age)).isoformat(timespec="seconds")
        new = (current - timedelta(hours=last_age)).isoformat(timespec="seconds")
        with monitor.connect() as db:
            db.execute("INSERT INTO sellers VALUES (?,?,?,?,?)", ("seller123", "测试卖家", "https://www.goofish.com/personal?userId=seller123", 1, old))
            db.execute("INSERT INTO items VALUES (?,?,?,?,?,?,?)", ("1234567890", "seller123", "测试商品", "https://www.goofish.com/item?id=1234567890", "", "120", new))
            db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?)", ("1234567890", old, 20, 3, "100", "在售"))
            db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?)", ("1234567890", new, 73, 8, "120", "在售"))

    def test_comparison_uses_snapshot_before_window(self):
        self.seed()
        result = monitor.report(24)
        self.assertEqual(result["views_delta"], 53)
        self.assertEqual(result["wants_delta"], 5)
        self.assertEqual(result["price_changes"], 1)
        self.assertEqual(result["top"][0]["id"], "1234567890")

    def test_missing_baseline_is_unknown(self):
        self.seed(first_age=2, last_age=1)
        result = monitor.report(24)
        self.assertIsNone(result["views_delta"])
        self.assertIsNone(result["wants_delta"])
        self.assertIsNone(result["price_changes"])
        self.assertEqual(result["uncomparable"], 1)

    def test_rejects_non_official_links(self):
        for url in ("http://www.goofish.com/personal?userId=123456",
                    "https://evil.example/personal?userId=123456",
                    "https://www.goofish.com/item?id=12345678"):
            with self.assertRaises(ValueError):
                monitor.seller_identity(url)
        self.assertIsNone(monitor.item_identity("https://evil.example/item?id=12345678"))

    def test_metrics_do_not_invent_zero(self):
        self.assertEqual(monitor.metric("浏览 1.2万 想要 50", "views"), 12000)
        self.assertEqual(monitor.metric("浏览 1.2万 想要 50", "wants"), 50)
        self.assertIsNone(monitor.metric("没有展示浏览计数", "views"))

    def test_chrome_capture_persists_snapshot(self):
        with monitor.connect() as db:
            db.execute("INSERT INTO sellers VALUES (?,?,?,?,?)", ("seller123", "测试卖家", "https://www.goofish.com/personal?userId=seller123", 1, monitor.now()))
        run = monitor.chrome_run_start()
        saved = monitor.chrome_run_item(monitor.CapturedItem(
            seller_id="seller123", url="https://www.goofish.com/item?id=1234567890",
            title="测试商品", price="99", views=15, wants=2))
        self.assertEqual(saved["id"], "1234567890")
        monitor.chrome_run_progress(monitor.RunProgress(run_id=run["run_id"], found=1, valid=1))
        self.assertEqual(monitor.report(24)["run"]["valid"], 1)
        finish = monitor.chrome_run_finish(monitor.RunFinish(run_id=run["run_id"], found=1, valid=1))
        self.assertEqual(finish["state"], "完成")
        result = monitor.report(24)
        self.assertEqual(len(result["items"]), 1)
        self.assertIsNone(result["views_delta"])

    def test_favorite_import_and_owner_confirmation(self):
        url = "https://www.goofish.com/item?id=1234567890&spm=collection"
        imported = monitor.save_favorites([monitor.FavoriteInput(url=url, title="收藏的宝贝")])
        self.assertEqual(imported["imported"], 1)
        self.assertEqual(imported["recognized"], 1)
        self.assertEqual(len(monitor.report(24)["favorites"]), 1)
        self.assertFalse(monitor.report(24)["favorites"][0]["selected"])
        monitor.patch_favorite("1234567890", monitor.FavoritePatch(selected=True))
        with self.assertRaises(Exception):
            monitor.chrome_run_item(monitor.CapturedItem(
                seller_id="seller123", seller_url="https://www.goofish.com/personal?userId=different123",
                url=url, title="收藏的宝贝", price="99"))
        saved = monitor.chrome_run_item(monitor.CapturedItem(
            seller_id="seller123", seller_url="https://www.goofish.com/personal?userId=seller123",
            seller_name="宝贝卖家", url=url, title="商品详情", price="99", views=20))
        self.assertEqual(saved["id"], "1234567890")
        result = monitor.report(24)
        self.assertEqual(result["items"][0]["seller_id"], "seller123")
        self.assertEqual(result["favorites"][0]["observed_title"], "商品详情")
        self.assertTrue(result["sellers"][0]["linked"])
        self.assertFalse(result["sellers"][0]["selected"])

    def test_deselected_favorite_is_not_reported_without_selected_seller(self):
        url = "https://www.goofish.com/item?id=1234567890"
        monitor.save_favorites([monitor.FavoriteInput(url=url, title="商品")])
        monitor.patch_favorite("1234567890", monitor.FavoritePatch(selected=True))
        monitor.chrome_run_item(monitor.CapturedItem(
            seller_id="seller123", seller_url="https://www.goofish.com/personal?userId=seller123",
            url=url, title="商品", price="99"))
        monitor.patch_favorite("1234567890", monitor.FavoritePatch(selected=False))
        self.assertEqual(monitor.report(24)["items"], [])
        self.assertFalse(monitor.report(24)["sellers"][0]["linked"])

    def test_only_selected_favorites_count_for_automatically_linked_seller(self):
        urls = [f"https://www.goofish.com/item?id={item_id}" for item_id in ("1234567890", "1234567891")]
        monitor.save_favorites([monitor.FavoriteInput(url=url, title="候选") for url in urls])
        for url in urls:
            monitor.patch_favorite(url.split("=")[-1], monitor.FavoritePatch(selected=True))
            monitor.chrome_run_item(monitor.CapturedItem(
                seller_id="seller123", seller_url="https://www.goofish.com/personal?userId=seller123",
                seller_name="同一卖家", url=url, title="商品", price="99"))
        monitor.patch_favorite("1234567891", monitor.FavoritePatch(selected=False))
        result = monitor.report(24)
        self.assertEqual([item["id"] for item in result["items"]], ["1234567890"])
        self.assertEqual(result["sellers"][0]["count"], 1)
        self.assertTrue(result["sellers"][0]["linked"])
        self.assertFalse(result["sellers"][0]["selected"])

    def test_selecting_favorite_can_resolve_owner_without_user_entering_url(self):
        item_id = "1234567890"
        url = f"https://www.goofish.com/item?id={item_id}"
        monitor.save_favorites([monitor.FavoriteInput(url=url, title="候选")])
        monitor.patch_favorite(item_id, monitor.FavoritePatch(selected=True))
        detail = {
            "seller_url": "https://www.goofish.com/personal?userId=seller123",
            "seller_name": "自动找到的卖家", "title": "商品详情", "image": "",
            "price": "99", "views": 20, "wants": 3, "status": "未知",
        }
        with patch.object(monitor.edge_session, "verify_login", AsyncMock(return_value=True)), \
             patch.object(monitor.edge_session, "read_item", AsyncMock(return_value=detail)):
            saved = asyncio.run(monitor.edge_resolve_favorite(item_id))
        self.assertEqual(saved["seller_name"], "自动找到的卖家")
        result = monitor.report(24)
        self.assertEqual(result["sellers"][0]["name"], "自动找到的卖家")
        self.assertTrue(result["sellers"][0]["linked"])
        self.assertFalse(result["sellers"][0]["selected"])
        self.assertEqual(result["items"][0]["seller_id"], "seller123")

    def test_favorite_failure_and_old_database_migration(self):
        with monitor.connect() as db:
            db.execute("DROP TABLE favorite_items")
            db.execute("""CREATE TABLE favorite_items (
                id TEXT PRIMARY KEY, url TEXT NOT NULL, title TEXT NOT NULL,
                selected INTEGER NOT NULL DEFAULT 1, added_at TEXT NOT NULL)""")
        monitor.init_db()
        monitor.save_favorites([monitor.FavoriteInput(
            url="https://www.goofish.com/item?id=1234567890", title="旧收藏", price="29.90")])
        monitor.favorite_failure("1234567890", monitor.FavoriteFailure(
            state="失效", reason="商品已被删除"))
        favorite = monitor.report(24)["favorites"][0]
        self.assertEqual(favorite["state"], "失效")
        self.assertEqual(favorite["last_error"], "商品已被删除")
        self.assertEqual(favorite["card_price"], "29.90")
        self.assertEqual(monitor.select_favorites(monitor.FavoriteSelection(
            ids=["1234567890"], selected=False))["updated"], 1)
        self.assertEqual(monitor.report(24)["favorites"][0]["selected"], 0)

    def test_edge_login_requires_page_without_visible_login(self):
        session = EdgeSession(Path(self.temp.name) / "profile")
        response = SimpleNamespace(ok=True, json=AsyncMock(return_value={"content": {"success": True}}))
        context = SimpleNamespace(
            cookies=AsyncMock(return_value=[{"name": "unb", "value": "test-user"}]),
            request=SimpleNamespace(post=AsyncMock(return_value=response)))
        page = SimpleNamespace(wait_for_timeout=AsyncMock(), evaluate=AsyncMock(return_value=True))
        session.connect = AsyncMock(return_value=context)
        session.current_page = AsyncMock(return_value=page)
        self.assertFalse(asyncio.run(session.verify_login()))


if __name__ == "__main__":
    unittest.main()
