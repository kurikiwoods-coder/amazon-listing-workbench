from __future__ import annotations

import io
from datetime import datetime
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .models import FIELDS, PUBLIC, SOURCE_STATUS
from .rewrite import STRATEGIES
from .rules import lines

STATUS = {
    "supported": "明确支持", "partial": "部分支持", "contradicted": "矛盾",
    "not_found": "未找到", "unknown": "无法判断",
}
DECISIONS = {"pending": "待审", "accepted": "接受", "rejected": "拒绝", "kept": "保留"}
COLOR = "0F766E"


def score_text(score) -> str:
    return "—" if score is None else str(score)


def local_stamp(value: str | None) -> str:
    return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d %H:%M") if value else ""


def sourced_copy(row):
    if not row.get("page_lines") and not row.get("notes"):
        return row["text"]
    origins = {m["line"]: m for m in row.get("page_lines", [])}
    labels = {"html": "网页正文", "alt": "图片替代文字", "ocr": "OCR 转录",
              "product": "本商品", "brand": "品牌故事", "comparison": "多商品对比"}
    result = []
    for index, text in enumerate(lines(row["text"])):
        if meta := origins.get(index):
            result.append(f"[{meta['location']} · {labels[meta['scope']]} · {labels[meta['kind']]}] {text}")
        else:
            result.append(text)
    return "\n".join([*result, *row.get("notes", [])])


def report_sections(product, snapshot, audit, context, candidate=None) -> list:
    data, result = audit["data"], audit["data"]["result"]
    screen = result.get("screen")
    modules = result.get("module_scores")
    show_screen = modules is None and result["provisional"] and screen
    reported_score = screen["score"] if show_screen else result["score"]
    reported_label = "已评分模块总分" if modules is not None else "通用文案初筛" if show_screen else "八维内容评分"
    sections = [
        ("评分摘要", ["项目", "内容"], [
            ["ASIN", product["asin"] or "未登记"], ["站点", product["market"]], ["SKU", product["sku"]],
            ["类目", product["category"] or "待补充"], ["快照", f"V{snapshot['version']} · {snapshot['id']}"],
            ["评分时间 (UTC+8)", local_stamp(audit["created_at"])],
            [reported_label, f"{score_text(reported_score)}/100" +
             ("（可评估权重不足）" if modules is None and result["provisional"] and not show_screen else "")],
            ["模块覆盖率" if modules is not None else "可评估权重",
             f"{result['coverage']}% ({result['assessed_weight']}/{result['applicable_weight']})"],
            ["评分口径", "已评分模块平均值" if modules is not None else "历史八维规则"], ["规则", data["rules"]["version"]],
            ["事实与问句", f"V{context['version']} · {context['id']}"],
            ["评分模式", data["model"] or "本地规则"],
            ["解读", "评分衡量文案内容与证据覆盖。Alexa 实测、排名和销量需独立观察。"],
        ]),
        ("评分维度", ["维度", "权重", "分数", "理由", "证据"], [
            [row["label"], row["weight"], score_text(row["score"]), row["reason"], " | ".join(
                f"{FIELDS[item['field']]} {item['line'] + 1}: {item['quote']}" for item in row["evidence"])]
            for row in result["dimensions"]
        ]),
        ("问题诊断", ["优先级", "位置", "问题", "原文", "建议", "来源", "影响问句", "需人工核实"], [
            [row["priority"], f"{FIELDS[row['field']]} {row['line'] + 1}", row["message"], row["quote"],
             row["recommendation"], row["source"], ", ".join(row["question_ids"]), "是" if row["needs_review"] else "否"]
            for row in result["issues"]
        ]),
        ("买家问题", ["问句", "使用意图", "结论", "判断依据", "原文", "待补信息"], [
            [row["text"], row["intent"], STATUS[row["status"]], row["detail"],
             " | ".join(f"{FIELDS[item['field']]} {item['line'] + 1}: {item['quote']}" for item in row["evidence"]),
             "、".join(row["missing"])] for row in result["questions"]
        ]),
        ("原始 Listing", ["字段", "状态", "数据来源", "采集时间", "内容"], [
            [FIELDS[key], SOURCE_STATUS[row["state"]], row["source"], local_stamp(row["collected_at"]), sourced_copy(row)]
            for key, row in snapshot["fields"].items()
        ]),
        ("核对记录", ["事实", "英文措辞", "审核状态", "依据"], [
            [row["label"], row["copy"], {"confirmed": "已确认", "pending": "待核实", "rejected": "不采用"}[row["state"]], row["source"]]
            for row in context["data"]["facts"]
        ]),
    ]
    if modules is not None:
        sections[1] = ("各模块得分", ["模块", "分数（满分 100）", "理由", "证据"], [
            [row["label"], "未评分" if row["score"] is None else row["score"],
             row["reason"] + (" 原文待核实。" if row["source_state"] == "pending" and row["score"] is not None else ""), " | ".join(
                f"{item['line'] + 1}: {item['quote']}" for item in row["evidence"])] for row in modules
        ])
    elif result.get("screen"):
        sections.insert(1, ("通用文案初筛", ["项目", "分数", "检查口径", "原文证据"], [
            ["初筛总分", result["screen"]["score"],
             "满分 100。按能看到的文字初步判断。模式为" +
             (" AI 与通用规则。" if result["screen"].get("mode") == "llm" else "通用规则。"), ""],
            *[[row["label"], row["score"], row["reason"], " | ".join(item["quote"] for item in row["evidence"])]
              for row in result["screen"]["checks"]],
        ]))
    edits = [(issue, issue["edit"]) for issue in result["issues"] if issue.get("edit")]
    if edits:
        sections.append(("修改意见", ["优先级", "建议位置", "原文", "建议文案", "为什么修改", "事实依据"], [
            [issue["priority"], edit["target"], edit["before"], edit["after"], edit["action"], "、".join(
                edit["fact_labels"] or list(dict.fromkeys(a["source"] for a in edit.get("answers", []) if a["source"])))]
            for issue, edit in edits
        ]))
    if candidate:
        validation = candidate["data"]["validation"]
        sections.extend([
            ("改写与审核", ["方案", "版本", "字段", "原文", "候选稿", "审核", "理由", "事实"], [
                [STRATEGIES[candidate["strategy"]]["label"], candidate["version"], row["key"], row["before"], row["effective"],
                 DECISIONS[row["decision"]], row["reason"], ", ".join(row["fact_ids"])]
                for row in validation["rows"]
            ]),
            ("发布检查", ["检查结果"], [[item] for item in validation["blockers"]] or [["可复制至人工上架流程。"]]),
        ])
    if data.get("comparison"):
        diff = data["comparison"]
        sections.append(("复评对比", ["模块" if diff.get("by_module") else "维度", "原评分", "复评", "分差"], [
            ["综合分", score_text(diff["before"]), score_text(diff["after"]), score_text(diff["score_delta"])],
            *[[item["label"], score_text(item["before"]), score_text(item["after"]), score_text(item["delta"])]
              for item in diff["dimensions"]],
        ]))
    return sections


def markdown(title: str, sections) -> bytes:
    def table_cell(value):
        return escape(str(value)).replace("|", "\\|").replace("\n", "<br>")
    rows = [f"# {title}", ""]
    for name, columns, items in sections:
        rows.extend([f"## {name}", "", "| " + " | ".join(columns) + " |",
                     "| " + " | ".join("---" for _ in columns) + " |"])
        rows.extend("| " + " | ".join(map(table_cell, item)) + " |" for item in items)
        rows.append("")
    return "\n".join(rows).encode("utf-8")


def alexa_skill_input(product, snapshot, context, rules, audit=None) -> bytes:
    matched = (audit and audit["snapshot_id"] == snapshot["id"] and audit["context_id"] == context["id"]
               and audit["data"]["product"] == product
               and all(audit["data"]["rules"][key] == rules[key] for key in ("version", "checks_version")))
    answers = {row["id"]: row for row in audit["data"]["result"]["questions"]} if matched else {}
    scope = sorted(context["data"]["questions"], key=lambda row: (row["source"] != "alexa", row["priority"] != "high"))
    sections = [
        ("诊断要求", ["项目", "要求"], [
            ["工作流", "用 amazon-listing-alexa-optimizer 检查已提供的 Listing，优先处理真实 Alexa 问句。"],
            ["证据", "逐问说明是否能从原文回答。注明字段、条目、引用及适用条件，再给出修改建议。"],
            ["图文", "品牌故事和多商品对比只供阅读。针对本型号的判断需引用本品段落，核对图片识别文字后再使用。"],
            ["改写", "用下方已确认事实编写候选稿。功能、参数或兼容性仍待确认时，给出具体核验问题。"],
            ["计分", "按已取得的模块文案分别评分，计算模块平均分。保留未提供和不适用状态。"],
            ["验证", "用同一组问句比较前后文案。商品卡、自然提及和回答正确率从 Alexa 实测记录核对。"],
        ]),
        ("商品与版本", ["项目", "值"], [
            ["ASIN", product["asin"] or "未登记"], ["站点", product["market"]], ["品牌", product["brand"]],
            ["品类", product["category"] or "待补"], ["原文快照", f"V{snapshot['version']} · {snapshot['id']}"],
            ["事实和问句", f"V{context['version']} · {context['id']}"],
            ["适用规则", f"{rules['version']} · {rules['checks_version']}"],
        ]),
        ("Listing 原文", ["字段", "核验状态", "来源", "原文"], [
            [label, SOURCE_STATUS[snapshot["fields"][key]["state"]], snapshot["fields"][key]["source"],
             sourced_copy(snapshot["fields"][key])]
            for key, label in FIELDS.items()
        ]),
        ("已确认产品事实", ["ID", "事实", "已批准英文", "来源"], [
            [row["id"], row["label"], row["copy"], row["source"]] for row in context["data"]["facts"] if row["state"] == "confirmed"
        ] or [["", "待核实", "", ""]]),
        ("买家问句", ["问题", "来源", "优先级", "上次诊断", "引用"], [
            [row["text"], "Alexa 实测问句" if row["source"] == "alexa" else row["source"], row["priority"],
             STATUS[answer["status"]] if (answer := answers.get(row["id"])) else "待检查",
             " | ".join(f"{FIELDS[item['field']]} {item['line'] + 1}: {item['quote']}" for item in answer["evidence"]) if answer else ""]
            for row in scope
        ]),
    ]
    return markdown(f"{product['name']} · Alexa Skill 输入", sections)


def excel(title: str, sections) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    wb.properties.title = title
    for name, columns, items in sections:
        sheet = wb.create_sheet(name)
        sheet.append(columns)
        for item in items:
            sheet.append(item)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for index, column in enumerate(sheet.columns, 1):
            width = max(14, min(56, max((len(str(cell.value or "")) for cell in column), default=18) + 4))
            sheet.column_dimensions[column[0].column_letter].width = width
            for cell in column:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
                cell.alignment = Alignment(vertical="top", wrap_text=True)
            column[0].fill = PatternFill("solid", fgColor=COLOR)
            column[0].font = Font(color="FFFFFF", bold=True)
    output = io.BytesIO()
    wb.save(output)
    return output.getvalue()


def pdf(title: str, sections) -> bytes:
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    headline = ParagraphStyle(name="Headline", fontName="STSong-Light", fontSize=20, leading=27, textColor=colors.HexColor("#142b2f"))
    subhead = ParagraphStyle(name="Subhead", fontName="STSong-Light", fontSize=13, leading=17,
                             textColor=colors.HexColor(f"#{COLOR}"), keepWithNext=True)
    body = ParagraphStyle(name="Body", fontName="STSong-Light", fontSize=10, leading=15, wordWrap="CJK",
                          textColor=colors.HexColor("#26373b"))
    flow = [Paragraph(escape(title), headline), Spacer(1, 7 * mm)]
    for name, columns, items in sections:
        flow.extend([Paragraph(name, subhead), Spacer(1, 3 * mm)])
        for item in items:
            lines = [f"{label}　{value if value != '' else '—'}" for label, value in zip(columns, item)]
            safe_text = "<br/>".join(escape(line).replace("\n", "<br/>") for line in lines)
            flow.extend([Paragraph(safe_text, body), Spacer(1, 2.6 * mm)])
        flow.append(Spacer(1, 4 * mm))
    output = io.BytesIO()
    document = SimpleDocTemplate(output, title=title, leftMargin=16 * mm, rightMargin=16 * mm,
                                 topMargin=16 * mm, bottomMargin=16 * mm)
    document.build(flow)
    return output.getvalue()


EXPORTS = {
    "md": (markdown, "text/markdown; charset=utf-8"),
    "xlsx": (excel, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "pdf": (pdf, "application/pdf"),
}
