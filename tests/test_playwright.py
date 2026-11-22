import os
import shutil
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.amazon import CollectionError, check_page, collect_amazon
from backend.amazon_content import document

try:
    from playwright.sync_api import Error, TimeoutError, sync_playwright
except ImportError:
    sync_playwright = None


ASIN = "B0FAKE1001"
PRODUCT_URL = f"https://www.amazon.com/dp/{ASIN}"
PRODUCT_PAGE = f"""
<title>Amazon.com: Foldable Laptop Stand</title>
<input id="ASIN" type="hidden" value="{ASIN}">
<span id="productTitle"> Foldable\nLaptop Stand </span>
<a id="bylineInfo">Visit the ExampleBrand Store</a>
<div id="feature-bullets"><ul>
  <li><span class="a-list-item">Fits laptops up to 15 inches</span></li>
  <li><span class="a-list-item">Folds for storage</span></li>
  <li><span class="a-list-item">See more</span></li>
  <li style="display:none"><span class="a-list-item">Hidden detail</span></li>
</ul></div>
<div id="itemHighlights_feature_div"><p>Aluminum stand</p></div>
<div id="aplus"><h2>Use at your desk</h2><p>Raises the screen during video calls.</p></div>
<div id="productDescription">A folding laptop riser.</div>
<div id="productOverview_feature_div"><table><tr><th>Material</th><td>Aluminum</td></tr></table></div>
<div id="corePrice_feature_div"><span class="priceToPay">$29.99</span></div>
"""
HIDDEN_CAPTCHA = """
<style>.challenge-template { display: none }</style>
<form class="challenge-template" action="/errors/validateCaptcha"><input id="captchacharacters"></form>
"""


class ValidationSignalTests(unittest.TestCase):
    def test_uses_rendered_signals_and_stops_on_a_visible_challenge(self):
        root = document(PRODUCT_PAGE + HIDDEN_CAPTCHA)
        rendered = {"captcha": False, "sign_in": False, "product": True}
        check_page(root, PRODUCT_URL, ASIN, "US", rendered=rendered)
        with self.assertRaisesRegex(CollectionError, "浏览器显示了验证码表单"):
            check_page(root, PRODUCT_URL, ASIN, "US", rendered={**rendered, "captcha": True})
        with self.assertRaisesRegex(CollectionError, "商品详情尚未确认"):
            check_page(root, PRODUCT_URL, ASIN, "US", rendered={**rendered, "product": False})


def mock_snapshot(page):
    page.evaluate.side_effect = lambda *_: {
        "url": page.url, "html": page.content.return_value, "captcha": False, "sign_in": False, "product": True,
    }


@unittest.skipUnless(sync_playwright, "Optional Playwright package unavailable")
class CollectionFlowTests(unittest.TestCase):
    def test_canonical_request_and_capture_fallbacks(self):
        driver = MagicMock()
        browser = driver.__enter__.return_value.chromium.launch.return_value
        page = browser.new_page.return_value
        page.url = PRODUCT_URL
        page.content.return_value = PRODUCT_PAGE
        mock_snapshot(page)
        page.goto.return_value.status = 200
        with patch.dict(os.environ, {"LISTING_AMAZON_PLAYWRIGHT": "1"}), patch("playwright.sync_api.sync_playwright", return_value=driver):
            captured = collect_amazon(f"https://www.amazon.com/test/dp/{ASIN.lower()}?tag=example")
            self.assertEqual(page.goto.call_args.args[0], PRODUCT_URL)
            self.assertEqual(page.goto.call_args.kwargs["wait_until"], "commit")
            self.assertEqual(captured["fields"]["title"]["text"], "Foldable Laptop Stand")
            self.assertEqual(captured["fields"]["bullets"]["text"], "Fits laptops up to 15 inches\nFolds for storage")
            self.assertEqual(captured["fields"]["highlights"]["text"], "Aluminum stand")
            self.assertEqual(set(captured["fields"]), {"title", "bullets", "highlights", "aplus", "raw_text", "public_specs", "price"})
            self.assertEqual(captured["fields"]["title"]["state"], "pending")
            page.content.return_value = '<title>Robot Check</title><input id="captchacharacters">'
            with self.assertRaisesRegex(CollectionError, "网页标题为 Robot Check"):
                collect_amazon(ASIN)
            page.content.return_value = PRODUCT_PAGE
            page.goto.return_value.status = 403
            with self.assertRaisesRegex(CollectionError, "HTTP 403"):
                collect_amazon(ASIN)
            page.goto.return_value.status = 200
            page.url = PRODUCT_URL.replace(ASIN, "B0FAKE1002")
            with self.assertRaisesRegex(CollectionError, "实际打开 Amazon US B0FAKE1002"):
                collect_amazon(ASIN)
        self.assertEqual(browser.close.call_count, 4)

    def test_market_links_and_load_stages(self):
        driver = MagicMock()
        launch = driver.__enter__.return_value.chromium.launch
        browser = launch.return_value
        page = browser.new_page.return_value
        japanese_url = f"https://www.amazon.co.jp/dp/{ASIN}"
        page.url = japanese_url
        page.content.return_value = PRODUCT_PAGE.replace(" Foldable\nLaptop Stand ", "卓上スタンド")
        mock_snapshot(page)
        page.goto.return_value.status = 200
        with patch.dict(os.environ, {"LISTING_AMAZON_PLAYWRIGHT": "1"}), patch("playwright.sync_api.sync_playwright", return_value=driver):
            page.wait_for_function.side_effect = TimeoutError("Optional page content took too long")
            result = collect_amazon(f"{japanese_url}?tag=test", market="US")
            self.assertEqual(page.goto.call_args.args[0], japanese_url)
            browser.new_page.assert_called_with(locale="ja-JP")
            self.assertEqual(result["market"], "JP")
            self.assertEqual(result["fields"]["title"]["text"], "卓上スタンド")
            self.assertIn("已保留可读取的字段", result["message"])
            page.goto.side_effect = TimeoutError("No response")
            with self.assertRaisesRegex(CollectionError, "连接 Amazon JP 超时") as timeout:
                collect_amazon(ASIN, market="JP")
            self.assertEqual(timeout.exception.status, 504)
            page.goto.side_effect = Error("Page.goto: net::ERR_NAME_NOT_RESOLVED")
            with self.assertRaisesRegex(CollectionError, "ERR_NAME_NOT_RESOLVED"):
                collect_amazon(ASIN, market="JP")
            launch.side_effect = TimeoutError("Failed to start Chromium")
            with self.assertRaisesRegex(CollectionError, "Chromium 启动超时"):
                collect_amazon(ASIN, market="JP")


@unittest.skipUnless(sync_playwright, "Optional Playwright package unavailable")
class PlaywrightCollectionTests(unittest.TestCase):
    def collect(self, html, value=ASIN, status=200):
        requests = []

        @contextmanager
        def controlled_browser():
            with sync_playwright() as playwright:
                executable = playwright.chromium.executable_path
                if not Path(executable).is_file():
                    executable = shutil.which("chromium") or shutil.which("chromium-browser")
                if not executable:
                    self.skipTest("Chromium unavailable")

                def launch(**options):
                    try:
                        browser = playwright.chromium.launch(executable_path=executable, **options)
                    except Error as exc:
                        self.skipTest(f"Chromium unavailable: {str(exc).splitlines()[0]}")

                    def new_page(**options):
                        page = browser.new_page(**options)

                        def serve(route):
                            requests.append(route.request.url)
                            route.fulfill(status=status, content_type="text/html", body=html)

                        page.route("**/*", serve)
                        return page

                    return SimpleNamespace(new_page=new_page, close=browser.close)

                yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        with patch.dict(os.environ, {"LISTING_AMAZON_PLAYWRIGHT": "1"}), patch("playwright.sync_api.sync_playwright", controlled_browser):
            data = collect_amazon(value)
        self.assertEqual(requests, [PRODUCT_URL])
        return data

    def test_collects_visible_product_copy(self):
        data = self.collect(PRODUCT_PAGE + HIDDEN_CAPTCHA, f"https://www.amazon.com/Foldable-Stand/dp/{ASIN.lower()}?tag=example")
        self.assertEqual(data["asin"], ASIN)
        self.assertEqual(data["brand"], "ExampleBrand")
        self.assertEqual(data["fields"]["title"]["text"], "Foldable Laptop Stand")
        self.assertEqual(data["fields"]["bullets"]["text"], "Fits laptops up to 15 inches\nFolds for storage")
        self.assertIn("Raises the screen", data["fields"]["aplus"]["text"])
        self.assertEqual(data["fields"]["raw_text"]["text"], "A folding laptop riser.")
        self.assertIn("Material: Aluminum", data["fields"]["public_specs"]["text"])
        self.assertEqual(data["fields"]["price"]["text"], "$29.99")
        self.assertTrue(all(row["state"] == "pending" and row["collected_at"] for row in data["fields"].values()))
        self.assertNotIn("attributes", data["fields"])
        self.assertNotIn("search_terms", data["fields"])

    def test_stops_for_captcha_or_wrong_asin(self):
        with self.assertRaisesRegex(CollectionError, "人机验证"):
            self.collect('<title>Amazon.com Robot Check</title><input id="captchacharacters">')
        with self.assertRaisesRegex(CollectionError, "页面选中变体为 B0FAKE1002"):
            self.collect(PRODUCT_PAGE.replace(f'value="{ASIN}"', 'value="B0FAKE1002"'))
        with self.assertRaisesRegex(CollectionError, "HTTP 403"):
            self.collect("Forbidden", status=403)
