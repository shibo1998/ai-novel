// 后端响应类型（契约以 ainovel/api.py 为准，宽松字段用索引签名兜住 dict[str, Any]）。

export interface BookCard {
  name: string
  title?: string
  genre?: string
  target_words?: number
  platform?: string
  status?: string
  author?: string
  external?: boolean
  [k: string]: unknown
}

export interface ChapterRow {
  no: number
  status: string
  current_version: number
  fix_rounds: number
  cost_millicents: number
  cost_cents: number
  cost_over_yuan: boolean
  [k: string]: unknown
}

export interface CostSummary {
  /** 千分之一分（毫分）。按分聚合会把单章 0.56 分进位成 0 */
  cost_millicents: number
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
  state_revision?: number
  blocks?: ProseBlock[]
}

export interface MechHit {
  rule: string
  level: string
  line: number
  word: string
  message: string
  effective_level?: string
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

export interface MechIneffective {
  rule: string
  reason: string
  detail: string
  /** true=禁止推进（词表缺失）；false=本阶段拿不到数据（如定稿后才有快照） */
  blocking?: boolean
}

export interface MechReport {
  verdict: string
  issues: MechHit[]
  fingerprint?: string | null
  ineffective?: MechIneffective[]
  checked_at?: string | null
}

/** GET .../checkstatus：结论 + 是否过期（正文改过即 stale） */
export interface CheckStatus {
  verdict: string | null
  checked_status?: string | null
  checked_at: string | null
  checked_fingerprint: string | null
  current_fingerprint: string
  stale: boolean
  ineffective: MechIneffective[]
  hits_count: { block: number; warn: number }
  imported?: boolean
}

/** AI 味确定性检测结果（只报不改） */
export interface DeslopHit {
  rule: string
  level: string
  line: number
  quote: string
  weight: number
  message: string
  effective_level?: string
}

export interface DeslopResult {
  score: number
  hits: DeslopHit[]
  ineffective: MechIneffective[]
  word_count: number
  hits_count: { blocking: number; advisory: number }
  density_penalty: number
  effective_level?: string
}

/** 半成品草稿状态（生成为中断时仍在） */
export interface DraftInfo {
  exists: boolean
  chars: number | null
  generated_at: string | null
  preview: string | null
  stale: boolean | null
}

export interface MemoryPendingItem {
  no: number
  title: string
  reason: string
  has_card: boolean
}

export interface TimelineRow {
  [k: string]: string
}

export interface TimelineData {
  exists: boolean
  truth: TimelineRow[]
  released: TimelineRow[]
  issues: MechHit[]
  path: string
  fingerprint?: string
}

/** 状态卡提议须人工确认后才进入已确认档案（规格05 C-05）。 */
export interface StateData {
  content: string
  path: string
  rows: number
  proposed_content: string
  proposed_fingerprint: string | null
  accepted_fingerprint: string | null
  proposed_stale: boolean
}

export interface AttachDetected {
  layout: string
  chapters: number
  txt_files: number
  has_config: boolean
  has_meta: boolean
  has_bible: boolean
  has_outline: boolean
  has_plot_points: boolean
  has_locations: boolean
  has_characters: boolean
}

export interface AttachResult {
  ok: boolean
  name: string
  path: string
  detected: AttachDetected
  scaffolded: string[]
  backup: string
}

export interface ImportChapterPreview {
  no: number
  title: string
  chars: number
  start_line: number
}

export interface ImportPreview {
  encoding: string
  total_chars: number
  chapters: ImportChapterPreview[]
  warnings: { kind: string; detail: string }[]
  guessed_regex: string
  file_path: string
}

export interface WorkbenchData {
  no: number
  status: string
  row: { current_version: number; state_revision: number; [k: string]: unknown } | null
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
  actual_requests?: ActualRequest[]
  latest_request?: ActualRequest | null
  warnings?: unknown[]
}

export interface VersionInfo {
  version: number
  label: string
  created_at: string
  [k: string]: unknown
}

/** 规格06 §7—10：候选只读，ready 与 applied 分开，ok 不表示已写回。 */
export interface ProseBlock {
  id: string
  text: string
}

export interface PolishBlocks {
  source_revision: number
  source_fingerprint: string
  blocks: ProseBlock[]
}

export type PolishStatus =
  | 'generating'
  | 'ready'
  | 'rejected'
  | 'no_change'
  | 'error'
  | 'interrupted'
  | 'applying'
  | 'applied'
  | 'discarded'
  | 'stale'
  | 'recovery_required'

export interface PolishPayload {
  expected_revision: number
  client_request_id: string
  mode: 'auto' | 'selected'
  selected_blocks: string[]
}

export interface PolishEdit {
  start_block: string
  end_block: string
  original: string
  replacement: string
  category: string
  reason: string
}

export interface PolishV3 {
  verdict?: string
  clean?: boolean
  style_verdict?: 'improved' | 'unchanged' | 'worse' | 'unknown'
  fidelity_verdict?: string
  [k: string]: unknown
}

export interface PolishSummary {
  candidate_id: string
  status: PolishStatus
  ready?: boolean
  source_revision?: number
  reason_code?: string | null
  reason?: string | null
  created_at?: string
}

export interface PolishResult extends PolishSummary {
  ok: boolean
  ready: boolean
  applied: boolean
  source_fingerprint: string
  candidate_fingerprint: string
  source_revision: number
  word_count?: { before: number; after: number }
  edits: PolishEdit[]
  checks?: Record<string, unknown> | unknown[]
  v3?: PolishV3 | null
  source?: string
  candidate?: string
  cost?: { calls: number; cost_millicents: number }
  warnings?: unknown[]
  receipt?: unknown
}

export interface PolishApproval {
  expected_revision: number
  source_fingerprint: string
  candidate_fingerprint: string
}

export interface PolishApplyResult {
  ok: boolean
  applied: boolean
  receipt: unknown
  state_revision: number
  status: string
}

/** 规格06 §5：实际请求证据；旧日志没有快照时不伪造 system/user。 */
export interface ActualRequest {
  request_id: string
  system: string
  user: string
  meta: {
    tokens?: unknown
    est_tokens?: unknown
    token_estimates?: unknown
    templates?: unknown
    style?: unknown
    warnings?: unknown[]
    [k: string]: unknown
  }
}

export type StyleScene =
  | 'opening'
  | 'dialogue'
  | 'action'
  | 'reasoning'
  | 'transition'
  | 'breakthrough'
  | 'climax'
  | 'general'

export interface StyleExampleInput {
  id?: string
  start_paragraph: number
  end_paragraph: number
  scene_types: StyleScene[]
}

export interface StyleExample extends StyleExampleInput {
  id: string
  content_fingerprint: string
}

export interface StyleExamples {
  schema_version: number
  corpus_fingerprint: string
  examples: StyleExample[]
}

export interface StyleData {
  corpus: string
  card: string
  enabled: boolean
  corpus_fingerprint: string
  card_fingerprint: string
  examples: StyleExamples | null
  examples_stale: boolean
  paragraphs: { id: string; no: number; text: string }[]
  warnings: unknown[]
}

export interface StyleSaveResult {
  fingerprint: string
  concurrency_protected: boolean
}

export interface StyleAnalysis {
  ok: boolean
  card: string
  corpus_fingerprint: string
  card_fingerprint: string
  applied: false
  digest: string
}

export interface FinalizeResult {
  snapshot_version: number
  writeback: unknown
  /** 细纲声明的伏笔操作与记忆卡实际执行的不一致，台账按记忆卡推进 */
  foreshadow_drift?: string[]
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
