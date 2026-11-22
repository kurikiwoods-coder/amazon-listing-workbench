import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .marketplaces import DEFAULT_MARKET
from .models import FIELDS, PUBLIC

RULE_PATH = Path(__file__).with_name("rules.json")
RULE_CHECKS = "us-copy-checks-v2"
LEGACY_RULE_CHECKS = ("us-copy-checks-v1",)


def rules_for(product: dict, as_of: str | None = None) -> dict:
    date = as_of or datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    data = json.loads(Path(os.getenv("LISTING_RULES_FILE") or RULE_PATH).read_text(encoding="utf-8"))
    matching = [
        row for row in data["profiles"]
        if row["market"] == product["market"]
        and row["product_type"] == product["product_type"]
        and row.get("category", product["category"]) == product["category"]
        and row["effective"] <= date
    ]
    profile = max(matching, key=lambda row: (bool(row.get("category")), row["effective"]), default={})
    market = product["market"]
    return {
        "version": profile.get("version", data["version"] if profile or market == DEFAULT_MARKET else "listing-general-v1"),
        "market": market,
        "checks_version": RULE_CHECKS,
        "rubric": data["rubric"],
        "coverage_threshold": data["coverage_threshold"],
        "as_of": date,
        "effective": profile.get("effective"),
        "limits": profile.get("limits", {}),
        "sources": profile.get("sources", {}),
        "title_forbidden": profile.get("title_forbidden", ""),
    }


def lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def rule_findings(fields: dict, rules: dict, *, for_publication=False) -> list[dict]:
    outcomes = []
    eligible = {key: fields[key]["text"] for key in PUBLIC if fields[key]["state"] == "confirmed"}
    for key, limit in rules["limits"].items():
        if key not in {"title", "highlights", "bullet_count"}:
            continue
        field = "bullets" if key == "bullet_count" else key
        if field not in eligible:
            continue
        value = eligible[field]
        count = len(lines(value)) if key == "bullet_count" else len(value)
        if count > limit:
            if (key == "bullet_count" and rules.get("checks_version") == RULE_CHECKS and not for_publication
                    and re.match(r"^(?:Amazon [A-Z]{2} (?:商品页|保存的网页)|Nexscope 商品详情) · ", fields[field]["source"])):
                outcomes.append({
                    "key": "verify-bullet-count", "field": field, "line": 0, "quote": lines(value)[0],
                    "message": f"商品页显示 {count} 条五点，需要核对卖家实际录入的条数。",
                    "recommendation": f"从 Seller Central 核对卖家录入的五点。当前发布规则上限为 {limit} 条。",
                    "source": "https://sellercentral.amazon.com/seller-forums/discussions/t/804d6a1c-d630-4879-b548-3235cad90940",
                    "priority": "P2", "kind": "verification", "needs_review": True,
                })
                continue
            label = "条" if key == "bullet_count" else "字符"
            outcomes.append({
                "key": f"limit-{field}", "field": field, "line": 0, "quote": lines(value)[0],
                "message": f"{FIELDS[field]}有 {count} {label}，当前规则上限为 {limit} {label}。",
                "recommendation": "删减重复描述，优先保留产品名称和已核实的关键信息。",
                "source": rules["sources"].get(key, ""), "priority": "P0", "kind": "policy",
            })
    title = eligible.get("title", "")
    forbidden = "".join(sorted(set(title).intersection(rules.get("title_forbidden", ""))))
    if forbidden:
        outcomes.append({
            "key": "symbols-title", "field": "title", "line": 0, "quote": title,
            "message": f"标题含有本规则禁止的字符：{forbidden}",
            "recommendation": "去掉禁用符号，保留已核实的型号与商品名称。",
            "source": rules["sources"].get("title_symbols", ""), "priority": "P0", "kind": "policy",
        })
    contact_source = rules["sources"].get("contact")
    if not contact_source and rules.get("market", DEFAULT_MARKET) == DEFAULT_MARKET:
        contact_source = "https://sellercentral.amazon.com/gp/help/external/G200390640"
    if not contact_source:
        return outcomes
    for field, text in eligible.items():
        for index, line in enumerate(lines(text)):
            if re.search(r"https?://|www\.|[\w.+-]+@[\w.-]+\.[a-z]{2,}", line, re.I):
                outcomes.append({
                    "key": f"contact-{field}-{index}", "field": field, "line": index, "quote": line,
                    "message": "正文包含站外链接或邮箱。",
                    "recommendation": "删除站外地址及联系信息。",
                    "source": contact_source,
                    "priority": "P0", "kind": "policy",
                })
    return outcomes


def profile_complete(rules: dict) -> bool:
    return all(key in rules["limits"] for key in ("title", "highlights", "bullet_count"))
