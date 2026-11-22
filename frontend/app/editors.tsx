"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Check, ChevronRight, FileUp, Globe2, ImageIcon, LoaderCircle, PackagePlus, Save } from "lucide-react";
import {
  api, charCount, confirmPreview, fieldKeys, fields, listingFileTypes, marketplaceFromLink, message, paragraphLines, pathFor, sourceStatus, type CaptureProvider, type CaptureTask, type Detail, type FieldKey, type ImageField, type ImageRecord,
  type Imported, type Listing, type Marketplace, type ProductInput, type SnapshotInput, type SourceField, type StartCapture, type State,
} from "./model";
import { Badge, Button, Dialog, FormField, MarketplaceSelect, Notice, StateBadge } from "./ui";
import { activeCapture, BrowserPageImport, captureHasPreview, CaptureProgress, loadAmazonMirror, needsBrowserVerification, SourcePreview, useCapturePreview } from "./workflows";
import { PageNotes, ScreenshotSelect } from "./public-copy";

const blankProduct: ProductInput = { asin: "", name: "", sku: "", market: "US", brand: "", category: "", product_type: "unknown" };
const example = {
  title: "粘贴商品标题的完整原文", highlights: "粘贴 Item Highlights", bullets: "每行一条，保留原文",
  aplus: "按段落粘贴 A+ 文案", raw_text: "粘贴商品描述，或保留尚未分段的 Listing",
  public_specs: "从商品页的 Product information 粘贴公开规格", attributes: "Capacity: 5,000 g\nUnits: g, oz",
  search_terms: "一行一个词组，或使用逗号分隔", keywords: "一行一个目标词组", price: "$39.99",
  reviews: "粘贴评论摘要，保留来源", qa: "粘贴买家问答原文", competitors: "输入竞品 ASIN 或现有问句",
} as const;

export function ProductEditor({ detail, initial, marketplaces, onSave, onClose }: {
  detail?: Detail; initial?: ProductInput; marketplaces: Marketplace[]; onSave: (body: ProductInput) => Promise<void>; onClose: () => void;
}) {
  const product = detail?.product;
  const [data, set] = useState<ProductInput>(product
    ? { asin: product.asin, name: product.name, sku: product.sku, market: product.market, brand: product.brand,
        category: product.category, product_type: product.product_type } : { ...blankProduct, ...initial });
  const [err, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const linkMarket = marketplaceFromLink(data.asin, marketplaces);
  const market = linkMarket?.code ?? data.market;
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true); setError("");
    try { await onSave({ ...data, market }); } catch (error) { setError(message(error)); } finally { setBusy(false); }
  }
  return <Dialog title={detail ? "编辑商品资料" : initial ? "保存文案到 ASIN 库" : "新建 Listing 项目"} onClose={onClose}>
    <form onSubmit={submit} className="dialog-form">
      {err && <Notice warn>{err}</Notice>}
      {initial && <p className="muted">保存文案快照。已有的 ASIN 会新增历史版本，保存后可在项目里运行评分。</p>}
      <MarketplaceSelect value={market} marketplaces={marketplaces} disabled={!!product?.asin || !!initial?.asin || !!linkMarket}
        hint={linkMarket ? "已从商品链接识别站点。" : undefined} onChange={next => set({ ...data, market: next })} />
      <FormField name="ASIN 或 Amazon 商品详情链接（选填）" hint={detail || initial ? undefined : "Listing 原文可在下一步粘贴或上传。"}>
        <input maxLength={2048} readOnly={!!initial?.asin} disabled={!!product?.asin} value={data.asin} placeholder="例如 B0F1N26FT3"
          onChange={event => set({ ...data, asin: event.target.value })} />
      </FormField>
      <FormField name="项目名称"><input required autoComplete="off" maxLength={140} value={data.name} placeholder="例如 美国站营养秤"
        onChange={event => set({ ...data, name: event.target.value })} /></FormField>
      <div className="form-grid">
        <FormField name="品牌"><input maxLength={120} value={data.brand} placeholder="例如 COSORI" onChange={event => set({ ...data, brand: event.target.value })} /></FormField>
        <FormField name="SKU"><input maxLength={100} value={data.sku} onChange={event => set({ ...data, sku: event.target.value })} /></FormField>
      </div>
      <div className="form-grid">
        <FormField name="类目（选填）"><input maxLength={120} value={data.category} placeholder="例如 Kitchen & Dining"
          onChange={event => set({ ...data, category: event.target.value })} /></FormField>
        <FormField name="产品类型"><select value={data.product_type} onChange={event => set({ ...data, product_type: event.target.value as ProductInput["product_type"] })}>
          <option value="unknown">未指定</option><option value="non_media">非媒体类目</option><option value="media">媒体类目</option>
        </select></FormField>
      </div>
      <footer className="dialog-actions"><Button onClick={onClose}>取消</Button>
        <Button type="submit" kind="primary" disabled={busy}><PackagePlus size={16} />{detail ? "保存资料" : initial ? "确认保存文案" : "创建并录入 Listing"}</Button></footer>
    </form>
  </Dialog>;
}

export function ListingEditor({ detail, playwright, nexscope, marketplaces, captures, initialCapture, onStartCapture, onStored, onSave, onClose }: {
  detail: Detail; playwright: boolean; nexscope: boolean; marketplaces: Marketplace[]; captures: CaptureTask[]; initialCapture?: CaptureTask;
  onStartCapture: StartCapture; onStored: () => void; onSave: (body: SnapshotInput) => Promise<void>; onClose: () => void;
}) {
  const [data, set] = useState(detail.snapshot.fields);
  const [baseSnapshotId] = useState(detail.snapshot.id);
  const [asin, setAsin] = useState(detail.product.asin || initialCapture?.asin || "");
  const [registeredAsin, setRegisteredAsin] = useState(detail.product.asin);
  const [selectedMarket, setSelectedMarket] = useState(initialCapture?.market ?? detail.product.market);
  const [captureId, setCaptureId] = useState(initialCapture?.id);
  const [starting, setStarting] = useState<CaptureProvider | null>(null);
  const capture = captures.find(row => row.id === captureId);
  const loaded = useCapturePreview(capture);
  const preview = loaded?.preview;
  const [tab, select] = useState<FieldKey>("title");
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [imported, setImported] = useState<Imported | null>(null);
  const [images, setImages] = useState<ImageRecord[]>([]);
  const [oldImage, setOldImage] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [imageField, setImageField] = useState<ImageField>("auto");
  const [readingMirror, setReadingMirror] = useState(false);
  const base = pathFor(detail.product.id);
  const selected = data[tab];
  const working = busy || uploading || !!starting || readingMirror;
  const knownAsin = detail.product.asin || registeredAsin;
  const target = knownAsin || asin;
  const linkMarket = marketplaceFromLink(target, marketplaces);
  const market = detail.product.asin ? detail.product.market : (linkMarket?.code ?? selectedMarket);
  const canCapture = !!target.trim() && !working && !activeCapture(capture);
  const historicalPreview = !!(preview?.archived_at || preview?.source_observed_at);
  const historicalImport = !!(imported?.archived_at || imported?.source_observed_at);

  useEffect(() => {
    api<ImageRecord[]>(`${base}/imports`).then(setImages).catch(() => {});
  }, [base]);

  useEffect(() => {
    if (capture?.status === "succeeded") { setRegisteredAsin(capture.asin); setSelectedMarket(capture.market); }
  }, [capture?.status, capture?.asin, capture?.market]);

  async function collect(provider: CaptureProvider) {
    if (!canCapture || (provider === "playwright" && !playwright) || (provider === "nexscope" && !nexscope)) return;
    setStarting(provider); setError("");
    try {
      const task = await onStartCapture(target, market, detail.product.id, provider);
      setCaptureId(task.id);
      setAsin(task.asin);
      setSelectedMarket(task.market);
      setImported(null);
    } catch (err) { setError(message(err)); } finally { setStarting(null); }
  }

  function edit(value: Partial<SourceField>) {
    set(current => {
      const next = { ...current[tab], ...value };
      if (value.text !== undefined && value.text !== current[tab].text) {
        const old = paragraphLines(current[tab].text);
        const byText = new Map(current[tab].page_lines?.map(item => [old[item.line], item]));
        next.page_lines = paragraphLines(value.text).flatMap((text, line) => {
          const item = byText.get(text);
          return item ? [{ ...item, line }] : [];
        });
        next.notes = undefined;
      }
      return { ...current, [tab]: next };
    });
  }

  function setText(text: string) {
    const nextState: State = !text.trim() ? "missing" : selected.state === "pending" ? "pending" : "confirmed";
    edit({ text, state: nextState, source: selected.source || "手动录入", collected_at: null });
  }

  async function importFile(file: File) {
    setUploading(true); setError(""); setOldImage(null);
    try {
      const form = new FormData();
      form.set("file", file);
      const query = new URLSearchParams({ asin: target.trim(), market, image_field: imageField });
      const next = await api<Imported>(`${base}/imports?${query}`, "POST", form);
      setImported(next);
      setCaptureId(undefined);
      if (next.asin) { setAsin(next.asin); setRegisteredAsin(next.asin); setSelectedMarket(next.market); onStored(); }
    } catch (err) { setError(message(err)); } finally { setUploading(false); }
  }

  async function readMirror() {
    setReadingMirror(true); setError("");
    try {
      const next = await loadAmazonMirror(target.trim(), market);
      if (next.product_id !== detail.product.id) throw new Error("该归档属于另一个项目，请在 ASIN 库里打开对应项目。");
      setImported({ ...next, filename: next.label, raw: "", url: next.url ?? undefined, image_id: null });
      setCaptureId(undefined);
    } catch (err) { setError(message(err)); } finally { setReadingMirror(false); }
  }

  function applyFields(rows: Partial<Listing>, onlyMissing = false) {
    set(value => {
      const merged = { ...value };
      for (const key of fieldKeys) {
        const incoming = rows[key];
        if (onlyMissing && value[key].state !== "missing") continue;
        if (incoming && (incoming.state !== "missing" || value[key].state === "missing")) merged[key] = incoming;
      }
      return merged;
    });
    const first = fieldKeys.find(key => rows[key]);
    if (first) select(first);
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (working || preview) return;
    setError("");
    for (const key of fieldKeys) {
      const row = data[key];
      if ((row.state === "confirmed" || row.state === "pending") && !row.text.trim()) {
        select(key); setError(`${fields[key]}需要原文。`); return;
      }
      if (row.state !== "missing" && !row.source.trim()) {
        select(key); setError(`请补充${fields[key]}的来源或确认依据。`); return;
      }
    }
    setBusy(true);
    try { await onSave({ fields: data, note, asin: knownAsin || undefined, market, base_snapshot_id: baseSnapshotId }); }
    catch (err) { setError(message(err)); } finally { setBusy(false); }
  }

  const hasStateChoice = selected.state === "confirmed" || selected.state === "pending";
  return <Dialog wide title="编辑或导入 Listing 原文" onClose={onClose}>
    <form className="dialog-form" onSubmit={save}>
      <div className="listing-capture">
        <MarketplaceSelect value={market} marketplaces={marketplaces} disabled={working || !!knownAsin || !!linkMarket}
          hint={linkMarket ? "已从商品链接识别站点。" : undefined}
          onChange={next => { setSelectedMarket(next); setCaptureId(undefined); setError(""); }} />
        <div className="listing-capture-control">
          <FormField name="ASIN 或 Amazon 商品详情链接">
            <input maxLength={2048} autoComplete="off" value={target} readOnly={!!knownAsin} disabled={working}
              placeholder="输入 10 位 ASIN 或粘贴商品链接" onChange={event => { setAsin(event.target.value); setCaptureId(undefined); setError(""); }}
              onKeyDown={event => {
                if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                  event.preventDefault(); void collect("auto");
                }
              }} />
          </FormField>
          <Button disabled={!canCapture} onClick={() => void collect("auto")}>
            {starting === "auto" ? <LoaderCircle size={16} className="spinner" /> : <Globe2 size={16} />}
            {starting === "auto" ? "正在查找资料…" : "获取商品资料（优先归档）"}
          </Button>
          {playwright && <Button disabled={!canCapture} onClick={() => void collect("playwright")}>
            {starting === "playwright" ? "正在排队…" : "实时刷新商品页"}
          </Button>}
          {nexscope && <Button disabled={!canCapture} onClick={() => void collect("nexscope")}>
            {starting === "nexscope" ? "正在排队…" : "用 Nexscope 刷新商品资料"}
          </Button>}
        </div>
        {!playwright && !nexscope && market !== "US" &&
          <small className="muted">可上传已有网页或商品资料。在线采集的启用方法见 README。</small>}
        <CaptureProgress task={capture} error={loaded?.error} loading={captureHasPreview(capture) && !loaded} />
        {market === "US" && (needsBrowserVerification(capture) || nexscope
          || (preview?.archived_at && !preview.reference_url) || (imported?.archived_at && !imported.reference_url)) &&
          <Button disabled={!canCapture} onClick={() => void collect("public_archive")}>
            {starting === "public_archive" ? "正在排队…" : "查公开归档"}
          </Button>}
        <Button disabled={working || !target.trim()} onClick={() => void readMirror()}>
          {readingMirror ? "正在读取归档…" : "读取本地商品归档"}
        </Button>
        <BrowserPageImport input={target} market={market} marketplaces={marketplaces} blocked={needsBrowserVerification(capture)} />
      </div>
      <div className="import-strip">
        <div><strong>导入现有资料</strong><p>网页最多 32 MB，表格、文本或截图最多 8 MB。</p>
          <ScreenshotSelect value={imageField} onChange={setImageField} disabled={working} /></div>
        <label className="button button-secondary file-button"><FileUp size={16} />{uploading ? "正在读取…" : "选择文件"}
          <input type="file" disabled={working} accept={listingFileTypes} aria-label="选择网页或 Listing 文件"
            onChange={event => { const file = event.currentTarget.files?.[0]; if (file) void importFile(file); event.currentTarget.value = ""; }} />
        </label>
      </div>
      {error && <Notice warn>{error}</Notice>}
      {preview && <div className="import-result">
        <SourcePreview preview={preview} />
        <div className="row wrap">
          <Button disabled={working || (historicalPreview && !fieldKeys.some(key => data[key].state === "missing" && preview.fields[key]?.text))} onClick={() => {
            applyFields(historicalPreview ? preview.fields : confirmPreview(preview.fields), historicalPreview);
            setRegisteredAsin(preview.asin);
            setCaptureId(undefined);
          }}><Check size={15} />{historicalPreview ? "用归档补齐缺少字段" : "核对原文并应用"}</Button>
          <Button kind="quiet" onClick={() => setCaptureId(undefined)}>关闭采集预览</Button>
        </div>
      </div>}
      {imported && <div className="import-result">
        {imported.url ? <SourcePreview preview={{ ...imported, label: imported.filename, brand: imported.brand ?? "", url: imported.url }} /> : <>
          <div className="row wrap"><Badge tone="amber">待核实</Badge><strong>{imported.filename}</strong>
            <span>{Object.keys(imported.fields).map(key => fields[key as FieldKey]).join(" · ") || "需要选择正文所属字段"}</span></div>
          {imported.message && <p>{imported.message}</p>}
        </>}
        {imported.image_id && <a href={`/api${base}/imports/${imported.image_id}`} target="_blank" rel="noreferrer"><ImageIcon size={15} /> 查看原截图</a>}
        {!!imported.raw && <details><summary>查看导入文本</summary><pre>{imported.raw}</pre></details>}
        <div className="row wrap">
          {Object.keys(imported.fields).length > 0 && <Button
            disabled={historicalImport && !fieldKeys.some(key => data[key].state === "missing" && imported.fields[key]?.text)}
            onClick={() => { applyFields(imported.fields, historicalImport); setImported(null); }}>
            <Check size={15} />{historicalImport ? "用归档补齐缺少字段" : "应用识别字段"}</Button>}
          {imported.raw && Object.keys(imported.fields).length === 0 && <Button onClick={() => {
            edit({ state: "pending", text: imported.raw.slice(0, 12000), source: `资料导入 · ${imported.filename}`, collected_at: null });
            setImported(null);
          }}>放入当前字段</Button>}
          <Button kind="quiet" onClick={() => setImported(null)}>关闭导入预览</Button>
        </div>
      </div>}
      {images.length > 0 && <div className="row wrap saved-images">
        <span>已保存截图</span>{images.slice(0, 5).map(item => <button key={item.id} type="button" className="text-button" onClick={() => setOldImage(oldImage === item.id ? null : item.id)}>{item.filename}</button>)}
        {oldImage && <a href={`/api${base}/imports/${oldImage}`} target="_blank" rel="noreferrer">打开截图</a>}
      </div>}
      <div className="field-editor">
        <div className="editor-tabs" role="tablist" aria-label="选择录入字段">
          {fieldKeys.map(key => <button key={key} type="button" role="tab" aria-selected={tab === key}
            onClick={() => select(key)}><span>{fields[key]}</span>{data[key].state !== "missing" && <span className={`tiny-state ${data[key].state}`} />}
            <ChevronRight size={13} /></button>)}
        </div>
        <div className="field-edit-main">
          <div className="row spread"><h3>{fields[tab]}</h3><StateBadge state={selected.state} /></div>
          <PageNotes row={selected} />
          <FormField name="资料状态"><select value={selected.state} onChange={event => {
            const state = event.target.value as State;
            edit({ state, text: state === "confirmed" || state === "pending" ? selected.text : "",
              source: selected.source || "人工核对", collected_at: null });
          }}>{Object.entries(sourceStatus).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></FormField>
          {(hasStateChoice || selected.state === "missing") && <FormField name="原文" hint={tab === "bullets" ? "每行一条。" : undefined}>
            <textarea rows={5} maxLength={tab === "raw_text" ? 32000 : 12000} value={selected.text} placeholder={example[tab]}
              onChange={event => setText(event.target.value)} />
          </FormField>}
          {selected.state !== "missing" && <FormField name="资料来源" hint="可填说明书章节、卖家后台、URL 或截图名称。">
            <input maxLength={600} value={selected.source} onChange={event => edit({ source: event.target.value, collected_at: null })} />
          </FormField>}
          {selected.text && <small className="char-count">{charCount(selected.text)} 字符</small>}
        </div>
      </div>
      <FormField name="快照备注"><input value={note} maxLength={600} onChange={event => setNote(event.target.value)} placeholder="例如 11 月详情页留档（选填）" /></FormField>
      <footer className="dialog-actions"><Button onClick={onClose}>取消</Button>
        <Button type="submit" disabled={working || !!preview} kind="primary"><Save size={16} />保存原文快照</Button></footer>
    </form>
  </Dialog>;
}
