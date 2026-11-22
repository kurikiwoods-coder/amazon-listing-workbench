from __future__ import annotations

import re
from difflib import SequenceMatcher
from itertools import groupby

from .audit import fact_matches
from .models import FIELDS, PUBLIC, utcnow
from .rules import lines, profile_complete, rule_findings

STRATEGIES = {
    "A": {"label": "保守修改", "detail": "修正事实、超长和明确的信息缺口"},
    "B": {"label": "用户问题导向", "detail": "按买家决定前最想确认的问题组织"},
    "C": {"label": "差异化导向", "detail": "优先使用已有证据的产品差异"},
}


def owner_field(key: str) -> str:
    return key.split(".", 1)[0]


def supported_ids(text: str, facts: list[dict]) -> list[str] | None:
    """Only accept complete approved clauses and the separators emitted by the editor."""
    if not text:
        return []
    known = sorted([(fact["copy"], fact["id"]) for fact in facts if fact["state"] == "confirmed"],
                   key=lambda pair: len(pair[0]), reverse=True)
    pattern = re.compile(r"^(?:\n+| · | — |\. )")
    remaining, used = text, []
    while remaining:
        match = next((
            (phrase, fid) for phrase, fid in known if remaining.startswith(phrase)
            and (remaining == phrase or pattern.match(remaining[len(phrase):]))
        ), None)
        if not match:
            return None
        phrase, fid = match
        used.append(fid)
        remaining = remaining[len(phrase):]
        if remaining:
            separator = pattern.match(remaining)
            remaining = remaining[separator.end():]
            if not remaining:
                return None
    return list(dict.fromkeys(used))


def initial_values(fields: dict) -> dict[str, str]:
    return {
        "title": fields["title"]["text"],
        "highlights": fields["highlights"]["text"],
        **{f"bullets.{i}": line for i, line in enumerate(lines(fields["bullets"]["text"]))},
        "aplus": fields["aplus"]["text"],
    }


def compact_copy(choices: list[dict], limit: int | None, joiner: str = " · ") -> str:
    parts = []
    for fact in choices:
        candidate = joiner.join([*parts, fact["copy"]])
        if limit is None or len(candidate) <= limit:
            parts.append(fact["copy"])
    return joiner.join(parts)


def proposed_lines(strategy: str, fields: dict, facts: list[dict], result: dict, rules: dict,
                   choices: dict | None = None) -> list[dict]:
    decisions = initial_values(fields)
    after = dict(decisions)
    confirmed = {fact["id"]: fact for fact in facts if fact["state"] == "confirmed"}
    if not confirmed:
        raise ValueError("请先在产品事实库确认至少一条英文事实，再从新评分生成候选稿。")
    issue_facts = [
        fid for issue in result["issues"] for fid in issue["fact_ids"] if fid in confirmed
    ]
    favored = list(dict.fromkeys(issue_facts))
    roles = {
        "A": ["identity", "boundary", "feature", "spec", "use", "proof", "difference"],
        "B": ["feature", "boundary", "use", "spec", "proof", "difference", "identity"],
        "C": ["difference", "feature", "boundary", "proof", "use", "spec", "identity"],
    }[strategy]
    ordered = sorted(confirmed.values(), key=lambda f: (roles.index(f["role"]),
                                                        favored.index(f["id"]) if f["id"] in favored else len(favored)))
    identity = [fact for fact in ordered if fact["role"] == "identity"]
    helpful = [fact for fact in ordered if fact["role"] != "identity"]
    limits = rules["limits"]
    affected = {issue["field"] for issue in result["issues"]}
    critical = {issue["field"] for issue in result["issues"] if issue["priority"] == "P0"}

    def selection(field: str, default: list[dict]) -> list[dict]:
        chosen = [confirmed[fid] for fid in dict.fromkeys(choices[field])] if choices else []
        return chosen or default

    if fields["title"]["state"] != "na" and identity and (strategy != "A" or "title" in affected or not after["title"]):
        title_picks = selection("title", identity)
        identity_pick = next((fact for fact in title_picks if fact["role"] == "identity"), identity[0])
        title_facts = [identity_pick]
        if strategy == "C":
            difference = next((fact for fact in [*title_picks, *helpful] if fact["role"] == "difference"
                               and len(fact["copy"]) <= 32), None)
            if difference:
                title_facts.append(difference)
        copy = compact_copy(title_facts, limits.get("title"))
        if copy:
            after["title"] = copy
    if fields["highlights"]["state"] != "na" and (strategy != "A" or "highlights" in affected or not after["highlights"]):
        copy = compact_copy([fact for fact in selection("highlights", helpful)
                             if fact["copy"] != after["title"]][:3], limits.get("highlights"))
        if copy:
            after["highlights"] = copy

    bullet_issues = [issue for issue in result["issues"] if issue["field"] == "bullets"]
    if fields["bullets"]["state"] != "na" and helpful:
        original = lines(fields["bullets"]["text"])
        limit = limits.get("bullet_count", 5)
        if strategy != "A":
            planned = [fact for fact in selection("bullets", helpful) if fact["role"] != "identity"]
            # Distinct lines carry distinct claims, even if separate sources repeated a fact.
            selected = list(dict.fromkeys(fact["copy"] for fact in [*planned, *helpful]))[:limit]
            for index in range(max(len(original), len(selected))):
                after[f"bullets.{index}"] = selected[index] if index < len(selected) else ""
        else:
            used = set(original)
            by_index = {ix: list(group) for ix, group in groupby(
                sorted(bullet_issues, key=lambda issue: issue["line"]), key=lambda issue: issue["line"]
            )}
            for index, old in enumerate(original):
                if index >= limit and "bullets" in critical:
                    after[f"bullets.{index}"] = ""
                elif index in by_index:
                    ids = [fid for issue in by_index[index] for fid in issue["fact_ids"] if fid in confirmed]
                    plan = [*(confirmed[fid] for fid in ids), *selection("bullets", helpful)]
                    replacement = next((fact["copy"] for fact in plan if fact["copy"] not in used), "")
                    if replacement:
                        after[f"bullets.{index}"] = replacement
                        used.add(replacement)
            if not original:
                for index, fact in enumerate(selection("bullets", helpful)[:limit]):
                    after[f"bullets.{index}"] = fact["copy"]

    if fields["aplus"]["state"] != "na" and helpful and (strategy != "A" or "aplus" in critical or not after["aplus"]):
        some = selection("aplus", helpful)[:6 if strategy == "B" else 4]
        after["aplus"] = "\n".join(dict.fromkeys(fact["copy"] for fact in some))

    return [
        {"key": key, "text": text, "decision": "pending" if text != decisions.get(key, "") else "kept"}
        for key, text in after.items()
    ]


def inspect_candidate(review: list[dict], original_fields: dict, facts: list[dict], issues: list[dict],
                      rules: dict) -> dict:
    originals = initial_values(original_fields)
    provided = {row["key"]: row for row in review}
    if len(provided) != len(review) or any(key not in provided for key in originals):
        raise ValueError("审核列表需包含每一条原文，且字段不能重复。")
    keys = sorted(provided, key=lambda key: (PUBLIC.index(owner_field(key)),
                                             int(key.split(".")[-1]) if key.startswith("bullets.") else 0))
    fields = {key: dict(value) for key, value in original_fields.items()}
    rows, bullets, blockers = [], [], []
    for key in keys:
        field = owner_field(key)
        original = originals.get(key, "")
        value, decision = provided[key]["text"], provided[key]["decision"]
        if field in {"title", "highlights", "bullets"} and len(lines(value)) > 1:
            raise ValueError(f"{FIELDS[field]}改写区每一项只能填写一行。")
        if decision == "kept" and value != original:
            raise ValueError("有改动的文案需要接受、拒绝或标记待审核。")
        if fields[field]["state"] == "na" and value:
            raise ValueError("当前类目不适用的字段无法加入候选稿。")
        effective = original if decision == "rejected" else value
        ids = supported_ids(effective, facts)
        if effective:
            if ids is None:
                blockers.append(f"{FIELDS[field]}{(' · ' + str(int(key.split('.')[1])+1)) if field == 'bullets' else ''}含有未核实措辞。")
            if field == "bullets":
                bullets.append(effective)
        if decision == "pending":
            blockers.append("还有变更未完成审核。")
        linked = [issue["key"] for issue in issues if issue["field"] == field
                  and (field != "bullets" or issue["line"] == int(key.split(".")[1]))]
        fields_to_check = [fact for fact in facts if fact["id"] in (ids or [])]
        reason = "保留原文。"
        if effective != original and fields_to_check:
            reason = "改用已核实的「" + "、".join(fact["label"] for fact in fields_to_check) + "」。"
        elif effective != original and not effective:
            reason = "移除本段文字。"
        elif effective != original:
            reason = "人工修改，需核实措辞。"
        rows.append({
            **provided[key], "before": original, "effective": effective, "fact_ids": ids or [], "reason": reason,
            "verified": ids is not None, "issue_ids": linked, "count": len(effective),
            "fact_labels": [fact["label"] for fact in fields_to_check],
            "limit": rules["limits"].get(field) if field in {"title", "highlights"} else None,
        })

    for field in PUBLIC:
        prior = original_fields[field]
        next_text = "\n".join(bullets) if field == "bullets" else next(row["effective"] for row in rows if row["key"] == field)
        if next_text != prior["text"]:
            updated = {"state": "confirmed" if next_text else "absent", "text": next_text,
                       "source": "候选稿 / 人工审核", "collected_at": utcnow()}
            if prior.get("page_lines"):
                original, revised = lines(prior["text"]), lines(next_text)
                kept = {a + offset: b + offset for a, b, size in SequenceMatcher(a=original, b=revised, autojunk=False).get_matching_blocks()
                        for offset in range(size)}
                updated["page_lines"] = [{**item, "line": kept[item["line"]]} for item in prior["page_lines"] if item["line"] in kept]
            fields[field] = updated
    rule_issues = rule_findings(fields, rules, for_publication=True)
    if not fields["title"]["text"]:
        blockers.append("缺少商品标题。")
    if any(fields[key]["state"] == "pending" for key in PUBLIC):
        blockers.append("仍有沿用的公开字段待核实。")
    if fields["raw_text"]["text"] and not original_fields["title"]["text"] and not original_fields["bullets"]["text"]:
        blockers.append("请先把未分段原文归入标题或五点，再建立发布用基准评分。")
    if not profile_complete(rules):
        blockers.append("当前类目的标题和 Highlights 规则尚未录入完整。")
    blockers.extend(issue["message"] for issue in rule_issues)
    blockers.extend(issue["message"] for issue in fact_matches(fields, facts)[1] if issue["field"] in PUBLIC)
    return {"rows": rows, "fields": fields, "blockers": list(dict.fromkeys(blockers)),
            "rule_issues": rule_issues, "ready": not blockers,
            "copy": "\n\n".join(f"{FIELDS[key]}\n{fields[key]['text']}" for key in PUBLIC if fields[key]["text"])}
