import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.llm import LLM, SemanticReport
from backend.main import create_app
from backend.skill_rewrite import SkillResponse, SkillReviewResponse

CONFLICT = "View Calories and Protein · All features work without the app"
DEMO_TITLE = "Evermeasure Smart Nutrition Food Scale for Kitchen, Meal Prep and Daily Calorie Tracking"


class SkillRewriteTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.client = self.enterContext(TestClient(create_app(Path(self.directory) / "test.sqlite3")))
        self.product = self.client.get("/api/products").json()[0]
        self.path = f"/api/products/{self.product['id']}"
        self.project = self.client.get(self.path).json()

    def field_rewrite(self, text, **options):
        response = self.client.post(f"{self.path}/field-rewrite", json={"text": text, "mode": "rules", **options})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_paste_recognizes_snapshot_and_applies_confirmed_conditions(self):
        sections = self.field_rewrite(f"**Title:** {DEMO_TITLE}\n### About this item\n- {CONFLICT}")["drafts"]
        title, bullet = sections
        self.assertEqual(title["field"], "title")
        self.assertEqual(title["after"], "Evermeasure Smart Nutrition Food Scale")
        self.assertEqual(bullet["target"], "五点描述 · 第 1 条")
        self.assertIn("initial setup", bullet["after"])
        self.assertIn("saved food on the scale", bullet["after"])
        self.assertIn("without reopening the app", bullet["after"])
        self.assertNotIn("All features work", bullet["after"])
        match = self.field_rewrite(CONFLICT)["drafts"][0]
        self.assertEqual(match["method"], "snapshot")
        self.assertEqual(match["after"], bullet["after"])
        self.assertEqual(match["save_edit"]["line"], 0)
        self.assertTrue(match["fact_labels"])

    def test_ambiguous_paste_and_standalone_format(self):
        current = {"state": "confirmed", "text": "For repeat meal prep", "source": "测试"}
        saved = self.client.post(f"{self.path}/snapshots", json={"fields": {"title": current, "highlights": current}})
        self.assertEqual(saved.status_code, 200, saved.text)
        ambiguous = self.field_rewrite(current["text"])["drafts"][0]
        self.assertEqual(ambiguous["status"], "choose")
        self.assertEqual(set(ambiguous["choices"]), {"title", "highlights"})
        chosen = self.field_rewrite(current["text"], field="highlights")["drafts"][0]
        self.assertEqual(chosen["field"], "highlights")
        self.assertEqual(chosen["method"], "manual")
        response = self.client.post("/api/quick-field-rewrite", json={
            "text": "### About this item\n- 1.5 g increments\n- Stainless steel · Premium quality", "mode": "rules",
        })
        self.assertEqual(response.status_code, 200, response.text)
        row = response.json()["drafts"][0]
        self.assertEqual(row["field"], "bullets")
        self.assertEqual(row["after"], "1.5 g increments\nStainless steel")
        self.assertEqual(len(self.client.get("/api/products").json()), 1)

    def test_diagnosis_to_rewrite_to_candidate_with_stale_guard(self):
        audit = self.project["latest_audit"]
        issues = audit["data"]["result"]["issues"]
        conflict = next(row for row in issues if row["key"] == "conflict-f-03-bullets-0")
        self.assertEqual(conflict["edit"]["before"], CONFLICT)
        self.assertIn("initial setup", conflict["edit"]["after"])
        unrelated = next(row for row in issues if row["key"] == "question-q-06")
        self.assertEqual(unrelated["edit"]["before"], "")
        self.assertIn("相关要点", unrelated["edit"]["target"])
        result = self.field_rewrite("", audit_id=audit["id"], issue_key=conflict["key"])
        draft = result["drafts"][0]
        body = {
            "snapshot_id": result["snapshot_id"], "context_id": result["context_id"],
            "edits": [{**draft["save_edit"], "text": draft["after"]}],
        }
        response = self.client.post(f"{self.path}/field-rewrite/candidate", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        candidate = response.json()
        self.assertEqual(candidate["data"]["skill"]["name"], "amazon-listing-alexa-optimizer")
        validation = candidate["data"]["validation"]
        self.assertFalse(validation["ready"])
        changed = [row for row in validation["rows"] if row["decision"] == "pending"]
        self.assertEqual([row["key"] for row in changed], ["bullets.0"])
        self.assertEqual(self.client.post(f"{self.path}/field-rewrite/candidate", json=body).json()["id"], candidate["id"])
        after = self.client.get(self.path).json()
        self.assertEqual(after["snapshot"]["id"], self.project["snapshot"]["id"])
        self.assertEqual(after["snapshot"]["fields"]["bullets"]["text"].splitlines()[0], CONFLICT)
        export = self.client.get(f"{self.path}/export", params={"audit_id": audit["id"], "format": "md"})
        self.assertIn("## 修改意见", export.text)
        self.assertIn("initial setup", export.text)
        incorrect = {**body, "edits": [{**body["edits"][0], "line": 1}]}
        self.assertEqual(self.client.post(f"{self.path}/field-rewrite/candidate", json=incorrect).status_code, 422)
        self.client.put(f"{self.path}/context", json={"facts": [], "questions": []}).raise_for_status()
        self.assertEqual(self.client.post(f"{self.path}/field-rewrite/candidate", json=body).status_code, 409)

    def test_mixed_aplus_rewrites_only_current_product_lines(self):
        original = "Designed for repeated breakfast prep.\nDesigned for repeated breakfast prep.\nComparison model holds 10 kg."
        saved = self.client.post(f"{self.path}/snapshots", json={"fields": {"aplus": {
            "state": "confirmed", "text": original, "source": "自有页面",
            "page_lines": [{"line": 2, "kind": "html", "scope": "comparison", "location": "A+ 对比表"}],
        }}})
        self.assertEqual(saved.status_code, 200, saved.text)
        result = self.field_rewrite(original)
        product, comparison = result["drafts"]
        self.assertEqual(product["after"], "Designed for repeated breakfast prep.")
        self.assertEqual(comparison["status"], "reference")
        self.assertEqual(comparison["after"], "")
        response = self.client.post(f"{self.path}/field-rewrite/candidate", json={
            "snapshot_id": result["snapshot_id"], "context_id": result["context_id"],
            "edits": [{**product["save_edit"], "text": product["after"]}],
        })
        self.assertEqual(response.status_code, 201, response.text)
        parsed = response.json()["data"]["validation"]["fields"]["aplus"]
        self.assertEqual(parsed["text"], "Designed for repeated breakfast prep.\nComparison model holds 10 kg.")
        self.assertEqual(parsed["page_lines"][0]["line"], 1)
        self.assertEqual(parsed["page_lines"][0]["scope"], "comparison")

    def test_model_rewrites_title_but_changed_numbers_and_conditions_fall_back(self):
        configured = LLM("http://localhost/v1/chat/completions", "fake-key", "test-model")
        output = SkillResponse.model_validate({"drafts": [
            {"id": 0, "copy": "DemoWorks Digital Food Scale with LCD Display", "reason": "突出产品类型。"},
            {"id": 1, "copy": "Capacity: 50 kg. Works without the app for setup.", "reason": "使功能更清晰。"},
        ]})
        text = ("Title: DemoWorks LCD Digital Food Scale\n"
                "About this item\n- Capacity: 5 kg. The app is required for initial setup.")
        review = SkillReviewResponse.model_validate({"reviews": [
            {"id": 0, "grounded": True, "preserves_conditions": True, "improves_answer": True,
             "answers": [{"question": "What kind of scale is this?", "before": "partial", "after": "supported",
                          "answer_quote": "Digital Food Scale", "source_quote": "LCD Digital Food Scale",
                          "detail": "先交代商品类型，再写屏幕。"}]},
        ]})
        with patch.object(LLM, "configured", return_value=configured), patch.object(
                LLM, "complete", side_effect=[(output, "test-model"), (review, "test-model")]) as request:
            response = self.client.post("/api/quick-field-rewrite", json={"text": text})
        self.assertEqual(response.status_code, 200, response.text)
        title, bullet = response.json()["drafts"]
        self.assertTrue(title["model_generated"])
        self.assertIn("LCD Display", title["after"])
        self.assertIn("5 kg", bullet["after"])
        self.assertIn("required for initial setup", bullet["after"])
        self.assertNotIn("50 kg", bullet["after"])
        self.assertEqual(request.call_args.args[2]["skill"], "amazon-listing-alexa-optimizer")
        self.assertEqual(len(request.call_args.args[2]["fields"]), 1)
        self.assertEqual(title["answers"][0]["answer_quote"], "Digital Food Scale")

    def test_purchase_question_rewrite_preserves_first_setup_and_saved_food_scope(self):
        configured = LLM("http://localhost/v1/chat/completions", "fake-key", "test-model")
        answer = "See calories and protein on the scale with the app closed after setup, for foods you saved in the app."
        copy = answer + " Use the app for initial setup and to save foods, then select a saved food on the scale."
        question = "Can I see calories and protein on the scale without opening the app each time?"
        kept = "Select a saved food on the scale to see calories and protein without reopening the app"
        review = SkillReviewResponse.model_validate({"reviews": [{
            "id": 0, "grounded": True, "preserves_conditions": True, "improves_answer": True,
            "answers": [{"question": question, "before": "contradicted", "after": "supported", "answer_quote": answer,
                         "source_id": "f-02", "source_quote": kept, "detail": "先回答秤端能看什么，再交代首次设置。"}],
        }]})
        draft = SkillResponse.model_validate({"drafts": [{"id": 0, "copy": copy, "reason": "明确屏幕、已保存的食物和首次设置。"}]})
        with patch.object(LLM, "configured", return_value=configured), patch.object(
                LLM, "complete", side_effect=[(draft, "test-model"), (review, "test-model")]) as request:
            result = self.field_rewrite(CONFLICT, focus_question=question, mode="auto")
        row = result["drafts"][0]
        self.assertEqual(row["after"], copy)
        self.assertEqual(row["answers"][0]["source_quote"], kept)
        self.assertEqual(row["answers"][0]["before"], "contradicted")
        self.assertEqual(row["answers"][0]["after"], "supported")
        supplied = request.call_args_list[0].args[2]["fields"][0]
        self.assertEqual(supplied["focus_question"], question)
        self.assertTrue(any(fact["id"] == "f-03" for fact in supplied["confirmed_facts"]))
        self.assertTrue(any(item["source"] == "alexa" for item in supplied["questions"]))

    def test_review_rejects_lost_compatibility_condition_and_untraceable_evidence(self):
        configured = LLM("http://localhost/v1/chat/completions", "fake-key", "test-model")
        inputs = "Bullets:\n- This sleeve fits only the Round model."
        for source_quote, preserves in [("fits only the Round model", False), ("Fits every model", True)]:
            revision = SkillResponse.model_validate({"drafts": [
                {"id": 0, "copy": "Compatible with the Round model.", "reason": "说明适用型号。"},
            ]})
            review = SkillReviewResponse.model_validate({"reviews": [{
                "id": 0, "grounded": True, "preserves_conditions": preserves, "improves_answer": True,
                "answers": [{"question": "Which model does it fit?", "before": "supported", "after": "supported",
                             "answer_quote": "with the Round model", "source_quote": source_quote}],
            }]})
            with self.subTest(source=source_quote), patch.object(LLM, "configured", return_value=configured), patch.object(
                    LLM, "complete", side_effect=[(revision, "test-model"), (review, "test-model")]):
                response = self.client.post("/api/quick-field-rewrite", json={"text": inputs})
                self.assertEqual(response.status_code, 200, response.text)
                row = response.json()["drafts"][0]
                self.assertEqual(row["after"], "This sleeve fits only the Round model.")
                self.assertIsNone(response.json()["model"])

    def test_quick_diagnosis_generates_grounded_before_after_and_reason(self):
        configured = LLM("http://localhost/v1/chat/completions", "fake-key", "test-model")
        original = "Bowl for Baking · Premium quality · Tare subtracts bowl weight before adding ingredients"
        improved = "Subtract the bowl weight with tare before adding baking ingredients."
        audit = SemanticReport.model_validate({"answers": [], "conflicts": [], "quality": [
            {"field": "bullets", "score": 6, "detail": "开头泛化，去皮信息在末尾。",
             "evidence": [{"field": "bullets", "quote": "Premium quality"}]},
        ]})
        draft = SkillResponse.model_validate({"drafts": [
            {"id": 0, "copy": improved, "reason": "把去皮的操作和使用时机放到句首。"},
        ]})
        review = SkillReviewResponse.model_validate({"reviews": [{
            "id": 0, "grounded": True, "preserves_conditions": True, "improves_answer": True,
            "answers": [{"question": "When should I tare the bowl?", "before": "partial", "after": "supported",
                         "answer_quote": "before adding baking ingredients",
                         "source_quote": "Tare subtracts bowl weight before adding ingredients"}],
        }]})
        with patch.object(LLM, "configured", return_value=configured), patch.object(
                LLM, "complete", side_effect=[(audit, "test-model"), (draft, "test-model"), (review, "test-model")]):
            response = self.client.post("/api/quick-audit", json={"text": f"Bullets:\n- {original}", "mode": "llm"})
        self.assertEqual(response.status_code, 200, response.text)
        finding = response.json()["latest_audit"]["data"]["result"]["issues"][0]
        self.assertEqual(finding["edit"]["before"], original)
        self.assertEqual(finding["edit"]["after"], improved)
        self.assertIn("去皮", finding["edit"]["action"])
        self.assertTrue(finding["edit"]["model_generated"])


if __name__ == "__main__":
    unittest.main()
