"use client";

import { useEffect, useState, type ReactNode } from "react";
import {
  Activity, AlertTriangle, BarChart3, BookOpenCheck, ChevronRight, ClipboardList, Download, FilePenLine,
  Layers3, MessageSquareText, Play, ScanLine, SlidersHorizontal, Target,
} from "lucide-react";
import {
  type DiagnosisDetail, type Evidence, type FieldKey, type Guidance, type Issue, type Priority, type QuestionResult, type RewriteEntry,
  answerStatus, charCount, fields, fullDate, paragraphLines, pathFor, percent, publishedCopy, score, sourceStatus,
} from "./model";
import { Badge, Button, Empty, ExportMenu, Flag, Header, StateBadge } from "./ui";
import { FieldSuggestionPanel } from "./field-suggestions";
import { lineLabel, MissingPublicField, OriginalTranscript, PageNotes } from "./public-copy";

const tabs: FieldKey[] = ["title", "bullets", "highlights", "aplus", "raw_text", "public_specs", "attributes", "search_terms", "keywords"];
const captions: Partial<Record<FieldKey, string>> = { title: "标题", bullets: "五点", highlights: "Item Highlights", aplus: "A+", raw_text: "商品描述", public_specs: "公开规格", attributes: "后台属性", search_terms: "Search Terms", keywords: "关键词" };

function Marked({ value, selection }: { value: string; selection?: string }) {
  const index = selection ? value.indexOf(selection) : -1;
  if (index < 0 || !selection) return <>{value}</>;
  return <>{value.slice(0, index)}<mark>{selection}</mark>{value.slice(index + selection.length)}</>;
}

function Metric({ name, number, suffix, caption, icon, tone, children }: {
  name: string; number: string | number; suffix?: string; caption: string;
  icon: React.ReactNode; tone: string; children?: React.ReactNode;
}) {
  return <div className={`panel metric metric-${tone}`}>
    <div className="metric-top"><span className="metric-icon">{icon}</span><span>{name}</span></div>
    <div className="metric-main"><div><strong>{number}</strong>{suffix && <span className="metric-suffix">{suffix}</span>}</div>{children}</div>
    <div className="metric-caption">{caption}</div>
  </div>;
}

function ModuleScores({ detail, onLocate, onEdit }: {
  detail: DiagnosisDetail; onLocate: (value: Evidence) => void; onEdit?: () => void;
}) {
  const data = detail.latest_audit?.data;
  const modules = data?.result.module_scores;
  const scored = modules?.filter(item => item.score !== null) ?? [];
  const skipped = modules?.filter(item => item.score === null) ?? [];
  return <section className="panel dimension-panel">
    <header className="panel-head"><div className="row"><BarChart3 size={19} /><h2>各模块得分</h2></div>
      {modules && <Badge>{scored.length} 项已评分</Badge>}</header>
    {!modules ? <Empty title={data ? "当前为历史评分" : "等待首次评分"}>
      {data ? "重新运行评分即可查看各模块得分。" : "导入任意商品模块后即可计分。"}
    </Empty> : <div className="module-scores">
      {scored.length === 0 && <p className="muted">暂无可评分的模块原文。</p>}
      {scored.map(item => <article className="module-score" key={item.key}>
        <div className="row spread"><h3>{captions[item.key] ?? item.label}</h3>
          <strong>{item.score}<small> / 100</small></strong></div>
        <span className="bar" aria-hidden="true"><span className={item.score! < 50 ? "bar-low" : ""}
          style={{ width: `${item.score}%` }} /></span>
        <p>{item.reason}</p>
        {item.source_state === "pending" && <div><Badge tone="amber">原文待核实</Badge></div>}
        <details><summary>查看评分依据</summary>
          {item.checks.map(check => <p key={check.label}><strong>{check.label}</strong> · {check.reason}</p>)}
          {item.evidence.map((ref, index) => <button key={index} type="button" className="evidence-button" onClick={() => onLocate(ref)}>
            <span>第 {ref.line + 1} 段</span><q>{ref.quote}</q></button>)}
        </details>
      </article>)}
      {skipped.length > 0 && <details className="unscored-modules">
        <summary>未评分模块 · {skipped.length}</summary>
        <ul>{skipped.map(item => <li key={item.key}><strong>{captions[item.key] ?? item.label}</strong><span>{item.reason}</span></li>)}</ul>
        {onEdit && <Button kind="quiet" onClick={onEdit}><FilePenLine size={14} />补录或编辑</Button>}
      </details>}
    </div>}
    {modules && <div className="panel-foot"><span className="muted">总分取已评分模块的平均值。{data?.model && ` AI · ${data.model}`}</span></div>}
  </section>;
}

export function IssueCard({ issue, related, onLocate, onRewrite }: {
  issue: Issue; related: QuestionResult[]; onLocate: (item: Evidence) => void; onRewrite?: () => void;
}) {
  const edit = issue.edit;
  return <article className="issue-card">
    <div className="issue-head"><Badge tone={issue.priority === "P0" ? "red" : issue.priority === "P1" ? "amber" : "neutral"}>{issue.priority}</Badge>
      <div><h3>{issue.message}</h3>{!edit && <p>{issue.recommendation}</p>}</div>
      <button className="text-button" onClick={() => onLocate(issue)}>{fields[issue.field]} <ChevronRight size={14} /></button>
    </div>
    {edit && <div className="issue-edit">
      <div className="row wrap spread"><strong>{edit.target}</strong>
        {edit.rewrite && onRewrite && <Button kind="quiet" onClick={onRewrite}>打开并改写 <ChevronRight size={14} /></Button>}</div>
      {!!edit.after && <div className="issue-comparison">
        <div><small>原文</small><blockquote>{edit.before || "补充到此位置"}</blockquote></div>
        <div><small>{edit.model_generated ? "AI 改写建议" : "建议改成"}</small><blockquote>{edit.after}</blockquote></div>
      </div>}
      <p><strong>{edit.after ? "为什么这样改" : "具体怎么改"}</strong> · {edit.action}</p>
      {!!edit.fact_labels.length && <small>事实依据 · {edit.fact_labels.join("、")}</small>}
      {edit.answers?.[0] ? <small>{edit.answers[0].question} · {answerStatus[edit.answers[0].after]}</small>
        : edit.question && <small>要回答的问题 · <span>{edit.question}</span></small>}
    </div>}
    <div className="issue-meta">{(issue.needs_review || edit?.model_generated) && <Badge tone="amber">需人工核实</Badge>}
      {issue.quote && !edit?.after && <blockquote>{issue.quote}</blockquote>}
      {issue.source && (issue.source.startsWith("https://") && !issue.source.includes(" ")
        ? <a href={issue.source} target="_blank" rel="noreferrer">查看规则来源</a>
        : <span>依据 · {issue.source}</span>)}
    </div>
    {related.length > 0 && <details className="issue-questions">
      <summary>影响的买家问句 · {related.length}</summary>
      {related.map(question => <div key={question.id}><span lang="en">{question.text}</span>
        {!!question.suggested_fields.length && <small>建议补充 · {question.suggested_fields.map(key => fields[key]).join("、")}</small>}</div>)}
    </details>}
  </article>;
}

function ReviewCard({ item, onLocate }: { item: Guidance; onLocate: (value: Evidence) => void }) {
  return <article className="issue-card review-card">
    <div className="issue-head"><Badge tone="amber">待核验</Badge>
      <div><h3>{item.label}</h3><p>{item.action}</p></div><span className="muted">{fields[item.field]}</span></div>
    <div className="review-details">
      {item.question && <p>相关买家问题 · <span lang="en">{item.question}</span></p>}
      <p>补充依据 · {item.source_needed}</p>
      {item.evidence.map((ref, index) => <button key={index} type="button" className="evidence-button" onClick={() => onLocate(ref)}>
        <span>{fields[ref.field]}</span><q lang="en">{ref.quote}</q></button>)}
    </div>
  </article>;
}

export function Diagnosis({ detail, busy, onAudit, onEdit, onEditProduct, onQuestions, onRewrite, jump, quickActions, aiEnabled }: {
  detail: DiagnosisDetail; busy?: boolean; onAudit?: () => void; onEdit?: () => void; onEditProduct?: () => void;
  onQuestions: () => void; onRewrite?: (entry?: RewriteEntry) => void; jump: (Evidence & { nonce: number }) | null; quickActions?: ReactNode; aiEnabled: boolean;
}) {
  const { product, snapshot, latest_audit: audit, current_rules: activeRules, context } = detail;
  const result = audit?.data.result;
  const byModule = !!result?.module_scores;
  const initial = !byModule && result?.provisional ? result.screen : null;
  const primaryScore = initial ? initial.score : result?.score;
  const rules = audit?.data.rules ?? activeRules;
  const priorityCount = result?.issues.filter(item => item.priority === "P0").length;
  const [tab, select] = useState<FieldKey>("title");
  const [allPublic, setAllPublic] = useState(true);
  const [target, locate] = useState<Evidence | null>(null);
  const [filter, setFilter] = useState<Priority | "all" | "review">("all");
  const row = snapshot.fields[tab];
  const pageLines = new Map(row.page_lines?.map(item => [item.line, lineLabel(item)]));
  const selected = target?.field === tab ? target : null;
  const p0 = priorityCount ?? 0;
  const changedContext = audit && (audit.context_id !== context.id ||
    (["name", "brand", "category", "sku", "product_type"] as const).some(key =>
      audit.data.product[key] !== product[key]));
  const issues = (result?.issues ?? []).filter(item => filter === "all" || item.priority === filter);
  const guidance = result?.guidance?.filter(item => item.dimension === null) ?? [];
  const review = filter === "all" || filter === "review" ? guidance : [];

  function openEvidence(item: Evidence) {
    setAllPublic(false);
    select(item.field);
    locate({ ...item });
  }
  useEffect(() => {
    if (jump) openEvidence(jump);
  }, [jump]);
  useEffect(() => {
    if (target) document.getElementById(`evidence-${target.field}-${target.line}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [tab, target]);

  return <>
    <Header title={quickActions ? "快速评分结果" : "Listing 诊断"}
      subtitle={quickActions ? "这次结果只显示在当前页面。需要留档时，可手动保存文案。" : "查看优先问题、原文证据和具体改法。"}
      actions={quickActions ?? <>{publishedCopy.some(key => snapshot.fields[key]?.text) &&
        <a className="button button-secondary" href={`/api${pathFor(product.id)}/alexa-skill-input`} download>
          <Download size={16} />导出 Alexa Skill 输入
        </a>}
        {audit && <ExportMenu base={pathFor(product.id)} auditId={audit.id} />}
        <Button kind="primary" disabled={busy} onClick={onAudit}><Play size={16} />运行评分</Button></>} />
    <section className="product-strip panel">
      <div className="product-emblem"><Layers3 size={23} strokeWidth={1.8} /></div>
      <div className="product-strip-copy"><div className="row wrap"><h2>{product.name}</h2>{product.demo && <Badge tone="blue">演示项目</Badge>}</div>
        <div className="product-meta"><span className="mono">{product.asin || "ASIN 待补"}</span><span>{product.market === "US" && <Flag />} Amazon {product.market}</span><span>{product.category || "类目待补"}</span></div>
      </div>
      {onEditProduct && <Button onClick={onEditProduct} kind="quiet"><SlidersHorizontal size={16} /> 商品资料</Button>}
    </section>
    {changedContext && <div className="inline-warning"><AlertTriangle size={16} />商品资料、事实或问句已更新。重新评分可使用最新版资料。</div>}
    <div className="metrics">
      <Metric name={byModule || !result ? "Listing 总分" : "历史评分"} number={score(primaryScore)} suffix="/ 100" icon={<Activity size={17} />} tone="teal"
        caption={byModule ? "已评分模块的平均值" : result ? "沿用当时的评分口径" : "运行评分后生成"}>
        {primaryScore != null && <span className="score-ring" style={{ "--progress": `${primaryScore}%` } as React.CSSProperties}><span /></span>}
      </Metric>
      <Metric name={byModule || !result ? "已评分模块" : "历史覆盖率"}
        number={byModule ? result.assessed_weight : result ? percent(result.coverage) : "—"}
        suffix={byModule ? `/ ${result.applicable_weight}` : undefined} icon={<ScanLine size={17} />} tone="blue"
        caption={byModule ? "按本次取得的原文" : result ? "沿用当时的统计口径" : "取得原文后开始评分"} />
      <Metric name="优先处理问题" number={result ? String(p0) : "—"} icon={<AlertTriangle size={17} />} tone="red"
        caption={result ? (p0 ? "P0 · 事实或规则风险" : initial ? "已评估资料中未发现 P0" : "当前评分未发现 P0") : "发现冲突后标记 P0"} />
      <Metric name="买家问题已明确回答" number={result ? String(result.question_states.supported ?? 0) : "—"}
        suffix={result ? `/ ${result.total_questions}` : ""} icon={<MessageSquareText size={17} />} tone="violet"
        caption={result ? `${result.assessed_questions} 条可判断` : "以当前问句库为准"} />
    </div>
    <div className="diagnosis-grid">
      <section className="panel listing-panel">
        <header className="panel-head"><div className="row"><ClipboardList size={19} /><h2>原始 Listing</h2>
          <Badge>{quickActions ? "临时" : `V${snapshot.version}`}</Badge></div>
          {onEdit && <Button onClick={onEdit} kind="quiet"><FilePenLine size={15} /> 编辑 / 导入</Button>}</header>
        <div className="tabline" role="tablist" aria-label="Listing 字段">
          <button type="button" role="tab" aria-selected={allPublic} onClick={() => { setAllPublic(true); locate(null); }}>前台原文</button>
          {tabs.map(key =>
          <button type="button" role="tab" aria-selected={!allPublic && tab === key} key={key} onClick={() => { setAllPublic(false); select(key); locate(null); }}>
            {captions[key]}</button>)}</div>
        <div className="listing-content" role="tabpanel">
          {allPublic ? <OriginalTranscript listing={snapshot.fields} asin={product.asin} market={product.market} /> : <>
          <div className="field-top"><div><h3>{fields[tab]}</h3><span>{row.source || "等待补充来源"}</span></div><StateBadge state={row.state} /></div>
          {row.text ? <>
            <div className={`listing-copy ${tab === "title" ? "title-copy" : ""}`}>
              {paragraphLines(row.text).map((line, index) => <div className={selected?.line === index ? "evidence-line selected" : "evidence-line"}
                key={index} id={`evidence-${tab}-${index}`}>
                {tab === "bullets" && <span className="text-dot" aria-hidden="true" />}
                <span>{pageLines.has(index) && <small className="page-line-label">{pageLines.get(index)}</small>}
                  <Marked value={line} selection={selected?.line === index ? selected.quote : undefined} /></span>
              </div>)}
            </div>
            <div className="listing-meta"><span>{row.collected_at ? `采集于 ${fullDate(row.collected_at)}` : ""}</span>
              {(tab === "title" || tab === "highlights") && <span className={rules.limits[tab] && charCount(row.text) > rules.limits[tab]! ? "text-red mono" : "mono"}>
                {charCount(row.text)}{rules.limits[tab] !== undefined ? ` / ${rules.limits[tab]}` : ""} 字符
              </span>}</div>
          </> : <Empty title={sourceStatus[row.state]} actions={row.state === "missing" && onEdit && <Button onClick={onEdit}>补充资料</Button>}>
            {row.state === "missing" ? <MissingPublicField field={tab} /> : "此状态会随本次 Listing 快照保存。"}
          </Empty>}
          <PageNotes row={row} />
          {(["keywords", "attributes", "search_terms"] as FieldKey[]).includes(tab) && <button className="text-button" type="button"
            onClick={() => {
              const suggestions = document.getElementById("optional-field-suggestions") as HTMLDetailsElement | null;
              if (suggestions) suggestions.open = true;
              const el = document.getElementById("draft-" + tab);
              el?.focus({ preventScroll: true });
              el?.scrollIntoView({ block: "center" });
            }}>
            查看该字段的备选内容 <ChevronRight size={14} /></button>}
          {selected && <div className="evidence-location"><Target size={15} /> 定位到 {fields[selected.field]}{selected.quote && ` · 第 ${selected.line + 1} 段`}
            <button className="text-button" onClick={() => locate(null)}>清除定位</button></div>}
          </>}
        </div>
      </section>
      <ModuleScores detail={detail} onLocate={openEvidence} onEdit={onEdit} />
    </div>
    <section className="panel issues-panel">
      <header className="panel-head"><div className="row wrap"><BookOpenCheck size={19} /><h2>问题诊断与修改意见</h2>
        {result && <><Badge>{result.issues.length} 项问题</Badge>{guidance.length > 0 && <Badge tone="amber">{guidance.length} 项待核验</Badge>}</>}</div>
        {onRewrite && <Button kind="quiet" onClick={() => onRewrite()}>进入 AI 改写 <ChevronRight size={16} /></Button>}
      </header>
      {result ? <>
        <div className="issues-tools">
          <div className="segments" aria-label="筛选优先级">{(["all", "P0", "P1", "P2"] as const).map(item =>
            <button type="button" key={item} className={filter === item ? "active" : ""} onClick={() => setFilter(item)}
              aria-pressed={filter === item}>{item === "all" ? "全部" : item}{item === "P0" ? ` ${p0}` : ""}</button>)}
            {guidance.length > 0 && <button type="button" className={filter === "review" ? "active" : ""} aria-pressed={filter === "review"}
              onClick={() => setFilter("review")}>待核验</button>}</div>
          <button className="text-button" onClick={onQuestions}><MessageSquareText size={15} /> 查看买家问句</button>
        </div>
        <div className="issues-list">{issues.map(item => <IssueCard key={item.key} issue={item} onLocate={openEvidence}
          onRewrite={audit && !changedContext && item.edit && onRewrite ? () =>
            onRewrite({ auditId: audit.id, issueKey: item.key, edit: item.edit! }) : undefined}
          related={result.questions.filter(question => item.question_ids.includes(question.id))} />)}
          {review.map(item => <ReviewCard key={item.key} item={item} onLocate={openEvidence} />)}
          {issues.length === 0 && review.length === 0 && <Empty title="当前筛选下没有项目" />}</div>
      </> : <Empty title="等待首次评分">评分完成后，优先改动的文字和对应证据会显示在这里。</Empty>}
    </section>
    <details className="details-card" id="optional-field-suggestions"><summary>查看后台字段备选</summary>
      <FieldSuggestionPanel key={snapshot.id} detail={detail} temporary={!!quickActions} aiEnabled={aiEnabled} onLocate={openEvidence} />
    </details>
  </>;
}
