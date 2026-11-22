"use client";

import { useState } from "react";
import { Check, Download, RotateCcw, Sparkles, X } from "lucide-react";
import {
  api, fields, message, pathFor, sourceStatus, type DiagnosisDetail, type DraftDecision, type DraftGroup,
  type Evidence, type FieldDraft, type FieldSuggestions,
} from "./model";
import { Badge, Button, Notice } from "./ui";
import { saveText } from "./public-copy";

const sections: { key: DraftGroup; label: string; hint: string; empty: string }[] = [
  { key: "search_terms", label: "Search Terms 候选", hint: "买家可能输入的同义词和补充表达。",
    empty: "尚无可追溯的补充词。可补充使用场景或上传卖家后台导出。" },
  { key: "keywords", label: "目标关键词候选", hint: "可用于研究商品、功能和购买场景的词组。",
    empty: "可补充标题或五点，再结合自己的关键词表核对。" },
  { key: "attributes", label: "商品属性候选", hint: "前台提到的属性和值，可与类目模板核对。",
    empty: "前台尚无可可靠映射的属性值。可补充公开规格表或说明书。" },
];

export function FieldSuggestionPanel({ detail, temporary, aiEnabled, onLocate }: {
  detail: DiagnosisDetail; temporary: boolean; aiEnabled: boolean; onLocate: (value: Evidence) => void;
}) {
  const [data, setData] = useState(detail.field_suggestions);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [more, setMore] = useState<Partial<Record<DraftGroup, boolean>>>({});
  const base = `${pathFor(detail.product.id)}/snapshots/${detail.snapshot.id}/field-suggestions`;
  const kept = data.items.filter(item => item.decision === "shortlisted");
  const hasEvidence = ["title", "bullets", "highlights", "aplus", "raw_text", "public_specs"].some(
    key => detail.snapshot.fields[key as keyof typeof fields]?.text);
  async function expand() {
    setBusy("model"); setError("");
    try {
      const next = temporary ? await api<FieldSuggestions>("/quick-field-suggestions", "POST", {
        fields: detail.snapshot.fields, market: detail.product.market, brand: detail.product.brand,
      }) : await api<FieldSuggestions>(base, "POST");
      if (temporary) {
        const previous = new Map(data.items.map(item => [item.id, item.decision]));
        next.items = next.items.map(item => ({ ...item, decision: previous.get(item.id) ?? item.decision }));
      }
      setData(next);
    } catch (err) { setError(message(err)); } finally { setBusy(""); }
  }
  async function review(item: FieldDraft, decision: DraftDecision) {
    setBusy(item.id); setError("");
    try {
      if (!temporary) await api(`${base}/${item.id}?decision=${decision}`, "PUT");
      setData(current => ({ ...current, items: current.items.map(row => row.id === item.id ? { ...row, decision } : row) }));
    } catch (err) { setError(message(err)); } finally { setBusy(""); }
  }
  function download() {
    const content = [`Amazon ${detail.product.market} · ${detail.product.asin || detail.product.name} · 字段备选清单`,
      "以下为根据前台资料推演的运营建议，真实后台值需由卖家资料核实。", ...kept.map(item =>
        [`${fields[item.group]} · ${item.name ? item.name + " = " : ""}${item.value}`,
         `${item.origin === "llm" ? "AI 建议" : "本地建议"} · ${item.reason}`,
         `依据 · ${fields[item.evidence.field]} 第 ${item.evidence.line + 1} 段 · ${item.evidence.quote}`,
         item.evidence.source].join("\n"))];
    saveText(content.join("\n\n"), `${detail.product.market}-${detail.product.asin || "listing"}-drafts.txt`);
  }
  return <section className="panel field-suggestion-panel" id="field-suggestions">
    <header className="panel-head"><div className="row wrap"><Sparkles size={19} /><h2>后台字段建议</h2>
      <Badge tone="amber">前台推演 · 待核实</Badge></div><div className="row wrap">
      {kept.length > 0 && <Button kind="quiet" onClick={download}><Download size={15} />导出已留用（{kept.length}）</Button>}
      {aiEnabled && !data.model && <Button disabled={!!busy || !hasEvidence || !data.groups.length} onClick={() => void expand()}>
        <Sparkles size={15} />{busy === "model" ? "正在核对 AI 引用…" : "用 AI 扩展建议"}</Button>}
      {data.model && <Badge tone="blue">{data.model}</Badge>}</div></header>
    <div className="draft-info">点击引文可回到对应原文。留用的建议单独保存，评分使用已核实的字段。{temporary && "快速评分中的选择仅在本页保留。"}
      {!aiEnabled && !data.model && " 当前使用本地提取，可在 .env 配置模型以扩展同义词和场景词。"}</div>
    {error && <Notice warn>{error}</Notice>}
    <div className="field-draft-columns">{sections.map(section => {
      const items = data.items.filter(item => item.group === section.key);
      const original = detail.snapshot.fields[section.key];
      const enabled = data.groups.includes(section.key);
      return <article className="draft-column" id={`draft-${section.key}`} key={section.key} tabIndex={-1}>
        <h3>{section.label}</h3><p>{section.hint}</p>
        {!enabled ? <p>原字段状态为「{sourceStatus[original.state]}」。可在原始 Listing 查看。</p> :
          !items.length ? <p>{section.empty}</p> : <>
            {(more[section.key] ? items : items.slice(0, 2)).map(item => <div className="field-draft" key={item.id}>
              <div className="row wrap"><strong>{item.name && `${item.name} · `}{item.value}</strong>
                <small>{item.origin === "llm" ? "AI 建议" : "本地建议"}</small></div>
              <p>{item.reason}</p>
              <button type="button" className="evidence-button" onClick={() => onLocate(item.evidence)}>
                <span>{fields[item.evidence.field]} · 第 {item.evidence.line + 1} 段</span><q>{item.evidence.quote}</q></button>
              {item.decision === "pending" ? <div className="draft-actions">
                <Button kind="quiet" disabled={!!busy} onClick={() => void review(item, "shortlisted")}><Check size={14} />留作备选</Button>
                <Button kind="quiet" disabled={!!busy} onClick={() => void review(item, "dismissed")}><X size={14} />忽略</Button></div> :
                <div className="draft-actions"><Badge tone={item.decision === "shortlisted" ? "green" : "neutral"}>
                  {item.decision === "shortlisted" ? "已留作备选" : "已忽略"}</Badge>
                <Button kind="quiet" disabled={!!busy} onClick={() => void review(item, "pending")}><RotateCcw size={13} />撤销</Button></div>}
            </div>)}
            {items.length > 2 && <Button kind="quiet" onClick={() => setMore(current => ({ ...current, [section.key]: !current[section.key] }))}>
              {more[section.key] ? "收起" : `查看其余 ${items.length - 2} 条`}</Button>}
          </>}
      </article>;
    })}</div>
  </section>;
}
