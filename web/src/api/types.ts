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

// 卷末审计报告（books/<书名>/audits/*.md，GET /api/books/{book}/audits）
export interface AuditReport {
  file: string
  path: string
  foreshadow_pay_rate: string
  content: string
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

// 机械检查阈值（规格03 §2.4 M-05~M-11；data/settings.json 的 mechcheck 对象，9 项）
export interface MechThresholds {
  sentence_comma_max: number
  exclaim_per_1k_max: number
  ellipsis_per_1k_max: number
  flat_sentence_window: number
  flat_sentence_ratio: number
  paragraph_max_chars: number
  repeat_para_prefix_n: number
  ending_window_chars: number
  first_person_ratio_max: number
}

export interface AppSettings {
  providers?: { openai?: { api_key?: string; base_url?: string; has_key?: boolean } }
  default_model?: string
  prices?: Record<string, unknown>
  mechcheck?: MechThresholds
  assembly_budget_tokens?: number
  [k: string]: unknown
}

export type CharacterData = Record<string, unknown> & { _body?: string }

// —— 档案编辑页（规格04 §1）新增类型 ——

/** meta.md frontmatter（规格01 §2.1） */
export interface BookMeta {
  title?: string
  author?: string
  genre?: string
  tags?: string[]
  target_words?: number
  chapter_word_range?: number[]
  platform?: string
  benchmark_book?: string
  compliance_note?: string
  status?: string
  _body?: string
  [k: string]: unknown
}

/** books/<书名>/config.json（规格01 §1） */
export interface BookConfig {
  genre_template?: string
  model_stages?: Record<string, string>
  chapter_word_range?: number[]
  review_fix_max_rounds?: number
  scene_mode?: boolean
  lint_whitelist?: string[]
  assembly_budget_overrides?: Record<string, unknown>
  [k: string]: unknown
}

/** 角色动态状态段（规格01 §2.3，每章由记忆卡回写） */
export interface CharacterStatus {
  realm?: string
  location?: string
  injuries?: string[]
  inventory?: string[]
  emotion?: string
  alive?: boolean
  known_info?: string[]
  updated_chapter?: number
  [k: string]: unknown
}

/** 快照正文回看（GET .../snapshots/{version}） */
export interface SnapshotDetail {
  version: number
  label: string
  created_at: string
  content: string
}

/** 人工手改正文结果（PUT .../prose） */
export interface ProseEditResult {
  ok: boolean
  word_count: number
}

/** 书级 model_stages 五档位（规格01 §1 config.json） */
export const MODEL_STAGES = ['plan', 'prose', 'polish', 'review', 'longctx'] as const
export type ModelStage = (typeof MODEL_STAGES)[number]
