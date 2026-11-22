from __future__ import annotations

import re

GUIDANCE_VERSION = "missing-data-v1"

# Ranges describe the proposed treatment under the given assumption, rather than the unseen field.
SCENARIOS = {
    "questions": ("bullets", (6, 8), "把已核实的答案写入五点或商品描述，并同时写明使用条件。",
                  "补一条能直接回答高优先级买家问题的原文或改写依据。", "真实买家问句、商品问答和对应的说明书章节"),
    "accuracy": ("bullets", (6, 8), "为核心卖点核实规格、首次设置、适用范围及例外，并在文案里注明边界。",
                 "先核对原文最重要的功能表述，注明它成立的条件。", "对应型号的说明书、规格表或可复现的实测"),
    "title": ("title", (6, 8), "标题用已核实的商品名称和关键区别说明它是什么。",
              "保存对应变体的完整标题，标出商品类型与可核实的关键规格。", "Amazon 商品页或 Seller Central 商品标题"),
    "highlights": ("highlights", (5, 7), "用一条已核实的核心信息补充标题，适用时写明场景。",
                   "核对该商品是否有 Item Highlights，确认后录入原文或标明不适用。", "公开商品页对应模块或卖家后台"),
    "bullets": ("bullets", (6, 8), "每条要点回答一个具体的买家问题，并写明适用条件。",
                "核对完整五点，标出每条回答的购买问题，优先补充含糊的步骤和限制。", "商品详情页 About this item 或卖家后台导出"),
    "aplus": ("aplus", (6, 8), "用已核实的场景、使用步骤和参数对照设计 A+，让图片与文字表达一致。",
              "补 A+ 截图或发布原文，再核对它是否回答五点里说不清的购买问题。", "A+ 内容管理器、发布预览或网页模块截图"),
    "attributes": ("attributes", (6, 8), "把该类目的关键属性填齐，并与说明书、标题和五点交叉核对。",
                   "导出类型、适用型号及关键规格，按实际类目核对必填项和冲突。", "Seller Central 商品属性或 Category Listings Report"),
    "keywords": ("keywords", (5, 7), "用已知查询整理与商品匹配的搜索意图，再核对这些词分布在哪些段落。",
                 "把已有目标词或 SQP 报告里的相关搜索词填进目标关键词，核对标题、五点和描述中哪里表达不清楚。", "Search Terms、目标词表或 SQP 搜索查询表现导出"),
}
BOUNDARY = re.compile(
    r"\b(?:all features|works with (?:all|any)|compatible with (?:all|any)|no app required|without (?:an?|the) app|"
    r"no (?:setup|subscription) required|guaranteed)\b|所有功能|无需.{0,5}(?:手机|应用|App)|完全不需要", re.I
)


def review_guidance(dimensions, questions, issues, observed, scenarios, checks, *, include_scenarios=True):
    references = observed[:1]
    open_questions = [row for row in questions if row["status"] in {"partial", "not_found", "unknown"}]
    open_questions.sort(key=lambda row: (row["status"] != "partial", row["priority"] != "high"))
    addressed = {(row["field"], row["line"]) for row in issues if row["kind"] == "factual"}
    items = []
    for check in checks:
        record = check["evidence"][0]
        location = (record["field"], record["line"])
        if location in addressed:
            continue
        items.append({"key": f"check-{location[0]}-{location[1]}", "dimension": None, "field": record["field"],
                      "label": check["label"], "range": None, "assumption": "", "action": check["action"],
                      "source_needed": "对应型号的说明书、实测或官方规格", "question": check["question"],
                      "evidence": check["evidence"], "mode": "llm"})
        addressed.add(location)
    for record in observed:
        if len(items) >= 4:
            break
        if (record["field"], record["line"]) in addressed or not BOUNDARY.search(record["quote"]):
            continue
        items.append({"key": f"scope-{record['field']}-{record['line']}", "dimension": None, "field": record["field"],
                      "label": "核对这句的适用范围", "range": None, "assumption": "",
                      "action": "确认这句话是否有首次设置、部分功能、兼容型号或使用条件的例外，再决定怎样改写。",
                      "source_needed": "对应型号的说明书、实测或官方规格", "question": "",
                      "evidence": [record], "mode": "rules"})
    if not include_scenarios:
        return items
    for dimension in dimensions:
        key = dimension["key"]
        if dimension["score"] is not None or dimension["status"] == "na" or key not in SCENARIOS:
            continue
        field, limits, assumption, action, source = SCENARIOS[key]
        relevant = next((row for row in open_questions if field in row["suggested_fields"]), None)
        citations = (relevant or {}).get("evidence") or next(([row] for row in observed if row["field"] == field), references)
        model = scenarios.get(key)
        items.append({"key": f"scenario-{key}", "dimension": key, "field": field, "label": dimension["label"],
                      "range": model["range"] if model else limits, "assumption": model["assumption"] if model else assumption,
                      "action": model["action"] if model else action, "source_needed": source,
                      "question": relevant["text"] if relevant else "", "evidence": model["evidence"] if model else citations[:2],
                      "mode": "llm" if model else "rules"})
    return items
