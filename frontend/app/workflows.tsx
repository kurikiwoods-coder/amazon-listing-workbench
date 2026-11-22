"use client";

import { useEffect, useState } from "react";
import { Clock3, Ellipsis, ExternalLink, FolderOpen, LoaderCircle, Trash2 } from "lucide-react";
import { api, fieldKeys, fields, fullDate, marketplaceFromLink, message, type CaptureTask, type ListingPreview, type Marketplace } from "./model";
import { Badge, Button, Dialog, Notice } from "./ui";
import { MissingPublicField, OriginalTranscript, publicOrder } from "./public-copy";

export const capturePath = "/amazon/workflows";
export const activeCapture = (task?: CaptureTask) => task?.status === "queued" || task?.status === "running";
export const captureHasPreview = (task?: CaptureTask) => task?.has_preview || task?.status === "succeeded";
const captions = { queued: "排队中", running: "采集中", succeeded: "采集完成", failed: "采集失败" } as const;

export function loadAmazonMirror(asin: string, market: string) {
  return api<ListingPreview>(`/amazon/mirror?${new URLSearchParams({ asin, market })}`);
}

export function useCapturePreview(task?: CaptureTask) {
  const [loaded, setLoaded] = useState<{ id: string; preview?: ListingPreview; error?: string }>();
  const id = task?.id;
  const ready = captureHasPreview(task);
  useEffect(() => {
    if (!id || !ready) return;
    let alive = true;
    api<{ preview: ListingPreview }>(`${capturePath}/${id}`).then(result => {
      if (alive) setLoaded({ id, preview: result.preview });
    }).catch(err => { if (alive) setLoaded({ id, error: message(err) }); });
    return () => { alive = false; };
  }, [id, ready]);
  return ready && loaded?.id === id ? loaded : undefined;
}

export function BrowserPageImport({ input, market, marketplaces, blocked = false }: {
  input: string; market: string; marketplaces: Marketplace[]; blocked?: boolean;
}) {
  const target = input.trim();
  const linkSite = marketplaceFromLink(target, marketplaces);
  const site = linkSite ?? marketplaces.find(item => item.code === market);
  const asin = /^[a-z0-9]{10}$/i.test(target)
    ? target : linkSite && new URL(target.includes("://") ? target : `https://${target}`)
      .pathname.match(/\/(?:dp|gp\/product|gp\/aw\/d)\/([a-z0-9]{10})(?:\/|$)/i)?.[1];
  return <div className="browser-page-import">
    {site && asin && <a href={`https://www.${site.domain}/dp/${asin.toUpperCase()}`} target="_blank" rel="noreferrer">
      <ExternalLink size={15} />用浏览器打开商品页
    </a>}
    <small>{blocked && "采集浏览器被 Amazon 验证页拦住。等常用浏览器里显示商品标题后，再保存并上传。"}
      在 Edge 或 Chrome 打开对应变体，滚动到 A+ 图文后按 Ctrl+S。要识别图片中的文字，选单文件网页 MHTML，或使用 SingleFile 保存。</small>
  </div>;
}

export function needsBrowserVerification(task?: CaptureTask) {
  return task?.status === "failed" && /人机验证|Amazon 页面要求登录/.test(task.detail);
}

export function CaptureProgress({ task, loading, error }: {
  task?: CaptureTask; loading?: boolean; error?: string;
}) {
  if (error || task?.status === "failed") {
    return <Notice warn>{error || task?.detail}
      {!error && task?.has_preview && " 已找到同站点、同 ASIN 的历史资料，可在本页继续。"}
    </Notice>;
  }
  if (!activeCapture(task) && !loading) return null;
  return <div className="capture-progress row" role="status"><LoaderCircle className="spinner" size={16} />
    <span>{task && activeCapture(task) ? task.detail : "正在读取采集结果"}。可稍后在左侧「最近」打开。</span></div>;
}

export function SourcePreview({ preview }: { preview: ListingPreview }) {
  const keys = fieldKeys.filter(key => !publicOrder.includes(key) && preview.fields[key]?.text);
  return <div className="quick-preview">
    <div className="row wrap"><Badge tone="amber">{preview.archived_at || preview.source_observed_at ? "历史资料" : "待核实"}</Badge><strong>{preview.label}</strong>
      {preview.product_id && <Badge tone="blue">{preview.market} · ASIN 已入库</Badge>}
      {preview.reference_url && <a href={preview.reference_url} target="_blank" rel="noreferrer">打开公开归档</a>}
      {preview.url && <a href={preview.url} target="_blank" rel="noreferrer">Amazon 商品页</a>}</div>
    {preview.source_observed_at && <small className="muted">源站采集时间 · {fullDate(preview.source_observed_at)}</small>}
    {preview.archived_at && <small className="muted">归档时间 · {fullDate(preview.archived_at)}</small>}
    {preview.message && <p>{preview.message}</p>}
    <OriginalTranscript listing={preview.fields} asin={preview.asin} market={preview.market} />
    {preview.url && (["highlights", "aplus"] as const).map(key => !preview.fields[key]?.text && !preview.fields[key]?.notes?.length &&
      <p className="source-note" key={key}><strong>{fields[key]}</strong> · <MissingPublicField field={key} /></p>)}
    {keys.map(key => <details className="details-card" key={key}>
      <summary>{fields[key]}</summary><div className="quick-preview-copy" lang="en">{preview.fields[key]?.text}</div>
    </details>)}
  </div>;
}

export function RecentCaptures({ items, onOpen, onDelete }: {
  items: CaptureTask[]; onOpen: (task: CaptureTask) => void; onDelete: (task: CaptureTask) => void;
}) {
  const [menu, setMenu] = useState<{ item: CaptureTask; x: number; y: number } | null>(null);
  useEffect(() => {
    if (!menu) return;
    function dismiss(event: PointerEvent) {
      if (!(event.target instanceof Element && event.target.closest(".capture-menu"))) setMenu(null);
    }
    function escape(event: KeyboardEvent) { if (event.key === "Escape") setMenu(null); }
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", escape); };
  }, [menu]);

  function showMenu(item: CaptureTask, x: number, y: number) {
    setMenu({ item, x: Math.min(x, window.innerWidth - 174), y: Math.min(y, window.innerHeight - 56) });
  }

  return <section className="recent-captures" aria-label="最近的采集工作流">
    <h2>最近</h2>
    {items.length ? <div className="capture-list">{items.map(item => <div key={item.id} className="capture-item" onContextMenu={event => {
      event.preventDefault(); showMenu(item, event.clientX, event.clientY);
    }}>
      <button type="button" className="capture-open" title={item.title} onClick={() => onOpen(item)}>
        {item.status === "running" ? <LoaderCircle className="spinner" size={15} /> : item.status === "succeeded" ? <FolderOpen size={15} /> : <Clock3 size={15} />}
        <span><strong>{item.title}</strong><small>{item.market} · {item.title === item.asin ? "" : `${item.asin} · `}{item.provider === "nexscope" && "Nexscope · "}
          {item.provider === "public_archive" && "公开归档 · "}
          {item.provider === "archive" ? "已读归档" : item.status === "failed" && item.has_preview ? "采集失败 · 可用归档" : captions[item.status]}
        </small></span>
      </button>
      <button type="button" className="capture-more" aria-haspopup="menu" aria-expanded={menu?.item.id === item.id}
        aria-label={`${item.market} ${item.asin} 的工作流操作`} onClick={event => {
          const rect = event.currentTarget.getBoundingClientRect(); showMenu(item, rect.right, rect.top);
        }}><Ellipsis size={16} /></button>
    </div>)}</div> : <p>还没有采集任务</p>}
    {menu && <div className="capture-menu" role="menu" aria-label="工作流操作" style={{ left: menu.x, top: menu.y }}>
      <button type="button" role="menuitem" autoFocus onClick={() => { onDelete(menu.item); setMenu(null); }}><Trash2 size={15} />删除工作流</button>
    </div>}
  </section>;
}

export function DeleteCaptureDialog({ task, onDelete, onClose }: {
  task: CaptureTask; onDelete: (id: string) => Promise<void>; onClose: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function remove() {
    setBusy(true); setError("");
    try { await onDelete(task.id); }
    catch (err) { setError(message(err)); } finally { setBusy(false); }
  }
  return <Dialog title="删除采集工作流" onClose={() => { if (!busy) onClose(); }}>
    <div className="dialog-form">
      <p>将删除 {task.market} 站 {task.asin} 的这条采集任务及其预览。</p>
      {error && <Notice warn>{error}</Notice>}
      <footer className="dialog-actions"><Button disabled={busy} onClick={onClose}>取消</Button>
        <Button kind="danger" disabled={busy} onClick={() => void remove()}><Trash2 size={16} />{busy ? "删除中…" : "确认删除"}</Button></footer>
    </div>
  </Dialog>;
}
