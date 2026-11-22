"use client";

import { useEffect, useId, useRef, type ReactNode } from "react";
import { AlertCircle, CheckCircle2, ChevronDown, Download, FileText, Inbox, X } from "lucide-react";
import { type Marketplace, type State, sourceStatus } from "./model";

export function Button({ children, className = "", kind = "secondary", ...props }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { kind?: "primary" | "secondary" | "quiet" | "danger" }) {
  return <button type="button" {...props} className={`button button-${kind} ${className}`}>{children}</button>;
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function StateBadge({ state }: { state: State }) {
  return <Badge tone={state === "confirmed" ? "green" : state === "pending" ? "amber" : "neutral"}>{sourceStatus[state]}</Badge>;
}

export function Flag() {
  return <span className="flag-us" aria-hidden="true" />;
}

export function Empty({ title, children, actions }: { title: string; children?: ReactNode; actions?: ReactNode }) {
  return <div className="empty"><span className="empty-icon"><Inbox size={22} /></span>
    <h3>{title}</h3>{children && <p>{children}</p>}{actions && <div className="row wrap">{actions}</div>}
  </div>;
}

export function Header({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  return <header className="page-heading"><div><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div>
    {actions && <div className="row wrap heading-actions">{actions}</div>}</header>;
}

export function Notice({ children, warn = false }: { children: ReactNode; warn?: boolean }) {
  return <div className={`notice ${warn ? "notice-warn" : ""}`} role={warn ? "alert" : undefined}>
    {warn ? <AlertCircle size={17} /> : <CheckCircle2 size={17} />}<div>{children}</div>
  </div>;
}

export function Dialog({ title, children, onClose, wide = false }: {
  title: string; children: ReactNode; onClose: () => void; wide?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const labelled = useId();
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => { element?.close(); };
  }, []);
  return <dialog ref={dialog} aria-labelledby={labelled} className={wide ? "dialog dialog-wide" : "dialog"}
    onCancel={event => { event.preventDefault(); onClose(); }}>
    <header className="dialog-head"><h2 id={labelled}>{title}</h2><Button aria-label="关闭" kind="quiet" onClick={onClose}><X size={20} /></Button></header>
    {children}
  </dialog>;
}

export function FormField({ name, hint, children }: { name: string; hint?: string; children: ReactNode }) {
  return <label className="form-field"><span>{name}</span>{children}{hint && <small>{hint}</small>}</label>;
}

export function MarketplaceSelect({ value, marketplaces, onChange, disabled, hint }: {
  value: string; marketplaces: Marketplace[]; onChange: (value: string) => void; disabled?: boolean; hint?: string;
}) {
  return <FormField name="Amazon 站点" hint={hint}>
    <select value={value} disabled={disabled} onChange={event => onChange(event.target.value)}>
      {marketplaces.map(site => <option key={site.code} value={site.code}>{site.label} · {site.domain}</option>)}
    </select>
  </FormField>;
}

export function ExportMenu({ base, auditId, candidateId }: { base: string; auditId: string; candidateId?: string }) {
  return <details className="export-menu">
    <summary className="button button-secondary"><Download size={16} /> 导出报告 <ChevronDown size={14} /></summary>
    <div className="export-dropdown">
      {([["md", "Markdown"], ["xlsx", "Excel"], ["pdf", "PDF"]] as const).map(([type, label]) =>
        <a key={type} href={`/api${base}/export?audit_id=${auditId}&format=${type}${candidateId ? `&candidate_id=${candidateId}` : ""}`} download>
          <FileText size={15} /> {label}
        </a>
      )}
    </div>
  </details>;
}
