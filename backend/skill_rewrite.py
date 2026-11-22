"""Apply the Alexa Listing optimization guidance to specific pieces of copy."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from itertools import groupby
from typing import Annotated, Literal

from pydantic import Field

from .audit import anchor_match
from .importers import bullet_number, field_key, split_sections
from .llm import LLM, ModelFailed
from .models import FIELDS, ID, PUBLIC, Model, ShortText
from .rewrite import initial_values, supported_ids
from .rules import lines, rule_findings

SKILL = {
    "name": "amazon-listing-alexa-optimizer",
    "version": "field-rewrite-v2",
    "url": "https://github.com/liangdabiao/amazon-listing-alexa-optimizer",
}
GUIDE = {
    "title": "先写品牌和商品类型，保留型号。再写一个有依据、能帮助买家区分商品的核心信息。",
    "highlights": "用短语补充标题未交代的材质、用途、兼容范围或其他购买条件。",
    "bullets": "每条先给一个买家问题的直接答案，再交代适用场景、操作、结果和必要条件。",
    "aplus": "用实际场景解释怎么使用。按操作顺序写步骤和结果，在相关段落写清适用范围及可核对的证据。",
    "raw_text": "先写产品是什么、适合什么情况，再分段回答买家关心的操作、结果和使用条件。",
    "public_specs": "每行保留一项公开规格，完整保留属性名、数值、单位和适用型号。",
    "attributes": "每行保留一个属性及其值。遇到同名不同值时，按同站点、同型号的资料核对。",
    "search_terms": "删除重复词组，保留相关用途和同义表达。",
    "keywords": "合并重复词组，保留商品身份、用途和购买问题对应的词。",
}

BULLET = re.compile(r"^(?:[-*•▪]\s+|\d{1,2}[.)]\s+|\d{1,2}、\s*)")
HEADING = re.compile(r"^(?:#{1,6}\s*)?([^:：]{1,34})[:：]\s*(.*)$")
GENERIC_COPY = re.compile(
    r"(?:premium quality(?: and easy to use)?|high quality|easy to use|perfect gift|best on the market|top quality)", re.I
)
COPY_BREAK = re.compile(r"\s*[|·]\s*|\s+[—–-]\s+")
BOUNDARY = re.compile(
    r"\b(?:without|only|except|not|never|no|after(?:ward)?|before|initial setup|first[- ]time setup|"
    r"first setup|requires?|required|compatible|compatibility|offline|incompatible|up to|at least|optional|once|closed)\b"
    r"|首次|初次|设置后|仅限|不支持|不需要|无需|需要|兼容|之后|之前", re.I
)
SENSITIVE_CLAIM = re.compile(
    r"\b(?:waterproof|water-resistant|dishwasher[- ]safe|bluetooth|wifi|usb[- ]c|"
    r"medical|certified|fda|bpa[- ]free|lifetime|warranty|guarantee(?:d)?|"
    r"battery|batteries|cordless|offline|subscription|included|third[- ]party tested|spillproof|leakproof)\b"
    r"|完全离线|无需联网|不需联网|防水|食品级|已认证|终身保修", re.I
)
ABSOLUTE = re.compile(r"\b(?:all features|all (?:models|devices|modes)|no app (?:ever|required)|never needs?|"
                      r"any (?:device|model)|fully offline)\b|任何型号|所有功能|完全不需要", re.I)
QUANTITY = re.compile(
    r"\d+(?:,\d{3})*(?:\.\d+)?(?:\s*(?:°[cf]|%|fl\.?\s*oz|grams?|lbs?|pounds?|kg|mg|g|oz|"
    r"ml|l|qt|cm|mm|inches|inch|in|gb|tb|mah|wh|w|v|hz|minutes?|hours?|days?)(?![a-z]))?", re.I
)


@dataclass(frozen=True)
class Match:
    field: str
    line: int
    count: int
    complete: bool


def comparable(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def source_lines(text: str, field: str) -> list[str]:
    original = lines(text)
    if field == "bullets":
        return [BULLET.sub("", line) for line in original]
    return original


def matches(text: str, fields: dict, only: str | None = None) -> list[Match]:
    complete, excerpts = [], []
    for field, row in fields.items():
        if only and field != only:
            continue
        pasted = [comparable(line) for line in source_lines(text, field)]
        original = [comparable(line) for line in source_lines(row["text"], field)]
        if not pasted:
            continue
        if comparable(text) == comparable(row["text"]):
            complete.append(Match(field, 0, len(original), True))
            continue
        for index, line in enumerate(original):
            if original[index:index + len(pasted)] == pasted:
                complete.append(Match(field, index, len(pasted), True))
            elif len(pasted) == 1 and len(pasted[0]) >= 13 and pasted[0] in line:
                excerpts.append(Match(field, index, 1, False))
    return complete or excerpts


def labelled_text(text: str) -> str:
    prepared = []
    for line in text.splitlines():
        clean = re.sub(r"^(#{1,6}\s+)?\*\*([^*]+)\*\*(.*)$", r"\1\2\3", line.lstrip()).strip()
        heading = HEADING.fullmatch(clean)
        if heading and bullet_number(heading[1]):
            prepared.append("Bullets: " + heading[2])
        else:
            prepared.append(clean)
    return "\n".join(prepared)


def detected_blocks(text: str, fields: dict, brand: str, override: str | None) -> list[tuple]:
    exact = matches(text, fields, override)
    if exact and all(match.complete for match in exact):
        return [unlabelled(text, fields, brand, override)]
    labelled, remaining = split_sections(labelled_text(text))
    if labelled:
        if override and len(labelled) == 1 and not remaining:
            copy = next(iter(labelled.values()))
            return [(override, copy, "manual", matches(copy, fields, override), [])]
        result = [(field, copy, "heading", matches(copy, fields, field), [])
                  for field, copy in labelled.items()]
        if remaining:
            result.append(unlabelled(remaining, fields, brand, override))
        return result
    if re.fullmatch(r"(?:#{1,6}\s*)?[^:：]{1,34}[:：]?\s*", text) and field_key(text.strip("# :：")):
        return [(None, text, "unclear", [], [])]
    return [unlabelled(text, fields, brand, override)]


def unlabelled(text, fields, brand, override):
    found = matches(text, fields, override)
    possible = list(dict.fromkeys(item.field for item in found))
    if override:
        return override, text, "manual", found, []
    if len(possible) == 1:
        return possible[0], text, "snapshot", found, []
    if not possible:
        entries = lines(text)
        if entries and all(BULLET.match(line) for line in entries):
            return "bullets", text, "format", [], []
        if len(entries) == 1 and not re.search(r"[:：;；]|[.!?。！？](?:\s|$)", text):
            words = [word for word in re.findall(r"[A-Za-z][A-Za-z\d-]*", text)
                     if word.casefold() not in {"for", "and", "with", "in", "a", "the", "of"}]
            starts_with_brand = brand and comparable(text).startswith(comparable(brand) + " ")
            title_case = len(words) >= 4 and 5 * sum(word[0].isupper() for word in words) >= 4 * len(words)
            sentence_words = re.search(r"\b(?:use|uses|choose|include[sd]?|press|has|enjoy|you|requires?|required|works)\b", text, re.I)
            if len(text) <= 200 and not sentence_words and (starts_with_brand or title_case):
                return "title", text, "format", [], []
    return None, text, "unclear", [], possible


def location(field: str, line: int | None = None, count: int = 1) -> str:
    if line is None or field in {"title", "highlights", "search_terms", "keywords", "attributes"}:
        return FIELDS[field]
    label = "条" if field == "bullets" else "段"
    suffix = f"第 {line + 1} {label}" if count == 1 else f"第 {line + 1}–{line + count} {label}"
    return f"{FIELDS[field]} · {suffix}"


def clean_copy(text: str, field: str, rules: dict) -> str:
    if field in {"keywords", "search_terms"}:
        terms = [word.strip() for word in re.split(r"[,，、\n]+", text) if word.strip()]
        unique = {}
        for term in terms:
            unique.setdefault(comparable(term), term)
        return ", ".join(unique.values())
    cleaned, seen = [], set()
    for line in source_lines(text, field):
        if field in {"attributes", "public_specs"} and (pair := HEADING.fullmatch(line)):
            line = f"{pair[1].strip()}: {pair[2]}"
        parts = COPY_BREAK.split(line) if field in {"title", "highlights", "bullets"} else [line]
        kept = list(dict.fromkeys(part.strip() for part in parts if part.strip() and not GENERIC_COPY.fullmatch(part.strip(" .!"))))
        if not kept:
            continue
        copy = " · ".join(kept) if kept != parts else line
        if field == "title":
            copy = copy.rstrip("".join(char for char in "!?" if char in rules.get("title_forbidden", "")) or " ")
        if comparable(copy) not in seen:
            cleaned.append(copy)
            seen.add(comparable(copy))
    return (" " if field in {"title", "highlights"} else "\n").join(cleaned)


def protected_section(row: dict, start: int, count: int) -> bool:
    return any(start <= item["line"] < start + count and item["scope"] != "product"
               for item in row.get("page_lines", []))


def separate_aplus(blocks: list[tuple], fields: dict) -> list[tuple]:
    result = []
    excluded = {item["line"] for item in fields["aplus"].get("page_lines", []) if item["scope"] != "product"}
    for field, before, method, anchors, possible in blocks:
        anchor = anchors[0] if len(anchors) == 1 else None
        original = lines(before)
        if (field != "aplus" or not anchor or not anchor.complete or len(original) != anchor.count
                or not excluded.intersection(range(anchor.line, anchor.line + anchor.count))):
            result.append((field, before, method, anchors, possible))
            continue
        for _, offsets in groupby(range(anchor.count), key=lambda offset: anchor.line + offset in excluded):
            indices = list(offsets)
            copy = "\n".join(original[index] for index in indices)
            match = Match(field, anchor.line + indices[0], len(indices), True)
            result.append((field, copy, method, [match], []))
    return result


def copy_checks(field: str, text: str, rules: dict) -> list[str]:
    if field in PUBLIC:
        fields = {key: {"state": "missing", "text": "", "source": ""} for key in PUBLIC}
        if text:
            fields[field] = {"state": "confirmed", "text": text, "source": "字段改写"}
        return [issue["message"] for issue in rule_findings(fields, rules, for_publication=True)]
    if field in {"attributes", "public_specs"}:
        values, conflicts = {}, []
        for line in lines(text):
            if pair := HEADING.fullmatch(line):
                name = comparable(pair[1])
                value = comparable(pair[2])
                if name in values and values[name] != value:
                    conflicts.append(f"「{pair[1]}」有不同的值，请核对同一型号的规格。")
                values[name] = value
        return list(dict.fromkeys(conflicts))
    return []


def issue_edit(issue, fields, context, result, rules) -> dict:
    seen_answers = {item["id"]: item for item in result["questions"]}
    linked = [question for question in context["questions"] if question["id"] in issue["question_ids"]]
    question = next((item["text"] for item in linked), "")
    field, start = issue["field"], issue["line"]
    if issue["kind"] == "question" and linked:
        targets = seen_answers.get(linked[0]["id"], {}).get("suggested_fields", [])
        field = next((target for target in targets if target in GUIDE and fields[target]["state"] != "na"), field)
        if field != issue["field"] or not issue["quote"] or any(
                other["field"] == field and other["line"] == start and other["kind"] == "factual"
                for other in result["issues"]):
            start = len(lines(fields[field]["text"]))
    row = fields[field]
    originals = lines(row["text"])
    if field in {"title", "highlights"} or issue["key"] in {"limit-bullets", "verify-bullet-count"}:
        before, start, count = row["text"], 0, len(originals)
    else:
        start = min(start, len(originals))
        before = originals[start] if start < len(originals) else ""
        count = int(bool(before))
    can_rewrite = field in GUIDE and row["state"] != "na" and not protected_section(row, start, count)
    facts = {fact["id"]: fact for fact in context["facts"] if fact["state"] == "confirmed"}
    ids, after = [], ""
    action = issue["recommendation"]
    if can_rewrite and field == "title" and issue["kind"] == "policy":
        pick = next((fact for fact in facts.values() if fact["role"] == "identity"
                     and not copy_checks(field, fact["copy"], rules)), None)
        if pick:
            after, ids = pick["copy"], [pick["id"]]
        action = "把品牌和商品类型放在标题开头，移去重复的场景描述。"
    if can_rewrite and field in {"bullets", "aplus", "raw_text"} and issue["kind"] in {"factual", "question", "boundary"}:
        if issue["kind"] != "factual" and before and not supported_ids(before, list(facts.values())):
            start, count, before = len(originals), 0, ""
        primary = [facts[fid] for fid in issue["fact_ids"] if fid in facts]
        if primary and issue["kind"] == "factual":
            complement = [facts[fid] for item in linked for fid in item["fact_ids"]
                          if fid in facts and facts[fid]["role"] in {"feature", "boundary"}]
            primary = sorted([*primary, *complement], key=lambda fact: fact["role"] != "boundary")
        if primary:
            ids = list(dict.fromkeys(fact["id"] for fact in primary))[:3]
            after = " · ".join(facts[fid]["copy"] for fid in ids)
            action = "把已核实的功能和适用条件写在同一个要点。" if issue["kind"] == "factual" else "用已核实的说法回答这条购买问题。"
            if issue["kind"] != "factual" and before:
                existing = supported_ids(before, list(facts.values())) or []
                appended = [facts[fid]["copy"] for fid in ids if fid not in existing]
                after = " · ".join([before, *appended])
            if field == "bullets" and not before and len(originals) >= rules["limits"].get("bullet_count", 5):
                action = "把这句并入回答同一问题的现有要点，先核对五点条数。"
        elif linked:
            intent = linked[0]["intent"].strip() or linked[0]["text"]
            action = f"先核实买家关心的「{intent}」，再在{FIELDS[field]}补上一句直接回答。"
    if can_rewrite and before and not after and issue["kind"] in {"quality", "policy"}:
        after = clean_copy(before, field, rules)
        if not after and field == "bullets":
            action = "删掉泛化形容词，在该条写入能回答买家问题的已核实规格或用法。"
    if not after or after == before or copy_checks(field, after, rules):
        after, ids = "", []
    if field == "bullets" and not before:
        target = f"五点描述 · 在相关要点补充" if start >= rules["limits"].get("bullet_count", 5) else f"五点描述 · 补充第 {start + 1} 条"
    else:
        target = location(field, start if count else None, count)
    return {"field": field, "line": start, "target": target, "before": before, "after": after, "action": action,
            "question": question, "fact_labels": [facts[fid]["label"] for fid in ids],
            "fact_ids": ids, "rewrite": can_rewrite and bool(before or after)}


def with_issue_edits(audit: dict, fields: dict, context: dict) -> dict:
    data = audit["data"]
    original = data["result"]
    issues = [{**issue, "edit": issue.get("edit") or issue_edit(issue, fields, context, original, data["rules"])}
              for issue in original["issues"]]
    return {**audit, "data": {**data, "result": {**original, "issues": issues}}}


class SkillDraft(Model):
    id: int = Field(ge=0, le=12_000)
    text: Annotated[str, Field(alias="copy", min_length=1, max_length=12_000)]
    reason: ShortText


class SkillResponse(Model):
    drafts: list[SkillDraft] = Field(max_length=20)


CheckStatus = Literal["supported", "partial", "not_found", "contradicted", "unknown"]


class AnswerCheck(Model):
    question: Annotated[str, Field(min_length=1, max_length=600)]
    before: CheckStatus
    after: CheckStatus
    answer_quote: ShortText = ""
    source_id: ID | None = None
    source_quote: ShortText = ""
    detail: ShortText = ""


class DraftReview(Model):
    id: int = Field(ge=0, le=12_000)
    grounded: bool
    preserves_conditions: bool
    improves_answer: bool
    answers: list[AnswerCheck] = Field(min_length=1, max_length=5)


class SkillReviewResponse(Model):
    reviews: list[DraftReview] = Field(max_length=20)


def model_preserves(original: str, copy: str, verified: list[dict]) -> bool:
    source = "\n".join([original, *(fact["copy"] for fact in verified)])

    def quantities(text):
        normalized = unicodedata.normalize("NFKC", text)
        return {re.sub(r"[,\s]+", "", match.group().casefold()) for match in QUANTITY.finditer(normalized)}
    revised = quantities(copy)
    if not quantities(original) <= revised <= quantities(source):
        return False
    if BOUNDARY.search(original) and not BOUNDARY.search(copy):
        return False
    for pattern in (ABSOLUTE, SENSITIVE_CLAIM):
        if {comparable(word) for word in pattern.findall(copy)} - {comparable(word) for word in pattern.findall(source)}:
            return False
    return not any(anchor_match(comparable(copy), comparable(term)) for fact in verified for term in fact["conflicts"])


def semantic_inputs(row: dict, context: dict, fields: dict, focus_question: str, linked_question: str = "") -> dict:
    before, approved = row["before"], row["after"]
    key = row["field"]
    seed = comparable(before + "\n" + approved)
    scoped = row["line"] is not None and row["save_edit"] is not None
    verified = [fact for fact in context["facts"] if scoped and fact["state"] == "confirmed"]
    matching = {fact["id"] for fact in verified
                if anchor_match(seed, comparable(fact["copy"])) or any(anchor_match(seed, comparable(term))
                                                                        for term in fact["conflicts"])}
    priorities = {comparable(value) for value in (focus_question, linked_question) if value}
    questions = [question for question in context["questions"] if scoped and key in question["fields"]
                 and (comparable(question["text"]) in priorities or matching.intersection(question["fact_ids"])
                      or any(anchor_match(seed, comparable(term)) for term in question["anchors"]))]
    questions.sort(key=lambda q: (comparable(q["text"]) not in priorities, q["source"] != "alexa", q["priority"] != "high"))
    questions = questions[:4]
    relevant = matching.union(fid for question in questions for fid in question["fact_ids"])
    verified = [fact for fact in verified if fact["id"] in relevant]
    return {
        "id": row["id"], "field": key, "direction": GUIDE[key],
        "before": before, "basis_copy": approved, "source": row["source"] or "粘贴原文",
        "source_state": fields[key]["state"] if scoped else "pasted",
        "goal": row["reason"], "focus_question": focus_question or linked_question,
        "questions": questions,
        "confirmed_facts": verified,
    }


def local_answers(data: dict, row: dict) -> list[dict]:
    confirmed = {fact["id"]: fact for fact in data["confirmed_facts"]}
    result = []
    for question in data["questions"][:3]:
        ids = question["fact_ids"]
        if not ids:
            continue

        def status(text):
            value = comparable(text)
            found = [confirmed[fid] for fid in ids if fid in confirmed
                     and anchor_match(value, comparable(confirmed[fid]["copy"]))]
            conflicting = any(anchor_match(value, comparable(term)) for fid in ids if fid in confirmed
                              for term in confirmed[fid]["conflicts"])
            return ("contradicted" if conflicting else "supported" if len(found) == len(ids) else
                    "partial" if found else "unknown"), found

        original, _ = status(row["before"])
        final, matches = status(row["after"])
        if final in {"supported", "partial"}:
            quote = next((line for line in lines(row["after"])
                          if anchor_match(comparable(line), comparable(matches[0]["copy"]))), "")
            result.append(dict(question=question["text"], before=original, after=final,
                               answer_quote=quote, detail="按该问句关联的已确认事实核对。",
                               source=matches[0]["source"], source_quote=matches[0]["copy"]))
    if data["focus_question"] and not any(comparable(data["focus_question"]) == comparable(item["question"]) for item in result):
        result.insert(0, dict(question=data["focus_question"], before="unknown", after="unknown", answer_quote="",
                              source="", source_quote="", detail="请核对原文或补充这条问句的产品事实。"))
    return result


WRITE_INSTRUCTION = (
    "Rewrite each supplied field so a shopper can get a direct, quotable answer to a specific purchase question. "
    "Use its supplied field direction and the purchase decision it serves. Use the focus question only where this field can answer it. "
    "If none is supplied, infer the question from the product, intended user, scenario, constraints and evidence in the field. "
    "Put the actual answer first. For prose, explain when it applies, what the buyer does, what happens, and the relevant exception or prerequisite. "
    "For example, describe first-time setup and later daily use separately where the evidence distinguishes them. "
    "A title identifies the product and one useful difference; Highlights adds useful material, use or fit; one independently readable bullet "
    "answers one main question; A+ and description explain the use sequence and substantiate comparisons only if supplied. "
    "Use natural search language and avoid generic adjectives or repeated keywords. Preserve the input language. "
    "Only basis_copy and confirmed_facts are claim sources. Before shows what might need correction. A buyer question is never proof. "
    "Preserve all product identities, every meaningful quantity, unit, negation, supported scope, and condition in the basis. "
    "Leave factual gaps for human confirmation. Do not turn 'app closed after setup' into 'offline' or 'no app required'. "
    "Keep each bullet on its own line and preserve the supplied count; use one line for title and Highlights. "
    "Return draft ID, copy and a concrete reason in Simplified Chinese. Omit a draft if the only changes would be cosmetic."
)
REVIEW_INSTRUCTION = (
    "Independently verify each proposed copy as a shopper and a product fact reviewer. Read basis_copy and confirmed_facts as the only claim sources. "
    "Use before only to assess how well the original answers the question. Check EVERY claim, model identity, quantity, unit, "
    "polarity, step, compatibility and exception, including the link between a condition and its feature. "
    "If any added claim lacks evidence, set grounded=false. If any intended meaning was dropped, broadened, softened or changed "
    "relative to basis_copy, set preserves_conditions=false. Distinguish closed app, first-time setup, saved inputs and true offline operation. "
    "A change is useful if it removes vague wording or makes a material buying answer more direct, precise or readable. "
    "Set improves_answer=false for paraphrasing that achieves none of these. "
    "Return one to five questions and their before and after statuses. For each supported or partial answer, provide an exact answer_quote "
    "from the proposal and an exact source_quote excerpt from basis_copy (source_id=null) or approved wording for that fact (source_id=fact ID). "
    "For an unanswerable focus question or missing condition explain what to verify in detail. Preserve user question wording when possible. "
    "Return a short, concrete explanation in Simplified Chinese for each answer."
)


def checked_answers(data: dict, copy: str, review: DraftReview) -> list[dict]:
    if not (review.grounded and review.preserves_conditions and review.improves_answer):
        return []
    facts = {fact["id"]: fact for fact in data["confirmed_facts"]}
    answers = []
    for answer in review.answers:
        fact = facts.get(answer.source_id)
        if answer.source_id and not fact:
            return []
        if answer.after in {"supported", "partial"}:
            source_copy = fact["copy"] if fact else data["basis_copy"]
            if (not answer.answer_quote or not answer.source_quote
                    or not any(answer.answer_quote in line for line in lines(copy))
                    or not any(answer.source_quote in line for line in lines(source_copy))):
                return []
        elif answer.answer_quote or answer.source_quote or not answer.detail:
            return []
        answers.append({
            **answer.model_dump(exclude={"source_id"}),
            "source": fact["source"] if fact else data["source"],
        })
    return answers if any(answer["after"] in {"supported", "partial"} for answer in answers) else []


def apply_confirmed_edits(before: str, field: str, anchor: Match | None, suggestions: list[dict]) -> tuple[str, str]:
    if not anchor or not anchor.complete:
        return before, ""
    original = source_lines(before, field)
    parts = original.copy()
    reasons = []
    for edit in suggestions:
        if edit["field"] != field or not edit["after"] or not edit["before"]:
            continue
        index = edit["line"] - anchor.line
        size = len(lines(edit["before"]))
        if index < 0 or index + size > len(original):
            continue
        if comparable("\n".join(parts[index:index + size])) != comparable(edit["before"]):
            continue
        if size == 1:
            parts[index] = edit["after"]
            reasons.append(edit["action"])
        elif index == 0 and size == len(original):
            return edit["after"], edit["action"]
    return ("\n".join(parts), " ".join(dict.fromkeys(reasons))) if reasons else (before, "")


def local_drafts(body, fields, product, context, rules, issue, suggestions) -> list[dict]:
    facts = context["facts"]
    if issue:
        field, before, after = issue["field"], issue["before"], issue["after"]
        anchors = [Match(field, issue["line"], len(lines(before)), True)]
        blocks = [(field, before, "diagnosis", anchors, [])]
    else:
        blocks = separate_aplus(detected_blocks(body.text, fields, product["brand"], body.field), fields)
    drafts = []
    for index, (field, before, method, anchors, possible) in enumerate(blocks):
        if field is None:
            drafts.append({"id": index, "field": None, "before": before, "after": "", "method": method,
                           "target": "待选字段", "reason": "原文同时出现在多个字段。" if possible else "这段文字缺少可唯一定位的字段线索。",
                           "choices": possible, "source": "", "line": None, "save_edit": None,
                           "warnings": [], "fact_labels": [], "status": "choose"})
            continue
        current = fields[field]
        anchor = anchors[0] if len(anchors) == 1 else None
        start = anchor.line if anchor else None
        count = anchor.count if anchor else 1
        if anchor and not anchor.complete and field in GUIDE:
            before = lines(current["text"])[anchor.line]
            anchor = Match(field, anchor.line, 1, True)
        in_scope = not anchor or not protected_section(current, start, count)
        if field not in GUIDE or current["state"] == "na" or not in_scope:
            draft, reason, status = "", "这一项可作为资料保留。可粘贴本商品要改写的正文。", "reference"
        else:
            if issue:
                draft, reason = issue["after"] or before, issue["action"]
            else:
                draft, reason = apply_confirmed_edits(before, field, anchor, suggestions)
            draft = clean_copy(draft, field, rules)
            reason = reason or GUIDE[field]
            # A verified identity can shorten an existing title without guessing a model name.
            if field == "title" and anchor and anchor.complete and copy_checks(field, draft, rules):
                identity = next((fact["copy"] for fact in facts if fact["state"] == "confirmed" and fact["role"] == "identity"
                                 and comparable(fact["copy"]) in comparable(before)
                                 and not copy_checks(field, fact["copy"], rules)), "")
                draft = identity or draft
            status = "draft" if draft and draft != before else "check"
        staged = None
        at_bullet_limit = field == "bullets" and not before and count == 0 and len(lines(current["text"])) >= rules["limits"].get("bullet_count", 5)
        if field in PUBLIC and status != "reference" and not at_bullet_limit:
            if (anchor and anchor.complete) or (not current["text"] and method in {"heading", "manual"}):
                originals = lines(current["text"])
                before_edit = "\n".join(originals[start:start + count]) if start is not None else ""
                staged = {"field": field, "line": start or 0, "before": before_edit}
        ids = supported_ids(draft, facts) if draft and anchor and anchor.complete else []
        warnings = copy_checks(field, draft, rules)
        if anchor and current["state"] == "pending" and status in {"draft", "check"}:
            warnings.append("原文来源待核实。请核对型号及商品信息后使用。")
        draft_record = {"id": index, "field": field, "before": before, "after": draft, "method": method,
                        "target": issue["target"] if issue else location(field, start, count),
                        "source": current["source"] if anchor else "", "line": start,
                        "reason": reason, "choices": [], "save_edit": staged, "status": status,
                        "warnings": warnings,
                        "fact_labels": [fact["label"] for fact in facts if fact["id"] in (ids or [])]}
        drafts.append(draft_record)
    return drafts


def semantic_revisions(rows, product, rules, client: LLM) -> str | None:
    by_id = {row["id"]: (row, data) for row, data in rows if row["after"]}
    if not by_id:
        return None
    payload = {"market": product["market"], "skill": SKILL["name"], "skill_version": SKILL["version"],
               "limits": rules["limits"], "fields": [data for _, data in by_id.values()]}
    result, model = client.complete(SkillResponse, WRITE_INSTRUCTION, payload)
    revisions = {}
    for revision in result.drafts:
        if revision.id not in by_id or revision.id in revisions:
            continue
        row, data = by_id[revision.id]
        field, copy = row["field"], revision.text
        if comparable(copy) in {comparable(row["before"]), comparable(row["after"])}:
            continue
        if field in {"title", "highlights"} and len(lines(copy)) != 1:
            continue
        if field == "bullets" and len(lines(copy)) != len(lines(row["after"])):
            continue
        if copy_checks(field, copy, rules) or not model_preserves(row["after"], copy, data["confirmed_facts"]):
            continue
        revisions[revision.id] = revision
    if not revisions:
        return None
    result, _ = client.complete(SkillReviewResponse, REVIEW_INSTRUCTION, {
        **payload, "fields": [{**by_id[rid][1], "proposal": revision.text} for rid, revision in revisions.items()],
    })
    used = False
    for checked in result.reviews:
        revision = revisions.pop(checked.id, None)
        if not revision:
            continue
        row, data = by_id[checked.id]
        answers = checked_answers(data, revision.text, checked)
        if not answers:
            continue
        row.update(
            after=revision.text, reason=revision.reason or row["reason"], status="draft", model_generated=True,
            warnings=[warning for warning in row["warnings"] if warning.startswith("原文来源")],
            fact_labels=[], answers=answers,
        )
        used = True
    return model if used else None


def preview_rewrite(body, fields, product, context, rules, *, issue=None, suggestions=(), client: LLM | None = None):
    drafts = local_drafts(body, fields, product, context, rules, issue, suggestions)
    targets = []
    for row in drafts:
        if row["field"] not in {*PUBLIC, "raw_text"} or row["status"] not in {"draft", "check"}:
            continue
        data = semantic_inputs(row, context, fields, body.focus_question, issue["question"] if issue else "")
        row["answers"] = local_answers(data, row)
        targets.append((row, data))
    model, notice = None, ""
    if client and any(row["after"] for row, _ in targets):
        try:
            model = semantic_revisions(targets[:20], product, rules, client)
            if not model:
                notice = "已显示本地改写。AI 没有提交通过语义复核的新文案。"
        except ModelFailed as failure:
            notice = f"{failure} 已显示本地改写。"
    return {"skill": SKILL, "drafts": drafts, "model": model, "notice": notice}


def generate_issue_edits(data: dict, fields: dict, context: dict, client: LLM | None) -> dict:
    """Generate the change previews while scoring, then keep them with the audit."""
    result, rules = data["result"], data["rules"]
    issues = [{**item, "edit": issue_edit(item, fields, context, result, rules)} for item in result["issues"]]
    revisions = []
    for index, item in enumerate(issues):
        edit = item["edit"]
        if (len(revisions) == 6 or edit["field"] not in {*PUBLIC, "raw_text"} or not edit["rewrite"]
                or not (edit["after"] or edit["before"])):
            continue
        row = {
            "id": index, "before": edit["before"], "after": edit["after"] or edit["before"],
            "field": edit["field"], "line": edit["line"], "save_edit": True,
            "source": fields[edit["field"]]["source"], "reason": edit["action"], "warnings": [],
        }
        revision = semantic_inputs(row, context, fields, "", edit["question"])
        revision["goal"] = item["message"] + " " + edit["action"]
        revisions.append((row, revision))
    if client and revisions:
        try:
            model = semantic_revisions(revisions, data["product"], rules, client)
        except ModelFailed:
            model = None
    else:
        model = None
    for row, _ in revisions:
        if row.get("model_generated"):
            edit = issues[row["id"]]["edit"]
            edit.update(after=row["after"], action=row["reason"], answers=row["answers"], model_generated=True,
                        model=model, fact_labels=[], fact_ids=[])
    return {**data, "result": {**result, "issues": issues}}


def staged_review(fields: dict, edits: list[dict]) -> list[dict]:
    updated = dict(fields)
    for field in PUBLIC:
        original = lines(fields[field]["text"])
        field_edits = sorted((item for item in edits if item["field"] == field), key=lambda item: item["line"])
        if not field_edits:
            continue
        if len({item["line"] for item in field_edits}) < len(field_edits):
            raise ValueError("每个原文位置须对应一份改写稿。")
        cursor, result = 0, []
        for item in field_edits:
            start, before = item["line"], lines(item["before"])
            end = start + len(before)
            if (start < cursor or start > len(original) or original[start:end] != before
                    or fields[field]["state"] == "na" or protected_section(fields[field], start, len(before))
                    or not before and start != len(original)):
                raise ValueError("原文位置不匹配。请按当前 Listing 重新生成改写。")
            replacement = lines(item["text"])
            if field in {"title", "highlights"} and len(replacement) != 1:
                raise ValueError(f"{FIELDS[field]}请保留一行。")
            result.extend([*original[cursor:start], *replacement])
            cursor = end
        text = "\n".join([*result, *original[cursor:]])
        if len(text) > 12_000 or field == "bullets" and len(lines(text)) > 100:
            raise ValueError(f"{FIELDS[field]}超出工作台的录入长度。")
        if field in {"title", "highlights"} and len(lines(text)) > 1:
            raise ValueError(f"{FIELDS[field]}请保留一行。")
        updated[field] = {**fields[field], "text": text}
    before, after = initial_values(fields), initial_values(updated)
    keys = dict.fromkeys([*before, *after])
    if all(before.get(key, "") == after.get(key, "") for key in keys):
        raise ValueError("改写稿与原文一致。")
    return [{"key": key, "text": after.get(key, ""), "decision": "pending" if after.get(key, "") != before.get(key, "") else "kept"}
            for key in keys]
