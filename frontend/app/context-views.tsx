"use client";

import { useState, type FormEvent } from "react";
import { BookOpenCheck, Check, ChevronDown, CircleHelp, FileInput, Info, Plus, Sparkles, Trash2 } from "lucide-react";
import {
  type AnswerState, type Context, type Detail, type Evidence, type Fact, type FieldKey, type Question,
  answerStatus, factStates, fields, id, message, paragraphLines, publishedCopy, questionSources, roles,
} from "./model";
import { Badge, Button, Dialog, Empty, FormField, Header, Notice } from "./ui";

type SaveContext = (next: Context) => Promise<void>;
const scopes: FieldKey[] = [...publishedCopy, "attributes", "search_terms"];
const answerTone: Record<AnswerState, string> = {
  supported: "green", partial: "amber", contradicted: "red", not_found: "neutral", unknown: "neutral",
};

function FactEditor({ item, onSave, onClose, onRemove }: {
  item: Fact | null; onSave: (fact: Fact) => Promise<void>; onRemove?: () => Promise<void>; onClose: () => void;
}) {
  const [value, set] = useState<Fact>(item ?? { id: id(), label: "", copy: "", role: "feature", source: "", state: "pending", conflicts: [] });
  const [terms, setTerms] = useState(value.conflicts.join("\n"));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  async function run(callback: () => Promise<void>) {
    setSaving(true); setError("");
    try { await callback(); onClose(); } catch (err) { setError(message(err)); } finally { setSaving(false); }
  }
  return <Dialog title={item ? "编辑产品事实" : "新增产品事实"} onClose={onClose}>
    <form className="dialog-form" onSubmit={event => { event.preventDefault(); void run(() => onSave({ ...value, conflicts: paragraphLines(terms) })); }}>
      {error && <Notice warn>{error}</Notice>}
      <FormField name="事实名称"><input required maxLength={140} value={value.label}
        placeholder="例如 首次设置需要手机 App" onChange={event => set({ ...value, label: event.target.value })} /></FormField>
      <FormField name="核实过的英文措辞" hint="审核和规则检查会用这段措辞作为依据。标题需要单独录一条「产品身份」。">
        <input required maxLength={800} lang="en" value={value.copy}
          placeholder="Use the app for initial setup and to save foods." onChange={event => set({ ...value, copy: event.target.value })} />
      </FormField>
      <div className="form-grid">
        <FormField name="内容角色"><select value={value.role} onChange={event => set({ ...value, role: event.target.value as Fact["role"] })}>
          {Object.entries(roles).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></FormField>
        <FormField name="核实状态"><select value={value.state} onChange={event => set({ ...value, state: event.target.value as Fact["state"] })}>
          {Object.entries(factStates).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></FormField>
      </div>
      <FormField name="事实来源" hint="可填说明书章节、实测说明或产品团队确认记录。">
        <input required={value.state === "confirmed"} maxLength={600} value={value.source} onChange={event => set({ ...value, source: event.target.value })} />
      </FormField>
      <FormField name="已知错误说法（选填）" hint="每行一条。评分时定位这些说法并标记冲突。"><textarea rows={3} value={terms}
        onChange={event => setTerms(event.target.value)} /></FormField>
      <footer className="dialog-actions">
        {onRemove && <Button kind="quiet" className="push-left text-red" disabled={saving} onClick={() => void run(onRemove)}><Trash2 size={15} />移出最新版</Button>}
        <Button onClick={onClose}>取消</Button><Button type="submit" kind="primary" disabled={saving}><Check size={16} />保存事实</Button>
      </footer>
    </form>
  </Dialog>;
}

export function FactsView({ detail, save }: { detail: Detail; save: SaveContext }) {
  const context = detail.context.data;
  const [edit, setEdit] = useState<Fact | null | undefined>(undefined);
  const [filter, setFilter] = useState<"all" | Fact["state"]>("all");
  const facts = context.facts.filter(item => filter === "all" || item.state === filter);
  return <>
    <Header title="产品事实库" subtitle="核对产品规格、使用条件和差异点，为评分与改写提供依据。"
      actions={<Button kind="primary" onClick={() => setEdit(null)}><Plus size={16} />新增事实</Button>} />
    <section className="panel">
      <header className="panel-head"><div className="row"><BookOpenCheck size={19} /><h2>事实清单</h2><Badge>V{detail.context.version}</Badge></div>
        <span className="muted">{context.facts.filter(item => item.state === "confirmed").length} 条已确认</span></header>
      <div className="issues-tools"><div className="segments">{(["all", "confirmed", "pending", "rejected"] as const).map(key =>
        <button type="button" key={key} aria-pressed={filter === key} className={filter === key ? "active" : ""}
          onClick={() => setFilter(key)}>{key === "all" ? "全部" : factStates[key]}</button>)}</div></div>
      {facts.length ? <div className="fact-list">{facts.map(item =>
        <article className="fact-row" key={item.id}>
          <div className="fact-main"><div className="row wrap"><h3>{item.label}</h3><Badge>{roles[item.role]}</Badge></div>
            <p lang="en">{item.copy}</p><small>{item.source || "等待补充来源"}</small></div>
          <Badge tone={item.state === "confirmed" ? "green" : item.state === "pending" ? "amber" : "neutral"}>{factStates[item.state]}</Badge>
          <Button kind="quiet" onClick={() => setEdit(item)}>编辑</Button>
        </article>)}</div>
        : <Empty title={context.facts.length ? "当前筛选下没有事实" : "还没有产品事实"}
          actions={!context.facts.length && <Button onClick={() => setEdit(null)}>新增第一条事实</Button>}>
          {!context.facts.length && "可以从商品身份、核心功能和明确的使用限制开始。"}
        </Empty>}
    </section>
    {edit !== undefined && <FactEditor item={edit} onClose={() => setEdit(undefined)} onSave={value => save({
      ...context, facts: edit ? context.facts.map(item => item.id === edit.id ? value : item) : [...context.facts, value],
    })} onRemove={edit ? () => save({
      facts: context.facts.filter(item => item.id !== edit.id),
      questions: context.questions.map(item => ({ ...item, fact_ids: item.fact_ids.filter(factId => factId !== edit.id) })),
    }) : undefined} />}
  </>;
}

function QuestionEditor({ value, facts, onClose, onSave, onRemove }: {
  value: Question | null; facts: Fact[]; onClose: () => void; onSave: (next: Question) => Promise<void>; onRemove?: () => Promise<void>;
}) {
  const [item, set] = useState<Question>(value ?? {
    id: id(), text: "", intent: "", source: "manual", priority: "standard",
    fields: [...publishedCopy], anchors: [], fact_ids: [],
  });
  const [anchors, setAnchors] = useState(item.anchors.join("\n"));
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  async function run(callback: () => Promise<void>) {
    setSaving(true); setError("");
    try { await callback(); onClose(); } catch (err) { setError(message(err)); } finally { setSaving(false); }
  }
  return <Dialog title={value ? "编辑买家问句" : "添加买家问句"} onClose={onClose}>
    <form className="dialog-form" onSubmit={event => { event.preventDefault(); void run(() => onSave({ ...item, anchors: paragraphLines(anchors) })); }}>
      {error && <Notice warn>{error}</Notice>}
      <FormField name="问题原文"><textarea required rows={2} maxLength={450} value={item.text}
        onChange={event => set({ ...item, text: event.target.value })} /></FormField>
      <FormField name="买家实际想确认什么"><input maxLength={400} value={item.intent} onChange={event => set({ ...item, intent: event.target.value })} /></FormField>
      <div className="form-grid">
        <FormField name="问句来源"><select value={item.source} onChange={event => {
          const source = event.target.value as Question["source"];
          set({ ...item, source, priority: source === "alexa" ? "high" : item.priority });
        }}>{Object.entries(questionSources).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></FormField>
        <FormField name="优先级"><select value={item.priority} disabled={item.source === "alexa"}
          onChange={event => set({ ...item, priority: event.target.value as Question["priority"] })}>
          <option value="standard">普通</option><option value="high">高优先</option></select></FormField>
      </div>
      <fieldset className="check-field"><legend>查找范围</legend><div className="check-options">
        {scopes.map(key => <label key={key}><input type="checkbox" checked={item.fields.includes(key)} onChange={event =>
          set({ ...item, fields: event.target.checked ? [...item.fields, key] : item.fields.filter(field => field !== key) })} />{fields[key]}</label>)}
      </div></fieldset>
      <FormField name="本地规则核对词组（选填）" hint="每行一个词组，例如 without reopening the app。">
        <textarea rows={2} value={anchors} onChange={event => setAnchors(event.target.value)} /></FormField>
      {facts.length > 0 && <fieldset className="check-field"><legend>关联产品事实</legend><div className="check-options facts-check">
        {facts.map(fact => <label key={fact.id}><input type="checkbox" checked={item.fact_ids.includes(fact.id)} onChange={event =>
          set({ ...item, fact_ids: event.target.checked ? [...item.fact_ids, fact.id] : item.fact_ids.filter(fid => fid !== fact.id) })} />{fact.label}</label>)}
      </div></fieldset>}
      <footer className="dialog-actions">{onRemove && <Button className="push-left text-red" kind="quiet" onClick={() => void run(onRemove)}
        disabled={saving}><Trash2 size={15} />移出最新版</Button>}
        <Button onClick={onClose}>取消</Button><Button type="submit" kind="primary" disabled={saving || !item.fields.length}>保存问句</Button></footer>
    </form>
  </Dialog>;
}

function AlexaImport({ remaining, onSave, onClose }: { remaining: number; onSave: (items: Question[]) => Promise<void>; onClose: () => void }) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    const rows = [...new Set(paragraphLines(value))];
    if (!rows.length || rows.length > remaining) { setError(`每行一条，本次最多添加 ${remaining} 条。`); return; }
    setError(""); setBusy(true);
    try {
      await onSave(rows.map(text => ({
        id: id(), text, intent: "", source: "alexa", priority: "high", fields: [...publishedCopy], anchors: [], fact_ids: [],
      })));
      onClose();
    } catch (err) { setError(message(err)); } finally { setBusy(false); }
  }
  return <Dialog title="导入 Alexa 问句" onClose={onClose}>
    <form className="dialog-form" onSubmit={submit}>
      {error && <Notice warn>{error}</Notice>}
      <FormField name="问句原文" hint={`每行一条，将保存为高优先级。当前可添加 ${remaining} 条。`}>
        <textarea required rows={7} value={value} placeholder="Can this scale show protein with the app closed?"
          onChange={event => setValue(event.target.value)} /></FormField>
      <footer className="dialog-actions"><Button onClick={onClose}>取消</Button>
        <Button kind="primary" type="submit" disabled={busy || remaining === 0}>导入到问句库</Button></footer>
    </form>
  </Dialog>;
}

function SuggestionReview({ options, capacity, onClose, onApply }: {
  options: Question[]; capacity: number; onClose: () => void; onApply: (rows: Question[]) => Promise<void>;
}) {
  const [selected, setSelected] = useState(new Set(options.slice(0, capacity).map(item => item.id)));
  const ai = options.some(item => item.source === "ai");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function apply() {
    setBusy(true); setError("");
    try { await onApply(options.filter(item => selected.has(item.id)).map(item => ({ ...item, id: id() }))); }
    catch (err) { setError(message(err)); } finally { setBusy(false); }
  }
  return <Dialog title={ai ? "审核 AI 生成的候选问句" : "选择商品问句模板"} onClose={onClose}>
    <div className="dialog-form">
      {error && <Notice warn>{error}</Notice>}
      <p className="muted">选中的候选会替换类目模板。已导入和手动保存的问句会保留。</p>
      {options.map(item => <label className="candidate-question" key={item.id}>
        <input type="checkbox" checked={selected.has(item.id)} onChange={event => {
          const next = new Set(selected); if (event.target.checked) next.add(item.id); else next.delete(item.id); setSelected(next);
        }} /><span lang="en">{item.text}</span></label>)}
      <footer className="dialog-actions"><Button onClick={onClose}>取消</Button>
        <Button kind="primary" disabled={busy || !selected.size || selected.size > capacity} onClick={() => void apply()}>采用 {selected.size} / {capacity} 条</Button></footer>
    </div>
  </Dialog>;
}

export function QuestionsView({ detail, save, locate, llmEnabled, onSuggest, onTemplate, options, clearOptions, busy }: {
  detail: Detail; save: SaveContext; locate: (value: Evidence) => void; llmEnabled: boolean;
  onSuggest: () => void; onTemplate: () => void; options: Question[] | null; clearOptions: () => void; busy: boolean;
}) {
  const context = detail.context.data;
  const result = detail.latest_audit?.context_id === detail.context.id ? detail.latest_audit.data.result : undefined;
  const results = new Map(result?.questions.map(item => [item.id, item]));
  const [edit, setEdit] = useState<Question | null | undefined>(undefined);
  const [showImport, setImport] = useState(false);
  const [filter, setFilter] = useState<"all" | "high" | "gaps">("all");
  const questions = [...context.questions].sort((a, b) => Number(b.source === "alexa") - Number(a.source === "alexa")
    || Number(b.priority === "high") - Number(a.priority === "high")).filter(item =>
      filter === "all" || (filter === "high" && item.priority === "high") ||
      (filter === "gaps" && results.get(item.id)?.status !== "supported"));
  const retain = context.questions.filter(item => item.source !== "template" && item.source !== "ai");
  return <>
    <Header title="买家问题" subtitle="核对用户购买前想知道什么，以及 Listing 有哪些原文可以回答。"
      actions={<><Button disabled={context.questions.length >= 20} onClick={() => setImport(true)}><FileInput size={16} />导入 Alexa 问句</Button>
        <Button kind="primary" disabled={context.questions.length >= 20} onClick={() => setEdit(null)}><Plus size={16} />添加问句</Button></>} />
    <section className="panel">
      <header className="panel-head"><div className="row"><CircleHelp size={19} /><h2>问句清单</h2>
        <Badge>{context.questions.length} / 20</Badge></div>
        <div className="row wrap"><Button kind="quiet" disabled={busy || retain.length >= 20} onClick={onTemplate}>载入商品问句模板</Button>
          {llmEnabled && <Button kind="quiet" disabled={busy || retain.length >= 20} onClick={onSuggest}><Sparkles size={16} />生成候选问句</Button>}</div></header>
      <div className="issues-tools">
        <div className="segments">{([["all", "全部"], ["high", "高优先级"], ["gaps", "待补或待判断"]] as const).map(([key, label]) =>
          <button key={key} type="button" aria-pressed={filter === key} className={filter === key ? "active" : ""} onClick={() => setFilter(key)}>{label}</button>)}</div>
        <span className="muted">{result ? `${result.assessed_questions} 条已判断` : "本版问句待评分"}</span>
      </div>
      {!questions.length && <Empty title="当前没有匹配的问句" />}
      <div className="question-list">{questions.map(item => {
        const response = results.get(item.id);
        return <details key={item.id} className="question-row">
          <summary><span className="question-title"><span lang="en">{item.text}</span><small>{questionSources[item.source]}{item.priority === "high" ? " · 高优先" : ""}</small></span>
            <Badge tone={response ? answerTone[response.status] : "neutral"}>{response ? answerStatus[response.status] : "待评分"}</Badge>
            <ChevronDown size={16} /></summary>
          <div className="question-answer">
            {item.intent && <p>{item.intent}</p>}
            {response && <p>{response.detail}</p>}
            {response?.evidence.map((ev, index) => <button type="button" key={index} className="evidence-button" onClick={() => locate(ev)}>
              <span>{fields[ev.field]}</span><q lang="en">{ev.quote}</q></button>)}
            {!!response?.missing.length && <p>待核对 · {response.missing.join("、")}</p>}
            {!!response?.suggested_fields.length && <p>建议查看 · {response.suggested_fields.map(key => fields[key]).join("、")}</p>}
            <Button kind="quiet" onClick={() => setEdit(item)}>编辑问句</Button>
          </div>
        </details>;
      })}</div>
      <div className="panel-foot"><Info size={15} />这里检查文案的可回答性。真实 Alexa 回答需另行实测。</div>
    </section>
    {edit !== undefined && <QuestionEditor value={edit} facts={context.facts} onClose={() => setEdit(undefined)}
      onSave={row => save({ ...context, questions: edit ? context.questions.map(item => item.id === row.id ? row : item) : [...context.questions, row] })}
      onRemove={edit ? () => save({ ...context, questions: context.questions.filter(item => item.id !== edit.id) }) : undefined} />}
    {showImport && <AlexaImport remaining={20 - context.questions.length} onClose={() => setImport(false)}
      onSave={rows => save({ ...context, questions: [...context.questions, ...rows.filter(row => !context.questions.some(q => q.text === row.text))] })} />}
    {options && <SuggestionReview options={options} capacity={20 - retain.length} onClose={clearOptions}
      onApply={async rows => { await save({ ...context, questions: [...retain, ...rows] }); clearOptions(); }} />}
  </>;
}
