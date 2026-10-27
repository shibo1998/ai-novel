// 全部后端 HTTP 封装（契约见 ainovel/api.py）。错误统一由 client.ts 拦截器弹 toast。
import { http } from './client'
import type {
  AppSettings,
  AttachResult,
  AuditReport,
  AuditResult,
  BookCard,
  BookConfig,
  BookMeta,
  CharacterData,
  CheckStatus,
  Conflict,
  DeslopResult,
  DraftInfo,
  FinalizeResult,
  ImportPreview,
  MechHit,
  MemoryPendingItem,
  OutlineFull,
  Overview,
  PolishResult,
  PreviewResult,
  ProseEditResult,
  ReviewResult,
  SnapshotDetail,
  StateData,
  TimelineData,
  TimelineRow,
  VersionInfo,
  WorkbenchData,
} from './types'

const enc = encodeURIComponent
const unwrap = <T>(p: Promise<{ data: T }>): Promise<T> => p.then((r) => r.data)

// —— 系统 ——
export const getFtsMode = (): Promise<{ mode: string }> => unwrap(http.get('/api/fts/mode'))
export const rebuildDb = (): Promise<Record<string, unknown>> =>
  unwrap(http.post('/api/db/rebuild'))

// —— 书 ——
export interface BookCreatePayload {
  name: string
  genre_template: string
  target_words: number
  author: string
  platform: string
}

export const listBooks = (): Promise<BookCard[]> => unwrap(http.get('/api/books'))
export const createBook = (body: BookCreatePayload): Promise<unknown> =>
  unwrap(http.post('/api/books', body))
export const deleteBook = (name: string): Promise<unknown> =>
  unwrap(http.delete(`/api/books/${enc(name)}`))
export const getOverview = (book: string): Promise<Overview> =>
  unwrap(http.get(`/api/books/${enc(book)}/overview`))

// —— 档案：meta / 书级 config ——
// meta.md（规格01 §2.1）：title/author/genre/tags/target_words/chapter_word_range/
// platform/benchmark_book/compliance_note/status 十个字段，UI 侧做成结构化表单。
export const getMeta = (book: string): Promise<BookMeta> =>
  unwrap(http.get(`/api/books/${enc(book)}/meta`))
export const putMeta = (book: string, meta: BookMeta): Promise<BookMeta> =>
  unwrap(http.put(`/api/books/${enc(book)}/meta`, meta))

// 书级配置（规格01 §1 config.json）：model_stages 五档位 / chapter_word_range /
// review_fix_max_rounds / scene_mode / lint_whitelist。
export const getBookConfig = (book: string): Promise<BookConfig> =>
  unwrap(http.get(`/api/books/${enc(book)}/config`))
export const putBookConfig = (book: string, cfg: BookConfig): Promise<BookConfig> =>
  unwrap(http.put(`/api/books/${enc(book)}/config`, cfg))

// —— 档案：Bible / 角色 / 大纲 / 台账 / 地点 / 文风 ——
export const getBible = (book: string): Promise<Record<string, string>> =>
  unwrap(http.get(`/api/books/${enc(book)}/bible`))
export const putBible = (book: string, sections: Record<string, string>): Promise<unknown> =>
  unwrap(http.put(`/api/books/${enc(book)}/bible`, sections))

export const listCharactersFull = (book: string): Promise<CharacterData[]> =>
  unwrap(http.get(`/api/books/${enc(book)}/characters`))
export const getCharacter = (book: string, name: string): Promise<CharacterData> =>
  unwrap(http.get(`/api/books/${enc(book)}/characters/${enc(name)}`))
export const putCharacter = (
  book: string,
  name: string,
  payload: CharacterData,
): Promise<unknown> => unwrap(http.put(`/api/books/${enc(book)}/characters/${enc(name)}`, payload))
export const createCharacter = (book: string, name: string, role: string): Promise<unknown> =>
  unwrap(http.post(`/api/books/${enc(book)}/characters`, { name, role }))

export const getOutlineFull = (book: string): Promise<OutlineFull> =>
  unwrap(http.get(`/api/books/${enc(book)}/outline`))
export const putOutlineFull = (book: string, od: OutlineFull): Promise<unknown> =>
  unwrap(http.put(`/api/books/${enc(book)}/outline`, od))

export const getPlotPoints = (book: string): Promise<Record<string, string>[]> =>
  unwrap(http.get(`/api/books/${enc(book)}/plot_points`))
export const putPlotPoints = (book: string, rows: Record<string, string>[]): Promise<unknown> =>
  unwrap(http.put(`/api/books/${enc(book)}/plot_points`, { rows }))
// 伏笔超期预警（规格01 PLOT_POINTS 校验之一）：planted 超 30 章未进 building。
// 后端只读重算，章号缺省按已有章节最大号；不传 chapter 即"按当前进度看"。
export const getOverdueForeshadows = (
  book: string,
  chapter?: number,
): Promise<{ chapter: number; grace: number; overdue: string[] }> =>
  unwrap(
    http.get(`/api/books/${enc(book)}/plot_points/overdue`, {
      params: chapter === undefined ? {} : { chapter },
    }),
  )

// 地点档案（规格01 §2.6，5 列）：原先只有 GET、前端零入口，规格04 §1 要求可编辑。
export const getLocations = (book: string): Promise<Record<string, string>[]> =>
  unwrap(http.get(`/api/books/${enc(book)}/locations`))
export const putLocations = (book: string, rows: Record<string, string>[]): Promise<unknown> =>
  unwrap(http.put(`/api/books/${enc(book)}/locations`, { rows }))

export interface StyleData {
  corpus: string
  card: string
  enabled: boolean
}
export const getStyle = (book: string): Promise<StyleData> =>
  unwrap(http.get(`/api/books/${enc(book)}/style`))
export const putStyleCorpus = (book: string, text: string): Promise<unknown> =>
  unwrap(http.put(`/api/books/${enc(book)}/style/corpus`, { text }))
export const putStyleCard = (book: string, text: string): Promise<unknown> =>
  unwrap(http.put(`/api/books/${enc(book)}/style/card`, { text }))
export const putStyleEnabled = (book: string, enabled: boolean): Promise<unknown> =>
  unwrap(http.put(`/api/books/${enc(book)}/style/enabled`, { enabled }))
export const analyzeStyleF1 = (
  book: string,
): Promise<{ ok: boolean; card: string; digest: string }> =>
  unwrap(http.post(`/api/books/${enc(book)}/style/analyze`, null, { timeout: 0 }))

// —— G 链：AI 生成档案 → 人工改定 → 采纳落盘 ——
export interface AIGenResult {
  text: string
  prompt_id: string
  digest: string
  input_tokens: number
  output_tokens: number
}
export const aiGenerate = (
  book: string,
  prompt_id: string,
  variables: Record<string, string>,
): Promise<AIGenResult> =>
  unwrap(http.post(`/api/books/${enc(book)}/ai/generate`, { prompt_id, variables }, { timeout: 0 }))

export const adoptArchive = (
  book: string,
  target: 'bible' | 'characters' | 'outline',
  content: string,
): Promise<Record<string, unknown>> =>
  unwrap(http.post(`/api/books/${enc(book)}/adopt/${target}`, { content }))

// —— 章节工作台 ——
export const getWorkbench = (book: string, no: number): Promise<WorkbenchData> =>
  unwrap(http.get(`/api/books/${enc(book)}/chapters/${no}`))
export const generateOutlineG6 = (book: string, no: number): Promise<unknown> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/outline/generate`, null, { timeout: 0 }))
export const confirmOutline = (
  book: string,
  no: number,
): Promise<{ ok: boolean; status: string }> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/outline/confirm`))
export const getOutlineRaw = (book: string, no: number): Promise<{ content: string }> =>
  unwrap(http.get(`/api/books/${enc(book)}/chapters/${no}/outline/raw`))
export interface ChapterOutlinePayload {
  fm?: Record<string, unknown>
  fm_raw?: string
  body: string
}
export const putChapterOutline = (
  book: string,
  no: number,
  payload: ChapterOutlinePayload,
): Promise<unknown> => unwrap(http.put(`/api/books/${enc(book)}/chapters/${no}/outline`, payload))
export const getAssemblyPreview = (book: string, no: number): Promise<PreviewResult> =>
  unwrap(http.get(`/api/books/${enc(book)}/chapters/${no}/preview`))
export const runMechcheck = (
  book: string,
  no: number,
): Promise<{ hits: MechHit[]; status: string }> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/mechcheck`, null, { timeout: 0 }))
export const runReview = (book: string, no: number): Promise<ReviewResult & { status: string }> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/review`, null, { timeout: 0 }))
export const runFix = (book: string, no: number): Promise<{ ok: boolean; word_count: number }> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/fix`, {}, { timeout: 0 }))
export const runPolish = (book: string, no: number): Promise<PolishResult> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/polish`, null, { timeout: 0 }))
export const runExempt = (book: string, no: number, reason: string): Promise<unknown> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/exempt`, { reason }))
export const backToOutline = (book: string, no: number): Promise<unknown> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/edit-outline`))
// error ──retry──▶ prev_status（规格01 §4：异常落 error 后由用户一键恢复，
// 否则该章永久卡死在 error，点「刷新」状态也不会变）。
export const retryChapter = (book: string, no: number): Promise<{ ok: boolean; status: string }> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/retry`))
// 定稿含 V4 记忆卡回写 + V6 卷末审计两次 LLM 调用（末章才触发 V6），
// 真实模型下长上下文必超 30s 默认超时 → 前端先报错、后端其实已定稿，用户重复提交得 400。
// 与 review/fix/polish/audit 一致：这条走 timeout: 0（不设前端超时，由后端决定）。
export const finalizeChapter = (book: string, no: number): Promise<FinalizeResult> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/finalize`, null, { timeout: 0 }))
// 定稿返工：finalized → pending_finalize。定稿后正文受快照保护，
// 要手改/修复/重写得先解锁（否则后端一律 400「已定稿，请先解锁」）。
export const unlockChapter = (book: string, no: number): Promise<{ ok: boolean; status: string }> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/unlock`))
// 定稿章的字词级订正：逐条 old→new，old 必须唯一命中、总改动 ≤5%、强制理由。
// 与 PUT /prose 的分工：那是整篇覆盖（仅未定稿），这是受控替换（仅已定稿）。
export const correctProse = (
  book: string,
  no: number,
  edits: { old: string; new: string }[],
  reason: string,
): Promise<{ version: number; applied: { old: string; new: string }[]; change_ratio: number }> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/prose/correct`, { edits, reason }))
export const listVersions = (book: string, no: number): Promise<VersionInfo[]> =>
  unwrap(http.get(`/api/books/${enc(book)}/chapters/${no}/versions`))
export const rewriteFrom = (
  book: string,
  no: number,
  base_version: number | null,
): Promise<{ ok: boolean; new_version: number }> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/rewrite`, { base_version }))

// 人工手改正文（规格03 §2.5 人工关三选项之一；finalized 状态后端返回 400）。
// 版本不变；正文变更后原检查结论失效，必须重新机检。修订号按打开编辑器时的值提交。
export const putProse = (
  book: string,
  no: number,
  content: string,
  expectedRevision?: number,
): Promise<ProseEditResult> =>
  unwrap(
    http.put(`/api/books/${enc(book)}/chapters/${no}/prose`, {
      content,
      ...(expectedRevision === undefined ? {} : { expected_revision: expectedRevision }),
    }),
  )

// 快照正文回看（规格04 §2「版本列表可回看任意快照」——只读，不触发重写）。
export const getSnapshot = (book: string, no: number, version: number): Promise<SnapshotDetail> =>
  unwrap(http.get(`/api/books/${enc(book)}/chapters/${no}/snapshots/${version}`))
export const runAudit = (book: string, lastChapter: number): Promise<AuditResult> =>
  unwrap(http.post(`/api/books/${enc(book)}/audit/${lastChapter}`, null, { timeout: 0 }))
// 卷末审计报告回看：审计结论写进 review_records，报告正文落在 books/<书名>/audits/*.md
export const listAudits = (book: string): Promise<AuditReport[]> =>
  unwrap(http.get(`/api/books/${enc(book)}/audits`))

// —— 裁决队列 ——
export const listConflicts = (): Promise<Conflict[]> => unwrap(http.get('/api/conflicts'))
export const resolveConflictApi = (
  id: number,
  action: string,
  manualValue?: unknown,
): Promise<unknown> =>
  unwrap(http.post(`/api/conflicts/${id}/resolve`, { action, manual_value: manualValue ?? null }))

// —— 设置 ——
export const getSettings = (): Promise<AppSettings> => unwrap(http.get('/api/settings'))
export const putSettings = (settings: Record<string, unknown>): Promise<unknown> =>
  unwrap(http.put('/api/settings', { settings }))
export interface ConnTestResult {
  ok: boolean
  reply?: string
  model?: string
  error?: string
}
export const testConnection = (): Promise<ConnTestResult> =>
  unwrap(http.post('/api/settings/test', {}, { timeout: 60_000 }))
export const getLint = (which: string): Promise<{ which: string; content: string }> =>
  unwrap(http.get(`/api/lint/${which}`))
export const putLint = (which: string, content: string): Promise<unknown> =>
  unwrap(http.put(`/api/lint/${which}`, { which, content }))

// —— 导出（后端为 POST；blob 下载。旧版 window.open 走 GET 会 405，此处一并修正） ——
async function downloadBlob(url: string): Promise<void> {
  const res = await http.post(url, null, { responseType: 'blob' })
  const dispo = res.headers['content-disposition'] as string | undefined
  const m = dispo?.match(/filename\*=UTF-8''([^;]+)/i)
  const fallback = url.split('/').pop() ?? 'download'
  const name = m ? decodeURIComponent(m[1]!) : fallback
  const href = URL.createObjectURL(res.data as Blob)
  const a = document.createElement('a')
  a.href = href
  a.download = name
  a.click()
  URL.revokeObjectURL(href)
}

export const exportTxt = (book: string): Promise<void> =>
  downloadBlob(`/api/books/${enc(book)}/export/txt`)
export const exportMd = (book: string): Promise<void> =>
  downloadBlob(`/api/books/${enc(book)}/export/md`)
export const exportZip = (book: string): Promise<void> =>
  downloadBlob(`/api/books/${enc(book)}/export/zip`)

// —— 规格05 能力补齐 ——
/** 机检结论 + 过期判定（正文改过 → stale） */
export const getCheckStatus = (book: string, no: number): Promise<CheckStatus> =>
  unwrap(http.get(`/api/books/${enc(book)}/chapters/${no}/checkstatus`))
/** AI 味确定性检测（零 token，只报不改） */
export const getDeslop = (book: string, no: number): Promise<DeslopResult> =>
  unwrap(http.get(`/api/books/${enc(book)}/chapters/${no}/deslop`))
/** 半成品草稿：生成中断后仍在，可采纳/丢弃 */
export const getDraft = (book: string, no: number): Promise<DraftInfo> =>
  unwrap(http.get(`/api/books/${enc(book)}/chapters/${no}/draft`))
export const adoptDraft = (
  book: string,
  no: number,
  force = false,
): Promise<{ ok: boolean; chars: number; status: string }> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/draft/adopt`, { force }))
export const discardDraft = (book: string, no: number): Promise<{ ok: boolean }> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/draft/discard`))
/** 缺卡章列表（导入待补 / 回写失败 / 卡过期） */
export const listMemoryPending = (book: string): Promise<{ chapters: MemoryPendingItem[] }> =>
  unwrap(http.get(`/api/books/${enc(book)}/memory-card/pending`))
export const rebuildMemoryCard = (book: string, no: number): Promise<Record<string, unknown>> =>
  unwrap(
    http.post(`/api/books/${enc(book)}/chapters/${no}/memory-card/rebuild`, null, { timeout: 0 }),
  )
export const rebuildMemoryBatch = (
  book: string,
  chapters?: number[],
): Promise<{ queued: number; done: number[]; failed: { no: number; error: string }[] }> =>
  unwrap(
    http.post(
      `/api/books/${enc(book)}/memory-card/rebuild-batch`,
      chapters ? { chapters } : { all_missing: true },
      { timeout: 0 },
    ),
  )
/** 挂载已存在的书稿目录（原地接管，不搬迁） */
export const attachBook = (body: {
  name: string
  path: string
  scaffold?: boolean
}): Promise<AttachResult> => unwrap(http.post('/api/books/attach', body))
export const detachBook = (book: string): Promise<Record<string, unknown>> =>
  unwrap(http.post(`/api/books/${enc(book)}/detach`, { delete_files: false }))
/** 导入 txt：两段式（预演不写盘 → 确认后落盘） */
export const previewImport = (
  book: string,
  file_path: string,
  regex?: string,
): Promise<ImportPreview> =>
  unwrap(http.post(`/api/books/${enc(book)}/import/novel/preview`, { file_path, regex }))
export const commitImport = (
  book: string,
  body: {
    file_path: string
    regex?: string
    encoding?: string
    mode?: 'draft' | 'finalized'
    rebuild_memory?: boolean
    confirm_warnings?: boolean
  },
): Promise<Record<string, unknown>> =>
  unwrap(http.post(`/api/books/${enc(book)}/import/novel/commit`, body, { timeout: 0 }))
/** 长期记忆状态卡：已确认内容与待确认提议（规格05 F-05 / C-05）。 */
export const getState = (book: string): Promise<StateData> =>
  unwrap(http.get(`/api/books/${enc(book)}/state`))
export const approveState = (
  book: string,
  fingerprint: string,
  expectedFingerprint: string | null,
): Promise<{ ok: true }> =>
  unwrap(
    http.post(`/api/books/${enc(book)}/state/approve`, {
      fingerprint,
      expected_fingerprint: expectedFingerprint,
    }),
  )
export const discardState = (book: string, fingerprint: string): Promise<{ ok: true }> =>
  unwrap(http.post(`/api/books/${enc(book)}/state/discard`, { fingerprint }))
export const getTimeline = (book: string): Promise<TimelineData> =>
  unwrap(http.get(`/api/books/${enc(book)}/timeline`))
export const putTimeline = (
  book: string,
  truth: TimelineRow[],
  released: TimelineRow[],
  expectedFingerprint?: string,
): Promise<{ ok: boolean; issues: MechHit[] }> =>
  unwrap(
    http.put(`/api/books/${enc(book)}/timeline`, {
      truth,
      released,
      ...(expectedFingerprint === undefined ? {} : { expected_fingerprint: expectedFingerprint }),
    }),
  )
