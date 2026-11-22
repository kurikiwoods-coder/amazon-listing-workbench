"use client";

import { useState, type FormEvent } from "react";
import { ArrowLeft, BookOpen, ChevronRight, ClipboardPaste, FileUp, Globe2, LoaderCircle, Play, Save } from "lucide-react";
import { Diagnosis } from "./diagnosis";
import { ProductEditor } from "./editors";
import {
  answerStatus, api, charCount, defaultMarketplace, fields, listingFileTypes, marketplaceFromLink, message, publishedCopy, type ImageField,
  type CaptureProvider, type CaptureTask, type Config, type DiagnosisDetail, type Evidence, type Listing, type ListingPreview, type ProductInput, type QuickInput, type StartCapture,
} from "./model";
import { Badge, Button, Dialog, FormField, Header, MarketplaceSelect, Notice } from "./ui";
import { activeCapture, BrowserPageImport, captureHasPreview, CaptureProgress, loadAmazonMirror, needsBrowserVerification, SourcePreview, useCapturePreview } from "./workflows";
import { ScreenshotSelect } from "./public-copy";

const example = [
  "Title: DemoWorks Nutrition Food Scale",
  "Item Highlights: Stainless steel, Meal prep",
  "About this item\n- Save a food in the app during first setup.\n- After setup, select a saved food on the scale to view calories and protein with the app closed.",
  "Product Description: Weigh a breakfast portion and view the nutrients for a saved food.",
  "A+: Select the saved food before weighing the portion.",
  "Product Information:\nMaterial: Stainless steel\nCapacity: 5 kg",
].join("\n\n");

export function QuickView({ config, captures, initialCapture, onStartCapture, onStored, onSave, onDemo, onOverview }: {
  config: Config | null; onSave: (product: ProductInput, fields: Listing) => Promise<void>;
  captures: CaptureTask[]; initialCapture?: CaptureTask; onStartCapture: StartCapture; onStored: () => void;
  onDemo: () => void; onOverview: () => void;
}) {
  const [data, setData] = useState<QuickInput>({ text: "", asin: initialCapture?.asin ?? "",
    market: initialCapture?.market ?? config?.default_market ?? defaultMarketplace.code, category: "", brand: "", product_type: "unknown", mode: "rules" });
  const [busy, setBusy] = useState<CaptureProvider | "upload" | "mirror" | "score" | null>(null);
  const [error, setError] = useState("");
  const [imageField, setImageField] = useState<ImageField>("auto");
  const [imported, setImported] = useState<ListingPreview | null>(null);
  const [captureId, setCaptureId] = useState(initialCapture?.id);
  const capture = captures.find(row => row.id === captureId);
  const loaded = useCapturePreview(capture);
  const preview = loaded?.preview ?? imported;
  const marketplaces = config?.marketplaces ?? [defaultMarketplace];
  const linkMarket = marketplaceFromLink(data.asin, marketplaces);
  const market = linkMarket?.code ?? data.market;
  const [result, setResult] = useState<DiagnosisDetail | null>(null);
  const [showSave, setShowSave] = useState(false);
  const [showQuestions, setShowQuestions] = useState(false);
  const [jump, setJump] = useState<(Evidence & { nonce: number }) | null>(null);
  const hasCopy = preview ? publishedCopy.some(key => preview.fields[key]?.text) : !!data.text.trim();
  const canCapture = !busy && !activeCapture(capture) && !!data.asin.trim();

  function clearPreview() { setImported(null); setCaptureId(undefined); setError(""); }

  async function collect(provider: CaptureProvider) {
    if (!canCapture || (provider === "nexscope" && !config?.nexscope)
      || (provider === "playwright" && !config?.amazon_playwright)) return;
    setBusy(provider); setError("");
    try {
      const task = await onStartCapture(data.asin, market, undefined, provider);
      setCaptureId(task.id);
      setImported(null);
      setData(current => ({ ...current, asin: task.asin, market: task.market }));
    } catch (err) { setError(message(err)); } finally { setBusy(null); }
  }

  async function upload(file: File) {
    setBusy("upload"); setError("");
    const form = new FormData();
    form.set("file", file);
    try {
      const query = new URLSearchParams({ asin: data.asin.trim(), market, image_field: imageField });
      const next = await api<ListingPreview>(`/listing-import?${query}`, "POST", form);
      setCaptureId(undefined);
      setImported(next);
      setData(current => ({ ...current, asin: next.asin, market: next.market }));
      if (next.product_id) onStored();
    } catch (err) { setError(message(err)); } finally { setBusy(null); }
  }

  async function readMirror() {
    setBusy("mirror"); setError("");
    try {
      const next = await loadAmazonMirror(data.asin.trim(), market);
      setCaptureId(undefined); setImported(next);
      setData(current => ({ ...current, asin: next.asin, market: next.market }));
    } catch (err) { setError(message(err)); } finally { setBusy(null); }
  }

  async function run(event: FormEvent) {
    event.preventDefault();
    if (!hasCopy || busy) return;
    setBusy("score"); setError("");
    const input = preview ? { ...data, market: preview.market, text: "", brand: data.brand || preview.brand,
      fields: preview.fields } : { ...data, market };
    try {
      setResult(await api<DiagnosisDetail>("/quick-audit", "POST", input));
      setJump(null);
      window.scrollTo({ top: 0 });
    } catch (err) { setError(message(err)); } finally { setBusy(null); }
  }

  if (result) {
    const { asin, name, sku, market, brand, category, product_type } = result.product;
    return <>
      <Diagnosis detail={result} jump={jump} aiEnabled={!!config?.model} onQuestions={() => setShowQuestions(true)}
        quickActions={<><Button onClick={() => setResult(null)}><ArrowLeft size={16} />返回输入</Button>
          <Button kind="primary" onClick={() => setShowSave(true)}><Save size={16} />保存文案到 ASIN 库</Button></>} />
      {showSave && <ProductEditor marketplaces={marketplaces} initial={{ asin, name, sku, market, brand, category, product_type }} onClose={() => setShowSave(false)}
        onSave={product => onSave(product, result.snapshot.fields)} />}
      {showQuestions && <Dialog wide title="买家问题覆盖" onClose={() => setShowQuestions(false)}>
        <div className="question-list">{result.latest_audit?.data.result.questions.map(question => <details className="question-row" key={question.id}>
          <summary><span lang="en">{question.text}</span><Badge>{answerStatus[question.status]}</Badge><ChevronRight size={16} /></summary>
          <div className="question-answer"><p>{question.detail}</p>
            {question.evidence.map((item, index) => <button key={index} type="button" className="evidence-button" onClick={() => {
              setJump({ ...item, nonce: Date.now() }); setShowQuestions(false);
            }}><span>{fields[item.field]}</span><q lang="en">{item.quote}</q></button>)}
          </div>
        </details>)}</div>
      </Dialog>}
    </>;
  }

  return <>
    <Header title="快速评分" subtitle="粘贴文案、输入 ASIN 或上传资料。可以从一个模块开始评分。"
      actions={<Button onClick={onOverview}>打开 ASIN 库 <ChevronRight size={15} /></Button>} />
    {error && <Notice warn>{error}</Notice>}
    <div className="quick-layout">
      <section className="panel quick-main">
        <header className="panel-head"><div className="row"><ClipboardPaste size={20} /><h2>{preview ? "Listing 预览" : "Listing 原文"}</h2></div>
          {preview ? <Button kind="quiet" disabled={!!busy} onClick={clearPreview}>改用粘贴</Button> :
            <Button kind="quiet" disabled={!!busy} onClick={() => setData({ ...data, text: example, asin: "", brand: "DemoWorks",
              category: "", product_type: "unknown" })}>填入示例</Button>}</header>
        <form className="dialog-form" onSubmit={run}>
          {preview ? <>
            <SourcePreview preview={preview} />
            {!hasCopy && <p>请通过粘贴或上传补充标题、要点或商品描述，再运行评分。</p>}
          </> : <>
            <FormField name="粘贴商品页文字" hint="接受整段原文，也能识别 Title、About this item、Item Highlights 和 A+ 等字段标题。">
              <textarea required rows={10} value={data.text} maxLength={32000} placeholder="粘贴英文商品标题、五点、详情或任何已有文案…"
                onChange={event => setData({ ...data, text: event.target.value })} />
            </FormField>
            <div className="quick-char mono">{charCount(data.text)} / 32,000 字符</div>
          </>}
          <details className="quick-options">
            <summary>补充商品资料（选填）</summary>
            <div className="form-grid">
              <FormField name="品牌"><input maxLength={120} value={data.brand} onChange={event => setData({ ...data, brand: event.target.value })} /></FormField>
              <FormField name="类目（自由填写）"><input maxLength={120} value={data.category} onChange={event => setData({ ...data, category: event.target.value })} /></FormField>
              <FormField name="产品类型"><select value={data.product_type} onChange={event => setData({ ...data, product_type: event.target.value as QuickInput["product_type"] })}>
                <option value="unknown">未指定</option><option value="non_media">非媒体类目</option><option value="media">媒体类目</option>
              </select></FormField>
            </div>
          </details>
          <footer className="quick-actions row spread wrap">
            {config?.model ? <label className="checkbox-label"><input type="checkbox" checked={data.mode === "llm"} onChange={event =>
              setData({ ...data, mode: event.target.checked ? "llm" : "rules" })} />使用已配置的 AI 语义检查</label> : <span className="muted">本地规则评分</span>}
            <Button type="submit" kind="primary" disabled={!!busy || !hasCopy}>
              {busy === "score" ? <LoaderCircle size={16} className="spinner" /> : <Play size={16} />}
              {busy === "score" ? "正在评分…" : preview ? "原文已核对，开始评分" : "粘贴并评分"}
            </Button>
          </footer>
        </form>
      </section>
      <div className="quick-side">
        <section className="panel">
          <header className="panel-head"><div className="row"><Globe2 size={19} /><h2>采集或导入</h2></div></header>
          <form className="dialog-form" onSubmit={event => {
            event.preventDefault(); void collect("auto");
          }}>
            <MarketplaceSelect value={market} marketplaces={marketplaces} disabled={!!busy || !!linkMarket}
              hint={linkMarket ? "已从商品链接识别站点。" : "只填 ASIN 时使用这里选择的站点。"}
              onChange={next => { setData({ ...data, market: next }); clearPreview(); }} />
            <FormField name="ASIN 或 Amazon 商品详情链接" hint="上传多商品文件前，可在这里填写要评分的 ASIN。">
              <input maxLength={2048} autoComplete="off" disabled={!!busy} placeholder="例如 B0FKBD63D4" value={data.asin}
                onChange={event => { setData({ ...data, asin: event.target.value }); clearPreview(); }} />
            </FormField>
            <Button type="submit" kind="primary" disabled={!canCapture}>
              {busy === "auto" && <LoaderCircle size={16} className="spinner" />}{busy === "auto" ? "正在查找资料…" : "获取商品资料（优先归档）"}
            </Button>
            {config?.amazon_playwright && <Button disabled={!canCapture} onClick={() => void collect("playwright")}>
              {busy === "playwright" ? "正在排队…" : "实时刷新商品页"}
            </Button>}
            {config?.nexscope && <Button disabled={!canCapture} onClick={() => void collect("nexscope")}>
              {busy === "nexscope" ? "正在排队…" : "用 Nexscope 刷新商品资料"}
            </Button>}
            <CaptureProgress task={capture} error={loaded?.error} loading={captureHasPreview(capture) && !loaded} />
            {market === "US" && (needsBrowserVerification(capture) || config?.nexscope
              || (preview?.archived_at && !preview.reference_url)) &&
              <Button disabled={!canCapture} onClick={() => void collect("public_archive")}>
                {busy === "public_archive" ? "正在排队…" : "查公开归档"}
              </Button>}
            <BrowserPageImport input={data.asin} market={market} marketplaces={marketplaces} blocked={needsBrowserVerification(capture)} />
            <Button disabled={!!busy || !data.asin.trim()} onClick={() => void readMirror()}>
              {busy === "mirror" ? "正在读取归档…" : "读取本地商品归档"}
            </Button>
            {!config?.amazon_playwright && !config?.nexscope && market !== "US" &&
              <small className="muted">可上传已有网页或商品资料。在线采集的启用方法见 README。</small>}
            <label className={`button button-secondary file-button${busy ? " file-button-busy" : ""}`}>
              <FileUp size={16} />{busy === "upload" ? "正在读取…" : "上传网页或 Listing 文件"}
              <input type="file" disabled={!!busy} accept={listingFileTypes} aria-label="上传网页或 Listing 文件"
                onChange={event => { const file = event.currentTarget.files?.[0]; if (file) void upload(file); event.currentTarget.value = ""; }} />
            </label>
            <small className="muted">网页最多 {config?.max_webpage_mb ?? 32} MB，表格、文本或截图最多 {config?.max_upload_mb ?? 8} MB。</small>
            <ScreenshotSelect value={imageField} onChange={setImageField} disabled={!!busy} />
          </form>
        </section>
        <section className="panel quick-guide">
        <header className="panel-head"><div className="row"><BookOpen size={19} /><h2>评分包含什么</h2></div></header>
        <div className="quick-guide-body">
          <strong>按已有模块评分</strong><p>只有五点也能评分。多个模块分别打分，汇总显示平均分。</p>
          <strong>改写意见</strong><p>查看原文、建议怎么改，以及怎样回答买家问题。</p>
          <Button kind="quiet" onClick={onDemo}>打开营养秤演示项目 <ChevronRight size={15} /></Button>
        </div>
        </section>
      </div>
    </div>
  </>;
}
