import base64
import json
import os
import re
import shutil
import tempfile
import unittest
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.audit import evidence
from backend.field_suggestions import ProposedFields, public_records
from backend.importers import parse_import
from backend.llm import LLM
from backend.main import create_app
from backend.models import ListingInput

SAMPLE = (Path(__file__).resolve().parents[1] / "examples" / "nutrition-scale-demo.html").read_text(encoding="utf-8")
IMAGE_URL = re.search(r'src="(data:image/png;base64,[^"]+)"', SAMPLE)[1]
PNG = base64.b64decode(IMAGE_URL.split(",", 1)[1])
ASIN = "DEMOV11001"
SHOP = f"https://www.amazon.com/dp/{ASIN}"


def parse(page, binary="listing-test-no-ocr"):
    return parse_import(page.encode(), "saved.html", ASIN, binary, "US")


class PublicPageTests(unittest.TestCase):
    def test_scoped_sections_and_public_only_snapshot(self):
        preview = parse(SAMPLE)
        fields = preview["fields"]
        self.assertEqual(fields["highlights"]["text"], "Stainless steel, Meal prep")
        self.assertEqual(len(fields["bullets"]["text"].splitlines()), 3)
        self.assertTrue(fields["raw_text"]["text"].startswith("Weigh a breakfast"))
        self.assertNotIn("Product Description", fields["raw_text"]["text"])
        self.assertFalse({"attributes", "search_terms", "keywords"} & fields.keys())
        ap = fields["aplus"]
        content = ap["text"].splitlines()
        self.assertEqual(len(content), len(ap["page_lines"]))
        meta = {content[m["line"]]: m for m in ap["page_lines"]}
        self.assertEqual(meta["Setup instructions on the display"]["kind"], "alt")
        self.assertEqual(meta["Capacity: 10 kg"]["scope"], "comparison")
        self.assertTrue(ap["notes"])
        refs = public_records(ListingInput(fields=fields).normalized())
        self.assertFalse(any("10 kg" in ref["quote"] for ref in refs))
        self.assertFalse(any("10 kg" in ref["quote"] for ref in evidence({"aplus": {**ap, "state": "confirmed"}}, ["aplus"])))

    def test_title_labels_and_description_do_not_absorb_other_fields(self):
        body = f"""<meta charset=utf-8><link rel="canonical" href="{SHOP}">
        <h1 id="productTitle">Café<!-- markup --> Grinder | <span itemprop="item_highlight">Manual operation</span></h1>
        <div id="productDescription_feature_div">Grind beans.
        <div id="aplus3p_feature_div"><p>Fits a kitchen counter.</p></div></div>
        <section id="aplusBrandStory_feature_div"><p>Our brand offers 8 finishes.</p></section>
        <div id="sims-products"><div class="aplus-v2">Competitor copy</div></div>"""
        fields = parse(body)["fields"]
        self.assertEqual(fields["title"]["text"], "Café Grinder")
        self.assertEqual(fields["highlights"]["text"], "Manual operation")
        self.assertEqual(fields["raw_text"]["text"], "Grind beans.")
        self.assertEqual(fields["aplus"]["text"], "Fits a kitchen counter.\nOur brand offers 8 finishes.")
        self.assertEqual(fields["aplus"]["page_lines"][1]["scope"], "brand")
        base = f'<link rel="canonical" href="{SHOP}"><span id="productTitle">Café | Grinder</span>'
        generic = '<h2>Top highlights</h2><dl><dt>Finish</dt><dd>Metal</dd></dl><div id="customerReviews"><b>Item Highlights</b> Deal</div>'
        fields = parse(base + generic)["fields"]
        self.assertEqual(fields["title"]["text"], "Café | Grinder")
        self.assertNotIn("highlights", fields)
        for label in ("<dt>Item Highlights</dt><dd>Matte finish</dd>", "<b>Item Highlights:</b> Matte finish",
                      '<div aria-label="Item highlights">Matte finish</div>'):
            self.assertEqual(parse(base + generic + "<div>" + label + "</div>")["fields"]["highlights"]["text"], "Matte finish")

    def test_image_only_and_missing_description(self):
        body = f'<link rel="canonical" href="{SHOP}"><h1 id="productTitle">Clock</h1><div id="aplus"><img src="https://example.invalid/image.png" alt="Image 1"></div>'
        fields = parse(body)["fields"]
        self.assertEqual(fields["aplus"]["state"], "missing")
        self.assertEqual(fields["aplus"]["text"], "")
        self.assertIn("A+ 区块", " ".join(fields["aplus"]["notes"]))
        self.assertNotIn("raw_text", fields)
        self.assertNotIn("highlights", fields)

    @unittest.skipUnless(shutil.which("tesseract"), "Optional local OCR unavailable")
    def test_singlefile_and_mhtml_real_image_ocr(self):
        with patch.dict(os.environ, {"LISTING_OCR_LANG": "eng"}):
            archive = EmailMessage()
            archive.make_related()
            archive["MIME-Version"] = "1.0"
            doc = EmailMessage()
            doc.set_content(SAMPLE.replace(IMAGE_URL, "cid:sample-image"), subtype="html", charset="utf-8")
            doc["Content-Location"] = SHOP
            archive.attach(doc)
            nested = EmailMessage()
            nested.set_content(PNG, maintype="image", subtype="png")
            nested["Content-ID"] = "<sample-image>"
            archive.attach(nested)
            for data, name in [(SAMPLE.encode(), "singlefile.html"), (archive.as_bytes(), "captured.mhtml")]:
                with self.subTest(name=name):
                    preview = parse_import(data, name, ASIN, "tesseract")
                    ap = preview["fields"]["aplus"]
                    ocr = [ap["text"].splitlines()[m["line"]] for m in ap["page_lines"] if m["kind"] == "ocr"]
                    self.assertIn("first setup requires the app", " ".join(ocr).lower())
                    refs = public_records({"aplus": ap})
                    self.assertFalse(any("first setup" in r["quote"].lower() for r in refs))

    def test_explicit_terms_in_flat_files(self):
        result = parse_import(b"Title,generic_keyword,item_highlight,bullet_point\nStand,desk accessory,Metal,For work",
                              "seller.csv", "", "")
        self.assertEqual(result["fields"]["search_terms"]["text"], "desk accessory")
        self.assertEqual(result["fields"]["highlights"]["text"], "Metal")


class FieldSuggestionTests(unittest.TestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.client = self.enterContext(TestClient(create_app(Path(temp) / "workbench.sqlite", demo=False)))
        self.enterContext(patch.dict(os.environ, {"LISTING_EDITOR_OCR": "listing-test-no-ocr"}))
        response = self.client.post("/api/listing-import", files={"file": ("demo.html", SAMPLE.encode())})
        self.assertEqual(response.status_code, 200, response.text)
        self.pid = response.json()["product_id"]
        self.base = "/api/products/" + self.pid
        self.initial = self.client.get(self.base).json()
        self.path = f"{self.base}/snapshots/{self.initial['snapshot']['id']}/field-suggestions"

    def test_three_groups_review_snapshot_and_score_independence(self):
        original = self.initial["snapshot"]
        items = self.initial["field_suggestions"]["items"]
        self.assertEqual({item["group"] for item in items}, {"keywords", "attributes", "search_terms"})
        self.assertIn(("Capacity", "5 kg"), [(x["name"], x["value"]) for x in items])
        self.assertNotIn("10 kg", [x["value"] for x in items])
        for item in items:
            ref = item["evidence"]
            self.assertIn(ref["quote"], original["fields"][ref["field"]]["text"].splitlines()[ref["line"]])
        self.assertEqual(self.client.put(self.path + "/" + items[0]["id"], params={"decision": "shortlisted"}).status_code, 200)
        kept = self.client.get(self.base).json()
        self.assertEqual(kept["field_suggestions"]["items"][0]["decision"], "shortlisted")
        self.assertEqual(kept["snapshot"], original)
        for key in ("keywords", "search_terms", "attributes"):
            self.assertEqual(kept["snapshot"]["fields"][key]["state"], "missing")
        body = {"fields": {k: {**v, "state": "confirmed"} if v["text"] else v for k,v in original["fields"].items()}, "market": "US"}
        audit = self.client.post("/api/quick-audit", json=body).json()
        dims = {d["key"]: d for d in audit["latest_audit"]["data"]["result"]["dimensions"]}
        for key in ("attributes", "keywords"):
            self.assertIsNone(dims[key]["score"])
        other = self.client.post("/api/products", json={"name": "Different product"}).json()
        self.assertEqual(self.client.get(self.path.replace(self.pid, other["id"])).status_code, 404)
        new = self.client.post(self.base + "/snapshots", json={**body, "note": "checked source"}).json()
        current = self.client.get(self.base).json()["field_suggestions"]
        self.assertTrue(all(item["decision"] == "pending" for item in current["items"]))
        self.assertNotEqual(new["id"], original["id"])
        self.assertEqual(self.client.get(self.path).json()["items"][0]["decision"], "shortlisted")
        self.assertEqual(self.client.delete(self.base).status_code, 204)
        self.assertEqual(self.client.get(self.path).status_code, 404)

    def test_model_citations_conditions_and_private_fields(self):
        fake = LLM("https://localhost.invalid", "test", "test-model")
        response = ProposedFields(candidates=[
            {"group": "search_terms", "value": "portion weighing device", "field": "title", "line": 0,
             "quote": "Nutrition Food Scale", "reason": "同义候选。"},
            {"group": "attributes", "name":"Capacity", "value": "500 kg", "field": "public_specs", "line": 1,
             "quote": "Capacity: 5 kg", "reason": "编造数字。"},
            {"group": "keywords", "value": "no app required", "field": "title", "line": 0,
             "quote": "Food Scale", "reason": "错误扩展。"},
            {"group": "keywords", "value": "wireless scale", "field": "bullets", "line": 1,
             "quote": "wireless product", "reason": "无原文引用。"},
            {"group": "attributes", "name":"Capacity", "value": "10 kg", "field": "aplus", "line": 4,
             "quote": "Capacity: 10 kg", "reason": "竞品参数。"},
        ])
        with patch.object(LLM, "configured", return_value=fake), patch.object(LLM, "complete", return_value=(response,"model-fixture")) as mock:
            generated = self.client.post(self.path)
            self.assertEqual(generated.status_code, 200, generated.text)
            items = [i for i in generated.json()["items"] if i["origin"]=="llm"]
            self.assertEqual([i["value"] for i in items], ["portion weighing device"])
            self.assertEqual(self.client.post(self.path).json(), generated.json())
            self.assertEqual(mock.call_count, 1)
            mock.return_value = (ProposedFields(candidates=[{
                "group": "keywords", "value": "nutrition scale", "field": "title", "line": 0,
                "quote": "Nutrition Food Scale", "reason": "买家可能用的品类短语。",
            }]), "model-fixture")
            quick = self.client.post("/api/quick-field-suggestions", json={"text": "Title: Nutrition Food Scale\nSearch Terms: secret seller phrase"})
            self.assertEqual(quick.status_code, 200)
            self.assertNotIn("secret seller phrase", str(mock.call_args.args[2]))
            self.assertNotIn("search_terms", quick.json()["groups"])
            self.assertEqual(len(self.client.get("/api/products").json()), 1)

    def test_private_evidence_only_and_invalid_model_response(self):
        with patch.object(LLM, "configured", return_value=None):
            self.assertEqual(self.client.post(self.path).status_code, 409)
        with patch.object(LLM, "configured", return_value=LLM("local","test","m")), patch.object(
              LLM, "complete", return_value=(ProposedFields(candidates=[{"group":"attributes","name":"Capacity","value":"20 kg",
              "field":"title","line":4,"quote":"imaginary title","reason":"无证据。"}]), "m")):
            self.assertEqual(self.client.post(self.path).status_code, 502)
            self.assertEqual(self.client.get(self.path).json(), self.initial["field_suggestions"])
        private = self.client.post("/api/quick-field-suggestions", json={"text":"Search Terms: secret old value"})
        self.assertEqual(private.status_code, 422)

    def test_ollama_loopback_contract_and_local_model_validation(self):
        requests = []
        class Stub(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, self.headers.get("Authorization"), body))
                self.send_response(404 if body["model"] == "missing-model" else 200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                content = {"candidates":[{"group":"keywords","field":"title","line":0,"quote":"Food Scale",
                    "value":"nutrition scale","reason":"商品类型。"}]}
                self.wfile.write(json.dumps({"model":body["model"], "choices":[{"message":{"content":json.dumps(content)}}]}).encode())

        with ThreadingHTTPServer(("127.0.0.1", 0), Stub) as http:
            server = Thread(target=http.serve_forever, daemon=True)
            server.start()
            try:
                url = f"http://127.0.0.1:{http.server_port}/v1/chat/completions"
                with patch.dict(os.environ, {"LISTING_LLM_PROVIDER":"ollama","LISTING_OLLAMA_MODEL":"local-test",
                                            "LISTING_LLM_API_KEY":"never-send-this-key"}):
                    self.assertEqual(LLM.configured().endpoint, "http://127.0.0.1:11434/v1/chat/completions")
                    with patch("backend.llm.OLLAMA_ENDPOINT", url):
                        response = self.client.post(self.path)
                        self.assertEqual(response.status_code,200,response.text)
                        self.assertEqual(response.json()["model"], "local-test")
                        self.assertEqual(requests[0][:2], ("/v1/chat/completions", None))
                        self.assertEqual(requests[0][2]["response_format"]["type"], "json_schema")
                        with patch.dict(os.environ, {"LISTING_OLLAMA_MODEL":"missing-model"}):
                            failure = self.client.post("/api/quick-field-suggestions", json={"text":"Title: Food Scale"})
                            self.assertEqual(failure.status_code, 502)
                            self.assertIn("ollama list", failure.json()["detail"])
            finally:
                http.shutdown()
                server.join(timeout=2)

    def test_reimport_enriches_existing_asin_and_keeps_human_fields(self):
        fields = self.initial["snapshot"]["fields"]
        fields["title"] = {"state": "confirmed", "source": "人工", "text":"Checked model"}
        fields["highlights"] = {"state":"missing"}
        self.assertEqual(self.client.post(self.base + "/snapshots", json={"fields":fields}).status_code, 200)
        uploaded = self.client.post("/api/listing-import", files={"file":("again.html", SAMPLE.encode())}).json()
        self.assertEqual(uploaded["product_id"], self.pid)
        updated = self.client.get(self.base).json()
        self.assertEqual(updated["snapshot"]["fields"]["highlights"]["text"],"Stainless steel, Meal prep")
        self.assertEqual(updated["snapshot"]["fields"]["highlights"]["state"], "pending")
        self.assertEqual(updated["snapshot"]["fields"]["title"]["text"], "Checked model")
        count = len(updated["snapshots"])
        self.client.post("/api/listing-import", files={"file":("same.html", SAMPLE.encode())})
        self.assertEqual(len(self.client.get(self.base).json()["snapshots"]), count)

    def test_large_webpage_and_normal_upload_limits(self):
        large = SAMPLE.encode() + b"<!--" + b" " * (8 * 1024 * 1024) + b"-->"
        self.assertEqual(self.client.post("/api/listing-import", files={"file":("large.html",large)}).status_code,200)
        self.assertEqual(self.client.post("/api/listing-import", files={"file":("large.csv",large)}).status_code,413)

    @unittest.skipUnless(shutil.which("tesseract"), "Optional local OCR unavailable")
    def test_screenshot_can_go_directly_to_aplus(self):
        with patch.dict(os.environ, {"LISTING_EDITOR_OCR":"tesseract", "LISTING_OCR_LANG":"eng"}):
            data = self.client.post(self.base+"/imports", params={"image_field":"aplus"}, files={"file":("scale.png",PNG)})
            self.assertEqual(data.status_code, 200, data.text)
            field = data.json()["fields"]["aplus"]
            self.assertEqual(field["state"], "pending")
            self.assertIn("requires the app",field["text"].lower())
            self.assertEqual(field["page_lines"][0]["kind"], "ocr")
            self.assertTrue(data.json()["image_id"])
