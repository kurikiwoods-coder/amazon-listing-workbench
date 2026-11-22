export const fields = {
  title: "商品标题", highlights: "Item Highlights", bullets: "五点描述", aplus: "A+ 内容",
  raw_text: "商品描述 / 未分段原文", public_specs: "公开商品规格", attributes: "后台属性", search_terms: "Search Terms", price: "价格", reviews: "评论摘要",
  qa: "Q&A", keywords: "目标关键词", competitors: "竞品 ASIN / 问题",
} as const;
export type FieldKey = keyof typeof fields;
export const fieldKeys = Object.keys(fields) as FieldKey[];
export const publicFields: FieldKey[] = ["title", "highlights", "bullets", "aplus"];
export const publishedCopy: FieldKey[] = [...publicFields, "raw_text", "public_specs"];
export const sourceStatus = {
  confirmed: "已确认", pending: "待核实", missing: "未提供", absent: "已确认不存在", na: "类目不适用",
} as const;
export type State = keyof typeof sourceStatus;
export type PageLine = { line: number; kind: "html" | "alt" | "ocr"; scope: "product" | "brand" | "comparison"; location: string };
export type SourceField = { state: State; text: string; source: string; collected_at: string | null; page_lines?: PageLine[]; notes?: string[] };
export type Listing = Record<FieldKey, SourceField>;
export type SnapshotInput = { fields: Listing; note: string; asin?: string; market?: string; base_snapshot_id: string };

export const roles = {
  identity: "产品身份", use: "使用场景", feature: "核心功能", spec: "技术参数",
  boundary: "使用边界", difference: "产品差异", proof: "产品证明",
} as const;
export const factStates = { confirmed: "已确认", pending: "待核实", rejected: "不采用" } as const;
export type Fact = {
  id: string; label: string; copy: string; source: string; role: keyof typeof roles;
  state: keyof typeof factStates; conflicts: string[];
};

export const questionSources = {
  template: "类目模板", manual: "手动录入", alexa: "Alexa 问句", review: "用户评论",
  competitor: "竞品问句", ai: "AI 候选",
} as const;
export type Question = {
  id: string; text: string; intent: string; source: keyof typeof questionSources;
  priority: "high" | "standard"; fields: FieldKey[]; anchors: string[]; fact_ids: string[];
};
export const answerStatus = {
  supported: "明确支持", partial: "部分支持", contradicted: "矛盾", not_found: "未找到", unknown: "无法判断",
} as const;
export type AnswerState = keyof typeof answerStatus;
export type Evidence = { field: FieldKey; line: number; quote: string; source: string };
export type DraftGroup = "search_terms" | "keywords" | "attributes";
export type DraftDecision = "pending" | "shortlisted" | "dismissed";
export type FieldDraft = { id: string; group: DraftGroup; name: string; value: string; reason: string;
  evidence: Evidence; origin: "rules" | "llm"; decision: DraftDecision };
export type FieldSuggestions = { version: string; model: string | null; groups: DraftGroup[]; items: FieldDraft[] };
export type QuestionResult = Pick<Question, "id" | "text" | "intent" | "priority" | "source"> & {
  status: AnswerState; detail: string; evidence: Evidence[]; missing: string[]; suggested_fields: FieldKey[];
};
export type Priority = "P0" | "P1" | "P2";
export type RewriteAnswer = {
  question: string; before: AnswerState; after: AnswerState; answer_quote: string;
  source: string; source_quote: string; detail: string;
};
export type IssueEdit = { field: FieldKey; line: number; target: string; before: string; after: string; action: string;
  question: string; fact_labels: string[]; rewrite: boolean; model_generated?: boolean; answers?: RewriteAnswer[] };
export type Issue = Evidence & {
  key: string; message: string; recommendation: string; priority: Priority; kind: string;
  question_ids: string[]; fact_ids: string[]; needs_review: boolean; edit?: IssueEdit;
};
export type Dimension = {
  key: string; label: string; weight: number; score: number | null; status: State | "assessed";
  reason: string; evidence: Evidence[];
};
export type ModuleScore = Pick<Dimension, "label" | "score" | "reason" | "evidence"> & {
  key: FieldKey; status: State | "assessed" | "reference";
  source_state: State;
  checks: { label: string; reason: string; evidence: Evidence[] }[];
};
export type Guidance = {
  key: string; dimension: string | null; field: FieldKey; label: string; range: [number, number] | null;
  assumption: string; action: string; source_needed: string; question: string; evidence: Evidence[]; mode: "rules" | "llm";
};
export type AuditResult = {
  score: number | null; coverage: number; assessed_weight: number; applicable_weight: number;
  provisional: boolean; confidence: string; dimensions: Dimension[]; issues: Issue[];
  module_scores?: ModuleScore[];
  questions: QuestionResult[]; question_states: Partial<Record<AnswerState, number>>; guidance?: Guidance[];
  screen?: { score: number; mode?: "rules" | "llm"; checks: { label: string; score: number; reason: string; evidence: Evidence[] }[] } | null;
  question_coverage: number | null; assessed_questions: number; total_questions: number;
};
export type Rules = {
  version: string; effective: string | null; as_of: string; coverage_threshold: number;
  rubric: Pick<Dimension, "key" | "label" | "weight">[];
  limits: Partial<Record<"title" | "highlights" | "bullet_count", number>>; sources: Record<string, string>;
};

export type ProductInput = {
  asin: string; name: string; sku: string; market: string; brand: string;
  category: string; product_type: "unknown" | "non_media" | "media";
};
export type QuickInput = Pick<ProductInput, "asin" | "market" | "category" | "brand" | "product_type"> & {
  text: string; mode: "rules" | "llm"; fields?: Partial<Listing>;
};
export type Product = ProductInput & { id: string; demo: boolean; created_at: string };
export type ListedProduct = Product & {
  score: number | null; coverage: number | null; p0: number | null;
  provisional: boolean | null; updated_at: string | null; draft_ready: boolean | null; screen_score: number | null;
  by_module?: boolean;
};
export type SnapshotSummary = { id: string; version: number; cause: string; note: string; created_at: string };
export type Snapshot = SnapshotSummary & { fields: Listing };
export type Context = { facts: Fact[]; questions: Question[] };
export type ContextRecord = { id: string; version: number; data: Context; created_at: string };
export type Comparison = {
  score_delta: number | null; same_basis: boolean; before: number | null; after: number | null;
  coverage_before: number; coverage_after: number; questions_before: number | null; questions_after: number | null;
  dimensions: { key: string; label: string; before: number | null; after: number | null; delta: number | null }[];
  resolved: Issue[]; unverified: Issue[]; remaining: Issue[]; new: Issue[];
  by_module?: boolean;
};
export type Audit = {
  id: string; snapshot_id: string; context_id: string; kind: "audit" | "rescore"; created_at: string;
  data: {
    result: AuditResult; rules: Rules; mode: "rules" | "llm"; model: string | null;
    comparison?: Comparison; candidate_id?: string; baseline_id?: string; product: Product; engine_version: string;
  };
};
export type AuditSummary = Omit<Audit, "data"> & {
  score: number | null; coverage: number; mode: "rules" | "llm"; provisional: boolean; candidate_id?: string; screen_score?: number | null;
  by_module?: boolean;
};
export const strategies = {
  A: { label: "保守修改", detail: "保留原结构，修正错误或缺少的关键信息。" },
  B: { label: "用户问题导向", detail: "围绕购买前最常见的问题重组文案。" },
  C: { label: "差异化导向", detail: "优先使用有证据的产品差异和使用场景。" },
} as const;
export type Strategy = keyof typeof strategies;
export type Decision = "pending" | "accepted" | "rejected" | "kept";
export type ReviewLine = { key: string; text: string; decision: Decision };
export type DraftLine = ReviewLine & {
  before: string; effective: string; fact_ids: string[]; fact_labels: string[]; reason: string; verified: boolean;
  issue_ids: string[]; count: number; limit: number | null;
};
export type CandidateSummary = {
  id: string; version: number; audit_id: string; parent_id: string | null; strategy: Strategy;
  created_at: string; ready: boolean; pending: number;
};
export type Candidate = Omit<CandidateSummary, "ready" | "pending"> & {
  data: {
    validation: { rows: DraftLine[]; fields: Listing; blockers: string[]; rule_issues: Issue[]; ready: boolean; copy: string };
    note: string; forecast: { before: number | null; after: number | null; delta: number | null };
    skill?: { name: string; version: string; url: string };
  };
};

export type RewriteEntry = { auditId: string; issueKey: string; edit: IssueEdit };
export type RewriteAnchor = { field: FieldKey; line: number; before: string };
export type SkillDraft = {
  id: number; field: FieldKey | null; target: string; before: string; after: string; source: string; line: number | null;
  reason: string; method: "diagnosis" | "snapshot" | "heading" | "format" | "manual" | "unclear";
  status: "draft" | "check" | "choose" | "reference"; choices: FieldKey[]; warnings: string[];
  save_edit: RewriteAnchor | null; fact_labels: string[]; model_generated?: boolean; answers?: RewriteAnswer[];
};
export type SkillPreview = {
  skill: { name: string; version: string; url: string }; drafts: SkillDraft[]; model: string | null; notice: string;
  snapshot_id?: string; context_id?: string;
};
export type Publication = { id: string; candidate_id: string; published_at: string; note: string };
export type JobResult = { kind: "audit" | "rewrite" | "rescore"; id: string } | {
  kind: "questions"; questions: Question[]; model: string;
};
export type Job = {
  id: string; product_id: string; kind: JobResult["kind"]; status: "queued" | "running" | "succeeded" | "failed" | "cancelled";
  progress: number; detail: string; result: JobResult | null;
};
export type JobStart = { job: Job | null; cached: JobResult | null };
export type Detail = {
  product: Product; snapshot: Snapshot; context: ContextRecord; latest_audit: Audit | null;
  snapshots: SnapshotSummary[]; audits: AuditSummary[]; candidates: CandidateSummary[];
  publications: Publication[]; jobs: Job[]; current_rules: Rules; field_suggestions: FieldSuggestions;
};
export type DiagnosisDetail = Pick<Detail, "product" | "snapshot" | "context" | "latest_audit" | "current_rules" | "field_suggestions">;
export type Marketplace = { code: string; domain: string; label: string };
export const defaultMarketplace: Marketplace = { code: "US", domain: "amazon.com", label: "美国" };
export const listingFileTypes = ".html,.htm,.mhtml,.mht,.csv,.tsv,.xlsx,.json,.txt,.md,.png,.jpg,.jpeg,.webp";
export type Config = { model: string | null; max_upload_mb: number; max_webpage_mb?: number; amazon_playwright: boolean; nexscope: boolean;
  default_market: string; marketplaces: Marketplace[]; };
export type ListingPreview = {
  label: string; asin: string; market: string; brand: string; url: string | null; fields: Partial<Listing>; message: string; product_id?: string | null;
  archived_at?: string; source_observed_at?: string; reference_url?: string;
};
export type CaptureProvider = "auto" | "playwright" | "nexscope" | "public_archive";
export type CaptureTask = {
  id: string; product_id: string | null; asin: string; market: string; title: string;
  status: "queued" | "running" | "succeeded" | "failed"; detail: string;
  provider: "archive" | Exclude<CaptureProvider, "auto">; has_preview?: boolean;
};
export type StartCapture = (asin: string, market: string, productId?: string, provider?: CaptureProvider) => Promise<CaptureTask>;
export type Imported = Pick<ListingPreview, "fields" | "message" | "asin" | "market" | "archived_at" | "source_observed_at" | "reference_url"> & {
  raw: string; filename: string; image_id: string | null; url?: string; brand?: string;
};
export type ImageRecord = { id: string; filename: string; created_at: string };
export type ImageField = "auto" | "aplus" | "highlights" | "bullets" | "raw_text" | "public_specs";

export function confirmPreview(rows: Partial<Listing>) {
  return Object.fromEntries(Object.entries(rows).map(([key, value]) =>
    [key, value.state === "pending" && value.text.trim() ? { ...value, state: "confirmed" } : value]));
}

export function marketplaceFromLink(input: string, marketplaces: Marketplace[]) {
  try {
    const value = input.trim();
    const { hostname, protocol } = new URL(value.includes("://") ? value : `https://${value}`);
    if (protocol !== "https:" && protocol !== "http:") return undefined;
    const domain = hostname.toLowerCase().replace(/\.$/, "").replace(/^(?:www|m|smile)\./, "");
    return marketplaces.find(site => site.domain === domain);
  } catch {
    return undefined;
  }
}

export async function api<T>(path: string, method = "GET", data?: unknown): Promise<T> {
  const connect = "请检查服务是否运行，并打开启动窗口显示的地址。EC2 部署请检查 Session Manager 会话。";
  if (typeof window !== "undefined" && window.location.protocol === "file:") {
    throw new Error("当前打开的是本地 HTML 文件。请用 start-workbench.cmd 启动工作台，再打开终端显示的地址。");
  }
  const body = data instanceof FormData ? { body: data } : data !== undefined
    ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) } : {};
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { method, cache: "no-store", ...body });
  } catch {
    throw new Error(`浏览器连不上本机工作台。${connect}`);
  }
  if (response.status === 204) return undefined as T;
  const result = await response.json().catch(() => undefined);
  if (result === undefined && (response.ok || response.status === 404)) {
    throw new Error(`当前地址未返回工作台数据。${connect}`);
  }
  if (!response.ok) {
    if (typeof result?.detail === "string") throw new Error(result.detail);
    if (response.status >= 500) {
      throw new Error(`工作台请求出错（HTTP ${response.status}）。请查看启动窗口的报错。`);
    }
    throw new Error(`请求失败（HTTP ${response.status}）。`);
  }
  return result as T;
}

export const pathFor = (id: string) => `/products/${id}`;
export const score = (value: number | null | undefined) => value == null ? "—" : String(value);
export const percent = (value: number | null | undefined) => value == null ? "—" : `${value}%`;
export const signed = (value: number | null | undefined) => value == null ? "—" : `${value > 0 ? "+" : ""}${value}`;
export const paragraphLines = (value: string) => value.split(/\r?\n/).map(row => row.trim()).filter(Boolean);
export const charCount = (value: string) => Array.from(value).length;
export const shortDate = (value: string | null | undefined) => value
  ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Taipei", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(value)) : "—";
export const fullDate = (value: string | null | undefined) => value
  ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Taipei", dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";
export function id() {
  return globalThis.crypto?.randomUUID?.() ?? `local-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
export function lineName(key: string) {
  return key.startsWith("bullets.") ? `要点 ${Number(key.slice(8)) + 1}` : fields[key as FieldKey];
}
export function message(error: unknown) {
  return error instanceof Error ? error.message : "操作未完成，请重试。";
}

export async function copyText(value: string) {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    const input = document.createElement("textarea");
    input.value = value;
    input.className = "clipboard-helper";
    document.body.append(input);
    input.select();
    const copied = document.execCommand("copy");
    input.remove();
    return copied;
  }
}
