// 后端响应类型（契约以 ainovel/api.py 为准，宽松字段用索引签名兜住 dict[str, Any]）。

export interface BookCard {
  name: string
  title?: string
  genre?: string
  target_words?: number
  platform?: string
  status?: string
  author?: string
  [k: string]: unknown
}

export interface ChapterRow {
  no: number
  status: string
  current_version: number
  fix_rounds: number
  cost_cents: number
  cost_over_yuan: boolean
  [k: string]: unknown
}

export interface CostSummary {
  cost_cents: number
  calls: number
  [k: string]: unknown
}

export interface Overview {
  meta: BookCard
  chapters: ChapterRow[]
  cost_total: CostSummary
  open_conflicts: number
  characters: string[]
  outline_rows: Record<string, string>[]
  prompts_source: string
}

export interface OutlineHook {
  type?: string
  content?: string
  [k: string]: unknown
}

export interface OutlineContract {
  core_event?: string
  stage?: string
  emotion_task?: string
  pov?: string
  hook?: OutlineHook
  foreshadow_ops?: unknown[]
  confirmed?: boolean
  _body?: string
  [k: string]: unknown
}

export interface OutlineFull {
  logline?: string
  ending_direction?: string
  volumes?: unknown[]
  chapter_rows?: Record<string, string>[]
  [k: string]: unknown
}

export interface Prose {
  _body: string
  status?: string
  [k: string]: unknown
}

export interface MechHit {
  rule: string
  level: string
  line: number
  word: string
  message: string
}

export interface ReviewIssue {
  category: string
  severity: string
  location: string
  quote: string
  description: string
  suggestion: string
}

export interface ReviewResult {
  verdict: string
  scores?: Record<string, number>
  issues?: ReviewIssue[]
}

export interface MechReport {
  verdict: string
  issues: MechHit[]
}

export interface WorkbenchData {
  no: number
  status: string
  row: { current_version: number; [k: string]: unknown } | null
  outline: OutlineContract | null
  prose: Prose | null
  mech: MechReport | null
  review: (ReviewResult & { status?: string }) | null
  cost: CostSummary | null
  golden3: boolean
}

export interface PreviewSlot {
  title: string
  est_tokens: number
  truncated: boolean
  content: string
}

export interface PreviewResult {
  total_est_tokens: number
  budget_tokens: number
  saved_to?: string
  slots: PreviewSlot[]
}

export interface VersionInfo {
  version: number
  label: string
  created_at: string
  [k: string]: unknown
}

export interface PolishResult {
  ok: boolean
  word_count?: { before: number; after: number }
  v3?: { verdict: string }
  changes?: unknown[]
  reason?: string
}

export interface FinalizeResult {
  snapshot_version: number
  writeback: unknown
  [k: string]: unknown
}

export interface AuditResult {
  foreshadow_pay_rate: unknown
  report_path: string
  [k: string]: unknown
}

export interface Conflict {
  id: number
  kind: string
  chapter_no: number
  book: string
  char?: string
  field?: string
  detail: string
  old: unknown
  new: unknown
  [k: string]: unknown
}

export interface AppSettings {
  providers?: { openai?: { api_key?: string; base_url?: string } }
  default_model?: string
  prices?: Record<string, unknown>
  [k: string]: unknown
}

export type CharacterData = Record<string, unknown> & { _body?: string }
