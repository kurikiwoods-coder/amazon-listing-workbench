"use client";

import { useEffect, useRef, useState } from "react";
import {
  Activity, BookOpenCheck, ChevronRight, CircleHelp, ClipboardCheck, ClipboardPaste, Database, Globe2,
  History, LayoutDashboard, ListChecks, LoaderCircle, Package, Plus, Search, Sparkles, Trash2, X,
} from "lucide-react";
import { Diagnosis } from "./diagnosis";
import { FactsView, QuestionsView } from "./context-views";
import { ListingEditor, ProductEditor } from "./editors";
import { HistoryView } from "./history";
import { RewriteView } from "./rewrite-view";
import { SkillRewrite } from "./skill-rewrite";
import { QuickView } from "./quick";
import { capturePath, DeleteCaptureDialog, RecentCaptures } from "./workflows";
import {
  type Audit, type Candidate, type CaptureProvider, type CaptureTask, type Config, type Context, type Detail, type Evidence, type Job,
  type JobResult, type JobStart, type ListedProduct, type Listing, type Product, type ProductInput,
  type Question, type RewriteEntry, type ReviewLine, type SnapshotInput, type Strategy,
  api, defaultMarketplace, message, pathFor, percent, score, shortDate,
} from "./model";
import { Badge, Button, Dialog, Empty, Header, Notice } from "./ui";

const navigation = [
  { id: "quick", label: "快速评分", icon: ClipboardPaste },
  { id: "overview", label: "ASIN 库", icon: LayoutDashboard },
  { id: "diagnosis", label: "Listing 诊断", icon: ListChecks },
  { id: "facts", label: "产品事实库", icon: BookOpenCheck },
  { id: "questions", label: "买家问题", icon: CircleHelp },
  { id: "rewrite", label: "AI 改写", icon: Sparkles },
  { id: "history", label: "版本与验证", icon: History },
] as const;
type Page = typeof navigation[number]["id"];
type Modal = "new" | "product" | "listing" | "audit" | "help" | null;

function Overview({ projects, onCreate, onDemo, onOpen, onDelete }: {
  projects: ListedProduct[]; onCreate: () => void; onDemo: () => void; onOpen: (id: string) => void;
  onDelete: (item: ListedProduct) => void;
}) {
  const [query, search] = useState("");
  const [filter, setFilter] = useState("all");
  const [marketFilter, setMarketFilter] = useState("");
  const markets = [...new Set(projects.map(item => item.market))].sort();
  const selectedMarket = markets.includes(marketFilter) ? marketFilter : "";
  const matches = projects.filter(item => {
    if (selectedMarket && item.market !== selectedMarket) return false;
    if (filter === "pending" && (item.score !== null || item.screen_score !== null)) return false;
    if (filter === "p0" && !item.p0) return false;
    const key = query.trim().toLowerCase();
    return [item.name, item.asin, item.brand, item.sku].some(value => value.toLowerCase().includes(key));
  });
  return <>
    <Header title="ASIN 库" subtitle="按 Amazon 站点管理商品、评分与版本。"
      actions={<Button kind="primary" onClick={onCreate}><Plus size={17} />新建项目</Button>} />
    <section className="panel overview-panel">
      <header className="panel-head"><div className="row"><Package size={19} /><h2>商品项目</h2><Badge>{projects.length}</Badge></div>
        {!projects.some(product => product.demo) && <Button onClick={onDemo} kind="quiet">加载演示项目</Button>}</header>
      <div className="overview-tools">
        <label className="search"><Search size={17} /><input placeholder="搜索 ASIN、商品、SKU" aria-label="搜索 ASIN、商品、SKU"
          value={query} onChange={event => search(event.target.value)} /></label>
        {markets.length > 1 && <label className="row"><span className="muted">站点</span><select aria-label="筛选站点" value={selectedMarket} onChange={event => setMarketFilter(event.target.value)}>
          <option value="">全部站点</option>{markets.map(market => <option key={market} value={market}>{market}</option>)}
        </select></label>}
        <label className="row"><span className="muted">筛选</span><select aria-label="筛选项目" value={filter} onChange={event => setFilter(event.target.value)}>
          <option value="all">全部项目</option><option value="pending">待评分</option><option value="p0">有 P0 问题</option>
        </select></label>
      </div>
      {matches.length > 0 ? <div className="table-scroll"><table><thead><tr>
        <th>商品 / ASIN</th><th>内容评分</th><th>数据覆盖</th><th>P0</th><th>改写状态</th><th>最近检测</th><th>操作</th>
      </tr></thead><tbody>{matches.map(item => <tr key={item.id}>
        <td><button type="button" className="product-link" onClick={() => onOpen(item.id)}><strong>{item.name}</strong>
          <span className="mono">{item.market} · {item.asin || "ASIN 待补"}</span></button>{item.demo && <Badge tone="blue">演示</Badge>}</td>
        <td><strong>{score(item.provisional && item.screen_score != null ? item.screen_score : item.score)}</strong>
          {item.provisional && <Badge tone="amber">{item.by_module ? "部分模块" : item.screen_score != null ? "初筛" : "临时"}</Badge>}</td>
        <td>{percent(item.coverage)}</td><td>{item.p0 ? <Badge tone="red">{item.p0}</Badge> : item.p0 ?? "—"}</td>
        <td><Badge tone={item.draft_ready ? "green" : "neutral"}>{item.draft_ready === null ? "尚无候选" : item.draft_ready ? "审核通过" : "审核中"}</Badge></td>
        <td>{shortDate(item.updated_at)}</td><td><div className="row row-actions">
          <Button kind="quiet" onClick={() => onOpen(item.id)} aria-label={`打开 ${item.name}`}><ChevronRight size={18} /></Button>
          <Button kind="quiet" className="row-delete" aria-label={`删除 ${item.market} ${item.asin || item.name}`} onClick={() => onDelete(item)}><Trash2 size={15} />删除</Button>
        </div></td>
      </tr>)}</tbody></table></div>
        : <Empty title={projects.length ? "没有匹配的商品" : "还没有 ASIN 项目"}
          actions={!projects.length && <><Button kind="primary" onClick={onCreate}>新建项目</Button><Button onClick={onDemo}>加载演示</Button></>}>
          {projects.length ? "换一个名称或 ASIN 搜索。" : "录入商品资料后即可开始核验与评分。"}
        </Empty>}
    </section>
  </>;
}

function DeleteProjectDialog({ item, onDelete, onClose }: {
  item: ListedProduct; onDelete: (id: string) => Promise<void>; onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function remove() {
    setBusy(true); setError("");
    try { await onDelete(item.id); }
    catch (err) { setError(message(err)); } finally { setBusy(false); }
  }
  return <Dialog title="删除 ASIN 项目" onClose={() => { if (!busy) onClose(); }}>
    <div className="dialog-form">
      <div><strong>{item.name}</strong>{item.asin && <p className="mono muted">{item.market} · {item.asin}</p>}</div>
      <p>该项目的原文快照、产品事实、评分、改写稿和历史记录会一并删除。此操作无法撤销。</p>
      {error && <Notice warn>{error}</Notice>}
      <footer className="dialog-actions"><Button disabled={busy} onClick={onClose}>取消</Button>
        <Button kind="danger" disabled={busy} onClick={() => void remove()}><Trash2 size={16} />{busy ? "正在删除…" : "确认删除"}</Button></footer>
    </div>
  </Dialog>;
}

export default function Workbench() {
  const [projects, setProjects] = useState<ListedProduct[]>([]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [page, setPage] = useState<Page>("quick");
  const [modal, setModal] = useState<Modal>(null);
  const [auditMode, setAuditMode] = useState<"rules" | "llm">("rules");
  const [opening, setOpening] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [activeJob, setActiveJob] = useState<Job | null>(null);
  const [starting, setStarting] = useState(false);
  const [strategy, setStrategy] = useState<Strategy>("A");
  const [rewriteEntry, setRewriteEntry] = useState<RewriteEntry>();
  const [candidate, setCandidate] = useState<Candidate | null>(null);
  const [baseline, setBaseline] = useState<Audit | null>(null);
  const [comp, setComp] = useState<Audit | null>(null);
  const [jump, setJump] = useState<(Evidence & { nonce: number }) | null>(null);
  const [options, setOptions] = useState<Question[] | null>(null);
  const [deleting, setDeleting] = useState<ListedProduct | null>(null);
  const [captures, setCaptures] = useState<CaptureTask[]>([]);
  const [deleteCapture, setDeleteCapture] = useState<CaptureTask | null>(null);
  const [editorCapture, setEditorCapture] = useState<CaptureTask>();
  const [quickEntry, setQuickEntry] = useState<{ version: number; task?: CaptureTask }>({ version: 0 });

  const activePid = useRef<string | null>(null);
  const requestCount = useRef(0);
  const listRequest = useRef(0);
  const captureWrites = useRef(0);
  const candidateRequest = useRef(0);
  const lastAction = useRef<(() => void) | null>(null);
  const busy = starting || activeJob?.status === "queued" || activeJob?.status === "running";
  const pid = detail?.product.id;
  const marketplaces = config?.marketplaces ?? [defaultMarketplace];
  const base = pid ? pathFor(pid) : "";
  const heading = navigation.find(item => item.id === page)?.label;

  function navigate(destination: Page) {
    setRewriteEntry(undefined);
    setPage(destination);
    window.scrollTo({ top: 0 });
  }

  function openRewrite(entry?: RewriteEntry) {
    navigate("rewrite");
    setRewriteEntry(entry);
  }

  async function loadProjects() {
    const ticket = ++listRequest.current;
    const list = await api<ListedProduct[]>("/products");
    if (ticket === listRequest.current) setProjects(list);
    return list;
  }

  async function fetchCandidate(productId: string, candidateId: string, knownDetail?: Detail) {
    const ticket = ++candidateRequest.current;
    const root = pathFor(productId);
    const row = await api<Candidate>(`${root}/candidates/${candidateId}`);
    const info = knownDetail ?? await api<Detail>(root);
    const assessment = info.audits.find(item => item.candidate_id === candidateId && item.kind === "rescore");
    const [initial, compared] = await Promise.all([
      api<Audit>(`${root}/audits/${row.audit_id}`),
      assessment ? api<Audit>(`${root}/audits/${assessment.id}`) : Promise.resolve(null),
    ]);
    if (activePid.current !== productId || ticket !== candidateRequest.current) return;
    setCandidate(row); setStrategy(row.strategy); setBaseline(initial); setComp(compared);
  }

  function openListing(task?: CaptureTask) {
    setEditorCapture(task);
    setModal("listing");
  }

  async function openProduct(productId: string, capture?: CaptureTask) {
    const ticket = ++requestCount.current;
    setOpening(true); setError(""); setModal(null); setJump(null); setOptions(null);
    setCandidate(null); setBaseline(null); setComp(null); setActiveJob(null); setStrategy("A");
    activePid.current = productId;
    lastAction.current = null;
    try { localStorage.setItem("listing-active-product", productId); } catch {}
    try {
      const next = await api<Detail>(pathFor(productId));
      if (ticket !== requestCount.current) return;
      setDetail(next);
      setActiveJob(next.jobs.find(item => item.status === "queued" || item.status === "running") ?? null);
      navigate("diagnosis");
      if (capture) openListing(capture);
      if (next.candidates[0]) await fetchCandidate(productId, next.candidates[0].id, next);
    } catch (err) {
      if (ticket === requestCount.current) { setError(message(err)); setDetail(null); navigate("overview"); }
    } finally {
      if (ticket === requestCount.current) setOpening(false);
    }
  }

  async function init() {
    setOpening(true); setError("");
    try {
      const [list, settings] = await Promise.all([loadProjects(), api<Config>("/config")]);
      setConfig(settings);
      setAuditMode(settings.model ? "llm" : "rules");
      let previous: string | null = null;
      try { previous = localStorage.getItem("listing-active-product"); } catch {}
      const selected = list.find(item => item.id === previous);
      if (selected) await openProduct(selected.id);
      else { setPage("quick"); setOpening(false); }
    } catch (err) {
      setError(message(err)); setOpening(false); setPage("quick");
    }
  }

  useEffect(() => { void init(); }, []);
  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    const completed = new Set<string>();
    async function poll() {
      const version = captureWrites.current;
      try {
        const next = await api<CaptureTask[]>(capturePath);
        if (!active || version !== captureWrites.current) return;
        setCaptures(next);
        const fresh = next.filter(task => task.status === "succeeded" && !completed.has(task.id));
        for (const task of fresh) completed.add(task.id);
        if (fresh.some(task => task.product_id && task.product_id === activePid.current)) {
          await refresh(activePid.current!);
        } else if (fresh.length) await loadProjects();
      } catch {
        // Preserve visible tasks while the local server is reconnecting.
      } finally {
        if (active) timer = window.setTimeout(poll, 1200);
      }
    }
    void poll();
    return () => { active = false; window.clearTimeout(timer); };
  }, []);
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 3600);
    return () => window.clearTimeout(timer);
  }, [toast]);

  async function refresh(productId: string) {
    const [next] = await Promise.all([api<Detail>(pathFor(productId)), loadProjects()]);
    if (activePid.current === productId) setDetail(next);
    return next;
  }

  async function startCapture(asin: string, market: string, productId?: string, provider: CaptureProvider = "auto") {
    const task = await api<CaptureTask>(capturePath, "POST", { asin, market, product_id: productId, provider });
    ++captureWrites.current;
    setCaptures(current => [task, ...current.filter(item => item.id !== task.id)]);
    return task;
  }

  function openCapture(task: CaptureTask) {
    if (task.product_id) { void openProduct(task.product_id, task); return; }
    ++requestCount.current;
    setOpening(false); setModal(null); setError("");
    setQuickEntry(current => ({ version: current.version + 1, task }));
    navigate("quick");
  }

  async function removeCapture(id: string) {
    await api<void>(`${capturePath}/${id}`, "DELETE");
    ++captureWrites.current;
    setCaptures(current => current.filter(item => item.id !== id));
    setDeleteCapture(null);
    setToast("已删除采集工作流。");
  }

  function refreshLibrary() { void loadProjects().catch(err => setError(message(err))); }

  async function applyJob(result: JobResult, productId: string) {
    if (activePid.current !== productId) return;
    if (result.kind === "questions") {
      setOptions(result.questions);
      return;
    }
    const updated = await refresh(productId);
    if (activePid.current !== productId) return;
    if (result.kind === "audit") {
      if (updated.latest_audit?.id !== result.id) {
        const selected = await api<Audit>(`${pathFor(productId)}/audits/${result.id}`);
        if (activePid.current === productId && selected.snapshot_id === updated.snapshot.id) {
          setDetail(value => value && ({ ...value, latest_audit: selected }));
        }
      }
      setToast("评分完成。");
    } else if (result.kind === "rewrite") {
      await fetchCandidate(productId, result.id, updated);
      setToast("候选稿已生成，可逐条审核。");
    } else {
      const resultAudit = await api<Audit>(`${pathFor(productId)}/audits/${result.id}`);
      if (resultAudit.data.candidate_id) await fetchCandidate(productId, resultAudit.data.candidate_id, updated);
      setToast("复评完成。已沿用基准评分的规则、事实版本和问句。");
    }
  }

  const jobId = activeJob?.id;
  useEffect(() => {
    if (!jobId) return;
    let current = true;
    let timer: number | undefined;
    async function poll() {
      try {
        const updated = await api<Job>(`/jobs/${jobId}`);
        if (!current) return;
        setActiveJob(updated);
        if (updated.status === "succeeded" && updated.result) {
          await applyJob(updated.result, updated.product_id);
          if (current) setActiveJob(null);
        } else if (updated.status === "queued" || updated.status === "running") {
          timer = window.setTimeout(poll, 550);
        }
      } catch (err) {
        if (current) {
          setError(message(err));
          setActiveJob(null);
        }
      }
    }
    timer = window.setTimeout(poll, 150);
    return () => { current = false; window.clearTimeout(timer); };
  }, [jobId]);

  async function kick(endpoint: string, payload: unknown, productId: string) {
    const retry = () => { void kick(endpoint, payload, productId); };
    lastAction.current = retry;
    setStarting(true); setError("");
    try {
      const started = await api<JobStart>(endpoint, "POST", payload);
      if (activePid.current !== productId) return;
      if (started.job) setActiveJob(started.job);
      if (started.cached) {
        setActiveJob(null);
        await applyJob(started.cached, productId);
      }
    } catch (err) {
      if (activePid.current === productId) setError(message(err));
    } finally {
      setStarting(false);
    }
  }

  async function cancelJob() {
    if (!activeJob) return;
    try { setActiveJob(await api<Job>(`/jobs/${activeJob.id}/cancel`, "POST")); }
    catch (err) { setError(message(err)); }
  }

  function locate(item: Evidence) {
    setJump({ ...item, nonce: Date.now() });
    setPage("diagnosis");
  }

  async function saveProduct(body: ProductInput) {
    if (modal === "product" && pid) {
      await api<Product>(base, "PUT", body);
      setModal(null);
      await refresh(pid);
      setToast("商品资料已保存。");
      return;
    }
    const created = await api<Product>("/products", "POST", body);
    await loadProjects();
    await openProduct(created.id);
    openListing();
  }

  async function saveListing(body: SnapshotInput) {
    if (!pid) return;
    await api(`${base}/snapshots`, "POST", body);
    await refresh(pid);
    setModal(null);
    setToast("原文快照已保存。");
  }

  async function saveContext(next: Context) {
    if (!pid) return;
    await api(`${base}/context`, "PUT", next);
    await refresh(pid);
    setToast("新版本已保存。");
  }

  async function saveDraft(rows: ReviewLine[], note: string, rescore: boolean) {
    if (!candidate || !pid) return;
    const next = await api<Candidate>(`${base}/candidates/${candidate.id}/review`, "POST", { lines: rows, note });
    const updated = await refresh(pid);
    await fetchCandidate(pid, next.id, updated);
    if (rescore) await kick(`${base}/candidates/${next.id}/rescore`, undefined, pid);
    else setToast("审核版本已保存。");
  }

  async function showSavedFieldDraft(next: Candidate) {
    if (!pid) return;
    const updated = await refresh(pid);
    await fetchCandidate(pid, next.id, updated);
    if (activePid.current === pid) setToast("字段改写已保存，可在新旧文案对照中审核。");
  }

  async function publication(publishedAt: string, note: string) {
    if (!pid || !candidate) return;
    await api(`${base}/candidates/${candidate.id}/publication`, "POST", { published_at: publishedAt, note });
    await refresh(pid);
    setToast("上线记录已保存。");
  }

  async function switchStrategy(value: Strategy) {
    if (!detail) return;
    setStrategy(value);
    const version = detail.candidates.find(item => item.strategy === value);
    if (!version) { ++candidateRequest.current; setCandidate(null); setBaseline(null); setComp(null); return; }
    setCandidate(null);
    try { await fetchCandidate(detail.product.id, version.id, detail); } catch (err) { setError(message(err)); }
  }

  function showCandidate(productId: string, candidateId: string) {
    void fetchCandidate(productId, candidateId, detail ?? undefined).catch(err => setError(message(err)));
  }

  function runAudit(mode: "rules" | "llm") {
    if (!pid) return;
    setModal(null);
    void kick(`${base}/audit`, { mode }, pid);
  }

  function openAudit() {
    if (config?.model) setModal("audit");
    else runAudit("rules");
  }

  async function saveQuick(product: ProductInput, fields: Listing) {
    const source = await api<Product>("/listing-projects", "POST", { product, listing: { fields, note: "从快速评分保存文案" } });
    await loadProjects();
    await openProduct(source.id);
    setToast("文案已保存到 ASIN 库。");
  }

  async function deleteProject(id: string) {
    await api<void>(pathFor(id), "DELETE");
    ++listRequest.current;
    setProjects(current => current.filter(item => item.id !== id));
    if (activePid.current === id) {
      ++requestCount.current;
      activePid.current = null;
      lastAction.current = null;
      setDetail(null); setActiveJob(null); setCandidate(null); setBaseline(null); setComp(null); setOpening(false);
      try { localStorage.removeItem("listing-active-product"); } catch {}
    }
    setDeleting(null);
    setToast("已删除 ASIN 项目。");
  }

  function loadDemo() {
    void api<Product>("/demo", "POST").then(async item => { await loadProjects(); await openProduct(item.id); })
      .catch(err => setError(message(err)));
  }

  return <div className="workspace">
    <aside className="sidebar">
      <button className="brand" type="button" onClick={() => navigate("quick")}>
        <span className="brand-icon"><ClipboardCheck size={22} strokeWidth={2} /></span>
        <span><strong>Listing Studio</strong><small>Amazon Listing 工作台</small></span>
      </button>
      <div className="aside-top"><Button className="new-asin" onClick={() => setModal("new")}><Plus size={18} />新建项目</Button></div>
      <nav aria-label="工作台导航">{navigation.map(item => <button key={item.id} type="button"
        className={page === item.id ? "active" : ""} aria-current={page === item.id ? "page" : undefined}
        disabled={!detail && item.id !== "overview" && item.id !== "quick" && item.id !== "rewrite"} onClick={() => navigate(item.id)}>
        <item.icon size={18} strokeWidth={1.8} /><span>{item.label}</span>{page === item.id && <span className="nav-indicator" />}
      </button>)}</nav>
      <RecentCaptures items={captures} onOpen={openCapture} onDelete={setDeleteCapture} />
      <div className="aside-bottom">
        <button type="button" className="help-button" onClick={() => setModal("help")}><CircleHelp size={17} />使用说明</button>
        <div className="sidebar-context"><span><Database size={15} /></span><div><strong>Listing 工作区</strong><small>Amazon 多站点 · V1.3.2</small></div></div>
      </div>
    </aside>
    <main className="workspace-main">
      <div className="topbar">
        <div className="breadcrumb"><button type="button" onClick={() => navigate("overview")}>工作台</button>
          <ChevronRight size={14} /><span>{heading}</span></div>
        <div className="top-meta"><span className="market"><Globe2 size={15} /> <span>Amazon {detail && page !== "quick" && page !== "overview" ? detail.product.market : "多站点"}</span></span>
          <span className="mode"><span className="mode-dot" />{config?.model ? "AI 可用" : "本地规则"}</span></div>
      </div>
      <div className="page">
        {error && <div className="global-notice"><Notice warn>{error}</Notice><Button className="icon-close" kind="quiet"
          aria-label="关闭提示" onClick={() => setError("")}><X size={16} /></Button></div>}
        {activeJob && activeJob.status !== "succeeded" && <div className={`job-strip ${activeJob.status === "failed" ? "job-error" : ""}`}>
          <div className="row">{busy ? <LoaderCircle className="spinner" size={18} /> : <Activity size={17} />}
            <div><strong>{activeJob.detail}</strong>{busy && <small>{activeJob.progress}%</small>}</div></div>
          {busy ? <Button kind="quiet" onClick={() => void cancelJob()}>取消任务</Button> : <div className="row">
            {lastAction.current && <Button kind="quiet" onClick={() => lastAction.current?.()}>重试</Button>}
            <Button kind="quiet" aria-label="隐藏任务记录" onClick={() => setActiveJob(null)}><X size={16} /></Button></div>}
        </div>}
        {opening ? <div className="panel loading" role="status"><LoaderCircle className="spinner" size={21} />正在加载项目…</div> :
          page === "quick" ? <QuickView key={quickEntry.version} config={config} captures={captures} initialCapture={quickEntry.task}
            onStartCapture={startCapture} onStored={refreshLibrary} onSave={saveQuick} onDemo={loadDemo} onOverview={() => navigate("overview")} /> :
          page === "rewrite" && !detail ? <>
            <Header title="AI 改写" subtitle="粘贴 Listing 原文，识别字段后生成建议文案。" />
            <SkillRewrite model={config?.model} marketplaces={marketplaces} defaultMarket={config?.default_market ?? "US"} />
          </> : page === "overview" || !detail ? <Overview projects={projects} onCreate={() => setModal("new")} onDemo={loadDemo}
            onOpen={productId => void openProduct(productId)} onDelete={setDeleting} /> : <>
          {page === "diagnosis" && <Diagnosis key={detail.snapshot.id} detail={detail} jump={jump} busy={busy} aiEnabled={!!config?.model}
            onAudit={openAudit} onEdit={() => openListing()} onEditProduct={() => setModal("product")}
            onQuestions={() => navigate("questions")} onRewrite={openRewrite} />}
          {page === "facts" && <FactsView key={detail.product.id} detail={detail} save={saveContext} />}
          {page === "questions" && <QuestionsView key={detail.product.id} detail={detail} save={saveContext} locate={locate}
            llmEnabled={!!config?.model} busy={busy} onSuggest={() => void kick(`${base}/suggest-questions`, undefined, detail.product.id)}
            onTemplate={() => { void api<Question[]>(`${base}/question-template`, "POST").then(setOptions).catch(err => setError(message(err))); }}
            options={options} clearOptions={() => setOptions(null)} />}
          {page === "rewrite" && <RewriteView detail={detail} strategy={strategy} setStrategy={value => void switchStrategy(value)}
            candidate={candidate} baseline={baseline} comparison={comp} busy={busy}
            fieldRewrite={<SkillRewrite key={detail.snapshot.id + detail.context.id} detail={detail} model={config?.model}
              marketplaces={marketplaces} defaultMarket={detail.product.market} strategy={strategy} entry={rewriteEntry} onSaved={showSavedFieldDraft} />}
            onGenerate={() => { if (detail.latest_audit) void kick(`${base}/rewrite`, { strategy, audit_id: detail.latest_audit.id }, detail.product.id); }}
            onCandidate={identifier => showCandidate(detail.product.id, identifier)} onSave={saveDraft} onPublication={publication}
            onNotice={setToast} onFacts={() => navigate("facts")} onDiagnosis={() => navigate("diagnosis")} />}
          {page === "history" && <HistoryView key={detail.product.id} detail={detail} onRestore={async identifier => {
            await api(`${base}/snapshots/${identifier}/restore`, "POST");
            await refresh(detail.product.id); setToast("历史快照已恢复为新版本。");
          }} onCandidate={row => { setStrategy(row.strategy); navigate("rewrite"); showCandidate(detail.product.id, row.id); }} />}
        </>}
        <footer className="workbench-foot"><span>Amazon Listing 工作台</span><span>内容诊断</span></footer>
      </div>
    </main>
    {deleting && <DeleteProjectDialog item={deleting} onDelete={deleteProject} onClose={() => setDeleting(null)} />}
    {deleteCapture && <DeleteCaptureDialog task={deleteCapture} onDelete={removeCapture} onClose={() => setDeleteCapture(null)} />}
    {(modal === "new" || modal === "product") && <ProductEditor marketplaces={marketplaces} detail={modal === "product" ? detail ?? undefined : undefined}
      onClose={() => setModal(null)} onSave={saveProduct} />}
    {modal === "listing" && detail && <ListingEditor detail={detail} playwright={!!config?.amazon_playwright} nexscope={!!config?.nexscope} marketplaces={marketplaces} captures={captures}
      initialCapture={editorCapture} onStartCapture={startCapture} onStored={() => { void refresh(detail.product.id).catch(err => setError(message(err))); }}
      onClose={() => setModal(null)} onSave={saveListing} />}
    {modal === "audit" && <Dialog title="运行 Listing 评分" onClose={() => setModal(null)}>
      <div className="dialog-form">
        <label className="mode-option"><input type="radio" checked={auditMode === "llm"} onChange={() => setAuditMode("llm")} />
          <span><strong>AI 语义评分</strong><small>向已配置的模型发送当前 Listing、问句和已核实事实。</small><small>{config?.model}</small></span></label>
        <label className="mode-option"><input type="radio" checked={auditMode === "rules"} onChange={() => setAuditMode("rules")} />
          <span><strong>本地规则评分</strong><small>依据字段规则、已核实措辞和问句核对词组。</small></span></label>
        <footer className="dialog-actions"><Button onClick={() => setModal(null)}>取消</Button>
          <Button kind="primary" onClick={() => runAudit(auditMode)}>开始评分</Button></footer>
      </div>
    </Dialog>}
    {modal === "help" && <Dialog title="工作台使用说明" onClose={() => setModal(null)}>
      <div className="dialog-form help-content"><ol>
        <li>粘贴商品链接时自动识别站点。仅输入 ASIN 时请先选站点。「获取商品资料」会优先读同站点的归档。</li>
        <li>成功采集或导入的 ASIN 会入库。粘贴文案的快速评分可按需保存。</li>
        <li>在事实库录入可追溯的英文措辞。在问句模块导入真实询问或编辑类目模板。</li>
        <li>运行评分，点击问题的字段名定位证据。</li>
        <li>选择一个改写策略，逐条接受、拒绝或编辑后保存审核。复评会沿用基准评分配置。</li>
        <li>审核通过后复制文案，在 Seller Central 手动上架，并回来记录时间。</li>
      </ol><p>可以先用演示项目了解评分和改写流程。</p>
      <div className="row wrap"><Button onClick={() => { setModal(null); loadDemo(); }}>打开演示项目</Button></div></div>
    </Dialog>}
    {toast && <div className="toast" role="status"><ClipboardCheck size={18} /><span>{toast}</span>
      <Button kind="quiet" aria-label="关闭通知" onClick={() => setToast("")}><X size={16} /></Button></div>}
  </div>;
}
