"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { diffWordsWithSpace } from "diff";
import { AlertTriangle, Check, GitCompareArrows, Copy, FileCheck2, FlaskConical, Plus, Save, ShieldCheck, Sparkles, X } from "lucide-react";
import {
  type Audit, type Candidate, type Comparison, type Detail, type DraftLine, type ReviewLine, type Strategy,
  charCount, copyText, fields, lineName, message, pathFor, percent, score, signed, strategies,
} from "./model";
import { Badge, Button, Dialog, Empty, ExportMenu, FormField, Header, Notice } from "./ui";

const decisionLabels = { pending: "待审核", accepted: "已接受", rejected: "已拒绝 · 保留原文", kept: "沿用原文" } as const;

export function DiffText({ before, after, side }: { before: string; after: string; side: "before" | "after" }) {
  if (before.length + after.length > 13000) return <>{side === "before" ? before : after}</>;
  const pieces = diffWordsWithSpace(before, after);
  return <>{pieces.filter(part => side === "before" ? !part.added : !part.removed)
    .map((part, index) => part.added || part.removed
      ? <mark key={index} className={part.added ? "add-mark" : "remove-mark"}>{part.value}</mark>
      : <span key={index}>{part.value}</span>)}</>;
}

export function ComparisonPanel({ value }: { value: Comparison }) {
  return <section className="panel comparison-panel">
    <header className="panel-head"><div className="row"><GitCompareArrows size={19} /><h2>复评结果</h2></div><Badge tone="teal">沿用基准评分口径</Badge></header>
    <div className="compare-summary">
      <div><span>内容质量分</span><strong>{score(value.before)} <small>至</small> {score(value.after)}</strong>
        {value.same_basis ? <Badge tone={value.score_delta && value.score_delta > 0 ? "green" : "neutral"}>{signed(value.score_delta)}</Badge> :
          <Badge tone="amber">{value.by_module ? "评分模块有变化" : "可评估维度变化"}</Badge>}</div>
      <div><span>{value.by_module ? "模块覆盖率" : "可评估权重"}</span><strong>{percent(value.coverage_before)} <small>至</small> {percent(value.coverage_after)}</strong></div>
      <div><span>已明确回答占可判断问句</span><strong>{percent(value.questions_before)} <small>至</small> {percent(value.questions_after)}</strong></div>
    </div>
    <details className="expanded-comparison">
      <summary>{value.by_module ? "查看模块分差与问题清单" : "查看维度分差与问题清单"}</summary>
      <div className="table-scroll"><table><thead><tr><th>{value.by_module ? "模块" : "维度"}</th><th>原评分</th><th>复评</th><th>分差</th></tr></thead>
        <tbody>{value.dimensions.map(row => <tr key={row.key}><td>{row.label}</td><td>{score(row.before)}</td>
          <td>{score(row.after)}</td><td>{signed(row.delta)}</td></tr>)}</tbody></table></div>
      <div className="comparison-issues">
        {([["resolved", "已解决"], ["remaining", "仍存在"], ["new", "新增问题"], ["unverified", "待确认"]] as const).map(([key, label]) =>
          <div key={key}><h3>{label} <Badge>{value[key].length}</Badge></h3>
            {value[key].map(item => <p key={item.key}><strong>{fields[item.field]}</strong> · {item.message}</p>)}</div>)}
      </div>
    </details>
    <div className="panel-foot"><FlaskConical size={15} />内容分差用于比较文案。销量、排名及 Alexa 表现需要上线观察。</div>
  </section>;
}

function DraftEditor({ candidate, detail, baseline, comparison, busy, onSave, onPublication, onNotice }: {
  candidate: Candidate; detail: Detail; baseline: Audit | null; comparison: Comparison | undefined; busy: boolean;
  onSave: (rows: ReviewLine[], note: string, rescore: boolean) => Promise<void>;
  onPublication: (publishedAt: string, note: string) => Promise<void>; onNotice: (text: string) => void;
}) {
  const validation = candidate.data.validation;
  const original = validation.rows;
  const [rows, setRows] = useState<DraftLine[]>(original);
  const [note, setNote] = useState(candidate.data.note);
  const [onlyChanges, filter] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [showPublish, setShowPublish] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const changed = rows.length !== original.length || rows.some((row, i) => row.text !== original[i]?.text || row.decision !== original[i]?.decision)
    || note !== candidate.data.note;
  const rowCount = rows.filter(item => item.decision === "pending").length;
  const published = detail.publications.find(row => row.candidate_id === candidate.id);
  const canCopy = validation.ready && !changed;
  const beforeIssues = new Map(baseline?.data.result.issues.map(item => [item.key, item]));
  const maxBullets = baseline?.data.rules.limits.bullet_count ?? 5;
  const bulletCount = rows.filter(row => row.key.startsWith("bullets.")).length;

  function edit(key: string, next: Partial<DraftLine>) {
    setRows(items => items.map(row => row.key === key ? { ...row, ...next } : row));
  }
  function addBullet() {
    const next = Math.max(-1, ...rows.filter(row => row.key.startsWith("bullets.")).map(row => Number(row.key.slice(8)))) + 1;
    const newRow: DraftLine = { key: `bullets.${next}`, text: "", before: "", effective: "", decision: "pending",
      reason: "", fact_ids: [], fact_labels: [], verified: false, issue_ids: [], count: 0, limit: null };
    setRows(items => [...items.slice(0, -1), newRow, ...items.slice(-1)]);
  }
  async function save(rescore: boolean) {
    setSaving(true); setError("");
    try { await onSave(rows.map(({ key, text, decision }) => ({ key, text, decision })), note, rescore); }
    catch (err) { setError(message(err)); } finally { setSaving(false); }
  }
  async function copy() {
    const copied = await copyText(validation.copy);
    onNotice(copied ? "已复制审核后的文案。" : "浏览器无法写入剪贴板，可导出报告。");
  }
  return <>
    <section className="panel draft-panel">
      <header className="panel-head">
        <div className="row wrap"><FileCheck2 size={19} /><h2>新旧文案对照</h2><Badge>V{candidate.version}</Badge>
          {published ? <Badge tone="green">已记录上线</Badge> : canCopy ? <Badge tone="green">审核和规则检查通过</Badge> :
            <Badge tone="amber">{changed ? "有未保存修改" : rowCount ? `${rowCount} 条待审核` : "待检查"}</Badge>}</div>
        <Button kind="quiet" disabled={!rowCount || saving} onClick={() => setRows(current => current.map(row =>
          row.decision === "pending" ? { ...row, decision: row.text === row.before ? "kept" : "accepted" } : row))}>接受所有待审变更</Button>
      </header>
      <div className="draft-toolbar">
        <label className="checkbox-label"><input type="checkbox" checked={onlyChanges} onChange={event => filter(event.target.checked)} />仅看改动</label>
        <span className="muted">本地规则预估 {score(candidate.data.forecast.before)} 至 {score(candidate.data.forecast.after)}
          {candidate.data.forecast.delta !== null ? `（${signed(candidate.data.forecast.delta)}）` : ""}
          {changed && " · 保存后更新"}</span>
      </div>
      <div className="draft-column-labels"><span>原始快照</span><span>AI / 规则候选稿</span></div>
      {rows.filter(row => !onlyChanges || row.text !== row.before).map(row => {
        const initial = original.find(item => item.key === row.key);
        const effective = row.decision === "rejected" ? row.before : row.text;
        const count = charCount(effective);
        const draftChanged = row.text !== initial?.text || row.decision !== initial?.decision;
        const sizeLimit = row.limit !== null && count > row.limit;
        const linked = row.issue_ids.flatMap(key => beforeIssues.has(key) ? [beforeIssues.get(key)!] : []);
        const key = row.key.split(".")[0] as keyof typeof fields;
        const unusable = key !== "bullets" && validation.fields[key].state === "na";
        return <article className="draft-row" key={row.key}>
          <div className="row spread draft-row-title"><h3>{lineName(row.key)}</h3><Badge tone={row.decision === "accepted" ? "green" : row.decision === "pending" ? "amber" : "neutral"}>
            {decisionLabels[row.decision]}</Badge></div>
          <div className="draft-columns">
            <div className="draft-original" lang="en">{row.before ? <DiffText before={row.before} after={effective} side="before" /> : <span className="muted">原文为空</span>}</div>
            <div className="draft-suggestion">
              <textarea lang="en" rows={row.key === "aplus" ? 5 : 2} value={row.text}
                aria-label={`${lineName(row.key)}候选文案`} disabled={unusable}
                maxLength={12000} onChange={event => edit(row.key, {
                  text: event.target.value, decision: event.target.value === row.before ? "kept" : "pending",
                })} />
              {effective && effective !== row.before && <div className="diff-preview" lang="en"><DiffText before={row.before} after={effective} side="after" /></div>}
              <div className="draft-controls">
                <span className={`mono ${sizeLimit ? "text-red" : "muted"}`}>{count}{row.limit !== null ? ` / ${row.limit}` : ""} 字符</span>
                {row.text !== row.before && <div className="row tight">
                  <Button disabled={row.decision === "accepted"} kind="quiet" onClick={() => edit(row.key, { decision: "accepted" })}><Check size={15} />接受</Button>
                  <Button disabled={row.decision === "rejected"} kind="quiet" onClick={() => edit(row.key, { decision: "rejected" })}><X size={15} />拒绝</Button>
                </div>}
              </div>
            </div>
          </div>
          <div className="draft-reason">
            <Badge tone={draftChanged ? "amber" : row.verified ? "green" : "red"}>
              {draftChanged ? "保存后检查事实" : row.verified ? "事实校验通过" : "存在未核实措辞"}</Badge>
            {!draftChanged && row.reason && <span>{row.reason}</span>}
            {row.fact_labels.length > 0 && <small>事实依据 · {row.fact_labels.join("、")}</small>}
            {linked.map(issue => <small key={issue.key}>对应问题 · {issue.message}</small>)}
          </div>
        </article>;
      })}
      {!rows.length && <Empty title="没有可以审核的内容" />}
      {bulletCount < maxBullets && validation.fields.bullets.state !== "na" &&
        <div className="add-bullet"><Button kind="quiet" onClick={addBullet}><Plus size={16} />添加要点</Button></div>}
      <div className="draft-review">
        {error && <Notice warn>{error}</Notice>}
        {!changed && validation.blockers.length > 0 && <Notice warn>
          <strong>待完成的发布检查</strong><ul>{validation.blockers.map(item => <li key={item}>{item}</li>)}</ul>
        </Notice>}
        <FormField name="审核备注"><input value={note} maxLength={600} placeholder="可记录核实依据和改动原因"
          onChange={event => setNote(event.target.value)} /></FormField>
        <div className="row wrap spread">
          <div className="row wrap">
            <Button disabled={!canCopy} onClick={() => void copy()}><Copy size={16} />复制已审核文案</Button>
            {canCopy && !published && <Button kind="quiet" onClick={() => setShowPublish(true)}>记录实际上线</Button>}
          </div>
          <div className="row wrap">
            <Button disabled={saving || busy || !changed} onClick={() => void save(false)}><Save size={16} />保存审核</Button>
            <Button kind="primary" disabled={saving || busy} onClick={() => void save(true)}><ShieldCheck size={16} />保存并复评</Button>
          </div>
        </div>
      </div>
    </section>
    {comparison && <ComparisonPanel value={comparison} />}
    {showPublish && <PublishDialog busy={publishing} onClose={() => setShowPublish(false)} onSave={async (date, note) => {
      setPublishing(true);
      try { await onPublication(date, note); setShowPublish(false); }
      finally { setPublishing(false); }
    }} />}
  </>;
}

function PublishDialog({ busy, onClose, onSave }: { busy: boolean; onClose: () => void; onSave: (date: string, note: string) => Promise<void> }) {
  const now = new Date();
  const local = new Date(now.getTime() + 480 * 60000).toISOString().slice(0, 16);
  const [value, set] = useState(local);
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault(); setError("");
    try { await onSave(new Date(`${value}:00+08:00`).toISOString(), note); } catch (err) { setError(message(err)); }
  }
  return <Dialog title="记录实际上线" onClose={onClose}>
    <form className="dialog-form" onSubmit={submit}>
      {error && <Notice warn>{error}</Notice>}
      <p className="muted">在 Seller Central 完成实际更新后，可在这里留档上线时间。</p>
      <FormField name="上线时间（台北时间 UTC+8）"><input required type="datetime-local" value={value} max={local} onChange={event => set(event.target.value)} /></FormField>
      <FormField name="备注"><textarea maxLength={600} rows={3} value={note} onChange={event => setNote(event.target.value)} /></FormField>
      <footer className="dialog-actions"><Button onClick={onClose}>取消</Button>
        <Button kind="primary" type="submit" disabled={busy}>保存上线记录</Button></footer>
    </form>
  </Dialog>;
}

export function RewriteView({ detail, strategy, setStrategy, candidate, baseline, comparison, busy, onGenerate, onCandidate,
  onSave, onPublication, onNotice, onFacts, onDiagnosis, fieldRewrite }: {
  detail: Detail; strategy: Strategy; setStrategy: (value: Strategy) => void; candidate: Candidate | null;
  baseline: Audit | null; comparison: Audit | null; busy: boolean; onGenerate: () => void; onCandidate: (id: string) => void;
  onSave: (rows: ReviewLine[], note: string, rescore: boolean) => Promise<void>;
  onPublication: (publishedAt: string, note: string) => Promise<void>; onNotice: (value: string) => void;
  onFacts: () => void; onDiagnosis: () => void; fieldRewrite: ReactNode;
}) {
  const latest = detail.latest_audit;
  const contextChanged = latest && (latest.context_id !== detail.context.id ||
    (["name", "brand", "category", "sku", "product_type"] as const).some(key =>
      latest.data.product[key] !== detail.product[key]));
  const versions = detail.candidates.filter(item => item.strategy === strategy);
  const facts = detail.context.data.facts.filter(item => item.state === "confirmed");
  return <>
    <Header title="AI 改写" subtitle="粘贴文案直接改写，或按策略生成整份 Listing。"
      actions={<>{candidate && <ExportMenu base={pathFor(detail.product.id)} auditId={comparison?.id ?? candidate.audit_id} candidateId={candidate.id} />}
        <Button kind="primary" disabled={!latest || busy || !facts.length || !!contextChanged}
          onClick={onGenerate}><Sparkles size={17} />生成{strategies[strategy].label}稿</Button></>} />
    {fieldRewrite}
    <section className="strategy-grid" aria-label="整份 Listing 改写策略">{(Object.keys(strategies) as Strategy[]).map(key =>
      <button type="button" key={key} aria-pressed={strategy === key} onClick={() => setStrategy(key)} className={`panel strategy ${strategy === key ? "selected" : ""}`}>
        <Badge tone={strategy === key ? "teal" : "neutral"}>方案 {key}</Badge>
        <strong>{strategies[key].label}</strong><span>{strategies[key].detail}</span>
      </button>)}</section>
    {(!latest || contextChanged || !facts.length) && <div className="inline-warning"><AlertTriangle size={16} />
      {!facts.length ? <>整份改写需要产品事实库中的已确认事实。<button className="text-button" onClick={onFacts}>查看事实库</button></>
        : <>生成整份改写稿前，请对当前 Listing 运行一次评分。<button className="text-button" onClick={onDiagnosis}>查看诊断</button></>}
    </div>}
    {versions.length > 0 && <div className="version-select"><label htmlFor="draft-version">改写版本</label>
      <select id="draft-version" value={candidate?.id ?? ""} onChange={event => onCandidate(event.target.value)}>
        <option value="" disabled>选择版本</option>
        {versions.map(item => <option value={item.id} key={item.id}>V{item.version} · {item.ready ? "审核通过" : item.pending ? "待审" : "待核实"}</option>)}
      </select></div>}
    {candidate ? <DraftEditor key={candidate.id} candidate={candidate} detail={detail} baseline={baseline}
      comparison={comparison?.data.comparison} busy={busy} onSave={onSave} onPublication={onPublication} onNotice={onNotice} />
      : <section className="panel"><Empty title="生成整份 Listing 候选稿"
        actions={latest && !contextChanged && !!facts.length && <Button onClick={onGenerate} disabled={busy}><Sparkles size={16} />生成候选稿</Button>}>
        候选稿将引用已核实的事实，并保留原始 Listing 快照。
      </Empty></section>}
  </>;
}
