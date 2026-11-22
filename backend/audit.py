from __future__ import annotations

import re
from collections import Counter

from .guidance import review_guidance
from .models import CONTENT, FIELDS, PUBLIC, PUBLISHED_COPY
from .rules import lines, rule_findings

ENGINE_VERSION = "audit-v3"
LEGACY_ENGINE = "audit-v2"
SCORE_MODULES = ("title", "highlights", "bullets", "aplus", "raw_text", "public_specs",
                 "attributes", "search_terms", "keywords")
VISIBLE_STATES = {"confirmed", "absent"}
SCREEN_AXES = {
    "clarity": "表达清晰度", "specificity": "信息具体度",
    "usage": "用途和场景", "repetition": "重复与空泛表述",
}
COPY_TERMS = {"the", "with", "and", "from", "this", "that", "your", "you", "use", "for", "its"}
FILLER = re.compile(r"\b(?:premium quality|perfect gift|best on the market|ultimate choice|easy to use|top quality)\b", re.I)


def words(text: str) -> list[str]:
    return [word for word in re.findall(r"[^\W_]+", text.casefold()) if len(word) > 2 and word not in COPY_TERMS]


def anchor_match(text: str, phrase: str) -> bool:
    text, term = text.casefold(), phrase.casefold()
    if term.isascii() and term[:1].isalnum() and term[-1:].isalnum():
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text))
    return term in text


def snippet(text: str, phrase: str) -> str:
    index = text.casefold().find(phrase.casefold())
    return text if index < 0 else text[max(0, index-45):index+len(phrase)+45]


def evidence(fields: dict, keys=CONTENT, *, states=("confirmed",), include_brand_and_comparison=False) -> list[dict]:
    found = []
    for key in keys:
        row = fields.get(key, {})
        if row.get("state") not in states:
            continue
        unrelated = {item["line"] for item in row.get("page_lines", []) if item["scope"] != "product"}
        for index, text in enumerate(lines(row["text"])):
            if include_brand_and_comparison or index not in unrelated:
                found.append({"field": key, "line": index, "quote": text, "source": row["source"]})
    return found


def locate_quote(records: list[dict], field: str, quote: str) -> dict | None:
    return next(({**item, "quote": quote} for item in records
                 if item["field"] == field and quote and quote in item["quote"]), None)


def make_issue(key: str, field: str, line: int, quote: str, message: str,
               recommendation: str, priority: str = "P1", kind: str = "quality",
               source: str = "", question_ids: list[str] | None = None,
               fact_ids: list[str] | None = None, needs_review: bool = False) -> dict:
    return dict(key=key, field=field, line=line, quote=quote, message=message,
                recommendation=recommendation, priority=priority, kind=kind, source=source,
                question_ids=question_ids or [], fact_ids=fact_ids or [], needs_review=needs_review)


def fact_matches(fields: dict, facts: list[dict]) -> tuple[dict, list[dict]]:
    matches = {}
    issues = []
    records = evidence(fields)
    for fact in facts:
        if fact["state"] != "confirmed":
            continue
        phrase = fact["copy"].strip().rstrip(".")
        hits = [{**item, "quote": snippet(item["quote"], phrase)}
                for item in records if phrase.casefold() in item["quote"].casefold()]
        if hits:
            matches[fact["id"]] = hits
        for item in records:
            conflict = next((term for term in fact["conflicts"] if term.casefold() in item["quote"].casefold()), None)
            if conflict:
                issues.append(make_issue(
                    f"conflict-{fact['id']}-{item['field']}-{item['line']}", item["field"], item["line"],
                    snippet(item["quote"], conflict), f"这里的说法与已确认的「{fact['label']}」冲突。",
                    "核对来源，并改用产品事实库中的准确说法。", "P0", "factual",
                    fact["source"], fact_ids=[fact["id"]],
                ))
    return matches, issues


def analyze_questions(fields: dict, questions: list[dict], matches: dict, issues: list[dict],
                      semantic: dict | None = None) -> tuple[list[dict], list[dict]]:
    results = []
    gaps = []
    for question in questions:
        qid = question["id"]
        scope = question["fields"]
        available = evidence(fields, scope)
        all_known = all(fields[key]["state"] in VISIBLE_STATES | {"na"}
                        for key in scope if key != "raw_text" or len(scope) == 1)
        conflicts = [item for item in issues if item["kind"] == "factual" and item["priority"] == "P0"
                     and item["field"] in scope and set(item["fact_ids"]).intersection(question["fact_ids"])]
        linked = [position for fid in question["fact_ids"]
                  if (position := next((item for item in matches.get(fid, []) if item["field"] in scope), None))]
        anchor_hits = {}
        for anchor in question["anchors"]:
            result = next((item for item in available if anchor_match(item["quote"], anchor)), None)
            if result:
                anchor_hits[anchor] = {**result, "quote": snippet(result["quote"], anchor)}
        decision = semantic.get(qid) if semantic else None
        citations = []
        if conflicts:
            status, detail = "contradicted", "原文与关联的已确认事实冲突。"
            citations = [{k: conflicts[0][k] for k in ("field", "line", "quote", "source")}]
        elif decision:
            status, detail = decision["status"], decision["detail"]
            citations = decision["evidence"]
            if status == "not_found" and not all_known:
                status, detail = "unknown", "部分相关字段尚未核实，无法判断 Listing 是否回答。"
        elif linked and len(linked) == len(question["fact_ids"]):
            status, detail, citations = "supported", "原文含有与关联事实一致的表达。", linked
        elif question["anchors"] and len(anchor_hits) == len(question["anchors"]):
            status, detail, citations = "supported", "核对词组全部在已确认的原文中出现。", list(anchor_hits.values())
        elif linked or anchor_hits:
            status, detail = "partial", "只找到部分已核实表达或核对词组。"
            citations = linked + list(anchor_hits.values())
        elif all_known and (question["anchors"] or question["fact_ids"]):
            status, detail = "not_found", "已确认的相关字段中未找到可核对的答案。"
        else:
            status, detail = "unknown", "请核实相关字段，添加核对词组，或使用 AI 语义检查。"
        missing = [anchor for anchor in question["anchors"] if anchor not in anchor_hits] if status != "supported" else []
        preferred = ("title", "bullets", "highlights", "aplus") if question["text"].startswith("What is this product") else (
            "bullets", "highlights", "aplus", "title")
        suggested = next((key for key in preferred if key in scope and fields[key]["state"] != "na"), scope[0])
        target = citations[0]["field"] if citations else suggested
        results.append({
            "id": qid, "text": question["text"], "intent": question["intent"], "priority": question["priority"],
            "source": question["source"], "status": status, "detail": detail, "evidence": citations[:4],
            "missing": missing, "suggested_fields": [] if status == "supported" else [suggested],
        })
        if status in {"partial", "not_found"}:
            rec = "补齐已核实的操作步骤、适用范围或参数，并确认文案回答了这条问句。"
            if missing:
                rec = f"核对这些信息再补入文案：{'、'.join(missing)}。"
            location = citations[0] if citations else {"line": 0, "quote": "", "source": fields[target]["source"]}
            gaps.append(make_issue(f"question-{qid}", target, location["line"], location["quote"],
                                   f"买家问题{'只回答了一部分' if status == 'partial' else '缺少明确答案'}。",
                                   rec, "P1" if question["priority"] == "high" else "P2", "question",
                                   location["source"], [qid], question["fact_ids"], True))
    return results, gaps


def local_dimensions(product: dict, fields: dict, facts: list[dict], question_results: list[dict],
                     fact_hits: dict, issues: list[dict], rules: dict) -> tuple[dict, list[dict]]:
    scores = {}
    extra = []

    def field_score(key: str, score: float, reason: str) -> None:
        row = fields[key]
        state = row["state"]
        if state in {"na", "missing", "pending"}:
            scores[key] = (None, state, {"na": "此类目不适用。", "missing": "字段未提供。",
                                         "pending": "字段已录入，来源或完整性待核实。"}[state], [])
            return
        if state == "absent":
            scores[key] = (0, "assessed", "已核实该字段没有内容。", [])
            extra.append(make_issue(f"absent-{key}", key, 0, "", f"{FIELDS[key]}已确认没有内容。",
                                    "结合类目和真实产品资料评估该字段应补充的内容。",
                                    "P1" if key == "title" else "P2", source=row["source"]))
            return
        policy_problem = any(item["kind"] == "policy" and item["field"] == key for item in issues)
        if policy_problem:
            score = min(score, 4)
            reason += " 已发现适用的发布规则问题。"
        refs = evidence(fields, [key], include_brand_and_comparison=True)
        scores[key] = (max(0, score), "assessed", reason, refs[:2])
        if score <= 7 and not policy_problem:
            extra.append(make_issue(f"quality-{key}", key, 0, refs[0]["quote"], reason,
                                    "优先给出与产品事实和买家问题直接对应的信息。", "P2",
                                    source=row["source"]))

    judged = [q for q in question_results if q["status"] != "unknown"]
    if judged:
        covered = sum((2 if q["priority"] == "high" else 1) * {"supported": 1, "partial": .5}.get(q["status"], 0)
                      for q in judged)
        total = sum(2 if q["priority"] == "high" else 1 for q in judged)
        scores["questions"] = (10 * covered / total, "assessed",
                               f"已判断 {len(judged)}/{len(question_results)} 条。高优先级问句按双倍权重计。",
                               [item for q in judged for item in q["evidence"]][:3])
    else:
        scores["questions"] = (None, "missing", "暂无可判断的买家问句。", [])

    verified = [fact for fact in facts if fact["state"] == "confirmed"]
    observed_facts = [item for item in issues if item["kind"] == "factual"]
    if verified and evidence(fields):
        critical = sum(item["priority"] == "P0" for item in observed_facts)
        boundaries = [f for f in verified if f["role"] == "boundary"]
        public_confirmed = all(fields[key]["state"] in VISIBLE_STATES | {"na"} for key in PUBLIC)
        absent_limits = [fact for fact in boundaries if fact["id"] not in fact_hits] if public_confirmed else []
        for fact in absent_limits:
            existing = any(fact["id"] in item["fact_ids"] for item in observed_facts)
            if existing:
                continue
            item = next((row for row in evidence(fields, ["bullets", "highlights", "aplus", "title"])), None)
            if item:
                extra.append(make_issue(f"boundary-{fact['id']}", item["field"], item["line"], item["quote"],
                                        f"暂未定位到已确认的限制「{fact['label']}」。",
                                        "明确说明使用条件，并放在对应功能附近。",
                                        "P1", "boundary", fact["source"], fact_ids=[fact["id"]], needs_review=True))
        review_risks = min(2, sum(item["priority"] == "P1" for item in observed_facts))
        if fact_hits or observed_facts or public_confirmed:
            evidence_ratio = len(fact_hits) / len(verified) if public_confirmed else 1
            value = 5 + 5 * evidence_ratio - min(4, len(absent_limits)) - critical * 3 - review_risks
            reason = f"在已确认的原文中找到 {len(fact_hits)}/{len(verified)} 条已核实事实，发现 {critical} 处明确冲突。"
            if not public_confirmed:
                reason += " 部分公开字段待补；本维度依据可定位的事实证据。"
            scores["accuracy"] = (max(0, value), "assessed", reason,
                                  [*observed_facts, *(positions[0] for positions in fact_hits.values())][:3])
        else:
            scores["accuracy"] = (None, "missing", "公开字段待补，当前原文暂无可定位的事实依据。", [])
    else:
        scores["accuracy"] = (None, "missing", "需要已核实的产品事实和已确认的 Listing 原文。", [])

    title = fields["title"]["text"]
    title_tokens = words(title)
    repeats = sum(count - 1 for count in Counter(title_tokens).values())
    missing_brand = bool(product["brand"] and product["brand"].casefold() not in title.casefold())
    title_strength = 10 - 2 * missing_brand - min(4, repeats) - 2 * bool(FILLER.search(title)) - (2 if len(title_tokens) < 2 else 0)
    title_reason = "核对品牌、产品识别度、重复词与字符上限。"
    if repeats or missing_brand:
        title_reason = "标题存在词语重复，或缺少已提供的品牌名称。"
    field_score("title", title_strength, title_reason)

    highlight = fields["highlights"]["text"]
    new_words = set(words(highlight)) - set(title_tokens)
    field_score("highlights", 10 - (4 if len(new_words) < 3 else 0) - 2 * bool(FILLER.search(highlight)),
                "检查 Highlights 是否补充标题以外的真实属性和使用场景。")

    bullets = lines(fields["bullets"]["text"])
    duplicate = len(bullets) - len({line.casefold() for line in bullets})
    vague = sum(bool(FILLER.search(line)) for line in bullets)
    field_score("bullets", 10 - min(4, 2 * duplicate) - min(4, 2 * vague) - (2 if len(bullets) < 3 else 0),
                f"已确认 {len(bullets)} 条，发现 {duplicate} 条重复、{vague} 条泛化措辞。")

    aplus = lines(fields["aplus"]["text"])
    aplus_records = evidence(fields, ["aplus", "raw_text"])
    actionable = bool(re.search(r"\b(?:when|step|how|before|after|while|for)\b", " ".join(aplus), re.I))
    field_score("aplus", 10 - (3 if len(aplus) < 2 else 0) - (3 if not actionable else 0),
                "检查 A+ 是否陈述操作、使用条件和典型场景。")

    attributes = lines(fields["attributes"]["text"])
    parsed = [re.split("[:：]", row, maxsplit=1) for row in attributes]
    seen = {}
    conflicts = 0
    for index, (row, pair) in enumerate(zip(attributes, parsed)):
        if len(pair) == 2 and pair[0].strip() and pair[1].strip():
            name, value = (term.strip().casefold() for term in pair)
            if name in seen and seen[name] != value and fields["attributes"]["state"] == "confirmed":
                conflicts += 1
                extra.append(make_issue(f"attr-conflict-{name}", "attributes", index, row,
                                        f"后台属性「{pair[0].strip()}」出现不同的值。",
                                        "对照产品事实与后台字段，保留正确值。", "P0", "factual",
                                        fields["attributes"]["source"]))
            seen[name] = value
    field_score("attributes", min(10, 4 + 2 * len(seen)) - 3 * conflicts,
                f"后台有 {len(seen)} 个可识别的属性和 {conflicts} 处同名字段冲突。")

    if fields["keywords"]["state"] == "na" and fields["search_terms"]["state"] == "na":
        scores["keywords"] = (None, "na", "关键词与 Search Terms 在此项目不适用。", [])
    else:
        keys = []
        for key in ("keywords", "search_terms"):
            if fields[key]["state"] == "confirmed":
                keys.extend(re.split(r"[,，;；\n]+", fields[key]["text"].casefold()))
        terms = list(dict.fromkeys(term.strip() for term in keys if term.strip()))
        visible = " ".join(item["quote"] for item in evidence(fields, PUBLISHED_COPY)).casefold()
        if terms and visible:
            signals = sum(term in visible for term in terms)
            repeated_terms = max(0, len([term for term in keys if term.strip()]) - len(terms))
            value = max(0, 4 + 6 * signals / len(terms) - min(3, repeated_terms))
            scores["keywords"] = (value, "assessed", f"{signals}/{len(terms)} 个目标词组出现在已确认的公开文案中。",
                                  [*evidence(fields, ["keywords", "search_terms"]), *aplus_records][:3])
        elif fields["keywords"]["state"] in VISIBLE_STATES and fields["search_terms"]["state"] == "absent" and visible:
            scores["keywords"] = (0, "assessed", "已确认无可用关键词或 Search Terms。", [])
        else:
            scores["keywords"] = (None, "missing", "需要目标关键词或 Search Terms，以及公开文案。", [])
    return scores, extra


def score_listing(product: dict, fields: dict, context: dict, rules: dict,
                  semantic: dict | None = None, *, engine_version: str = ENGINE_VERSION) -> dict:
    facts, questions = context["facts"], context["questions"]
    fact_hits, conflicts = fact_matches(fields, facts)
    issues = [{"question_ids": [], "fact_ids": [], "needs_review": False, **entry}
              for entry in rule_findings(fields, rules)]
    issues.extend(conflicts)
    if semantic:
        issues.extend(semantic["issues"])
    question_results, new_gaps = analyze_questions(fields, questions, fact_hits, issues,
                                                    semantic["answers"] if semantic else None)
    scores, findings = local_dimensions(product, fields, facts, question_results, fact_hits, issues, rules)
    issues.extend([*new_gaps, *findings])
    unresolved = [key for key in PUBLIC if fields[key]["state"] in {"missing", "pending"}]
    visible_copy = evidence(fields, PUBLISHED_COPY)
    screen = screen_copy(visible_copy, semantic.get("screen") if semantic else None)
    for item in issues:
        if not item["question_ids"] and item["fact_ids"]:
            item["question_ids"] = [q["id"] for q in questions if set(q["fact_ids"]).intersection(item["fact_ids"])]
    dimensions = []
    assessed_weight = applicable_weight = weighted_points = 0
    for spec in rules["rubric"]:
        key, weight = spec["key"], spec["weight"]
        score, status, reason, refs = scores[key]
        quality = semantic["quality"].get(key) if semantic else None
        if quality and score is not None and fields[key]["state"] == "confirmed":
            limit_failed = any(issue["kind"] == "policy" and issue["field"] == key for issue in issues)
            score = (score + quality["score"]) / 2
            if limit_failed:
                score = min(score, 4)
            reason += " " + quality["detail"]
            refs.extend(quality["evidence"])
        if status != "na":
            applicable_weight += weight
        if score is not None:
            assessed_weight += weight
            weighted_points += weight * score
        dimensions.append({**spec, "score": None if score is None else round(score, 1), "status": status,
                           "reason": reason, "evidence": refs[:4]})
    coverage = round(100 * assessed_weight / applicable_weight, 1) if applicable_weight else 0
    provisional = applicable_weight == 0 or 100 * assessed_weight < rules["coverage_threshold"] * applicable_weight
    question_states = Counter(q["status"] for q in question_results)
    judged = len(question_results) - question_states["unknown"]
    confidence = "低" if provisional else "中"
    if semantic and coverage >= 90 and not unresolved and question_results and judged / len(question_results) >= .8:
        confidence = "高"
    issues = sorted({entry["key"]: entry for entry in issues}.values(),
                    key=lambda entry: (entry["priority"], entry["field"] not in PUBLIC,
                                       list(FIELDS).index(entry["field"]), entry["line"]))
    observed = evidence(fields, PUBLISHED_COPY, states=("confirmed", "pending"))
    legacy = engine_version == LEGACY_ENGINE
    guidance = review_guidance(dimensions, question_results, issues, observed, (semantic or {}).get("scenarios", {}),
                               (semantic or {}).get("verification", []), include_scenarios=legacy)
    result = {
        "score": round(10 * weighted_points / assessed_weight, 1) if assessed_weight else None,
        "coverage": coverage, "assessed_weight": assessed_weight, "applicable_weight": applicable_weight,
        "provisional": provisional, "confidence": confidence, "dimensions": dimensions, "issues": issues,
        "questions": question_results, "question_states": dict(question_states),
        "question_coverage": round(100 * question_states["supported"] / judged, 1) if judged else None,
        "assessed_questions": judged, "total_questions": len(question_results),
        "field_counts": dict(Counter(row["state"] for row in fields.values())),
        "screen": screen, "guidance": guidance,
    }
    if not legacy:
        modules, issues = assess_modules(product, fields, issues, semantic or {})
        assessed = [item["score"] for item in modules if item["score"] is not None]
        available = len(assessed)
        applicable = sum(item["status"] != "na" for item in modules)
        result.update(
            module_scores=modules,
            score=round(sum(assessed) / available, 1) if assessed else None,
            coverage=round(100 * available / applicable, 1) if applicable else 0,
            assessed_weight=available, applicable_weight=applicable, provisional=available < applicable,
            issues=issues,
        )
    return result


def assess_modules(product, fields, findings, semantic):
    modules = []
    findings = [issue for issue in findings if fields[issue["field"]]["state"] == "confirmed"
                and fields[issue["field"]]["text"] and issue["kind"] != "quality"]
    text_checks = {
        "highlights": ("specificity", "repetition"),
        "bullets": tuple(SCREEN_AXES),
        "aplus": tuple(SCREEN_AXES),
        "raw_text": tuple(SCREEN_AXES),
    }
    inactive = {
        "missing": "未取得资料，可手动补录。",
        "pending": "已有资料待核实。",
        "absent": "已确认无内容。",
        "na": "该字段不适用。",
        "reference": "当前只取得品牌故事或多商品对比内容。",
    }
    for key in SCORE_MODULES:
        label = FIELDS[key]
        row = fields[key]
        records = evidence(fields, [key], states=("confirmed", "pending"))
        if not records:
            state = row["state"] if row["state"] != "confirmed" else "reference"
            modules.append(dict(key=key, label=label, score=None, status=state, source_state=row["state"],
                                reason=inactive[state], evidence=[], checks=[]))
            continue
        original = "\n".join(item["quote"] for item in records)
        checks = []
        value, reason = 10, ""
        if key == "title":
            repeats = sum(count - 1 for count in Counter(words(original)).values())
            brand_missing = product["brand"] and not anchor_match(original, product["brand"])
            short = len(words(original)) < 2 and original.isascii()
            value -= min(4, repeats) + 2 * bool(brand_missing) + 2 * bool(short) + 2 * bool(FILLER.search(original))
            reason = f"{len(original)} 字符"
            reason += f"，有 {repeats} 处词语重复" if repeats else "，未发现明显重复词"
            if brand_missing:
                reason += "，缺少已录入的品牌名"
            if short:
                reason += "，商品类型可写得更清楚"
            reason += "。"
        elif key in text_checks:
            screen = screen_copy(records)
            checks = [item for item in screen["checks"] if item["key"] in text_checks[key]]
            value = sum(item["score"] for item in checks) / len(checks)
            reason = min(checks, key=lambda item: item["score"])["reason"]
            if key == "bullets":
                reason = f"按现有 {len(records)} 条要点评估。" + reason
            if key == "highlights" and (title := evidence(fields, ["title"])):
                overlaps = set(words(original))
                if overlaps and overlaps <= set(words(title[0]["quote"])):
                    value -= 2
                    reason = "当前用词都出现在标题中，可补充已经核实的购买信息。"
        elif key in {"attributes", "public_specs"}:
            pairs = [re.split("[:：]", item["quote"], maxsplit=1) for item in records]
            parsed = [(pair[0].strip().casefold(), pair[1].strip().casefold()) for pair in pairs
                      if len(pair) == 2 and all(part.strip() for part in pair)]
            duplicates = len(parsed) - len(set(name for name, _ in parsed))
            value -= 4 * (len(records) - len(parsed)) / len(records) + min(4, 2 * duplicates)
            reason = f"有 {len(parsed)}/{len(records)} 行能明确读出属性和值，{duplicates} 行重复记录同一属性。"
        else:
            terms = [term.strip().casefold() for term in re.split(r"[,，;；\n]+", original) if term.strip()]
            duplicated = len(terms) - len(set(terms))
            value -= min(5, 2 * duplicated)
            reason = f"在提供的文本中找到 {len(terms)} 组用词，{duplicated} 组重复。"

        refs = records[:1]
        if quality := semantic.get("quality", {}).get(key):
            value = (value + quality["score"]) / 2
            reason += " " + quality["detail"]
            refs = [*quality["evidence"], *refs]
            ref = quality["evidence"][0]
            if quality["score"] < 8 and not any(item["field"] == key and item["line"] == ref["line"] for item in findings):
                findings.append(make_issue(
                    f"semantic-copy-{key}-{ref['line']}", key, ref["line"], ref["quote"],
                    quality["detail"], "按买家问题重写这句话，保留已知的场景和适用条件。", "P2",
                    source=ref["source"], needs_review=True,
                ))
        severe = next((item for item in findings if item["field"] == key and item["priority"] == "P0"
                       and item["kind"] in {"factual", "policy"}), None)
        if severe:
            value = min(value, 4)
            reason = severe["message"] + " " + reason
            refs.insert(0, severe)
        if key in {"title", *text_checks}:
            seen = set()
            for ref in records:
                quote = ref["quote"]
                repeated = quote.casefold() in seen
                seen.add(quote.casefold())
                if repeated:
                    message = "这段原文与前面的内容重复。"
                    action = "合并重复的段落，保留已经提供的参数、用法和限制。"
                elif FILLER.search(quote):
                    message = "这句用了笼统的宣传语，买家看不出具体功能。"
                    action = "删去空泛措辞，保留该段已有的用途或操作信息。"
                else:
                    continue
                if not any(item["field"] == key and item["line"] == ref["line"] for item in findings):
                    findings.append(make_issue(f"copy-{key}-{ref['line']}", key, ref["line"], quote, message,
                                               action, "P2", source=ref["source"], needs_review=row["state"] == "pending"))
        modules.append(dict(key=key, label=label, score=round(10 * max(0, value), 1), status="assessed",
                            source_state=row["state"], reason=reason, evidence=refs[:3], checks=checks))
    return modules, sorted(findings, key=lambda item: (item["priority"], list(FIELDS).index(item["field"]), item["line"]))


def screen_copy(records: list[dict], semantic: dict | None = None) -> dict | None:
    if not records:
        return None
    text = "\n".join(item["quote"] for item in records)
    tokens = words(text)
    repeated = len(records) - len({item["quote"].casefold() for item in records})

    def examples(pattern, limit=2):
        return [item for item in records if pattern.search(item["quote"])][:limit]

    concrete = examples(re.compile(
        r"\b\d+(?:[.,]\d+)?\s*(?:mm|cm|ft|inches?|oz|lbs?|kg|ml|liters?|watts?|w|volts?|hz|"
        r"hours?|minutes?|mah|gb|tb|ports?|modes?|g|l)\b|\d+(?:[.,]\d+)?\s*(?:%|°|"
        r"毫米|厘米|英寸|千克|克|升|小时|分钟)"
        r"|\b(?:made (?:of|from)|includes|holds|compatible with|supports)\b|由.+制成|适配|支持|包含", re.I))
    scenarios = examples(re.compile(r"\b(?:when|while|before|after|during|designed for|ideal for)\b|适用于|使用时|之后|之前", re.I))
    claims = examples(re.compile(FILLER.pattern + "|顶级品质|送礼佳品|完美选择|极致体验", re.I))
    term_run = max(Counter(tokens).values(), default=0)
    duplication_penalty = min(5, 2 * repeated + max(0, term_run - max(3, len(tokens) // 9)))
    sentence_sizes = [len(chunk) for row in records for chunk in re.split(r"[.!?。！？]+", row["quote"]) if chunk.strip()]
    length_penalty = min(5, 2 * sum(length > 210 for length in sentence_sizes))
    short_copy = len(text) < 25
    line_counts = Counter(item["quote"].casefold() for item in records)
    duplicated = [item for item in records if line_counts[item["quote"].casefold()] > 1][:2]
    clarity_issue = next((item for item in records if any(len(part) > 210 for part in re.split(
        r"[.!?。！？]+", item["quote"]))), None)
    checks = [
        {"key": "clarity", "label": SCREEN_AXES["clarity"], "score": max(0, 10 - length_penalty - (2 if short_copy else 0)),
         "reason": "，".join(text for match, text in [
             (length_penalty, "有偏长的句子，可按功能或场景拆开"),
             (short_copy, "文字较短，可补充一句说明商品用途"),
         ] if match) + "。" if length_penalty or short_copy else "句子长度适中。",
         "evidence": [clarity_issue] if clarity_issue else records[:1]},
        {"key": "specificity", "label": SCREEN_AXES["specificity"], "score": 5 + min(5, 3 * len(concrete)),
         "reason": "找到具体参数、材料、随附物品或兼容性表述。" if concrete
         else "可增加有来源的参数、材料、包装内容或兼容性说明。", "evidence": concrete},
        {"key": "usage", "label": SCREEN_AXES["usage"], "score": 5 + min(5, 3 * len(scenarios)),
         "reason": "原文提供了具体的使用条件或场景。" if scenarios
         else "可补充适用场景，以及买家需要完成的操作。", "evidence": scenarios},
        {"key": "repetition", "label": SCREEN_AXES["repetition"], "score": max(0, 10 - duplication_penalty - 2 * len(claims)),
         "reason": "有重复内容或通用宣传语，可换成具体产品说明。" if repeated or claims or duplication_penalty
         else "未发现明显的重复段落或泛化宣传语。", "evidence": claims or duplicated},
    ]
    for check in checks:
        if judged := (semantic or {}).get(check["key"]):
            check["score"] = round((check["score"] + judged["score"]) / 2, 1)
            check["reason"] = judged["detail"]
            check["evidence"] = judged["evidence"]
    return {"score": round(sum(item["score"] for item in checks) * 2.5, 1),
            "checks": checks, "mode": "llm" if semantic else "rules"}


def compare(before: dict, after: dict) -> dict:
    before_issues = {comparison_key(issue): issue for issue in before["issues"]}
    after_issues = {comparison_key(issue): issue for issue in after["issues"]}
    before_rows = before.get("module_scores", before["dimensions"])
    after_rows = after.get("module_scores", after["dimensions"])
    assessed_axes = {row["key"] for row in after_rows if row["status"] == "assessed"}
    after_questions = {row["id"]: row["status"] for row in after["questions"]}

    def can_recheck(issue: dict) -> bool:
        if issue["kind"] == "question":
            return all(after_questions.get(qid) in {"supported", "partial", "not_found", "contradicted"}
                       for qid in issue["question_ids"])
        return (issue["field"] if "module_scores" in after else issue_axis(issue)) in assessed_axes

    def delta(left: float | None, right: float | None) -> float | None:
        return None if left is None or right is None else round(right - left, 1)
    previous_scores = {item["key"]: item["score"] for item in before_rows}
    same_basis = {item["key"] for item in before_rows if item["status"] == "assessed"} == assessed_axes
    return {
        "score_delta": delta(before["score"], after["score"]) if same_basis else None,
        "same_basis": same_basis, "before": before["score"], "after": after["score"],
        "coverage_before": before["coverage"], "coverage_after": after["coverage"],
        "questions_before": before["question_coverage"], "questions_after": after["question_coverage"],
        "by_module": "module_scores" in after,
        "dimensions": [{"label": item["label"], "key": item["key"], "before": previous_scores.get(item["key"]),
                        "after": item["score"], "delta": delta(previous_scores.get(item["key"]), item["score"])}
                       for item in after_rows if "module_scores" not in after or item["score"] is not None
                       or previous_scores.get(item["key"]) is not None],
        "resolved": [issue for key, issue in before_issues.items() if key not in after_issues
                     and can_recheck(issue)],
        "unverified": [issue for key, issue in before_issues.items() if key not in after_issues
                       and not can_recheck(issue)],
        "remaining": [issue for key, issue in after_issues.items() if key in before_issues],
        "new": [issue for key, issue in after_issues.items() if key not in before_issues],
    }


def comparison_key(issue: dict) -> tuple:
    if issue["kind"] == "factual" and issue["fact_ids"]:
        return (issue["kind"], issue["field"], *sorted(issue["fact_ids"]))
    return (issue["key"],)


def issue_axis(issue: dict) -> str:
    if issue["kind"] == "question":
        return "questions"
    if issue["kind"] in {"factual", "boundary"} and issue["field"] != "attributes":
        return "accuracy"
    return issue["field"]
