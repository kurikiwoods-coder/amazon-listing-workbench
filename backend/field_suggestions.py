"""Produce reviewable keyword and attribute drafts from public, line-addressable evidence."""

import hashlib
import re
from itertools import islice
from typing import Annotated, Literal

from pydantic import Field

from .llm import LLM, ModelFailed
from .models import Model, PUBLISHED_COPY
from .rules import lines

VERSION = "public-field-drafts-v1"
GROUPS = ("search_terms", "keywords", "attributes")
ELIGIBLE = {"missing", "absent"}
TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
BOUNDARY = re.compile(r"\b(?:no|not|never|except|only|excluding|incompatible|without|requires?|required|must|after|before|up to)\b|非対応|不支持|仅限|sans\b|ohne\b", re.I)
CLAIMS = re.compile(r"\b(?:best|guaranteed|free shipping|discount|cure|fda|certified|waterproof|bluetooth|wireless|bpa[- ]free|medical grade|offline|app[- ]free|without (?:an? |the )?app|no app|no setup)\b", re.I)
STOP = set("a an the and or with for of on by from to at in is are as your you use it its this each features includes has up designed can easily "
           "save saved select press subtract choose set during view see place meet enjoy provides allows ideal perfect lets "
           "le la les des du pour et avec de der die das mit für und zum ein eine en con para los las y di da per un una e".split())
PAIR = re.compile(r"^([^:\n：]{2,55})[:：]\s*(.{1,150})$")
MATERIAL = re.compile(r"\b(?:stainless steel|alumin[iu]+m|silicone|cast iron|bamboo|borosilicate glass|cotton|linen|leather|nylon|polyester)\b", re.I)
QUANTITY = r"\d+(?:[.,]\d+)?(?:\s*(?:–|-|to)\s*\d+(?:[.,]\d+)?)?\s*(?:kg|g|lb|oz|ml|l|liters?|litres?|quarts?|qt|gb|tb)\b"
CAPACITY = re.compile(rf"\bcapacity\s*(?:of\s+|[:：]\s*|is\s+)?(?P<after>{QUANTITY})|(?P<before>{QUANTITY})\s+capacity\b", re.I)
# Use explicit lexical equivalents locally. Other categories and languages can use the configured model.
EQUIVALENTS = (
    ("meal prep", "meal preparation"), ("food scale", "kitchen weighing scale"), ("kitchen scale", "weighing scale"),
    ("laptop", "notebook computer"), ("foldable", "folding"), ("folds flat", "foldable"), ("riser", "stand"),
    ("sofa", "couch"), ("couch", "sofa"), ("sneakers", "trainers"), ("backpack", "rucksack"),
    ("coffee maker", "coffee brewer"), ("coffee grinder", "coffee mill"), ("air fryer", "air frying appliance"),
    ("humidifier", "air humidifying device"), ("smartwatch", "smart watch"), ("earbuds", "earphones"),
    ("television", "tv"), ("flashlight", "torch"),
    ("cookbook", "recipe book"), ("skillet", "frying pan"),
    ("klappbar", "faltbar"), ("faltbar", "klappbar"), ("pliable", "repliable"), ("plegable", "abatible"),
    ("折りたたみ", "折畳み"), ("折叠", "可折叠"), ("便携", "便携式"),
)


class ProposedField(Model):
    group: Literal["search_terms", "keywords", "attributes"]
    name: Annotated[str, Field(max_length=65)] = ""
    value: Annotated[str, Field(min_length=1, max_length=150)]
    field: Literal["title", "highlights", "bullets", "aplus", "raw_text", "public_specs"]
    line: int = Field(ge=0, le=32_000)
    quote: Annotated[str, Field(min_length=2, max_length=420)]
    reason: Annotated[str, Field(min_length=1, max_length=220)]


class ProposedFields(Model):
    candidates: list[ProposedField] = Field(max_length=24)


def tokens(value):
    return TOKEN.findall(value.casefold())


def contains(text, phrase):
    if re.search(r"[\u3040-\u30ff\u3400-\u9fff]", phrase):
        return phrase.casefold() in text.casefold()
    return bool(re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text, flags=re.I))


def public_records(fields):
    result = []
    for field in PUBLISHED_COPY:
        row = fields.get(field, {})
        if row.get("state") not in {"confirmed", "pending"}:
            continue
        meta = {m["line"]: m for m in row.get("page_lines", [])}
        kept = 0
        for index, text in enumerate(lines(row["text"])):
            origin = meta.get(index, {})
            if origin.get("scope", "product") != "product":
                continue
            if origin.get("kind") == "ocr" and row["state"] != "confirmed":
                continue
            # Keep comparable bounded passages in each field, even on long A+ pages.
            result.append({"field": field, "line": index, "quote": text[:420], "source": row.get("source", "")})
            kept += 1
            if kept == 18:
                break
    return result


def extract_phrases(text, brand):
    if brand:
        text = re.sub(r"(?<!\w)" + re.escape(brand) + r"(?!\w)", ",", text, flags=re.I)
    for chunk in re.split(r"[,;:|•()\[\]，。、：；/!?！？\n]| [–—-] ", text):
        group = []
        for word in [*TOKEN.findall(chunk), ""]:
            if word and word.casefold() not in STOP and not word.isdecimal():
                group.append(word)
                continue
            if group:
                # Very long clauses are poor keyword phrases; prefer shorter explicit wording.
                if len(group) <= 5:
                    phrase = " ".join(group)
                    if 3 <= len(phrase) <= 64 and not re.fullmatch(r"[A-Z0-9]{10}", phrase):
                        yield phrase
                group = []


def eligible(fields):
    return [key for key in GROUPS if fields.get(key, {}).get("state", "missing") in ELIGIBLE]


def make_item(group, value, ref, reason, origin, name=""):
    key = "|".join((group, " ".join(tokens(name)), " ".join(tokens(value))))
    return {"id": hashlib.sha256(key.encode()).hexdigest()[:24], "group": group, "name": name, "value": value,
            "reason": reason, "origin": origin, "evidence": ref, "decision": "pending"}


def local_items(fields, product, records):
    result = []
    allowed = eligible(fields)
    brand = product.get("brand", "")
    title_tokens = set(tokens(fields.get("title", {}).get("text", "")))
    brand_tokens = set(tokens(brand))

    def add(group, value, ref, reason, name=""):
        if group not in allowed:
            return
        if group != "attributes" and (set(tokens(value)) & brand_tokens or CLAIMS.search(value)):
            return
        if group == "keywords" and len(tokens(value)) == 1 and any(
                item["group"] == group and contains(item["value"], value) for item in result):
            return
        candidate = make_item(group, value, ref, reason, "rules", name)
        if sum(item["group"] == group for item in result) < 6 and all(item["id"] != candidate["id"] for item in result):
            result.append(candidate)

    for r in records:
        text = r["quote"]
        pair = PAIR.fullmatch(text)
        if pair and (r["field"] == "public_specs" or re.fullmatch(
                r"material|capacity|dimensions|weight|compatibility|color|colour|size|容量|尺寸|材质|重量|颜色|素材", pair[1], re.I)):
            add("attributes", pair[2], r, "按原文的属性名和值整理，请核对所选变体和类目字段。", pair[1])
    for r in records:
        text = r["quote"]
        if BOUNDARY.search(text):
            continue
        if r["field"] != "public_specs":
            if material := MATERIAL.search(text):
                add("attributes", material[0], r, "文案提及这种材料，请核对它属于整件商品还是某个部件。", "Material")
            if capacity := CAPACITY.search(text):
                add("attributes", capacity["after"] or capacity["before"], r, "文案明确写出容量和单位，请核对对应部件及变体。", "Capacity")
            if r["field"] in {"title", "highlights", "bullets", "raw_text"}:
                for phrase in islice(extract_phrases(text, brand), 2):
                    add("keywords", phrase, r, "前台原文中出现的商品、特征或场景用语，可用作研究起点。")
        for original, synonym in EQUIVALENTS:
            if contains(text, original) and not set(tokens(synonym)) <= title_tokens:
                add("search_terms", synonym, r, "根据原文联想到的同义搜索表达，请核对当地买家的用词。")
    if not any(item["group"] == "search_terms" for item in result):
        for r in records:
            if r["field"] not in {"bullets", "highlights", "aplus", "raw_text"} or BOUNDARY.search(r["quote"]):
                continue
            for phrase in islice(extract_phrases(r["quote"], brand), 2):
                if not set(tokens(phrase)) <= title_tokens:
                    add("search_terms", phrase.lower(), r, "标题以外出现的前台用语，请核对它是否值得占用后台词位。")
    return result


def model_items(fields, product, records, client: LLM):
    allowed = eligible(fields)
    if not records or not allowed:
        return [], None
    excerpts = []
    for field in PUBLISHED_COPY:
        budget = 1500
        for row in (r for r in records if r["field"] == field):
            if budget < 40:
                break
            quote = row["quote"][:min(300, budget)]
            excerpts.append({**row, "quote": quote})
            budget -= len(quote) + 35
    result, model = client.complete(ProposedFields,
        "Suggest research drafts ONLY for the requested missing groups. Use the language of the quoted product listing and "
        "market for keyword phrases and attribute values. Explain in Chinese. search_terms: natural synonyms a shopper "
        "might type, avoiding brand names, ASINs, already-covered title phrases and unsupported functionality. keywords: "
        "specific product identity, use or buyer-intent phrases, no metrics or ranks. attributes: descriptive attribute name "
        "and an exact value substring from the same source line, including units, negations, conditions and scope. "
        "Return just JSON. Every candidate must cite an exact continuous source quote and its field and zero-based line. "
        "Infer no private seller values. Never add features or remove a boundary. Omit ungrounded candidates.",
        {"market": product["market"], "language_context": "Use source language", "groups": allowed,
         "public_passages": [{k: row[k] for k in ("field", "line", "quote")} for row in excerpts]})
    by_location = {(r["field"], r["line"]): r for r in excerpts}
    items = []
    title_tokens = set(tokens(fields.get("title", {}).get("text", "")))
    for item in result.candidates:
        ref = by_location.get((item.field, item.line))
        if item.group not in allowed or not ref or not item.quote.strip() or item.quote not in ref["quote"]:
            continue
        value, name = item.value, item.name if item.group == "attributes" else ""
        if not tokens(value) or any(c in name + value for c in "\r\n<>") or re.search(r"https?://|\bB0[A-Z0-9]{8}\b", value, re.I):
            continue
        if item.group == "attributes":
            if not name or value not in item.quote:
                continue
            pair = PAIR.fullmatch(ref["quote"])
            if pair and ref["field"] == "public_specs" and (
                    tokens(pair[1]) != tokens(name) or value != pair[2].strip()):
                continue
            numbers = re.findall(r"\d+(?:[.,]\d+)?", value)
            if numbers and value.replace(".", "").replace(",", "").isdecimal() and ref["field"] != "public_specs":
                continue
        elif (len(tokens(value)) > 8 or set(tokens(value)) & set(tokens(product.get("brand", "")))
              or (item.group == "search_terms" and set(tokens(value)) <= title_tokens)):
            continue
        if any(term not in item.quote for term in re.findall(r"\d+(?:[.,]\d+)?", value)):
            continue
        if any(not contains(item.quote, claim) for claim in CLAIMS.findall(value)):
            continue
        if (boundary := BOUNDARY.search(ref["quote"])) and not contains(value, boundary[0]):
            continue
        items.append(make_item(item.group, value, {**ref, "quote": item.quote}, item.reason, "llm", name))
    if result.candidates and not items:
        raise ModelFailed("AI 给出的建议无法逐条对应当前前台原文。本地候选仍可查看。")
    return items, model


def suggested_fields(fields, product, *, saved=None, decisions=None):
    items = local_items(fields, product, public_records(fields))
    proposals = []
    for item in [*(saved or {}).get("items", []), *items]:
        if not any(other["id"] == item["id"] for other in proposals):
            proposals.append({**item, "decision": (decisions or {}).get(item["id"], "pending")})
    return {"version": VERSION, "model": (saved or {}).get("model"), "groups": eligible(fields), "items": proposals}
