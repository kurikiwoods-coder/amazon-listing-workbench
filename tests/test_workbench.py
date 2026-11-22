import io
import json
import tempfile
import time
import unittest
from contextlib import contextmanager
from email.message import EmailMessage
from pathlib import Path
from threading import Event
from unittest.mock import patch
from urllib.error import HTTPError

import httpx
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from PIL import Image

from backend.llm import LLM, SemanticReport
from backend.jobs import CaptureWorker
from backend.main import create_app
from backend.marketplaces import product_url
from backend.amazon import CollectionError
from backend.models import ListingInput, get_asin
from backend.public_archive import ARCHIVE_BASE, parse_archive
from backend.rules import rule_findings, rules_for
from backend.store import Store
from tests.calibration import evaluate, synthetic_controls


def completed(client, response):
    assert response.status_code in {200, 201}, response.text
    val = response.json()
    if val["cached"]:
        return val["cached"]
    for _ in range(70):
        job = client.get(f"/api/jobs/{val['job']['id']}").json()
        if job["status"] == "succeeded":
            return job["result"]
        assert job["status"] != "failed", job
        time.sleep(.05)
    raise AssertionError("任务超时")


def capture_result(client, response):
    task = response.raise_for_status().json()
    for _ in range(80):
        current = client.get(f"/api/amazon/workflows/{task['id']}").raise_for_status().json()
        if current["status"] in {"succeeded", "failed"}:
            return current
        time.sleep(.04)
    raise AssertionError("Capture workflow did not complete")


SAVED_PRODUCT = """
<!doctype html>
<html><head><meta charset="utf-8">
<link rel="canonical" href="https://www.amazon.co.uk/dp/B0FIXTURE0">
<title>Example Amazon detail page</title></head>
<body>
  <div id="recommendations"><input id="ASIN" value="B0FIXTURE2">
    <span class="priceToPay">£9.99</span></div>
  <form id="addToCart"><input type="hidden" name="ASIN" value="B0FIXTURE1"></form>
  <h1 id="productTitle">Fixture Café Grinder</h1>
  <div id="feature-bullets">
    <h2>About this item</h2>
    <ul><li>Adjust the grind size</li><li>Fits on a narrow counter</li>
      <li class="aok-hidden">Unshown claim</li><li>See more</li></ul>
    <div data-feature-name="itemHighlights">
      <h3>Item Highlights</h3><ul><li>Stainless steel</li><li>Manual grinding</li></ul>
    </div>
  </div>
  <div id="cr-summary-ai"><h3>Item Highlights</h3><p>Review sentiment</p></div>
  <div id="productOverview_feature_div"><table><tr><th>Material</th><td>Steel</td></tr></table></div>
  <div id="aplus"><h2>Use at home</h2><p>Set the dial before grinding.</p>
    <script>not listing copy</script></div>
  <div id="corePriceDisplay_desktop_feature_div"><span class="priceToPay">£19.99</span></div>
</body></html>
"""

PUBLIC_PRODUCT = """
<!doctype html><html><head><meta charset="utf-8">
<link rel="canonical" href="https://img.team/products/B0FIXTURE1"></head>
<body><main><h1>Fixture USA Stainless Steel Kettle</h1>
<section><h2>商品信息</h2><dl><dt>品牌</dt><dd>Fixture</dd></dl>
<p><a href="https://www.amazon.com/dp/B0FIXTURE1">商品原页</a></p></section>
<section><h2>商品参数</h2><table>
<tr><th>Material</th><td>Stainless steel</td></tr><tr><th>ASIN ‏ :</th><td>B0FIXTURE1</td></tr>
</table><h2>源页面卖点</h2><ul><li>Pour from a narrow spout</li><li>Heats up to 0.8 L</li></ul></section>
<section><h2>可见评论摘录</h2><ul><li>This comment is separate</li></ul></section>
<section><h2>商品图库与 A+ 素材</h2><img alt="A+ 图片" src="/assets/photo"></section>
<p>Amazon数据来源：amazon_public_detail · 原始采集时间：2026-10-19T14:45:36+00:00</p>
</main></body></html>
""".encode()


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "test.sqlite"
        self.client = self.enterContext(TestClient(create_app(self.db)))
        self.product = self.client.get("/api/products").json()[0]
        self.base = f"/api/products/{self.product['id']}"

    def detail(self):
        output = self.client.get(self.base)
        self.assertEqual(output.status_code, 200, output.text)
        return output.json()

    def test_review_rescore_release_and_export(self):
        project = self.detail()
        baseline = project["latest_audit"]
        p0 = [item for item in baseline["data"]["result"]["issues"] if item["priority"] == "P0"]
        self.assertEqual({item["kind"] for item in p0}, {"policy", "factual"})
        draft_ids = {}
        for strategy in ("A", "B", "C"):
            output = completed(self.client, self.client.post(f"{self.base}/rewrite", json={
                "audit_id": baseline["id"], "strategy": strategy,
            }))
            draft_ids[strategy] = output["id"]
        candidates = [self.client.get(f"{self.base}/candidates/{draft_ids[item]}").json() for item in ("A", "B", "C")]
        self.assertEqual(len({item["data"]["validation"]["copy"] for item in candidates}), 3)
        original = candidates[1]
        self.assertFalse(original["data"]["validation"]["ready"])
        rows = [{key: row[key] for key in ("key", "text", "decision")} for row in original["data"]["validation"]["rows"]]
        for row in rows:
            if row["decision"] == "pending":
                row["decision"] = "accepted"
        candidate_url = f"{self.base}/candidates/{original['id']}"
        approved = self.client.post(f"{candidate_url}/review", json={"lines": rows, "note": "人工核对演示"}).json()
        self.assertTrue(approved["data"]["validation"]["ready"], approved["data"]["validation"]["blockers"])
        approved_url = f"{self.base}/candidates/{approved['id']}"
        rejected = [row.copy() for row in rows]
        original_line = next(row for row in rejected if row["key"] == "bullets.0")
        original_line["decision"] = "rejected"
        rejection = self.client.post(f"{candidate_url}/review", json={"lines": rejected}).json()
        self.assertFalse(rejection["data"]["validation"]["ready"])
        self.assertIn("All features work", rejection["data"]["validation"]["fields"]["bullets"]["text"])
        rows[0]["text"] += " Waterproof up to 100 m"
        unverified = self.client.post(f"{candidate_url}/review", json={"lines": rows}).json()
        self.assertFalse(unverified["data"]["validation"]["ready"])
        self.assertEqual(self.client.post(f"{self.base}/candidates/{unverified['id']}/publication",
                                          json={"published_at": baseline["created_at"]}).status_code, 409)
        # Change the project-level context before rescoring. The review must still use the frozen context.
        facts = project["context"]["data"]
        facts["questions"][0]["text"] = "Does this use a power cord?"
        update = self.client.put(f"{self.base}/context", json=facts).json()
        self.assertNotEqual(update["id"], baseline["context_id"])
        rescore = completed(self.client, self.client.post(f"{approved_url}/rescore"))
        result = self.client.get(f"{self.base}/audits/{rescore['id']}").json()
        self.assertEqual(result["context_id"], baseline["context_id"])
        self.assertEqual(result["data"]["rules"], baseline["data"]["rules"])
        self.assertGreater(result["data"]["comparison"]["score_delta"], 0)
        self.assertEqual(sum(item["priority"] == "P0" for item in result["data"]["comparison"]["resolved"]), 2)
        self.assertEqual(completed(self.client, self.client.post(f"{approved_url}/rescore")), rescore)
        self.assertEqual(self.detail()["snapshot"]["id"], project["snapshot"]["id"])
        for kind in ("md", "xlsx", "pdf"):
            export = self.client.get(f"{self.base}/export", params={"audit_id": rescore["id"], "format": kind})
            self.assertEqual(export.status_code, 200, export.text[:240])
            if kind == "xlsx":
                workbook = load_workbook(io.BytesIO(export.content))
                self.assertIn("复评对比", workbook.sheetnames)
            elif kind == "pdf":
                self.assertTrue(export.content.startswith(b"%PDF-"))
            else:
                self.assertIn("复评对比", export.text)
        live = self.client.post(f"{approved_url}/publication", json={"published_at": baseline["created_at"], "note": "模拟上线记录"})
        self.assertEqual(live.status_code, 200)
        self.assertEqual(self.detail()["publications"][0]["candidate_id"], approved["id"])

    def test_import_and_partial_scores(self):
        product = self.client.post("/api/products", json={
            "asin": "https://www.amazon.com/test/dp/B0FAKE1001?tag=test",
            "name": "Import test", "category": "Kitchen", "brand": "Demo",
        }).json()
        base = f"/api/products/{product['id']}"
        csv = b"ASIN,Title,Bullet Point 1\nB0FAKE1001,Demo Scale,Measures grams\nB0FAKE1002,Other Product,Other text\n"
        output = self.client.post(f"{base}/imports", files={"file": ("listing.csv", csv)}).json()
        self.assertEqual(output["fields"]["title"]["state"], "pending")
        self.assertEqual(output["fields"]["bullets"]["text"], "Measures grams")
        data = output["fields"]
        data["title"]["state"] = "confirmed"
        data["highlights"] = {"state": "na", "source": "手动核对"}
        data["aplus"] = {"state": "absent", "source": "手动核对"}
        snap = self.client.post(f"{base}/snapshots", json={"fields": data}).json()
        audit = completed(self.client, self.client.post(f"{base}/audit", json={"mode": "rules"}))
        result = self.client.get(f"{base}/audits/{audit['id']}").json()["data"]["result"]
        modules = {row["key"]: row for row in result["module_scores"]}
        self.assertEqual(modules["highlights"]["status"], "na")
        self.assertEqual(modules["bullets"]["source_state"], "pending")
        self.assertIsNotNone(modules["bullets"]["score"])
        self.assertIsNone(modules["aplus"]["score"])
        self.assertTrue(result["provisional"])
        self.assertEqual(result["applicable_weight"], 8)
        self.assertEqual(result["assessed_weight"], 2)
        self.assertEqual(completed(self.client, self.client.post(f"{base}/audit", json={"mode": "rules"})), audit)
        restored = self.client.post(f"{base}/snapshots/{snap['id']}/restore").json()
        self.assertEqual(restored["version"], snap["version"] + 1)
        self.assertEqual(restored["fields"], snap["fields"])
        png = io.BytesIO()
        Image.new("RGB", (200, 60), "white").save(png, format="PNG")
        with patch.dict("os.environ", {"LISTING_EDITOR_OCR": "no-tesseract-test"}):
            image = self.client.post(f"{base}/imports", files={"file": ("original.png", png.getvalue())}).json()
        self.assertTrue(image["image_id"])
        original = self.client.get(f"{base}/imports/{image['image_id']}")
        self.assertEqual(original.headers["content-type"], "image/png")

    def test_url_validation_and_cross_project_isolation(self):
        self.assertEqual(get_asin("amazon.com/gp/product/B0DEMO1001"), "B0DEMO1001")
        with self.assertRaises(ValueError):
            get_asin("https://amazon.com.example.net/dp/B0DEMO1001")
        self.assertEqual(self.client.get(f"{self.base}/snapshots/unknown").status_code, 404)
        self.assertEqual(self.client.post("/api/demo", headers={"origin": "https://outside.example"}).status_code, 403)

    def test_30_cross_category_regression_cases(self):
        for case in synthetic_controls():
            with self.subTest(product=case["product"]["asin"], variant=case["variant"]):
                result = evaluate(case)
                self.assertEqual(result, evaluate(case))
                self.assertFalse(any(issue["kind"] not in {"factual", "policy"} for issue in result["issues"] if issue["priority"] == "P0"))
                outcome = case["variant"]
                p0 = {issue["kind"] for issue in result["issues"] if issue["priority"] == "P0"}
                self.assertEqual(p0, {"policy"} if outcome == "too_long" else {"factual"} if outcome == "conflict" else set())
                dims = {axis["key"]: axis for axis in result["dimensions"]}
                if outcome == "missing":
                    self.assertTrue(result["provisional"])
                    self.assertIsNone(dims["bullets"]["score"])
                    self.assertEqual(result["questions"][0]["status"], "unknown")
                if outcome == "known_absent":
                    self.assertIsNone(next(row["score"] for row in result["module_scores"] if row["key"] == "aplus"))
                if outcome == "not_applicable":
                    self.assertEqual(result["applicable_weight"], 8)

    def test_quick_audit_scores_plain_copy_for_any_category(self):
        raw = "Stainless steel chess clock for timed matches. Includes two AA batteries."
        data = {"text": raw}
        before_listing = self.client.get("/api/products").json()
        reply = self.client.post("/api/quick-audit", json=data)
        self.assertEqual(reply.status_code, 200, reply.text[:240])
        body = reply.json()
        product = body["product"]
        self.assertEqual(product["category"], "")
        self.assertEqual(product["asin"], "")
        self.assertEqual(self.client.get(f"/api/products/{product['id']}").status_code, 404)
        audit = body["latest_audit"]
        value = audit["data"]["result"]
        self.assertIsInstance(value["screen"]["score"], (int, float))
        self.assertEqual(len(value["screen"]["checks"]), 4)
        self.assertTrue(value["provisional"])
        self.assertEqual(value["coverage"], 11.1)
        self.assertEqual([item["key"] for item in value["module_scores"] if item["status"] == "assessed"], ["raw_text"])
        self.assertEqual(value["questions"][8]["status"], "unknown")
        self.assertEqual(audit["data"]["rules"]["limits"], {})
        self.assertEqual(body["snapshot"]["fields"]["raw_text"]["text"], raw)
        self.assertEqual(body["context"]["data"]["questions"][0]["text"], "What is this product and what does it do?")
        repeat = self.client.post("/api/quick-audit", json=data).json()["latest_audit"]["data"]["result"]
        self.assertEqual(value, repeat)
        self.assertEqual(before_listing, self.client.get("/api/products").json())

        project = self.detail()
        paste = "Title: Silver Chess Clock\n\nAbout this item\n- Use during tournaments\n- Includes AA batteries"
        revision = self.client.post("/api/quick-audit", json={"text": paste, "asin": self.product["asin"]})
        self.assertEqual(revision.status_code, 200, revision.text[:240])
        self.assertNotEqual(revision.json()["product"]["id"], self.product["id"])
        self.assertEqual(self.detail(), project)
        fields = revision.json()["snapshot"]["fields"]
        named = {key: self.product[key] for key in ("asin", "name", "sku", "market", "brand", "category", "product_type")}
        saved = self.client.post("/api/listing-projects", json={"product": named, "listing": {"fields": fields}})
        self.assertEqual(saved.json()["id"], self.product["id"])
        latest = self.detail()
        self.assertEqual(latest["snapshot"]["version"], project["snapshot"]["version"] + 1)
        self.assertEqual(latest["snapshot"]["fields"]["title"]["text"], "Silver Chess Clock")
        self.assertEqual(latest["snapshot"]["fields"]["bullets"]["text"], "Use during tournaments\nIncludes AA batteries")
        self.assertEqual(latest["snapshot"]["fields"]["aplus"], project["snapshot"]["fields"]["aplus"])
        self.assertEqual(latest["audits"], project["audits"])

    def test_quick_audit_keeps_title_policy_conditional_for_books(self):
        copy = "Title: " + "Book chapter on modern textile craft " * 3 + "\nDescription: Includes 12 tutorials."
        out = self.client.post("/api/quick-audit", json={"text": copy, "category": "Books", "product_type": "media"})
        self.assertEqual(out.status_code, 200, out.text[:240])
        audit = out.json()["latest_audit"]["data"]["result"]
        self.assertIsNotNone(audit["screen"])
        self.assertFalse(any(row["key"] == "limit-title" for row in audit["issues"]))

    def test_llm_quick_screen_requires_visible_evidence(self):
        with patch.dict("os.environ", {"LISTING_LLM_API_KEY": "test-only"}):
            semantic = SemanticReport.model_validate({
                "answers": [], "quality": [], "conflicts": [],
                "screen": [
                    {"axis": "usage", "score": 8, "detail": "说明了使用场景。",
                     "evidence": [{"field": "raw_text", "quote": "during a rehearsal"}]},
                    {"axis": "specificity", "score": 10, "detail": "模型引入了不存在的参数。",
                     "evidence": [{"field": "raw_text", "quote": "five power settings"}]},
                ],
            })
            with patch.object(LLM, "complete", return_value=(semantic, "test-version")):
                output = self.client.post("/api/quick-audit", json={
                    "text": "Play the practice pad during a rehearsal.", "mode": "llm",
                })
        self.assertEqual(output.status_code, 200, output.text[:240])
        audit = output.json()["latest_audit"]
        screen = audit["data"]["result"]["screen"]
        self.assertEqual(screen["mode"], "llm")
        checks = {item["key"]: item for item in screen["checks"]}
        self.assertEqual(checks["usage"]["evidence"][0]["quote"], "during a rehearsal")
        self.assertEqual(checks["specificity"]["evidence"], [])
        self.assertEqual(audit["data"]["model"], "test-version")

    def test_flexible_imports_save_identified_asin_only(self):
        before = self.client.get("/api/products").json()
        workbook = Workbook()
        workbook.active.append(["Listing for review"])
        workbook.active.append(["Key", "Value"])
        workbook.active.append(["商品名称", "Hand grinder"])
        workbook.active.append(["卖点 1", "Adjust grind size for different brewing methods"])
        spreadsheet = io.BytesIO()
        workbook.save(spreadsheet)
        common_files = (
            ("draft.csv", "March review\n标题;卖点 1;备注\nDesk Lamp;\"Fits 11, 13 and 15-inch desks\";optional".encode(), "Desk Lamp", "bullets"),
            ("draft.json", json.dumps({"data": [{"product": {"Listing title": "Toy train", "features": ["Includes tracks"]}}]}).encode(), "Toy train", "bullets"),
            ("draft.xlsx", spreadsheet.getvalue(), "Hand grinder", "bullets"),
            ("draft.txt", b"Title: Desk Lamp\n\nFeatures:\n- Runs on USB", "Desk Lamp", "bullets"),
        )
        for filename, content, title, secondary in common_files:
            with self.subTest(filename=filename):
                response = self.client.post("/api/listing-import", files={"file": (filename, content)})
                self.assertEqual(response.status_code, 200, response.text)
                fields = response.json()["fields"]
                self.assertEqual(fields["title"]["text"], title)
                self.assertEqual(fields[secondary]["state"], "pending")
                for row in fields.values():
                    row["state"] = "confirmed"
                rated = self.client.post("/api/quick-audit", json={"fields": fields})
                self.assertEqual(rated.status_code, 200, rated.text[:240])
                self.assertEqual(rated.json()["snapshot"]["fields"]["title"]["text"], title)
        text_column = self.client.post("/api/listing-import", files={"file": ("copy.csv", b"Description\nFirst paragraph.\nSecond paragraph.")})
        self.assertEqual(text_column.json()["fields"]["raw_text"]["text"], "First paragraph.\nSecond paragraph.")
        self.assertEqual(before, self.client.get("/api/products").json())
        multiple = {"file": ("multiple.csv", b"ASIN,Title\nB0FAKE1001,First product\nB0FAKE1002,Second product")}
        self.assertEqual(self.client.post("/api/listing-import", files=multiple).status_code, 422)
        target = self.client.post("/api/listing-import", params={"asin": "b0fake1002"}, files=multiple).json()
        self.assertEqual(target["asin"], "B0FAKE1002")
        self.assertEqual(target["fields"]["title"]["text"], "Second product")
        saved = self.client.get(f"/api/products/{target['product_id']}").json()
        self.assertEqual(saved["snapshot"]["fields"]["title"]["state"], "pending")
        self.assertEqual(saved["snapshot"]["fields"]["title"]["text"], "Second product")
        self.client.post("/api/listing-import", files={"file": ("second.json", b'{"asin":"B0FAKE1002","Title":"Revised"}')})
        self.assertEqual(self.client.get(f"/api/products/{target['product_id']}").json()["snapshot"], saved["snapshot"])
        self.assertEqual(len(self.client.get("/api/products").json()), len(before) + 1)
        unbound = self.client.post("/api/products", json={"name": "Blank ID"}).json()
        path = f"/api/products/{unbound['id']}"
        uploaded = self.client.post(f"{path}/imports", files={"file": ("product.csv", b"ASIN,Title\nB0FAKE1003,A desktop clock")})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.assertEqual(self.client.get(path).json()["product"]["asin"], "B0FAKE1003")

    def test_saved_page_is_imported_without_playwright_and_scores_its_own_fields(self):
        route = "/api/listing-import"
        html = ("amazon-page.html", SAVED_PRODUCT.encode())
        with patch.dict("os.environ", {"LISTING_AMAZON_PLAYWRIGHT": "0"}):
            wrong = self.client.post(route, params={"asin": "B0FIXTURE0"}, files={"file": html})
            self.assertEqual(wrong.status_code, 422)
            self.assertIn("Amazon UK B0FIXTURE1", wrong.json()["detail"])
            self.assertFalse(any(product["asin"].startswith("B0FIXTURE") for product in self.client.get("/api/products").json()))
            uploaded = self.client.post(route, files={"file": html})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        result = uploaded.json()
        self.assertEqual((result["market"], result["asin"]), ("UK", "B0FIXTURE1"))
        self.assertEqual(result["url"], "https://www.amazon.co.uk/dp/B0FIXTURE1")
        fields = result["fields"]
        self.assertEqual(fields["title"]["text"], "Fixture Café Grinder")
        self.assertEqual(fields["bullets"]["text"], "Adjust the grind size\nFits on a narrow counter")
        self.assertEqual(fields["highlights"]["text"], "Stainless steel, Manual grinding")
        self.assertEqual(fields["aplus"]["text"], "Use at home\nSet the dial before grinding.")
        self.assertEqual(fields["price"]["text"], "£19.99")
        self.assertEqual(fields["public_specs"]["text"], "Material: Steel")
        self.assertNotIn("raw_text", fields)
        self.assertEqual(fields["reviews"]["text"], "Review sentiment")
        self.assertFalse({"attributes", "search_terms"} & fields.keys())
        self.assertTrue(all(row["state"] == "pending" for row in fields.values()))
        project = self.client.get(f"/api/products/{result['product_id']}").json()
        self.assertEqual(project["snapshot"]["fields"]["bullets"], fields["bullets"])
        self.assertEqual(project["snapshot"]["fields"]["search_terms"]["state"], "missing")
        scored = self.client.post("/api/quick-audit", json={
            "asin": result["asin"], "market": result["market"],
            "fields": {key: {**row, "state": "confirmed"} for key, row in fields.items()},
        })
        self.assertEqual(scored.status_code, 200, scored.text)
        dimensions = {row["key"]: row for row in scored.json()["latest_audit"]["data"]["result"]["dimensions"]}
        self.assertEqual(dimensions["highlights"]["status"], "assessed")
        self.assertEqual(dimensions["bullets"]["status"], "assessed")
        self.assertEqual(dimensions["attributes"]["status"], "missing")

    def test_long_webpage_names_and_singlefile_source_import(self):
        route = "/api/listing-import"
        long_name = "Example Amazon listing " * 10 + ".HTML"
        uploaded = self.client.post(route, files={"file": (long_name, SAVED_PRODUCT.encode())})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        result = uploaded.json()
        self.assertTrue(result["label"].endswith(".HTML"))
        self.assertEqual((result["asin"], result["market"]), ("B0FIXTURE1", "UK"))
        product_route = f"/api/products/{result['product_id']}/imports"
        self.assertEqual(self.client.post(product_route, files={"file": (long_name, SAVED_PRODUCT.encode())}).status_code, 200)

        source = b"""<!DOCTYPE html><html><!--
 Page saved with SingleFile
 url: https://www.amazon.de/dp/B0FAKE2001
 saved date: Mon Aug 31 2026 11:00:00 GMT
--><head><title>Sample stand</title></head><body>
<h1 id="productTitle">Sample stand</h1>
<div id="feature-bullets"><ul><li>Folds flat</li></ul></div>
</body></html>"""
        for name in ("singlefile.html.txt", "singlefile"):
            with self.subTest(name=name):
                response = self.client.post(route, files={"file": (name, source, "text/plain")})
                self.assertEqual(response.status_code, 200, response.text)
                preview = response.json()
                self.assertEqual((preview["asin"], preview["market"]), ("B0FAKE2001", "DE"))
                self.assertEqual(preview["fields"]["bullets"]["text"], "Folds flat")
        shortcut = b"[InternetShortcut]\nURL=https://www.amazon.de/dp/B0FAKE2001\n"
        rejected = self.client.post(route, files={"file": ("product.url", shortcut)})
        self.assertEqual(rejected.status_code, 422)
        self.assertIn("网页快捷方式", rejected.json()["detail"])

    def test_single_file_web_archive_and_scoped_heading_fallback(self):
        archive = EmailMessage()
        variant = "https://www.amazon.co.jp/dp/B0FIXTURE3"
        archive.make_related()
        archive["MIME-Version"] = "1.0"
        page = EmailMessage()
        page.set_content("""<html><head><meta charset="utf-8"></head><body>
          <span id="productTitle">卓上スタンド</span>
          <div id="cr-summary-ai"><h3>Item Highlights</h3><p>Review sentiment</p></div>
          <h2>この商品について</h2><ul><li>折りたたみ式</li></ul>
          </body></html>""", subtype="html", charset="utf-8")
        page["Content-Location"] = variant
        archive.attach(page)
        product = self.client.post("/api/products", json={"name": "Japanese listing draft"}).json()
        response = self.client.post(f"/api/products/{product['id']}/imports",
                                    files={"file": ("archive.mhtml", archive.as_bytes())})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual((result["market"], result["asin"], result["url"]), ("JP", "B0FIXTURE3", variant))
        self.assertEqual(result["fields"]["title"]["text"], "卓上スタンド")
        self.assertEqual(result["fields"]["bullets"]["text"], "折りたたみ式")
        self.assertNotIn("highlights", result["fields"])
        renamed = self.client.post("/api/listing-import", files={"file": ("archive.mhtml.txt", archive.as_bytes())})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        self.assertEqual((renamed.json()["asin"], renamed.json()["market"]), ("B0FIXTURE3", "JP"))
        blocked = self.client.post("/api/listing-import", files={
            "file": ("login.html", b'<title>Amazon Sign-In</title><form><input id="ap_email"></form>'),
        })
        self.assertEqual(blocked.status_code, 422)
        self.assertIn("登录或人机验证", blocked.json()["detail"])

    def test_provider_imports_map_five_points_and_specs_from_documented_fields(self):
        route = "/api/listing-import"
        asin = "B0SRC00001"
        listing = {
            "asin": asin, "link": f"https://www.amazon.sg/dp/{asin}", "title": "Fixture Desk Lamp", "brand": "Brand: Fixture",
            "aboutItem": ["material: Steel", "Press the base to switch modes"],
            "aboutItemFivePoint": ["Press the base to switch modes"],
            "itemSpecifications": {"material": "Steel", "colour": "Blue"},
            "productDetails": {"asin": asin, "manufacturer": "Fixture"},
            "description": "Use the lamp indoors.",
        }
        payload = {"code": 0, "msg": None, "data": {"products": [listing]}}
        upload = self.client.post(route, files={"file": ("nexscope.json", json.dumps(payload))})
        self.assertEqual(upload.status_code, 200, upload.text)
        result = upload.json()
        self.assertEqual((result["asin"], result["market"], result["brand"]), (asin, "SG", "Fixture"))
        self.assertEqual(result["fields"]["bullets"]["text"], "Press the base to switch modes")
        self.assertIn("material: Steel", result["fields"]["public_specs"]["text"])
        self.assertIn("manufacturer: Fixture", result["fields"]["public_specs"]["text"])
        self.assertNotIn(asin, result["fields"]["public_specs"]["text"])
        self.assertFalse({"attributes", "highlights", "aplus"} & result["fields"].keys())
        imported = self.client.get("/api/amazon/mirror", params={"asin": result["url"]}).json()
        self.assertIn("Nexscope", imported["message"])
        self.assertEqual(imported["fields"], result["fields"])

        listing.pop("aboutItemFivePoint")
        raw = self.client.post(route, files={"file": ("nexscope.json", json.dumps(payload))}).json()
        self.assertNotIn("bullets", raw["fields"])
        self.assertIn("Press the base", raw["fields"]["raw_text"]["text"])

        csv = f"title,url,asin_code,image_url,price\nDesk Lamp,https://www.amazon.co.uk/dp/{asin},{asin},https://example.com/p.jpg,£17"
        csv_result = self.client.post(route, files={"file": ("amazon_products.csv", csv.encode())})
        self.assertEqual(csv_result.status_code, 200, csv_result.text)
        self.assertEqual(csv_result.json()["market"], "UK")
        self.assertNotEqual(csv_result.json()["product_id"], result["product_id"])
        self.assertEqual(set(csv_result.json()["fields"]), {"title", "price"})
        mismatch = self.client.post(route, files={"file": ("amazon_products.csv", csv.replace(
            f"dp/{asin}", "dp/B0SRC00002").encode())})
        self.assertEqual(mismatch.status_code, 422, mismatch.text)

    def test_mirror_reads_historical_data_after_failed_capture_and_follows_project_deletion(self):
        asin, market = "B0FIXTURE1", "UK"
        params = {"asin": asin, "market": market}
        mirror = "/api/amazon/mirror"
        route = "/api/amazon/workflows"
        self.assertEqual(self.client.get(mirror, params=params).status_code, 404)
        imported = self.client.post("/api/listing-import", files={"file": ("lamp.html", SAVED_PRODUCT.encode())}).json()
        self.assertTrue(imported["product_id"])
        original = self.client.get(mirror, params=params).json()
        self.assertTrue(original["archived_at"])
        self.assertEqual(original["fields"], imported["fields"])
        self.assertEqual(self.client.get(mirror, params={**params, "market": "US"}).status_code, 404)
        with patch.dict("os.environ", {"LISTING_AMAZON_PLAYWRIGHT": "0", "LISTING_NEXSCOPE_API_KEY": ""}), patch(
            "backend.main.collect_amazon"
        ) as live:
            response = self.client.post(route, json={**params, "provider": "auto"})
            self.assertEqual(response.status_code, 202, response.text)
            from_archive = response.json()
            self.assertEqual((from_archive["provider"], from_archive["status"]), ("archive", "succeeded"))
            self.assertEqual(from_archive["preview"], original)
            self.assertEqual(self.client.post(route, json={"asin": original["url"], "provider": "auto"}).json()["id"],
                             from_archive["id"])
            self.assertEqual(self.client.post(route, json={**params, "market": "DE", "provider": "auto"}).status_code, 409)
            live.assert_not_called()
        store = Store(self.db)
        with store.connect(write=True) as conn:
            store.enqueue_capture(conn, asin, market, None)
            job = store.claim_capture(conn)

        def challenged(*args, **kwargs):
            raise CollectionError(502, "Amazon 页面要求人机验证。")

        self.assertIsNotNone(job)
        CaptureWorker(store, challenged).process(job)
        recovered = self.client.get(f"{route}/{job['id']}").json()
        self.assertEqual(recovered["status"], "failed")
        self.assertIn("人机验证", recovered["detail"])
        self.assertEqual(recovered["preview"], original)
        self.assertTrue(recovered["has_preview"])
        listed = next(task for task in self.client.get(route).json() if task["id"] == job["id"])
        self.assertTrue(listed["has_preview"])
        self.assertNotIn("preview", listed)
        self.assertEqual(self.client.get(mirror, params=params).json(), original)
        self.assertEqual(self.client.get(f"/api/products/{imported['product_id']}").json()["snapshot"]["version"], 2)
        self.assertEqual(self.client.delete("/api/products/" + imported["product_id"]).status_code, 204)
        self.assertEqual(self.client.get(mirror, params=params).status_code, 404)
        self.assertEqual(self.client.get(f"{route}/{from_archive['id']}").status_code, 404)
        self.assertEqual(self.client.get(f"{route}/{job['id']}").status_code, 404)

    def test_archive_lookup_uses_saved_public_copy_without_exposing_backend_fields(self):
        asin = "B0SAVEDONE"
        product = self.client.post("/api/products", json={"name": "Stored Listing", "asin": asin}).json()
        path = f"/api/products/{product['id']}"
        snapshot = self.client.post(f"{path}/snapshots", json={"fields": {
            "bullets": {"text": "Wipes clean with a dry cloth", "source": "手动录入", "state": "confirmed"},
            "search_terms": {"text": "Seller-only keyword", "source": "Seller Central", "state": "confirmed"},
        }}).raise_for_status().json()
        with patch.dict("os.environ", {"LISTING_AMAZON_PLAYWRIGHT": "0", "LISTING_NEXSCOPE_API_KEY": ""}):
            task = self.client.post("/api/amazon/workflows", json={
                "asin": asin, "product_id": product["id"], "provider": "auto",
            }).raise_for_status().json()
        self.assertEqual(task["provider"], "archive")
        self.assertEqual(task["preview"]["archived_at"], snapshot["created_at"])
        self.assertEqual(task["preview"]["fields"], {"bullets": snapshot["fields"]["bullets"]})
        self.assertIn("ASIN 库已保存的原文", task["preview"]["message"])

    def test_public_archive_can_be_loaded_scored_and_reused(self):
        target = {"asin": "B0FIXTURE1", "market": "US"}
        route = "/api/amazon/workflows"
        requests = []

        @contextmanager
        def reply(request, *, timeout):
            requests.append(request.full_url)
            response = io.BytesIO(PUBLIC_PRODUCT)
            response.headers = EmailMessage()
            response.headers["Content-Type"] = "text/html; charset=utf-8"
            yield response

        with (
            patch.dict("os.environ", {"LISTING_AMAZON_PLAYWRIGHT": "0", "LISTING_NEXSCOPE_API_KEY": ""}),
            patch("backend.public_archive.build_opener") as opener,
            patch("backend.main.collect_amazon") as live,
        ):
            opener.return_value.open.side_effect = reply
            job = capture_result(self.client, self.client.post(route, json={**target, "provider": "auto"}))
            self.assertEqual((job["status"], job["provider"]), ("succeeded", "public_archive"))
            preview = job["preview"]
            self.assertEqual(preview["reference_url"], ARCHIVE_BASE + target["asin"])
            self.assertEqual(preview["source_observed_at"], "2026-10-19T14:45:36+00:00")
            self.assertEqual(set(preview["fields"]), {"title", "bullets", "public_specs"})
            self.assertEqual(preview["fields"]["public_specs"]["text"], "Material: Stainless steel")
            self.assertEqual(preview["fields"]["bullets"]["collected_at"], "2026-10-19T14:45:36Z")
            result = self.client.post("/api/quick-audit", json={**target, "fields": preview["fields"]}).raise_for_status().json()
            scores = result["latest_audit"]["data"]["result"]["module_scores"]
            self.assertEqual({row["key"] for row in scores if row["score"] is not None}, set(preview["fields"]))
            replay = self.client.post(route, json={**target, "provider": "auto"}).raise_for_status().json()
            self.assertEqual(replay["provider"], "archive")
            self.assertEqual(replay["preview"]["reference_url"], preview["reference_url"])
            self.assertEqual(replay["preview"]["source_observed_at"], preview["source_observed_at"])
            self.assertEqual(requests, [ARCHIVE_BASE + target["asin"]])
            live.assert_not_called()

    def test_public_archive_miss_non_us_and_identity_mismatch(self):
        asin = "B0FIXTURE1"
        route = "/api/amazon/workflows"
        url = ARCHIVE_BASE + asin
        with (
            patch.dict("os.environ", {"LISTING_AMAZON_PLAYWRIGHT": "1", "LISTING_NEXSCOPE_API_KEY": ""}),
            patch("backend.public_archive.build_opener") as opener,
            patch("backend.main.collect_amazon") as live,
        ):
            rejected = self.client.post(route, json={"asin": asin, "market": "UK", "provider": "public_archive"})
            self.assertEqual(rejected.status_code, 422)
            opener.return_value.open.side_effect = HTTPError(url, 404, "Not found", {}, None)
            task = capture_result(self.client, self.client.post(route, json={"asin": asin, "provider": "auto"}))
            self.assertEqual(task["status"], "failed")
            self.assertIn("暂未收录", task["detail"])
            self.assertIsNone(task["preview"])
            opener.return_value.open.assert_called_once()
            live.assert_not_called()
        self.assertEqual(self.client.get("/api/amazon/mirror", params={"asin": asin, "market": "US"}).status_code, 404)
        with self.assertRaisesRegex(ValueError, "参数表与目标 ASIN 不一致"):
            parse_archive(PUBLIC_PRODUCT.replace(b"<td>B0FIXTURE1</td>", b"<td>B0FIXTURE2</td>"), asin)
        with self.assertRaisesRegex(ValueError, "商品地址与目标美国站 ASIN 不一致"):
            parse_archive(PUBLIC_PRODUCT.replace(b"www.amazon.com/dp", b"www.amazon.co.uk/dp"), asin)

    def test_configured_nexscope_capture_queues_and_stores_verified_identity(self):
        asin, market = "B0SRC00005", "DE"
        route = "/api/amazon/workflows"
        body = {"asin": asin, "market": market, "provider": "nexscope"}
        with patch.dict("os.environ", {"LISTING_NEXSCOPE_API_KEY": ""}):
            self.assertEqual(self.client.post(route, json=body).status_code, 409)
        requests = []

        @contextmanager
        def reply(method, url, **options):
            requests.append((method, url, options["json"], options["headers"]["Authorization"]))
            yield httpx.Response(200, json={"code": 0, "data": {"products": [
                {"asin": asin, "link": f"https://www.amazon.de/dp/{asin}", "title": "Ficha",
                 "aboutItemFivePoint": ["Falten und verstauen"]},
            ]}})

        with (
            patch.dict("os.environ", {"LISTING_NEXSCOPE_API_KEY": "fixture-only", "LISTING_AMAZON_PLAYWRIGHT": "1"}),
            patch("backend.nexscope.httpx.stream", side_effect=reply),
            patch("backend.main.collect_amazon") as browser,
        ):
            config = self.client.get("/api/config")
            self.assertTrue(config.json()["nexscope"])
            self.assertNotIn("fixture-only", config.text)
            response = self.client.post(route, json={**body, "provider": "auto"})
            self.assertEqual(response.status_code, 202, response.text)
            task_id = response.json()["id"]
            for _ in range(50):
                task = self.client.get(f"{route}/{task_id}").json()
                if task["status"] in {"succeeded", "failed"}:
                    break
                time.sleep(.04)
            browser.assert_not_called()
        self.assertEqual(task["status"], "succeeded", task)
        self.assertEqual(task["provider"], "nexscope")
        self.assertEqual(task["preview"]["fields"]["bullets"]["text"], "Falten und verstauen")
        self.assertEqual(requests, [("POST", "https://api.nexscope.ai/amazon/product/detail",
                                     {"asins": asin, "amazonDomain": "amazon.de"}, "Bearer fixture-only")])
        archive = self.client.get("/api/amazon/mirror", params={"asin": asin, "market": market}).json()
        self.assertEqual(archive["product_id"], task["preview"]["product_id"])
        self.assertIn("Nexscope", archive["message"])

    def test_alexa_skill_packet_cites_saved_evidence_and_confirmed_facts(self):
        product = self.client.post("/api/products", json={"name": "Example kitchen scale", "asin": "B0SRC00003"}).json()
        base = f"/api/products/{product['id']}"
        fields = {
            "title": {"state": "confirmed", "text": "Example Kitchen Scale", "source": "Product page"},
            "bullets": {"state": "confirmed", "text": "Use the scale with the app closed after setup", "source": "Product page"},
        }
        saved = self.client.post(base + "/snapshots", json={"fields": fields})
        self.assertEqual(saved.status_code, 200, saved.text)
        context = {
            "facts": [
                {"id": "confirmed", "label": "Daily use", "copy": fields["bullets"]["text"], "state": "confirmed", "source": "Reviewed manual"},
                {"id": "pending", "label": "Wireless range", "copy": "Unreviewed distance claim", "state": "pending"},
            ],
            "questions": [
                {"id": "other", "text": "What is the scale made of?"},
                {"id": "alexa", "text": "Can I weigh with the app closed?", "source": "alexa",
                 "fields": ["bullets"], "anchors": ["app closed"]},
            ],
        }
        edited = self.client.put(base + "/context", json=context)
        self.assertEqual(edited.status_code, 200, edited.text)
        completed(self.client, self.client.post(base + "/audit", json={"mode": "rules"}))
        packet = self.client.get(base + "/alexa-skill-input")
        self.assertEqual(packet.status_code, 200, packet.text)
        self.assertIn("alexa-skill.md", packet.headers["content-disposition"])
        self.assertLess(packet.text.index("Can I weigh"), packet.text.index("What is the scale"))
        self.assertIn("五点描述 1: Use the scale", packet.text)
        self.assertIn("Search Terms | 未采集 / 未提供", packet.text)
        self.assertIn("Reviewed manual", packet.text)
        self.assertNotIn("Unreviewed distance claim", packet.text)

    def test_public_page_bullet_count_requires_seller_confirmation(self):
        fields = ListingInput(fields={"bullets": {
            "state": "confirmed", "text": "\n".join(f"Feature {number}" for number in range(6)),
            "source": "Amazon US 保存的网页 · fixture.html",
        }}).normalized()
        rules = rules_for({"market": "US", "category": "Kitchen", "product_type": "non_media"}, "2026-11-20")
        finding = rule_findings(fields, rules)[0]
        self.assertEqual((finding["key"], finding["priority"], finding["needs_review"]), ("verify-bullet-count", "P2", True))
        self.assertEqual(rule_findings(fields, rules, for_publication=True)[0]["priority"], "P0")
        self.assertEqual(rule_findings(fields, {**rules, "checks_version": "us-copy-checks-v1"})[0]["priority"], "P0")
        fields["bullets"]["source"] = "Seller Central export"
        self.assertEqual(rule_findings(fields, rules)[0]["key"], "limit-bullets")

    def test_import_rejects_invalid_number_or_mismatched_text_identity(self):
        route = "/api/listing-import"
        bad = self.client.post(route, params={"asin": "B0QATEST01"}, files={
            "file": ("candidate.csv", b"ASIN,Title\nwrong reference,Wrong product"),
        })
        self.assertEqual(bad.status_code, 422)
        self.assertIn("文件里的 ASIN", bad.json()["detail"])
        text = "ASIN: B0QATEST03\nMarketplace: UK\nTitle: Example Product"
        for name, content in (("candidate.txt", text), ("candidate.json", json.dumps(text))):
            response = self.client.post(route, params={"asin": "B0QATEST02"}, files={"file": (name, content.encode())})
            self.assertEqual(response.status_code, 422, response.text)
            self.assertIn("资料中的 ASIN 或站点", response.json()["detail"])
        products = self.client.get("/api/products").json()
        self.assertFalse(any(row["asin"] in {"B0QATEST01", "B0QATEST02"} for row in products))
        imported = self.client.post(route, files={"file": ("candidate.txt", text.encode())}).json()
        self.assertEqual((imported["asin"], imported["market"]), ("B0QATEST03", "UK"))
        self.assertTrue(imported["product_id"])

    def test_only_obtained_modules_are_scored(self):
        body = {"text": "Bullets:\n- Read calories without the app."}
        actual = self.client.post("/api/quick-audit", json=body).json()["latest_audit"]["data"]["result"]
        modules = {item["key"]: item for item in actual["module_scores"]}
        self.assertIsNone(modules["aplus"]["score"])
        self.assertIsNone(modules["attributes"]["score"])
        self.assertIsNone(modules["keywords"]["score"])
        self.assertEqual(actual["assessed_weight"], 1)
        self.assertEqual(actual["score"], modules["bullets"]["score"])
        self.assertTrue(all(item["dimension"] is None and item["range"] is None for item in actual["guidance"]))
        check = next(row for row in actual["guidance"] if row["dimension"] is None)
        self.assertEqual(check["evidence"][0]["quote"], "Read calories without the app.")
        self.assertFalse(any(item["priority"] == "P0" for item in actual["issues"]))

        model = SemanticReport.model_validate({"answers": [], "quality": [], "conflicts": [], "scenarios": [
            {"dimension": "aplus", "low": 4, "high": 6, "assumption": "核实 App 在日常使用时是否必需。",
             "action": "把实际操作步骤写成 A+ 模块。", "evidence": [{"field": "bullets", "quote": "without the app"}]},
            {"dimension": "keywords", "low": 8, "high": 9, "assumption": "模型虚构了引文。", "action": "核对搜索词。",
             "evidence": [{"field": "bullets", "quote": "No phone or setup ever needed"}]},
        ], "verification": [{"label": "核对首次设置是否需要 App", "question": "Do I need an app for initial setup?",
                               "action": "分别核对首次设置和日常称量。", "evidence": {"field": "bullets", "quote": "without the app"}}]})
        with patch.dict("os.environ", {"LISTING_LLM_API_KEY": "test-only"}), patch.object(LLM, "complete", return_value=(model, "test-version")):
            response = self.client.post("/api/quick-audit", json={**body, "mode": "llm"})
        self.assertEqual(response.status_code, 200, response.text[:240])
        result = response.json()["latest_audit"]["data"]["result"]
        self.assertEqual(result["coverage"], actual["coverage"])
        self.assertEqual(result["score"], actual["score"])
        self.assertEqual(len(result["guidance"]), 1)
        self.assertIsNone(result["guidance"][0]["range"])
        self.assertEqual(result["guidance"][0]["question"], "Do I need an app for initial setup?")
        self.assertEqual(result["guidance"][0]["evidence"][0]["quote"], "without the app")

    def test_average_and_pending_capture_evidence_are_distinct_from_backend(self):
        supplied = {"bullets": {"text": "Stainless steel whisk · Premium quality", "state": "pending",
                                "source": "Amazon US 商品页 · 测试"},
                    "public_specs": {"text": "Material: Stainless steel", "state": "pending", "source": "Amazon US 商品页 · 测试"}}
        response = self.client.post("/api/quick-audit", json={"fields": supplied})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["latest_audit"]["data"]["result"]
        modules = {item["key"]: item for item in data["module_scores"]}
        self.assertEqual(modules["bullets"]["source_state"], "pending")
        self.assertIsNone(modules["attributes"]["score"])
        self.assertEqual(data["assessed_weight"], 2)
        self.assertEqual(data["score"], round((modules["bullets"]["score"] + modules["public_specs"]["score"]) / 2, 1))
        edit = next(issue["edit"] for issue in data["issues"] if issue["field"] == "bullets")
        self.assertEqual(edit["before"], supplied["bullets"]["text"])
        self.assertEqual(edit["after"], "Stainless steel whisk")
        supplied["attributes"] = {"text": "Capacity: 5 L", "state": "confirmed", "source": "Seller Central"}
        response = self.client.post("/api/quick-audit", json={"fields": supplied})
        filled = response.json()["latest_audit"]["data"]["result"]
        self.assertEqual(filled["assessed_weight"], 3)
        self.assertIsNotNone(next(item["score"] for item in filled["module_scores"] if item["key"] == "attributes"))

    def test_capture_workflows_run_independently_and_save_to_library(self):
        release = Event()
        first_active = Event()
        finished_cancelled = Event()

        def fake(asin, *, market, still_running):
            if asin == "B0CAPTURE1" and market == "US":
                first_active.set()
                if not release.wait(5):
                    raise AssertionError("test timed out releasing the first capture")
                if not still_running():
                    finished_cancelled.set()
                    raise CollectionError(409, "采集已取消。")
            return {"asin": asin, "market": market, "label": f"Amazon {market} 商品页", "brand": "Example", "message": "",
                    "url": product_url(asin, market),
                    "fields": {"title": {"text": f"Product {asin}", "state": "pending", "source": "Sample page"}}}

        def wait_capture(id):
            for _ in range(80):
                job = self.client.get(f"/api/amazon/workflows/{id}").json()
                if job["status"] in {"succeeded", "failed"}:
                    return job
                time.sleep(.04)
            self.fail("Capture workflow did not complete")

        route = "/api/amazon/workflows"
        with patch.dict("os.environ", {"LISTING_AMAZON_PLAYWRIGHT": "1", "LISTING_NEXSCOPE_API_KEY": ""}), patch(
            "backend.main.collect_amazon", side_effect=fake
        ):
            first = self.client.post(route, json={"asin": "B0CAPTURE1", "provider": "playwright"}).json()
            try:
                self.assertTrue(first_active.wait(3))
                self.assertEqual(self.client.post(route, json={"asin": "b0capture1"}).json()["id"], first["id"])
                second = self.client.post(route, json={"asin": "https://www.amazon.co.uk/dp/b0capture1?tag=test", "market": "US"}).json()
                self.assertNotEqual(second["id"], first["id"])
                collected = wait_capture(second["id"])
                self.assertEqual(collected["status"], "succeeded", collected)
                project = self.client.get(f"/api/products/{collected['preview']['product_id']}").json()
                self.assertEqual(project["product"]["asin"], "B0CAPTURE1")
                self.assertEqual(project["product"]["market"], "UK")
                self.assertEqual(project["snapshot"]["fields"]["title"]["state"], "pending")
                jobs = {item["id"]: item for item in self.client.get(route).json()}
                self.assertEqual(jobs[first["id"]]["status"], "running")
                self.assertEqual(self.client.delete(f"{route}/{first['id']}").status_code, 204)
            finally:
                release.set()
            self.assertTrue(finished_cancelled.wait(3))
            self.assertEqual(self.client.delete(f"{route}/{second['id']}").status_code, 204)
            self.assertEqual(self.client.get(f"/api/products/{project['product']['id']}").status_code, 200)
            self.assertFalse(any(row["asin"] == "B0CAPTURE1" and row["market"] == "US" for row in self.client.get("/api/products").json()))

            original = self.client.post("/api/products", json={"name": "Draft with no ASIN"}).json()
            base = f"/api/products/{original['id']}"
            old = self.client.get(base).json()["snapshot"]
            queued = self.client.post(route, json={"asin": "B0CAPTURE3", "product_id": original["id"]}).json()
            result = wait_capture(queued["id"])
            self.assertEqual(result["status"], "succeeded", result)
            current = self.client.get(base).json()
            self.assertEqual(current["product"]["asin"], "B0CAPTURE3")
            self.assertEqual(current["snapshot"]["id"], old["id"])
            edited = {key: {**row, "state": "confirmed"} for key, row in result["preview"]["fields"].items()}
            saved = self.client.post(f"{base}/snapshots", json={"fields": edited, "asin": "B0CAPTURE3", "base_snapshot_id": old["id"]})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["fields"]["title"]["text"], "Product B0CAPTURE3")
            self.assertEqual(self.client.post(f"{base}/snapshots", json={"fields": edited, "base_snapshot_id": old["id"]}).status_code, 409)
            self.assertEqual(self.client.post(route, json={"asin": "B0CAPTURE4", "product_id": original["id"]}).status_code, 409)
            self.assertEqual(self.client.post(route, json={"asin": "B0CAPTURE3", "market": "JP", "product_id": original["id"]}).status_code, 409)

    def test_market_scope_for_uploads_and_policy_rules(self):
        marketplaces = self.client.get("/api/config").json()["marketplaces"]
        self.assertEqual(next(site["domain"] for site in marketplaces if site["code"] == "JP"), "amazon.co.jp")
        asin = "B0STORE001"
        rows = {"file": ("products.csv", f"ASIN,Marketplace,Title\n{asin},US,US title\n{asin},GB,UK title".encode())}
        uk = self.client.post("/api/listing-import", params={"asin": f"amazon.co.uk/dp/{asin}"}, files=rows).json()
        us = self.client.post("/api/listing-import", params={"asin": asin, "market": "US"}, files=rows).json()
        self.assertNotEqual(uk["product_id"], us["product_id"])
        self.assertEqual(uk["fields"]["title"]["text"], "UK title")
        self.assertEqual(us["fields"]["title"]["text"], "US title")
        self.assertEqual(uk["market"], "UK")
        self.assertEqual(us["market"], "US")
        uk_base = f"/api/products/{uk['product_id']}"
        self.assertEqual(self.client.post(uk_base + "/imports", params={"asin": asin, "market": "US"}, files=rows).status_code, 409)
        self.assertEqual(self.client.post(uk_base + "/imports", files={"file": ("wrong.json", b'{"market":"US","ASIN":"B0STORE001","Title":"Wrong site"}')}).status_code, 422)

        text = "Title: " + "Accessory kit for laptop desks and home office accessories " * 2
        uk_audit = self.client.post("/api/quick-audit", json={"text": text, "asin": asin, "market": "UK", "product_type": "non_media"}).json()
        us_audit = self.client.post("/api/quick-audit", json={"text": text, "asin": asin, "market": "US", "product_type": "non_media"}).json()
        self.assertFalse(uk_audit["current_rules"]["limits"])
        self.assertIn("title", us_audit["current_rules"]["limits"])
        self.assertFalse(any(issue["key"] == "limit-title" for issue in uk_audit["latest_audit"]["data"]["result"]["issues"]))
        self.assertTrue(any(issue["key"] == "limit-title" for issue in us_audit["latest_audit"]["data"]["result"]["issues"]))

    def test_capture_errors_leave_store_alone(self):
        before = self.client.get("/api/products").json()
        with patch.dict("os.environ", {"LISTING_AMAZON_PLAYWRIGHT": "0"}):
            output = self.client.post("/api/amazon/collect", json={"asin": "B0FAKE1001"})
            self.assertEqual(output.status_code, 409)
            bad_target = self.client.post("/api/amazon/collect", json={"asin": "https://example.org/dp/B0FAKE1001"})
            self.assertEqual(bad_target.status_code, 422)
        with patch("backend.main.collect_amazon", side_effect=CollectionError(502, "需要验证")):
            output = self.client.post("/api/amazon/collect", json={"asin": "B0FAKE1001"})
            self.assertEqual(output.json()["detail"], "需要验证")
        self.assertEqual(before, self.client.get("/api/products").json())

    def test_late_capture_cannot_restore_a_deleted_product(self):
        store = Store(Path(self.directory.name) / "capture.sqlite3")
        store.initialize()
        asin = "B0QATEST04"
        fields = {"title": {"text": "Fixture stand", "state": "pending", "source": "Synthetic page"}}
        with store.connect(write=True) as conn:
            item = store.save_intake(conn, asin, "US", fields, "Fixture")
            keep = store.save_intake(conn, asin, "UK", fields, "Fixture")
            store.enqueue_capture(conn, asin, "US", None)
            job = store.claim_capture(conn)
            other = store.enqueue_capture(conn, asin, "UK", None)

        def late_capture(asin, *, market, still_running):
            self.assertTrue(still_running())
            with store.connect(write=True) as conn:
                store.delete_product(conn, item["id"])
            return {"asin": asin, "market": market, "brand": "Example", "fields": fields}

        CaptureWorker(store, late_capture).process(job)
        with store.connect() as conn:
            self.assertIsNone(store.find_product(conn, "US", asin))
            self.assertEqual(store.find_product(conn, "UK", asin)["id"], keep["id"])
            self.assertEqual(store.capture(conn, job["id"])["status"], "failed")
            self.assertIn("项目已删除", store.capture(conn, job["id"])["detail"])
            self.assertEqual(store.capture(conn, other["id"])["status"], "queued")

    def test_delete_removes_only_target_product_and_its_history(self):
        other = self.client.post("/api/products", json={"asin": "B0FAKE1001", "name": "Keep this"}).json()
        baseline = self.detail()["latest_audit"]
        draft = completed(self.client, self.client.post(f"{self.base}/rewrite", json={"audit_id": baseline["id"], "strategy": "B"}))
        cid = f"{self.base}/candidates/{draft['id']}"
        candidate = self.client.get(cid).json()
        rows = [{key: row[key] for key in ("key", "text", "decision")} for row in candidate["data"]["validation"]["rows"]]
        for row in rows:
            if row["decision"] == "pending":
                row["decision"] = "accepted"
        child = self.client.post(f"{cid}/review", json={"lines": rows}).json()
        live = self.client.post(f"{self.base}/candidates/{child['id']}/publication", json={"published_at": baseline["created_at"]})
        self.assertEqual(live.status_code, 200, live.text)
        other_base = f"/api/products/{other['id']}"
        before = self.client.get(other_base).json()
        self.assertEqual(self.client.delete(self.base).status_code, 204)
        self.assertEqual(self.client.get(self.base).status_code, 404)
        self.assertEqual(self.client.get(other_base).json(), before)
        with Store(self.db).connect() as conn:
            for table in ("publication_events", "rewrite_versions", "audit_runs", "quick_requests", "jobs", "imports",
                          "context_versions", "listing_snapshots"):
                self.assertIsNone(conn.execute(f"SELECT 1 FROM {table} WHERE product_id=?", (self.product["id"],)).fetchone())
        with TestClient(create_app(self.db)) as another_start:
            self.assertEqual([item["id"] for item in another_start.get("/api/products").json()], [other["id"]])

    def test_semantic_api_retries_schema_error(self):
        messages = []

        def response(request):
            messages.append(request)
            data = {"answers": [], "quality": [], "conflicts": []}
            text = "invalid JSON" if len(messages) == 1 else json.dumps(data)
            return httpx.Response(200, json={"choices": [{"message": {"content": text}}], "model": "test-version"})

        original = httpx.Client
        with patch("backend.llm.httpx.Client", side_effect=lambda **kw: original(transport=httpx.MockTransport(response), **kw)):
            answer, model = LLM("https://provider.example/v1/chat/completions", "test-only", "test-version").complete(
                SemanticReport, "Audit", {"sample": "Only supplied information."}
            )
        self.assertEqual(len(messages), 2)
        self.assertEqual(model, "test-version")
        self.assertEqual(answer.answers, [])
        self.assertNotIn("test-only", messages[0].content.decode())


if __name__ == "__main__":
    unittest.main()
