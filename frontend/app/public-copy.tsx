"use client";

import { useState } from "react";
import { Download, FileText } from "lucide-react";
import {
  fields, paragraphLines, sourceStatus, type FieldKey, type ImageField, type Listing, type PageLine, type SourceField,
} from "./model";
import { Button, FormField, StateBadge } from "./ui";

export const publicOrder: FieldKey[] = ["title", "highlights", "bullets", "raw_text", "aplus", "public_specs"];
const scopeLabels = { product: "", comparison: "多商品对比", brand: "品牌故事" };
const kindLabels = { html: "网页正文", alt: "图片替代文字", ocr: "图片识别 · 请核对" };

export function lineLabel(item: PageLine) {
  return [item.location, scopeLabels[item.scope], kindLabels[item.kind]].filter(Boolean).join(" · ");
}

export function saveText(contents: string, filename: string) {
  const url = URL.createObjectURL(new Blob(["\ufeff", contents], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url; link.download = filename;
  document.body.append(link); link.click(); link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export function PageNotes({ row }: { row?: SourceField }) {
  return <>{row?.notes?.map(note => <p className="source-note" key={note}>{note}</p>)}</>;
}

export function OriginalTranscript({ listing, asin, market }: { listing: Partial<Listing>; asin: string; market: string }) {
  const [expanded, setExpanded] = useState(false);
  const present = publicOrder.filter(key => !!listing[key]?.text);
  const onlyMissing = publicOrder.filter(key => !listing[key]?.text && listing[key]?.notes?.length);
  function exportOriginal() {
    const blocks = present.map(key => {
      const data = listing[key]!;
      const meta = new Map(data.page_lines?.map(line => [line.line, line]));
      const text = paragraphLines(data.text).map((line, index) => {
        const origin = meta.get(index);
        return origin ? `[${lineLabel(origin)}] ${line}` : line;
      });
      return [fields[key], sourceStatus[data.state], data.source, ...text, ...(data.notes ?? [])].filter(Boolean).join("\n");
    });
    if (onlyMissing.length) blocks.push(...onlyMissing.map(key => [fields[key], ...listing[key]!.notes!].join("\n")));
    saveText(blocks.join("\n\n"), `${market}-${asin || "listing"}-public.txt`);
  }
  return <div className="original-transcript">
    <div className="row spread wrap"><div className="row"><FileText size={17} /><strong>前台原文汇总</strong></div>
      <Button kind="quiet" disabled={!present.length} onClick={exportOriginal}><Download size={14} />下载原文 TXT</Button></div>
    {!present.length && <p className="muted">采集商品页或导入文案后，可在这里按板块阅读原文。</p>}
    {present.map((key, index) => <details className="transcript-section" key={key} open={expanded || index === 0 || undefined}>
      <summary>{fields[key]} <small>{paragraphLines(listing[key]!.text).length} 段</small> <StateBadge state={listing[key]!.state} /></summary>
      <small className="muted">{listing[key]!.source}</small>
      <TranscriptLines row={listing[key]!} />
    </details>)}
    {onlyMissing.map(key => <div key={key}><strong>{fields[key]}</strong><PageNotes row={listing[key]} /></div>)}
    {present.length > 1 && <Button kind="quiet" onClick={() => setExpanded(value => !value)}>{expanded ? "折叠板块" : "展开全部板块"}</Button>}
  </div>;
}

export function TranscriptLines({ row }: { row: SourceField }) {
  const meta = new Map(row.page_lines?.map(item => [item.line, item]));
  return <div className="transcript-lines">{paragraphLines(row.text).map((text, index) => {
    const item = meta.get(index);
    return <div key={index}>{item && <small>{lineLabel(item)}</small>}<span>{text}</span></div>;
  })}<PageNotes row={row} /></div>;
}

export function ScreenshotSelect({ value, onChange, disabled }: { value: ImageField; onChange: (next: ImageField) => void; disabled?: boolean }) {
  return <details className="image-field-choice"><summary>指定截图所属板块</summary>
    <FormField name="截图文字导入到"><select value={value} disabled={disabled}
      onChange={event => onChange(event.target.value as ImageField)}>
      <option value="auto">自动识别</option>
      {(["aplus", "highlights", "bullets", "raw_text", "public_specs"] as const).map(key =>
        <option key={key} value={key}>{fields[key]}</option>)}
    </select></FormField></details>;
}

export function MissingPublicField({ field }: { field: FieldKey }) {
  const message: Partial<Record<FieldKey, string>> = {
    highlights: "这份资料中没有识别到独立的 Item Highlights。请查看商品标题旁是否展示了补充短语。",
    aplus: "这份资料中尚无可读的 A+ 文字。打开商品页并滚动到品牌图文，保存含图片的网页或上传该板块截图。",
    raw_text: "这份资料中没有独立商品描述。点击「前台原文」可查看已经提取的标题、五点和其他板块。",
  };
  return <>{message[field] ?? "补充对应字段和来源后可评估。"}</>;
}
