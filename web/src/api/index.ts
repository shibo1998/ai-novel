// 全部后端 HTTP 封装（契约见 ainovel/api.py）。错误统一由 client.ts 拦截器弹 toast。
import { http } from './client'
import type {
  AppSettings,
  AuditResult,
  BookCard,
  CharacterData,
  Conflict,
  FinalizeResult,
  MechHit,
  OutlineFull,
  Overview,
  PolishResult,
  PreviewResult,
  ReviewResult,
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

// —— 档案：Bible / 角色 / 大纲 / 台账 / 文风 ——
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
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/mechcheck`))
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
export const finalizeChapter = (book: string, no: number): Promise<FinalizeResult> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/finalize`))
export const listVersions = (book: string, no: number): Promise<VersionInfo[]> =>
  unwrap(http.get(`/api/books/${enc(book)}/chapters/${no}/versions`))
export const rewriteFrom = (
  book: string,
  no: number,
  base_version: number | null,
): Promise<{ ok: boolean; new_version: number }> =>
  unwrap(http.post(`/api/books/${enc(book)}/chapters/${no}/rewrite`, { base_version }))
export const runAudit = (book: string, lastChapter: number): Promise<AuditResult> =>
  unwrap(http.post(`/api/books/${enc(book)}/audit/${lastChapter}`, null, { timeout: 0 }))

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
export const exportZip = (book: string): Promise<void> =>
  downloadBlob(`/api/books/${enc(book)}/export/zip`)
