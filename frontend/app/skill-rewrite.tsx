"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Copy, ExternalLink, LoaderCircle, Save, Sparkles } from "lucide-react";
import {
  answerStatus, api, charCount, copyText, fields, message, pathFor,
  type Candidate, type Detail, type FieldKey, type Marketplace, type RewriteEntry, type SkillPreview, type Strategy,
} from "./model";
import { Badge, Button, FormField, MarketplaceSelect, Notice } from "./ui";

const editable: FieldKey[] = ["title", "highlights", "bullets", "aplus", "raw_text", "public_specs", "attributes", "search_terms", "keywords"];
const origin = {
  diagnosis: "来自诊断意见", snapshot: "匹配原文", heading: "识别到字段标签", format: "按文字格式识别", manual: "手动指定", unclear: "待选字段",
};

export function SkillRewrite({ detail, marketplaces, defaultMarket, entry, model, strategy = "B", onSaved }: {
  detail?: Detail; marketplaces: Marketplace[]; defaultMarket: string; entry?: RewriteEntry;
  model?: string | null; strategy?: Strategy; onSaved?: (candidate: Candidate) => Promise<void>;
}) {
  const [text, setText] = useState(entry?.edit.before ?? "");
  const [focusQuestion, setFocusQuestion] = useState(entry?.edit.question ?? "");
  const [manual, setManual] = useState<FieldKey | "">("");
  const [market, setMarket] = useState(detail?.product.market ?? defaultMarket);
  const [fromIssue, setFromIssue] = useState(entry);
  const [useAI, setUseAI] = useState(!!model);
  const [preview, setPreview] = useState<SkillPreview | null>(null);
  const [busy, setBusy] = useState<"rewrite" | "save" | "">("");
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const [finalText, setFinalText] = useState<Record<number, string>>({});
  const route = detail ? `${pathFor(detail.product.id)}/field-rewrite` : "/quick-field-rewrite";

  async function run(input: string, field: FieldKey | "", issue: RewriteEntry | undefined, focus = focusQuestion) {
    setBusy("rewrite");
    setError("");
    setStatus("");
    setPreview(null);
    setFinalText({});
    try {
      const next = await api<SkillPreview>(route, "POST", {
        text: input, focus_question: focus, field: field || undefined, market, mode: useAI ? "auto" : "rules",
        snapshot_id: detail?.snapshot.id, context_id: detail?.context.id,
        audit_id: issue?.auditId, issue_key: issue?.issueKey,
      });
      setPreview(next);
      setFinalText(Object.fromEntries(next.drafts.map(item => [item.id, item.after])));
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy("");
    }
  }

  useEffect(() => {
    if (entry) {
      setText(entry.edit.before);
      setFromIssue(entry);
      setFocusQuestion(entry.edit.question);
      setManual("");
      void run(entry.edit.before, "", entry, entry.edit.question);
    }
  }, [entry]);

  const changed = (preview?.drafts ?? []).filter(item => finalText[item.id]?.trim() && finalText[item.id].trim() !== item.before);
  const anchored = changed.filter(item => item.save_edit);
  const canSave = !!detail && !!preview?.snapshot_id && !!preview?.context_id && anchored.length > 0;

  async function copy(value: string) {
    setStatus(await copyText(value) ? "文案已复制。" : "浏览器无法访问剪贴板，请从改写后文本框复制。");
  }

  async function save() {
    if (!canSave || !preview || !onSaved) return;
    setBusy("save");
    setError("");
    try {
      const next = await api<Candidate>(`${route}/candidate`, "POST", {
        snapshot_id: preview.snapshot_id, context_id: preview.context_id, strategy,
        edits: anchored.map(item => ({ ...item.save_edit, text: finalText[item.id] })),
      });
      setStatus(`已保存候选 V${next.version}，可在下方审核。`);
      await onSaved(next);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy("");
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!busy && (text.trim() || fromIssue)) void run(text, manual, fromIssue);
  }

  function choose(field: FieldKey) {
    setManual(field);
    setFromIssue(undefined);
    void run(text, field, undefined);
  }

  const resolved = preview?.drafts.filter(item => item.field) ?? [];
  return <section className="panel skill-rewrite" id="field-rewrite">
    <header className="panel-head">
      <div className="row wrap"><Sparkles size={19} /><h2>粘贴并改写</h2>
        {detail && <Badge>{detail.product.asin || detail.product.name} · 原文 V{detail.snapshot.version}</Badge>}</div>
      <a className="text-button" target="_blank" rel="noreferrer" href="https://github.com/liangdabiao/amazon-listing-alexa-optimizer">
        Alexa Listing Skill <ExternalLink size={14} />
      </a>
    </header>
    <form className="skill-input" onSubmit={submit}>
      {fromIssue && <div className="skill-entry"><strong>{fromIssue.edit.target}</strong><span>{fromIssue.edit.action}</span></div>}
      <FormField name="粘贴 Listing 原文" hint="可粘贴单条要点，或整段带 Title、About this item、A+ 等标签的文案。">
        <textarea rows={5} maxLength={32000} value={text} disabled={!!busy}
          placeholder="例如 Title: DemoWorks Food Scale | Premium quality"
          onChange={event => { setText(event.target.value); setFromIssue(undefined); setPreview(null); setStatus(""); }} />
      </FormField>
      <FormField name="优先回答的买家问题（选填）" hint="可粘贴真实的 Alexa 问句或写下买家最想确认的使用条件。">
        <input maxLength={600} value={focusQuestion} disabled={!!busy}
          placeholder="例如 首次设置后，称重时关掉 App 还能看到热量和蛋白质吗？"
          onChange={event => { setFocusQuestion(event.target.value); setPreview(null); setStatus(""); }} />
      </FormField>
      <div className="skill-input-tools">
        {!detail && <MarketplaceSelect value={market} marketplaces={marketplaces} disabled={!!busy} onChange={value => {
          setMarket(value); setPreview(null); setStatus("");
        }} />}
        <FormField name="字段识别">
          <select value={manual} disabled={!!busy} onChange={event => {
            setManual(event.target.value as FieldKey | ""); setFromIssue(undefined); setStatus(""); setPreview(null);
          }}>
            <option value="">自动识别</option>
            {editable.map(field => <option key={field} value={field}>{fields[field]}</option>)}
          </select>
        </FormField>
        {model && <label className="checkbox-label skill-toggle"><input type="checkbox" checked={useAI} disabled={!!busy} onChange={event => {
          setUseAI(event.target.checked);
        }} />AI 改写与语义复核</label>}
        <Button type="submit" kind="primary" disabled={!!busy || (!text.trim() && !fromIssue)}>
          {busy === "rewrite" ? <LoaderCircle size={16} className="spinner" /> : <Sparkles size={16} />}
          {busy === "rewrite" ? (useAI ? "正在改写并复核…" : "正在识别并改写…") : "识别并改写"}
        </Button>
      </div>
    </form>
    {error && <div className="skill-notice"><Notice warn>{error}</Notice></div>}
    {preview && <div className="skill-results" aria-live="polite">
      {preview.notice && <Notice warn>{preview.notice}</Notice>}
      {preview.model && <p className="muted">本次使用模型 · {preview.model}</p>}
      {preview.drafts.map(item => <article className="skill-result" key={item.id}>
        <header className="row wrap">
          <h3>{item.target}</h3>
          <Badge tone={item.status === "choose" ? "amber" : item.method === "snapshot" ? "teal" : "neutral"}>{origin[item.method]}</Badge>
          {item.model_generated && <Badge tone="amber">AI 已复核 · 待人工确认</Badge>}
          {item.source && <small className="muted">{item.source}</small>}
        </header>
        <div className="skill-comparison">
          <div><strong>原文</strong><p lang="en">{item.before || "此处为补充内容"}</p></div>
          {item.status === "draft" || item.status === "check" ? <div>
            <label className="form-field"><strong>改写后</strong>
              <textarea rows={Math.max(3, Math.min(8, item.after.split("\n").length + 1))} value={finalText[item.id] ?? ""}
                lang="en" maxLength={12000} disabled={!!busy}
                onChange={event => setFinalText(previous => ({ ...previous, [item.id]: event.target.value }))} />
            </label>
            <div className="row spread wrap"><small className="muted">{charCount(finalText[item.id] ?? "")} 字符</small>
              <Button kind="quiet" disabled={!finalText[item.id]?.trim()} onClick={() => void copy(finalText[item.id])}><Copy size={14} />复制本段</Button></div>
          </div> : <div className="skill-awaiting">
            <p>{item.reason}</p>
            {!!item.choices.length && <div className="row wrap">{item.choices.filter(field => editable.includes(field)).map(field =>
              <Button key={field} kind="quiet" disabled={!!busy} onClick={() => choose(field)}>按{fields[field]}改写</Button>)}</div>}
            {item.status === "choose" && <small>在上方选择字段后再运行。</small>}
          </div>}
        </div>
        {item.field && item.status !== "reference" && <div className="skill-explanation">
          <p><strong>{item.status === "check" ? "改写建议" : "为什么这样改"}</strong> · {item.reason}</p>
          {!!item.fact_labels.length && <small>事实依据 · {item.fact_labels.join("、")}</small>}
          {item.warnings.map(warning => <p className="skill-warning" key={warning}>{warning}</p>)}
        </div>}
        {!!item.answers?.length && (finalText[item.id] !== item.after
          ? <p className="skill-warning">文案已编辑。可保存候选版本后复评。</p>
          : <div className="skill-answers">{item.answers.map((answer, index) => <article key={index}>
            <div className="row spread wrap"><strong>{answer.question}</strong><Badge tone={answer.after === "supported" ? "teal" : "neutral"}>
              {answerStatus[answer.before]} → {answerStatus[answer.after]}</Badge></div>
            {answer.answer_quote && <blockquote>{answer.answer_quote}</blockquote>}
            <p>{answer.detail}</p>
            {answer.source_quote && <details><summary>查看依据</summary><p>{answer.source}</p><q>{answer.source_quote}</q></details>}
          </article>)}</div>)}
      </article>)}
      <div className="row spread wrap skill-actions">
        <small className="muted" role="status">{status || (canSave ? `${anchored.length} 段可保存到当前项目` : "")}</small>
        <div className="row wrap">
          {resolved.length > 1 && <Button disabled={!changed.length} onClick={() => void copy(changed.map(item =>
            `${fields[item.field!]}\n${finalText[item.id]}`).join("\n\n"))}><Copy size={16} />复制改写稿</Button>}
          {canSave && <Button kind="primary" disabled={!!busy} onClick={() => void save()}><Save size={16} />
            {busy === "save" ? "正在保存…" : "保存为候选版本"}</Button>}
        </div>
      </div>
    </div>}
  </section>;
}
