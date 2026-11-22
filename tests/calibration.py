"""Regression controls and an evaluator for independently annotated real listings."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.audit import score_listing
from backend.models import ContextInput, ListingInput, ProductInput
from backend.rules import rules_for

CATEGORIES = [
    ("Food Scales", "Kitchen Scale", "Read weight in grams or ounces", "Keep the display dry", "Safe to submerge"),
    ("USB Chargers", "USB Charger", "Charge supported devices from two USB-C ports", "Use indoors only", "Made for outdoor use"),
    ("Water Bottles", "Water Bottle", "Holds 750 ml of cold water", "Use with cold drinks only", "For boiling liquids"),
    ("Camping Lanterns", "Camping Lantern", "Set brightness at three levels", "Use with AA batteries", "Recharge over USB"),
    ("Laptop Stands", "Laptop Stand", "Fits laptops up to 15 inches", "Use on a level desk", "Works on uneven surfaces"),
]
VARIANTS = ("complete", "too_long", "conflict", "missing", "not_applicable", "known_absent")


def synthetic_controls():
    for category_index, (category, noun, feature, boundary, wrong) in enumerate(CATEGORIES):
        for variant_index, variant in enumerate(VARIANTS):
            number = 1 + category_index * len(VARIANTS) + variant_index
            title = f"DemoBrand {noun}"
            product = {"asin": f"B0CAL{number:05}", "name": title, "category": category, "brand": "DemoBrand", "product_type": "non_media"}
            fields = {key: {"text": value, "state": "confirmed", "source": "回归测试固定文本"}
                      for key, value in {"title": title, "highlights": feature, "bullets": feature + "\n" + boundary}.items()}
            if variant == "too_long":
                fields["title"]["text"] = title + " " + "x" * 76
            if variant == "conflict":
                fields["bullets"]["text"] = wrong + "\n" + feature
            if variant == "missing":
                fields.pop("bullets")
                fields.pop("highlights")
            if variant == "not_applicable":
                fields["highlights"] = {"state": "na", "source": "回归测试手动标记"}
            if variant == "known_absent":
                fields["aplus"] = {"state": "absent", "source": "回归测试手动标记"}
            yield {
                "variant": variant, "product": product,
                "listing": {"fields": fields},
                "context": {
                    "facts": [
                        {"id": "f-a", "label": "功能", "copy": feature, "state": "confirmed", "source": "回归证据"},
                        {"id": "f-b", "label": "边界", "copy": boundary, "state": "confirmed", "source": "回归证据",
                         "role": "boundary", "conflicts": [wrong]},
                    ],
                    "questions": [{"id": "q1", "text": "What are the use restrictions?", "fields": ["bullets"], "fact_ids": ["f-b"]}],
                },
            }


def evaluate(case: dict) -> dict:
    product = ProductInput.model_validate(case["product"]).model_dump()
    fields = ListingInput.model_validate(case["listing"]).normalized()
    context = ContextInput.model_validate(case["context"]).model_dump(by_alias=True)
    return score_listing(product, fields, context, rules_for(product, case.get("as_of")))


def compare_human(rows: list[dict]) -> dict:
    identities = {(row["product"]["asin"].upper(), row["product"].get("market", "US")) for row in rows}
    if len(identities) < 30 or len({row["product"]["category"] for row in rows}) < 2:
        raise ValueError("真实校准集需包含至少 30 个不同 ASIN 和至少两个类目。")
    if any(not row.get("reviewer") or not isinstance(row.get("human_score"), (int, float))
           or not 0 <= row["human_score"] <= 100 or not isinstance(row.get("human_p0"), list)
           or any(item["kind"] not in {"factual", "policy"} for item in row["human_p0"]) for row in rows):
        raise ValueError("每个 ASIN 需包含人工复核人、0–100 分及 P0 的类型和所在字段。")
    errors, repeated = [], 0
    predicted = expected = correct = provisional = 0
    for row in rows:
        result = evaluate(row)
        repeated += int(result == evaluate(row))
        p0 = {(item["field"], item["kind"]) for item in result["issues"] if item["priority"] == "P0"}
        gold = {(item["field"], item["kind"]) for item in row["human_p0"]}
        predicted += len(p0)
        correct += len(p0 & gold)
        expected += len(gold)
        if result["score"] is None or result["provisional"]:
            provisional += 1
        else:
            errors.append(abs(result["score"] - row["human_score"]))
    return {
        "asin_count": len(identities), "rows": len(rows), "provisional_rows": provisional,
        "repeat_exact_rate": repeated / len(rows),
        "p0_field_kind_precision": correct / predicted if predicted else None,
        "p0_field_kind_recall": correct / expected if expected else None,
        "mean_absolute_score_error": sum(errors) / len(errors) if errors else None,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="JSON 格式的人工标注集")
    args = parser.parse_args()
    print(json.dumps(compare_human(json.loads(args.file.read_text(encoding="utf-8"))), ensure_ascii=False, indent=2))
