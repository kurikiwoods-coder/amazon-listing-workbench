"use client";

import { useState } from "react";
import { CalendarCheck, ClipboardList, GitCompareArrows, History, RotateCcw } from "lucide-react";
import {
  type Audit, type CandidateSummary, type Detail, type FieldKey, type Snapshot,
  api, fields, fullDate, message, pathFor, percent, publicFields, score, strategies,
} from "./model";
import { Badge, Button, Dialog, Empty, ExportMenu, Header, Notice, StateBadge } from "./ui";
import { ComparisonPanel, DiffText } from "./rewrite-view";

const names = { created: "项目创建", saved: "录入原文", quick: "快速粘贴", demo: "演示样本", restored: "恢复原文" } as Record<string, string>;
type Tab = "snapshots" | "audits" | "candidates" | "publication";

export function HistoryView({ detail, onRestore, onCandidate }: {
  detail: Detail; onRestore: (id: string) => Promise<void>; onCandidate: (item: CandidateSummary) => void;
}) {
  const [tab, select] = useState<Tab>("snapshots");
  const [left, setLeft] = useState(detail.snapshots[1]?.id ?? detail.snapshots[0]?.id ?? "");
  const [right, setRight] = useState(detail.snapshots[0]?.id ?? "");
  const [comparison, setComparison] = useState<{ from: Snapshot; to: Snapshot } | null>(null);
  const [audit, setAudit] = useState<Audit | null>(null);
  const [restore, setRestore] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const base = pathFor(detail.product.id);

  async function load(callback: () => Promise<void>) {
    setLoading(true); setError("");
    try { await callback(); } catch (err) { setError(message(err)); } finally { setLoading(false); }
  }
  async function compareSnapshots(from: string, to: string) {
    await load(async () => {
      const [previous, now] = await Promise.all([api<Snapshot>(`${base}/snapshots/${from}`), api<Snapshot>(`${base}/snapshots/${to}`)]);
      setComparison({ from: previous, to: now });
    });
  }
  async function viewAudit(identifier: string) {
    await load(async () => setAudit(await api<Audit>(`${base}/audits/${identifier}`)));
  }
  return <>
    <Header title="版本与验证" subtitle="查看 Listing 快照、改写审核、复评结果和实际上线记录。" />
    {error && <Notice warn>{error}</Notice>}
    <section className="panel">
      <header className="panel-head"><div className="row"><History size={19} /><h2>项目历史</h2></div></header>
      <div className="tabline" role="tablist" aria-label="历史类型">{([["snapshots", "原文快照"], ["audits", "评分与复评"],
        ["candidates", "改写版本"], ["publication", "上线记录"]] as const).map(([key, label]) =>
        <button key={key} type="button" role="tab" aria-selected={tab === key} onClick={() => select(key)}>{label}</button>)}</div>
      {tab === "snapshots" && <>
        <div className="history-actions row wrap">
          <label>原版本 <select value={left} onChange={event => setLeft(event.target.value)}>{detail.snapshots.map(item =>
            <option key={item.id} value={item.id}>V{item.version}</option>)}</select></label>
          <label>目标版本 <select value={right} onChange={event => setRight(event.target.value)}>{detail.snapshots.map(item =>
            <option key={item.id} value={item.id}>V{item.version}</option>)}</select></label>
          <Button disabled={loading || !left || !right || left === right} onClick={() => void compareSnapshots(left, right)}>
            <GitCompareArrows size={16} />对比快照</Button>
        </div>
        <div className="table-scroll"><table><thead><tr><th>版本</th><th>事件</th><th>时间</th><th>备注</th><th>操作</th></tr></thead>
          <tbody>{detail.snapshots.map(item => <tr key={item.id}><td><strong>V{item.version}</strong>{item.id === detail.snapshot.id && <Badge tone="teal">当前</Badge>}</td>
            <td>{names[item.cause] || item.cause}</td><td>{fullDate(item.created_at)}</td><td>{item.note || "—"}</td>
            <td><div className="row tight"><Button kind="quiet" onClick={() => void compareSnapshots(item.id, item.id)} disabled={loading}>查看原文</Button>
              {item.id !== detail.snapshot.id && <Button kind="quiet" onClick={() => setRestore(item.id)}><RotateCcw size={14} />恢复</Button>}</div></td></tr>)}</tbody>
        </table></div>
      </>}
      {tab === "audits" && (detail.audits.length ? <div className="table-scroll"><table><thead>
        <tr><th>评分类别</th><th>内容分</th><th>数据覆盖</th><th>时间</th><th>操作</th></tr></thead>
        <tbody>{detail.audits.map(row => <tr key={row.id}><td>{row.kind === "audit" ? "基准评分" : "修改后复评"}</td>
          <td><strong>{score(row.provisional && row.screen_score != null ? row.screen_score : row.score)}</strong>{row.provisional && <Badge tone="amber">{row.by_module ? "部分模块" : row.screen_score != null ? "初筛" : "临时"}</Badge>}</td>
          <td>{percent(row.coverage)}</td><td>{fullDate(row.created_at)}</td>
          <td><Button kind="quiet" onClick={() => void viewAudit(row.id)} disabled={loading}>查看结果</Button></td></tr>)}</tbody>
      </table></div> : <Empty title="还没有评分记录" />)}
      {tab === "candidates" && (detail.candidates.length ? <div className="table-scroll"><table><thead>
        <tr><th>版本</th><th>改写策略</th><th>审核状态</th><th>保存时间</th><th>操作</th></tr></thead>
        <tbody>{detail.candidates.map(row => <tr key={row.id}><td><strong>V{row.version}</strong></td><td>{strategies[row.strategy].label}</td>
          <td><Badge tone={row.ready ? "green" : "amber"}>{row.ready ? "通过" : row.pending ? `${row.pending} 条待审` : "待核实"}</Badge></td>
          <td>{fullDate(row.created_at)}</td><td><Button kind="quiet" onClick={() => onCandidate(row)}>打开候选稿</Button></td></tr>)}</tbody>
      </table></div> : <Empty title="还没有改写版本" />)}
      {tab === "publication" && (detail.publications.length ? <div className="table-scroll"><table><thead>
        <tr><th>上线版本</th><th>上线时间</th><th>备注</th></tr></thead>
        <tbody>{detail.publications.map(row => <tr key={row.id}><td>V{detail.candidates.find(item => item.id === row.candidate_id)?.version ?? "—"}</td>
          <td>{fullDate(row.published_at)}</td><td>{row.note || "—"}</td></tr>)}</tbody></table></div>
        : <Empty title="还没有上线记录">在 Seller Central 实际上线后，可在已审核候选稿中登记。</Empty>)}
    </section>
    {comparison && <SnapshotDialog {...comparison} onClose={() => setComparison(null)} />}
    {audit && <Dialog wide title={audit.kind === "audit" ? "历史基准评分" : "历史复评"} onClose={() => setAudit(null)}>
      <div className="dialog-form">
        <div className="row spread wrap"><div className="row wrap"><Badge tone={audit.data.result.provisional ? "amber" : "teal"}>
          {audit.data.result.module_scores ? "按模块评分" : "历史评分"}</Badge>
          <strong>{score(audit.data.result.provisional && audit.data.result.screen ? audit.data.result.screen.score : audit.data.result.score)} / 100</strong>
          <span>覆盖 {percent(audit.data.result.coverage)}</span></div><ExportMenu base={base} auditId={audit.id} /></div>
        <div className="row wrap muted"><span>评分时间 {fullDate(audit.created_at)}</span><span>规则 {audit.data.rules.version}</span>
          <span>{audit.data.model || "本地规则"}</span></div>
        {audit.data.comparison && <ComparisonPanel value={audit.data.comparison} />}
        {audit.kind === "audit" && audit.data.result.dimensions.map(item => <div className="audit-line" key={item.key}>
          <strong>{item.label} · {score(item.score)}</strong><p>{item.reason}</p>
          {item.evidence[0] && <blockquote>{item.evidence[0].quote}</blockquote>}
        </div>)}
      </div>
    </Dialog>}
    {restore && <Dialog title="恢复原文快照" onClose={() => setRestore(null)}><div className="dialog-form">
      <p>将用这个历史快照创建原文的新版本。现有版本可在历史里继续查看。</p>
      {error && <Notice warn>{error}</Notice>}
      <footer className="dialog-actions"><Button onClick={() => setRestore(null)}>取消</Button>
        <Button kind="primary" disabled={loading} onClick={() => void load(async () => { await onRestore(restore); setRestore(null); })}>恢复为当前原文</Button></footer>
    </div></Dialog>}
  </>;
}

function SnapshotDialog({ from, to, onClose }: { from: Snapshot; to: Snapshot; onClose: () => void }) {
  const standalone = from.id === to.id;
  const [all, setAll] = useState(standalone);
  const keys = (Object.keys(fields) as FieldKey[]).filter(key => all || from.fields[key].text !== to.fields[key].text ||
    from.fields[key].state !== to.fields[key].state || from.fields[key].source !== to.fields[key].source);
  return <Dialog wide title={standalone ? `原文 V${to.version}` : `快照对比 · V${from.version} 与 V${to.version}`} onClose={onClose}>
    <div className="dialog-form">
      {!standalone && <label className="checkbox-label"><input type="checkbox" checked={all} onChange={event => setAll(event.target.checked)} />显示未变更的字段</label>}
      {!keys.length && <Empty title="这两个快照的字段内容相同" />}
      {keys.map(key => <article className="snapshot-item" key={key}>
        <div className="row wrap"><ClipboardList size={15} /><h3>{fields[key]}</h3><StateBadge state={to.fields[key].state} /></div>
        {standalone ? <><p className="muted">{to.fields[key].source || "无来源"} · {fullDate(to.fields[key].collected_at)}</p>
          <div className="snapshot-copy" lang={publicFields.includes(key) ? "en" : undefined}>{to.fields[key].text || "—"}</div></>
          : <div className="draft-columns">
            <div className="snapshot-copy"><small>{from.fields[key].source || "无来源"}</small><DiffText before={from.fields[key].text} after={to.fields[key].text} side="before" /></div>
            <div className="snapshot-copy"><small>{to.fields[key].source || "无来源"}</small><DiffText before={from.fields[key].text} after={to.fields[key].text} side="after" /></div>
          </div>}
      </article>)}
    </div>
  </Dialog>;
}
