<script setup lang="ts">
// 章节工作台（规格07）：章节预检、编辑会话、正文映射、流式缓冲、统一检查与会话任务。
import { isAxiosError } from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  adoptDraft,
  backToOutline,
  confirmOutline,
  correctProse,
  discardDraft,
  finalizeChapter,
  generateOutlineG6,
  getAssemblyPreview,
  getCheckStatus,
  getDeslop,
  getDraft,
  getOutlineRawVersioned,
  getSnapshot,
  getWorkbench,
  listVersions,
  putChapterOutline,
  putProse,
  rebuildMemoryCard,
  retryChapter,
  rewriteFrom,
  runExempt,
  runFix,
  runMechcheck,
  runReview,
  unlockChapter,
} from '@/api'
import type {
  CheckStatus,
  DeslopResult,
  DraftInfo,
  MechHit,
  PreviewResult,
  ReviewIssue,
  SnapshotDetail,
  VersionInfo,
  WorkbenchData,
} from '@/api/types'
import { useBookStore } from '@/stores/book'
import {
  levelLabel,
  ruleLabel,
  stageLabel,
  statusLabel,
  tagType,
  verdictLabel,
} from '@/utils/status'
import { buildProseMapping, paragraphForLine, paragraphForUniqueQuote } from '@/utils/prose'
import { StreamTextBuffer, updateFollowState } from '@/utils/stream'
import { recordError, reportValidation } from '@/utils/errors'
import { hasPendingWrite, withTask } from '@/utils/tasks'
import { registerDirtyGuard } from '@/utils/editing'
import PolishPanel from '@/components/PolishPanel.vue'
import AssemblyPreview from '@/components/AssemblyPreview.vue'
import ChapterSelect from '@/components/ChapterSelect.vue'

const route = useRoute()
const router = useRouter()
const store = useBookStore()
const book = computed(() => String(route.params.name ?? ''))
const no = computed(() => Number(route.params.no))
const validNo = computed(() => Number.isInteger(no.value) && no.value > 0)
const writePending = computed(() => hasPendingWrite(book.value))
const data = ref<WorkbenchData | null>(null)
const loading = ref(false)
const loadFailed = ref(false)
const loadError = ref('')
const sideFailed = ref(false)
const versions = ref<VersionInfo[]>([])
const rewriteVer = ref<number | null>(null)
const viewVer = ref<number | null>(null)
const checkStatus = ref<CheckStatus | null>(null)
const deslopRes = ref<DeslopResult | null>(null)
const draft = ref<DraftInfo | null>(null)
let pageSession = 0
let loadRequest = 0

const promptOptions = [
  { value: '', label: '自动（卷首三章 W3 / 其余 W1）' },
  { value: 'W2', label: 'W2 对话专场' },
  { value: 'W4', label: 'W4 突破场景' },
  { value: 'W5', label: 'W5 高潮场景' },
]
const promptId = ref('')
const generating = ref(false)
const retrying = ref(false)
const streamText = ref('')
let streamBuffer: StreamTextBuffer | null = null
let abortCtrl: AbortController | null = null
const follow = ref(true)
const showReturn = ref(false)
const proseBox = ref<HTMLElement | null>(null)
const proseSource = computed(() => data.value?.prose?._body ?? '')
const proseMapping = computed(() => buildProseMapping(proseSource.value))
const showLineNumbers = ref(localStorage.getItem('proseLineNumbers') === '1')
const indent = ref(localStorage.getItem('proseIndent') !== '0')
const hitLine = ref<number | null>(null)
let hitTimer: number | undefined

const rawDialog = ref(false)
const rawText = ref('')
const rawBaseline = ref('')
const rawEtag = ref<string | null>(null)
const rawTarget = ref<{ book: string; no: number } | null>(null)
const rawSaving = ref(false)
const proseDialog = ref(false)
const proseEditText = ref('')
const proseEditBaseline = ref('')
const proseSaving = ref(false)
const proseEditRevision = ref<number>()
const proseEditConflict = ref(false)
const proseEditTarget = ref<{ book: string; no: number } | null>(null)
const preview = ref<PreviewResult | null>(null)
const previewLoading = ref(false)
const polishBusy = ref(false)
const autoRunning = ref(false)
const autoLog = ref<string[]>([])
const referenceOpen = ref<string[]>([])

const chapterNumbers = computed(() => data.value?.chapter_numbers ?? [])
const previousChapter = computed(
  () => chapterNumbers.value.filter((n) => n < no.value).at(-1) ?? null,
)
const nextChapter = computed(() => chapterNumbers.value.find((n) => n > no.value) ?? null)
const unplanned = computed(() =>
  Boolean(data.value && data.value.planned === false && !data.value.exists),
)
const missingOutlineRow = computed(() =>
  Boolean(data.value && data.value.planned === false && data.value.exists),
)
const canEditProse = computed(() =>
  [
    'generating',
    'mech_check',
    'lint_fix',
    'llm_review',
    'targeted_fix',
    'human_review',
    'pending_finalize',
    'error',
  ].includes(data.value?.status ?? ''),
)
const outlineEditable = computed(() =>
  ['awaiting_outline', 'outline_draft', 'error'].includes(data.value?.status ?? ''),
)
const restoreTo = computed(() =>
  statusLabel(
    typeof data.value?.row?.prev_status === 'string' ? String(data.value.row.prev_status) : '',
  ),
)
const dirty = computed(
  () =>
    (rawDialog.value && rawText.value !== rawBaseline.value) ||
    (proseDialog.value && proseEditText.value !== proseEditBaseline.value),
)

function autoNote(message: string): void {
  autoLog.value = [...autoLog.value, `${new Date().toLocaleTimeString()} ${message}`]
}
function archiveForOutline(): void {
  void router.push(
    `/book/${encodeURIComponent(book.value)}/archive?node=outline:rows&returnChapter=${no.value}`,
  )
}
function goChapter(target: number | null): void {
  if (target && chapterNumbers.value.includes(target))
    void router.push(`/book/${encodeURIComponent(book.value)}/chapter/${target}`)
}
function onScroll(): void {
  const el = proseBox.value
  if (!el || !generating.value) return
  const state = updateFollowState(el.scrollTop, el.clientHeight, el.scrollHeight)
  follow.value = state.following
  showReturn.value = state.showReturn
}
function scrollLatest(): void {
  const el = proseBox.value
  if (!el) return
  follow.value = true
  showReturn.value = false
  el.scrollTop = el.scrollHeight
}
function jumpToLine(line: number): void {
  if (!line) return // 0 是全文级提示，不能误跳第一段
  const paragraph = paragraphForLine(proseMapping.value, line)
  if (!paragraph) {
    ElMessage.warning('无法精确定位：原始行号不在当前正文中')
    return
  }
  follow.value = false
  hitLine.value = line
  void nextTick(() =>
    proseBox.value
      ?.querySelector<HTMLElement>(`[data-start-line="${paragraph.startLine}"]`)
      ?.scrollIntoView({ block: 'center', behavior: 'smooth' }),
  )
  if (hitTimer !== undefined) window.clearTimeout(hitTimer)
  hitTimer = window.setTimeout(() => {
    hitLine.value = null
  }, 2000)
}
function lineFromLocation(location: string): number {
  return Number(/第\s*(\d+)\s*行/.exec(location ?? '')?.[1] ?? 0)
}
function jumpReview(issue: ReviewIssue): void {
  const line = lineFromLocation(issue.location)
  if (line) {
    jumpToLine(line)
    return
  }
  const paragraph = paragraphForUniqueQuote(proseMapping.value, proseSource.value, issue.quote)
  if (!paragraph) {
    ElMessage.warning('无法精确定位：引用缺失、重复或不在当前正文中')
    return
  }
  jumpToLine(paragraph.startLine)
}
watch(showLineNumbers, (v) => localStorage.setItem('proseLineNumbers', v ? '1' : '0'))
watch(indent, (v) => localStorage.setItem('proseIndent', v ? '1' : '0'))

async function load(
  targetBook = book.value,
  targetNo = no.value,
  update = true,
): Promise<WorkbenchData | null> {
  if (!validNo.value && update) return null
  const session = pageSession
  const request = ++loadRequest
  if (update) {
    loading.value = true
    loadFailed.value = false
    loadError.value = ''
    data.value = null
  }
  try {
    const result = await getWorkbench(targetBook, targetNo)
    if (!update) return result
    if (
      session !== pageSession ||
      request !== loadRequest ||
      targetBook !== book.value ||
      targetNo !== no.value
    )
      return null
    if (
      !Array.isArray(result.chapter_numbers) ||
      result.outline_generation === undefined ||
      result.planned === undefined
    ) {
      data.value = {
        ...result,
        chapter_numbers: Array.isArray(result.chapter_numbers)
          ? result.chapter_numbers
          : [targetNo],
        max_chapter: result.max_chapter ?? targetNo,
        planning_error: result.planning_error ?? {
          code: 'SERVICE_MISMATCH',
          message: '服务版本缺少章节预检字段；已有内容可读，细纲生成已禁用',
        },
        planned: result.planned === undefined ? null : result.planned,
        exists: result.exists ?? Boolean(result.outline || result.prose),
        outline_generation: result.outline_generation ?? {
          allowed: false,
          code: 'SERVICE_MISMATCH',
          message: '请升级后端以恢复细纲生成预检',
        },
      }
    } else data.value = result
    streamText.value = ''
    store.rememberChapter(targetBook, targetNo)
    // 尚未生成正文时，检查/草稿/文风接口必然 404；这是空状态而不是操作失败。
    if (result.prose) {
      void loadSideState()
    } else {
      sideFailed.value = false
      checkStatus.value = null
      draft.value = null
      deslopRes.value = null
    }
    void loadVersions()
    return result
  } catch (error) {
    if (update && session === pageSession && request === loadRequest) {
      loadFailed.value = true
      loadError.value = error instanceof Error ? error.message : '章节读取失败'
    }
    if (!update) throw error
    return null
  } finally {
    if (update && request === loadRequest) loading.value = false
  }
}
async function reload(): Promise<void> {
  await load()
}
async function loadSideState(): Promise<void> {
  if (!data.value?.prose) {
    sideFailed.value = false
    checkStatus.value = null
    draft.value = null
    deslopRes.value = null
    return
  }
  const b = book.value,
    n = no.value,
    session = pageSession
  const results = await Promise.allSettled([getCheckStatus(b, n), getDraft(b, n), getDeslop(b, n)])
  if (session !== pageSession || b !== book.value || n !== no.value) return
  sideFailed.value = results.some((r) => r.status === 'rejected')
  checkStatus.value = results[0].status === 'fulfilled' ? results[0].value : null
  draft.value = results[1].status === 'fulfilled' ? results[1].value : null
  deslopRes.value = results[2].status === 'fulfilled' ? results[2].value : null
}
async function loadVersions(): Promise<void> {
  const b = book.value,
    n = no.value,
    session = pageSession
  try {
    const result = await listVersions(b, n)
    if (session !== pageSession || b !== book.value || n !== no.value) return
    versions.value = result
    rewriteVer.value = result.at(-1)?.version ?? null
  } catch {
    /* 附属失败不抹掉主内容 */
  }
}
watch(
  [book, no],
  () => {
    pageSession++
    abortCtrl?.abort()
    streamBuffer?.dispose()
    autoRunning.value = false
    data.value = null
    versions.value = []
    checkStatus.value = null
    draft.value = null
    deslopRes.value = null
    preview.value = null
    rawDialog.value = false
    proseDialog.value = false
    if (validNo.value) void load()
    else {
      loadFailed.value = true
      loadError.value = '章号必须是正整数'
    }
  },
  { immediate: true },
)

async function confirmDirty(): Promise<'save' | 'discard' | 'cancel'> {
  if (!dirty.value) return 'discard'
  if (rawSaving.value || proseSaving.value) return 'cancel'
  try {
    const action = await ElMessageBox.confirm(
      '当前编辑框有未保存修改。保存后继续，或放弃修改？',
      '章节修改未保存',
      {
        type: 'warning',
        distinguishCancelAndClose: true,
        confirmButtonText: '保存后继续',
        cancelButtonText: '放弃修改',
        showClose: true,
      },
    )
    return action === 'confirm' ? 'save' : 'discard'
  } catch (action) {
    return action === 'cancel' ? 'discard' : 'cancel'
  }
}
const unregisterDirty = registerDirtyGuard(
  async () => {
    const decision = await confirmDirty()
    if (decision !== 'save') return decision
    const ok = rawDialog.value ? await saveRaw() : await saveProseEdit()
    return ok ? 'save' : 'cancel'
  },
  () => dirty.value,
)
onUnmounted(() => {
  pageSession++
  abortCtrl?.abort()
  streamBuffer?.dispose()
  unregisterDirty()
  if (hitTimer !== undefined) window.clearTimeout(hitTimer)
})

async function longCall<T>(
  operation: string,
  write: boolean,
  call: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const b = book.value,
    n = no.value
  return withTask(
    {
      operation,
      book: b,
      chapter: n,
      write,
      returnPath: `/book/${encodeURIComponent(b)}/chapter/${n}`,
      confirm: async () => {
        const result = await load(b, n, false)
        return result ? `原目标可读取，当前状态：${statusLabel(result.status)}` : '原目标不可读取'
      },
    },
    (signal) => call(signal),
  )
}
async function genOutline(): Promise<void> {
  const precheck = data.value?.outline_generation
  if (!precheck?.allowed) {
    ElMessage.warning(precheck?.message ?? '预检不可用，禁止生成')
    return
  }
  try {
    await longCall('生成章节细纲', true, (signal) =>
      generateOutlineG6(book.value, no.value, signal),
    )
    ElMessage.success('细纲已生成（未确认）')
    await reload()
  } catch {
    /* 统一错误 */
  }
}
async function confirmOutlineAct(): Promise<void> {
  try {
    await ElMessageBox.confirm('确认细纲后将锁定，正文阶段不再接受细纲变更。确认？', '确认细纲', {
      type: 'warning',
    })
  } catch {
    return
  }
  try {
    await confirmOutline(book.value, no.value)
    ElMessage.success('细纲已锁定')
    await reload()
  } catch {
    /* 统一错误 */
  }
}
async function editRaw(): Promise<void> {
  const b = book.value,
    n = no.value
  try {
    const response = await getOutlineRawVersioned(b, n)
    if (b !== book.value || n !== no.value) return
    if (!response.etag) throw new Error('细纲读取缺少 ETag，禁止无保护保存')
    rawText.value = response.data.content
    rawBaseline.value = response.data.content
    rawEtag.value = response.etag
    rawTarget.value = { book: b, no: n }
    rawDialog.value = true
  } catch {
    /* 统一错误 */
  }
}
async function saveRaw(): Promise<boolean> {
  const target = rawTarget.value
  if (!target || !rawEtag.value || rawSaving.value) return false
  if (rawText.value === rawBaseline.value) return true
  const m = rawText.value.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/)
  if (!m) {
    reportValidation('格式应为 --- frontmatter --- 正文', {
      operation: '细纲编辑',
      book: target.book,
      chapter: target.no,
    })
    return false
  }
  try {
    await ElMessageBox.confirm(
      `将覆盖第 ${target.no} 章细纲原文；按打开时 ETag 校验，其他章节不变。`,
      '确认细纲变更',
      { type: 'warning' },
    )
  } catch {
    return false
  }
  rawSaving.value = true
  try {
    await putChapterOutline(target.book, target.no, { fm_raw: m[1]!, body: m[2]! }, rawEtag.value)
    rawBaseline.value = rawText.value
    rawDialog.value = false
    ElMessage.success('细纲已保存')
    if (target.book === book.value && target.no === no.value) await reload()
    return true
  } catch {
    return false
  } finally {
    rawSaving.value = false
  }
}
async function saveOutlineField(key: string, value: unknown): Promise<void> {
  const d = data.value
  if (!d?.outline) return
  const b = book.value,
    n = no.value
  try {
    const version = await getOutlineRawVersioned(b, n)
    if (!version.etag) throw new Error('细纲版本不可用')
    const fm: Record<string, unknown> = { ...d.outline }
    delete fm._body
    if (key === 'hook.type') fm.hook = { ...(d.outline.hook ?? {}), type: value }
    else fm[key] = value
    await putChapterOutline(b, n, { fm, body: d.outline._body ?? '' }, version.etag)
    ElMessage.success('细纲字段已保存')
    await reload()
  } catch {
    /* 统一错误 */
  }
}

function openProseEdit(): void {
  const revision = data.value?.row?.state_revision
  if (revision === undefined) {
    ElMessage.warning('章节修订号不可用，请刷新后重试')
    return
  }
  proseEditRevision.value = revision
  proseEditTarget.value = { book: book.value, no: no.value }
  proseEditConflict.value = false
  proseEditText.value = proseSource.value
  proseEditBaseline.value = proseSource.value
  proseDialog.value = true
}
async function saveProseEdit(): Promise<boolean> {
  const target = proseEditTarget.value
  if (
    !target ||
    proseSaving.value ||
    proseEditRevision.value === undefined ||
    proseEditConflict.value
  )
    return false
  if (proseEditText.value === proseEditBaseline.value) return true
  try {
    await ElMessageBox.confirm(
      `将覆盖第 ${target.no} 章正文（${proseEditBaseline.value.length} → ${proseEditText.value.length} 字符），保存后旧检查失效。`,
      '确认正文变更',
      { type: 'warning' },
    )
  } catch {
    return false
  }
  proseSaving.value = true
  try {
    const result = await putProse(
      target.book,
      target.no,
      proseEditText.value,
      proseEditRevision.value,
    )
    proseEditBaseline.value = proseEditText.value
    proseDialog.value = false
    ElMessage.success(`正文已保存，${result.word_count} 字；请重新机检`)
    if (target.book === book.value && target.no === no.value) await reload()
    return true
  } catch (error) {
    proseEditConflict.value = isAxiosError(error) && error.response?.status === 409
    return false
  } finally {
    proseSaving.value = false
  }
}

async function generate(): Promise<void> {
  if (generating.value) return
  const b = book.value,
    n = no.value,
    session = pageSession
  generating.value = true
  streamText.value = ''
  streamBuffer?.dispose()
  streamBuffer = new StreamTextBuffer((text) => {
    if (session === pageSession && b === book.value && n === no.value) {
      streamText.value = text
      if (follow.value) void nextTick(scrollLatest)
    }
  })
  follow.value = true
  abortCtrl = new AbortController()
  const controller = abortCtrl
  try {
    await withTask(
      {
        operation: '流式生成正文',
        book: b,
        chapter: n,
        write: true,
        returnPath: `/book/${encodeURIComponent(b)}/chapter/${n}`,
        confirm: async () => {
          const result = await load(b, n, false)
          return result ? `已核验原目标，当前状态：${statusLabel(result.status)}` : '仍无法确认'
        },
      },
      async () => {
        const response = await fetch(`/api/books/${encodeURIComponent(b)}/chapters/${n}/generate`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(promptId.value ? { prompt_id: promptId.value } : {}),
          signal: controller.signal,
        })
        if (!response.ok || !response.body) {
          let detail: unknown = `HTTP ${response.status}`
          try {
            detail = (await response.json()) as unknown
          } catch {
            /* 保留 HTTP */
          }
          const error = {
            response: {
              status: response.status,
              data: typeof detail === 'object' ? detail : { detail },
            },
            message: `正文生成 HTTP ${response.status}`,
          }
          recordError(error, { operation: '流式生成正文', book: b, chapter: n, write: true })
          throw error
        }
        const reader = response.body.getReader()
        const decoder = new TextDecoder()
        let buffer = '',
          terminal = false
        while (!terminal) {
          const { value, done } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
          let index: number
          while ((index = buffer.indexOf('\n\n')) >= 0) {
            const frame = buffer.slice(0, index)
            buffer = buffer.slice(index + 2)
            let event = 'message',
              payload = ''
            for (const line of frame.split('\n')) {
              if (line.startsWith('event:')) event = line.slice(6).trim()
              else if (line.startsWith('data:')) payload += line.slice(5).trimStart()
            }
            if (event === 'done') {
              terminal = true
              break
            }
            if (event === 'error') {
              const error = { code: 'SSE_ERROR', message: payload || '生成失败' }
              throw error
            }
            try {
              streamBuffer?.append((JSON.parse(payload) as { text?: string }).text ?? '')
            } catch {
              /* 非 JSON 心跳 */
            }
          }
        }
        buffer += decoder.decode()
        streamBuffer?.flush()
        if (!terminal) {
          const error = {
            code: 'SSE_INCOMPLETE',
            message: '流式连接结束但未收到完成/失败帧，结果待确认',
          }
          throw error
        }
      },
    )
    if (session === pageSession && b === book.value && n === no.value) {
      ElMessage.success('生成完成，已读取落盘正文')
      await reload()
    }
  } catch {
    if (controller.signal.aborted && session === pageSession)
      ElMessage.info('已停止本页等待；后台可能继续生成，请在任务条确认结果')
  } finally {
    streamBuffer?.flush()
    if (session === pageSession) generating.value = false
    if (abortCtrl === controller) abortCtrl = null
  }
}

async function mech(): Promise<void> {
  try {
    const r = await longCall('机械检查', true, (signal) =>
      runMechcheck(book.value, no.value, signal),
    )
    ElMessage.success(`机械检查完成：${statusLabel(r.status)}`)
    await reload()
  } catch {
    /* 统一错误 */
  }
}
async function review(): Promise<void> {
  try {
    const r = await longCall('模型审校', true, (signal) => runReview(book.value, no.value, signal))
    ElMessage.success(`审校${verdictLabel(r.verdict)}：${statusLabel(r.status)}`)
    await reload()
  } catch {
    /* 统一错误 */
  }
}
async function fix(): Promise<void> {
  try {
    const r = await longCall(stageLabel('P-V5'), true, (signal) =>
      runFix(book.value, no.value, signal),
    )
    ElMessage.success(`修复完成 ${r.word_count} 字，请重新机检`)
    await reload()
  } catch {
    /* 统一错误 */
  }
}
const AUTO_TERMINAL = ['pending_finalize', 'human_review', 'finalized', 'error']
async function runAuto(): Promise<void> {
  if (autoRunning.value) return
  const target = { book: book.value, no: no.value, session: pageSession }
  const current = () =>
    target.session === pageSession && target.book === book.value && target.no === no.value
  autoRunning.value = true
  autoLog.value = []
  try {
    let status = data.value?.status ?? ''
    if (status !== 'mech_check') {
      ElMessage.warning(`当前${statusLabel(status)}不能自动编排，需先进入机械检查`)
      return
    }
    for (let round = 1; round <= 3; round++) {
      autoNote(`第 ${round} 轮 · 机械检查`)
      const m = await runMechcheck(target.book, target.no)
      if (!current()) return
      await reload()
      status = m.status
      autoNote(`机检完成 → ${statusLabel(status)}`)
      if (AUTO_TERMINAL.includes(status) || status !== 'llm_review') break
      autoNote('模型审校')
      const r = await runReview(target.book, target.no)
      if (!current()) return
      await reload()
      status = r.status
      autoNote(`审校${verdictLabel(r.verdict)} → ${statusLabel(status)}`)
      if (AUTO_TERMINAL.includes(status) || status !== 'mech_check') break
    }
    const final = data.value?.status ?? status
    if (final === 'pending_finalize') ElMessage.success('已到待定稿，请人工通读')
    else if (final === 'human_review') ElMessage.warning('需人工复核，自动编排停止')
    else ElMessage.info(`自动编排停止：${statusLabel(final)}`)
  } catch {
    /* 统一错误 */
  } finally {
    if (target.session === pageSession) autoRunning.value = false
  }
}

const blockingHits = computed(() =>
  (data.value?.mech?.issues ?? []).filter((i) => effectiveLevel(i) === 'block'),
)
const warningHits = computed(() =>
  (data.value?.mech?.issues ?? []).filter((i) => effectiveLevel(i) === 'warn'),
)
const referenceHits = computed(() => {
  const all: (MechHit & { quote?: string })[] = [
    ...(data.value?.mech?.issues ?? []).filter((i) => effectiveLevel(i) === 'advisory'),
  ]
  for (const hit of deslopRes.value?.hits ?? []) {
    if (
      !all.some(
        (i) =>
          i.rule === hit.rule &&
          i.line === hit.line &&
          (i.word || i.message) === (hit.quote || hit.message),
      )
    )
      all.push({ ...hit, word: hit.quote })
  }
  return all
})
function effectiveLevel(hit: MechHit): 'block' | 'warn' | 'advisory' {
  if (hit.rule === 'M-12' || hit.rule.startsWith('D-') || hit.effective_level === 'advisory')
    return 'advisory'
  return hit.level === 'block' || hit.level === 'blocking' ? 'block' : 'warn'
}
function ineffectiveText(list: { rule: string; reason: string; detail: string }[]): string {
  const reasons: Record<string, string> = {
    missing_file: '文件缺失',
    empty_file: '文件为空',
    suspiciously_small: '条目过少',
    bad_threshold: '阈值非法',
    disabled: '已关闭',
    unparsed: '无法解析',
    crash: '检查器异常',
  }
  return list
    .map(
      (i) =>
        `${ruleLabel(i.rule)}（${reasons[i.reason] ?? `未知原因（${i.reason}）`}：${i.detail}）`,
    )
    .join('；')
}

/** 规格09 §2.2/3：只格式化有限数值；缺值不冒充零或通过。 */
function metricNumber(value: number | null | undefined, unit = '', scale = 1, digits = 1): string {
  if (typeof value !== 'number' || !Number.isFinite(value * scale)) return '无法计算'
  return `${(value * scale).toFixed(digits)}${unit}`
}
function proseMetricsText(metrics: DeslopResult['metrics']): {
  mean: string
  distribution: string
  density: string
  cues: string
} {
  if (!metrics || metrics.version !== 1)
    return { mean: '未提供', distribution: '未提供', density: '未提供', cues: '未提供' }
  const paragraphs = metrics.paragraphs
  const empty = paragraphs?.count === 0
  const similes = metrics.simile_cues
  const mean = empty ? '无正文，无法计算' : metricNumber(paragraphs?.mean_chars, ' 字')
  const distribution = `段数：${metricNumber(paragraphs?.count, '', 1, 0)} · 中位：${metricNumber(paragraphs?.median_chars, ' 字')} · P90：${metricNumber(paragraphs?.p90_chars, ' 字')} · ≤10字短段占比：${metricNumber(paragraphs?.short_ratio, '%', 100)}`
  if (similes?.status === 'unavailable') {
    return {
      mean,
      distribution,
      density: `无法计量：${similes.reason?.trim() || '计量配置不可用'}`,
      cues: '显式线索数：无法计算',
    }
  }
  if (similes?.status !== 'measured')
    return { mean, distribution, density: '无法计算', cues: '显式线索数：无法计算' }
  return {
    mean,
    distribution,
    density: empty ? '无正文，无法计算' : metricNumber(similes.per_1000_chars, ' 处/千可见字符'),
    cues: `显式线索数：${metricNumber(similes.count, '', 1, 0)}`,
  }
}
const proseMetricsDisplay = computed(() => proseMetricsText(deslopRes.value?.metrics))

async function simpleAction(
  operation: string,
  action: () => Promise<unknown>,
  success: string,
): Promise<void> {
  try {
    await action()
    ElMessage.success(`${operation}：${success}`)
    await reload()
  } catch {
    /* 统一错误 */
  }
}
const retry = async () => {
  retrying.value = true
  try {
    const r = await retryChapter(book.value, no.value)
    ElMessage.success(`已恢复到${statusLabel(r.status)}`)
    await reload()
  } catch {
    /* 统一错误 */
  } finally {
    retrying.value = false
  }
}
const backOutline = async () => {
  try {
    await ElMessageBox.confirm('回到细纲草稿？正文保留，改完需重新确认并生成。', '改细纲重写', {
      type: 'warning',
    })
    await simpleAction('返回细纲', () => backToOutline(book.value, no.value), '已回到细纲草稿')
  } catch {
    /* 取消 */
  }
}
const unlock = async () => {
  try {
    await ElMessageBox.confirm('解锁退回待定稿，快照和版本保留。确认？', '定稿返工', {
      type: 'warning',
    })
    await simpleAction('解锁返工', () => unlockChapter(book.value, no.value), '已解锁')
  } catch {
    /* 取消 */
  }
}
async function finalize(): Promise<void> {
  try {
    await ElMessageBox.confirm('定稿将写快照并触发记忆卡回写。第一次确认继续？', '定稿', {
      type: 'warning',
    })
    await ElMessageBox.confirm('第二次确认：真的定稿？', '定稿', { type: 'warning' })
  } catch {
    return
  }
  try {
    const r = await longCall('章节定稿', true, (signal) =>
      finalizeChapter(book.value, no.value, signal),
    )
    ElMessage.success(`已定稿，快照 v${r.snapshot_version}`)
    await reload()
    void store.refreshConflicts()
  } catch {
    /* 统一错误 */
  }
}
async function exempt(): Promise<void> {
  let reason = ''
  try {
    reason = (
      await ElMessageBox.prompt('豁免理由（必填并写入日志）', '豁免', {
        inputValidator: (v: string) => (v?.trim() ? true : '理由必填'),
      })
    ).value
  } catch {
    return
  }
  await simpleAction(
    '人工豁免',
    () => runExempt(book.value, no.value, reason),
    '已豁免，进入待定稿',
  )
}
async function rewrite(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `从 v${rewriteVer.value ?? '当前'} 重写？旧版本保留。`,
      '从版本重写',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    const r = await rewriteFrom(book.value, no.value, rewriteVer.value)
    ElMessage.success(`新分支 v${r.new_version}`)
    await reload()
  } catch {
    /* 统一错误 */
  }
}
async function adoptDraftNow(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `草稿将覆盖本章正文并立刻重跑机检，共 ${draft.value?.chars ?? 0} 字。`,
      '采纳草稿',
      { type: 'warning' },
    )
  } catch {
    return
  }
  await simpleAction('采纳断线草稿', () => adoptDraft(book.value, no.value, true), '草稿已采纳')
}
const discardDraftNow = () =>
  simpleAction('丢弃断线草稿', () => discardDraft(book.value, no.value), '草稿已丢弃')
async function rebuildMemory(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '补做记忆卡会调用 V4，并先回滚本章伏笔推进和状态快照。继续？',
      '补做记忆卡',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await longCall('补做记忆卡', true, (signal) => rebuildMemoryCard(book.value, no.value, signal))
    ElMessage.success('记忆卡已补做')
    await reload()
  } catch {
    /* 统一错误 */
  }
}
async function loadPreview(): Promise<void> {
  if (previewLoading.value) return
  previewLoading.value = true
  try {
    preview.value = await getAssemblyPreview(book.value, no.value)
  } catch {
    /* 统一错误 */
  } finally {
    previewLoading.value = false
  }
}
const snapshotDialog = ref(false),
  snapshotLoading = ref(false),
  snapshotData = ref<SnapshotDetail | null>(null)
async function viewSnapshot(version: number | null): Promise<void> {
  if (!version) {
    ElMessage.warning('先选择快照')
    return
  }
  snapshotDialog.value = true
  snapshotLoading.value = true
  try {
    snapshotData.value = await getSnapshot(book.value, no.value, version)
  } catch {
    snapshotDialog.value = false
  } finally {
    snapshotLoading.value = false
  }
}
const correctDialog = ref(false),
  correctText = ref(''),
  correctReason = ref(''),
  correctSaving = ref(false)
async function submitCorrect(): Promise<void> {
  const edits = correctText.value
    .split('\n')
    .filter((l) => l.trim())
    .map((line) => {
      const i = line.indexOf('==>>')
      if (i <= 0) throw new Error('每行格式：旧文 ==>> 新文')
      return { old: line.slice(0, i).trim(), new: line.slice(i + 4).trim() }
    })
  if (!edits.length || !correctReason.value.trim()) {
    reportValidation('至少一条订正且理由必填', {
      operation: '定稿章订正',
      book: book.value,
      chapter: no.value,
    })
    return
  }
  correctSaving.value = true
  try {
    const r = await correctProse(book.value, no.value, edits, correctReason.value)
    ElMessage.success(`已订正 ${r.applied.length} 处`)
    correctDialog.value = false
    await reload()
  } catch {
    /* 统一错误 */
  } finally {
    correctSaving.value = false
  }
}
</script>

<template>
  <el-result v-if="!validNo" icon="error" title="章号无效" sub-title="章号必须是正整数"
    ><template #extra
      ><el-button @click="router.push(`/book/${encodeURIComponent(book)}`)"
        >返回书总览</el-button
      ></template
    ></el-result
  >
  <el-skeleton v-else-if="loading" :rows="12" animated />
  <el-result v-else-if="loadFailed" icon="error" title="章节加载失败" :sub-title="loadError"
    ><template #extra
      ><el-button @click="reload">重试</el-button
      ><el-button @click="router.push(`/book/${encodeURIComponent(book)}`)">返回书总览</el-button
      ><el-button @click="router.push('/shelf')">返回书架</el-button></template
    ></el-result
  >
  <template v-else-if="data">
    <div class="panel flex flex-wrap items-center gap-2">
      <el-button @click="router.push(`/book/${encodeURIComponent(book)}`)">← 书总览</el-button>
      <el-button
        type="primary"
        plain
        @click="
          router.push({
            path: `/book/${encodeURIComponent(book)}/volumes`,
            query: { volume: route.query.volume, job: route.query.productionJob },
          })
        "
        >← 卷工作台 / 待审稿</el-button
      >
      <el-button :disabled="!previousChapter" @click="goChapter(previousChapter)"
        >← 上一章</el-button
      >
      <ChapterSelect
        :model-value="no"
        :chapters="chapterNumbers"
        :rows="
          data.no
            ? [
                {
                  no: data.no,
                  status: data.status,
                  current_version: data.row?.current_version ?? 0,
                  fix_rounds: 0,
                  cost_millicents: 0,
                  cost_cents: 0,
                  cost_over_yuan: false,
                },
              ]
            : []
        "
        @select="goChapter"
      />
      <el-button :disabled="!nextChapter" @click="goChapter(nextChapter)">下一章 →</el-button>
      <el-tag :type="tagType(data.status)" :title="data.status">{{
        statusLabel(data.status)
      }}</el-tag>
      <span class="dim"
        >版本 {{ data.row ? 'v' + data.row.current_version : '-' }} · 本章成本
        {{ data.cost ? (data.cost.cost_millicents / 100000).toFixed(2) + '元' : '未知' }}</span
      >
      <span class="flex-1"></span><el-button @click="archiveForOutline">补 / 修大纲</el-button
      ><el-button @click="reload">刷新</el-button>
    </div>

    <el-result
      v-if="unplanned"
      icon="info"
      :title="`第 ${no} 章尚未规划`"
      sub-title="数据库残留记录不代表章节已规划；请先补章节总表，或返回总览选择已有章。"
      ><template #extra
        ><el-button type="primary" @click="archiveForOutline">去补大纲</el-button
        ><el-button @click="router.push(`/book/${encodeURIComponent(book)}`)"
          >返回书总览</el-button
        ></template
      ></el-result
    >
    <template v-else>
      <el-alert
        v-if="data.planned === null"
        type="error"
        :closable="false"
        show-icon
        class="mb-2"
        :title="`大纲读取失败：${data.planning_error?.message ?? '未知解析错误'}。已有章节仍可查看，但禁止生成细纲。`"
        ><el-button @click="reload">重试</el-button
        ><el-button @click="archiveForOutline">修复档案</el-button></el-alert
      >
      <el-alert
        v-else-if="missingOutlineRow"
        type="warning"
        :closable="false"
        show-icon
        class="mb-2"
        title="大纲缺少本章行；已有导入正文/细纲仍保留，禁止重新生成细纲。"
        ><el-button @click="archiveForOutline">去补大纲</el-button></el-alert
      >
      <el-alert
        v-if="!data.outline_generation.allowed"
        type="info"
        :closable="false"
        class="mb-2"
        :title="data.outline_generation.message ?? '当前不可生成细纲'"
      />
      <el-alert
        v-if="data.planning_gate && !data.planning_gate.allowed"
        type="warning"
        :closable="false"
        show-icon
        class="mb-2"
        :title="`规划门禁：${data.planning_gate.message}`"
        ><el-button
          @click="router.push(`/book/${encodeURIComponent(book)}/planning?returnChapter=${no}`)"
          >去内容规划</el-button
        ></el-alert
      >
      <el-alert
        v-if="writePending"
        type="warning"
        :closable="false"
        show-icon
        class="mb-2"
        title="本书有结果待确认的写任务，后台可能仍在写入；浏览与刷新可用，请先确认结果再继续写操作。"
      />
      <el-alert
        v-if="sideFailed"
        type="warning"
        :closable="false"
        class="mb-2"
        title="部分检查/草稿附属信息读取失败，状态未知；主正文仍可用。"
        ><el-button @click="loadSideState">重试附属信息</el-button></el-alert
      >
      <el-alert
        v-if="draft?.exists"
        type="warning"
        :closable="false"
        class="mb-2"
        :title="`检测到 ${draft.chars} 字断线草稿，采纳会覆盖正文并重跑机检。`"
        ><el-button @click="adoptDraftNow">采纳草稿</el-button
        ><el-button @click="discardDraftNow">丢弃</el-button></el-alert
      >

      <div class="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <div>
          <div class="panel">
            <h3 class="panel-title">① 细纲（章合同）</h3>
            <div class="mb-2 flex flex-wrap gap-2">
              <el-button
                type="primary"
                :disabled="!data.outline_generation.allowed || writePending"
                @click="genOutline"
                title="提示词 G6"
                >生成细纲</el-button
              ><el-button :disabled="writePending" @click="confirmOutlineAct"
                >确认细纲（锁定）</el-button
              ><el-button @click="editRaw">编辑细纲原文</el-button>
            </div>
            <template v-if="data.outline"
              ><div><b>核心事件：</b>{{ data.outline.core_event }}</div>
              <div class="my-2 flex flex-wrap gap-2">
                <span>阶段</span
                ><el-select
                  :model-value="data.outline.stage"
                  :disabled="!outlineEditable"
                  @change="(v: string) => saveOutlineField('stage', v)"
                  ><el-option
                    v-for="s in ['引入', '发展', '转折', '高潮', '收束']"
                    :key="s"
                    :value="s"
                    :label="s" /></el-select
                ><span>情绪</span
                ><el-select
                  :model-value="data.outline.emotion_task"
                  :disabled="!outlineEditable"
                  @change="(v: string) => saveOutlineField('emotion_task', v)"
                  ><el-option
                    v-for="s in ['压', '小扬', '爆']"
                    :key="s"
                    :value="s"
                    :label="s" /></el-select
                ><span>视角 {{ data.outline.pov }}</span
                ><el-select
                  :model-value="data.outline.hook?.type"
                  :disabled="!outlineEditable"
                  @change="(v: string) => saveOutlineField('hook.type', v)"
                  ><el-option v-for="s in ['悬念', '选择', '反转']" :key="s" :value="s" :label="s"
                /></el-select>
              </div>
              <pre class="stream-box">{{ data.outline._body }}</pre>
              <div class="dim">
                {{ data.outline.confirmed ? '细纲已锁定' : '细纲未确认' }}
              </div></template
            ><el-empty v-else description="尚无细纲" />
          </div>
          <div class="panel">
            <h3 class="panel-title">② 正文生成与阅读</h3>
            <div class="flex flex-wrap items-center gap-2">
              <el-button
                type="primary"
                :disabled="generating || polishBusy || writePending"
                @click="generate"
                >{{ generating ? '生成中…' : '开始生成（流式）' }}</el-button
              ><el-select v-model="promptId" style="width: 230px"
                ><el-option v-for="o in promptOptions" :key="o.value" v-bind="o" /></el-select
              ><el-button :disabled="!canEditProse" @click="openProseEdit">编辑正文</el-button
              ><el-button v-if="data.status === 'finalized'" @click="correctDialog = true"
                >定稿章订正</el-button
              ><el-checkbox v-model="indent">首行缩进</el-checkbox
              ><el-checkbox v-model="showLineNumbers">全部原始行号</el-checkbox>
            </div>
            <div ref="proseBox" class="prose-scroll prose-body mt-2" @scroll="onScroll">
              <pre v-if="generating" class="stream-text">{{ streamText }}</pre>
              <template v-else
                ><div
                  v-for="paragraph in proseMapping.paragraphs"
                  :key="paragraph.id"
                  :data-start-line="paragraph.startLine"
                  class="prose-paragraph"
                  :class="{
                    indent,
                    'hit-active':
                      hitLine !== null &&
                      hitLine >= paragraph.startLine &&
                      hitLine <= paragraph.endLine,
                  }"
                >
                  <span
                    v-if="
                      hitLine !== null &&
                      hitLine >= paragraph.startLine &&
                      hitLine <= paragraph.endLine
                    "
                    class="hit-line"
                    >原始第 {{ hitLine }} 行</span
                  ><template v-if="showLineNumbers"
                    ><span
                      v-for="(line, i) in paragraph.text.split('\n')"
                      :key="i"
                      class="all-lines"
                      ><span class="original-line-no">{{ paragraph.startLine + i }}</span
                      >{{ line }}</span
                    ></template
                  ><template v-else>{{ paragraph.text }}</template>
                </div>
                <el-empty v-if="!proseMapping.paragraphs.length" description="尚无正文"
              /></template>
            </div>
            <el-button v-if="showReturn" class="mt-1" @click="scrollLatest">回到最新</el-button>
            <div class="dim">段落阅读不改变原文；行号按原始正文（含空行）从 1 起算。</div>
          </div>
          <div class="panel">
            <h3 class="panel-title">装配预览</h3>
            <el-button :loading="previewLoading" @click="loadPreview">生成装配预览</el-button
            ><AssemblyPreview v-if="preview" :preview="preview" />
          </div>
        </div>
        <div>
          <div class="panel">
            <h3 class="panel-title">③ 检查结果</h3>
            <div class="mb-2 flex flex-wrap gap-2">
              <el-button type="primary" :disabled="writePending" @click="mech">跑机械检查</el-button
              ><el-button :loading="autoRunning" :disabled="writePending" @click="runAuto"
                >一键跑完本章</el-button
              ><el-button @click="loadSideState">刷新检查</el-button>
            </div>
            <el-alert
              v-if="checkStatus?.stale"
              type="warning"
              :closable="false"
              class="mb-2"
              title="检查结论已过期：正文指纹已变化，请重跑机检。"
            />
            <el-alert
              v-for="item in data.mech?.ineffective ?? []"
              :key="item.rule"
              :type="item.blocking === false ? 'info' : 'error'"
              :closable="false"
              class="mb-1"
              :title="ineffectiveText([item])"
            />
            <div v-if="data.mech" class="mb-2">
              结论：<el-tag :type="tagType(data.mech.verdict)">{{
                verdictLabel(data.mech.verdict)
              }}</el-tag>
              · 正文指纹 <span class="mono">{{ data.mech.fingerprint || '未知' }}</span>
            </div>
            <div class="my-2 text-sm" data-prose-metrics>
              <div><b>段落平均长度：</b>{{ proseMetricsDisplay.mean }}</div>
              <div class="dim">{{ proseMetricsDisplay.distribution }}</div>
              <div>
                <b>显式比喻线索密度：</b>{{ proseMetricsDisplay.density }} ·
                {{ proseMetricsDisplay.cues }}
              </div>
              <div class="dim">
                口径：长度按去空白可见字符计；空行分段，段内换行不拆段。线索密度按全文含对白的每千可见字符统计，仅为词面线索，非语义比喻数。
              </div>
              <div class="dim">仅作回读参考，非AI生成概率或质量合格线</div>
            </div>
            <h4>阻断项（{{ blockingHits.length }}）</h4>
            <div
              v-for="(hit, i) in blockingHits"
              :key="i"
              class="issue blocking clickable"
              @click="jumpToLine(hit.line)"
            >
              <b>{{ ruleLabel(hit.rule) }}</b
              ><el-tag type="danger">{{ levelLabel('block') }}</el-tag> 原始行 {{ hit.line }} ·
              {{ hit.word }}<br /><span class="dim">{{ hit.message }}</span>
            </div>
            <div v-if="!blockingHits.length" class="dim">
              无已知阻断项；未生效/过期项不计为通过。
            </div>
            <h4>提醒项（{{ warningHits.length }}）</h4>
            <div
              v-for="(hit, i) in warningHits"
              :key="i"
              class="issue clickable"
              @click="jumpToLine(hit.line)"
            >
              <b>{{ ruleLabel(hit.rule) }}</b
              ><el-tag type="warning">{{ levelLabel('warn') }}</el-tag> 原始行 {{ hit.line }} ·
              {{ hit.word }}<br /><span class="dim">{{ hit.message }}</span>
            </div>
            <el-collapse v-model="referenceOpen" class="mt-2"
              ><el-collapse-item
                :title="`参考项（${referenceHits.length}，M-12/D 默认收起且不阻断）`"
                name="open"
                ><div
                  v-for="(hit, i) in referenceHits"
                  :key="i"
                  class="issue clickable"
                  @click="jumpToLine(hit.line)"
                >
                  <b>{{ ruleLabel(hit.rule) }}</b
                  ><el-tag type="info">参考项</el-tag> 原始行 {{ hit.line }} · {{ hit.word
                  }}<br /><span class="dim"
                    >{{ hit.message }}；历史/配置级别 {{ hit.level }}，有效级别建议。</span
                  >
                </div></el-collapse-item
              ></el-collapse
            >
            <div v-if="autoLog.length" class="dim mt-2">
              <div v-for="(line, i) in autoLog" :key="i">{{ line }}</div>
            </div>
          </div>
          <div class="panel">
            <h3 class="panel-title">④ 模型审校</h3>
            <el-button @click="review">跑审校</el-button
            ><template v-if="data.review"
              ><div class="my-2">
                结论：<el-tag>{{ verdictLabel(data.review.verdict) }}</el-tag>
              </div>
              <div
                v-for="(issue, i) in data.review.issues ?? []"
                :key="i"
                class="issue clickable"
                @click="jumpReview(issue)"
              >
                <b>{{ issue.category }}</b> · {{ levelLabel(issue.severity) }} · {{ issue.location
                }}<br />引用：{{ issue.quote }}<br /><span class="dim"
                  >{{ issue.description }}；建议：{{ issue.suggestion }}</span
                >
              </div></template
            ><el-empty v-else description="尚未审校" />
          </div>
          <PolishPanel
            :key="`${book}:${no}`"
            :book="book"
            :no="no"
            :revision="data.row?.state_revision"
            :status="data.status"
            :disabled="generating || autoRunning || proseDialog"
            @busy="polishBusy = $event"
            @applied="reload"
          />
          <div class="panel">
            <h3 class="panel-title">⑤ 人工关口 / 定稿</h3>
            <div class="flex flex-wrap gap-2">
              <el-button :disabled="writePending" @click="fix" title="提示词 V5">定向修复</el-button
              ><el-button :disabled="writePending" @click="exempt">豁免（必填理由）</el-button
              ><el-button :disabled="writePending" @click="backOutline">改细纲重写</el-button
              ><el-button type="primary" :disabled="writePending" @click="finalize"
                >定稿（二次确认）</el-button
              ><el-button
                v-if="data.status === 'finalized'"
                :disabled="writePending"
                @click="rebuildMemory"
                >补做记忆卡</el-button
              ><el-button :disabled="writePending" @click="rewrite">从版本重写</el-button
              ><el-button
                v-if="data.status === 'error'"
                :loading="retrying"
                :disabled="writePending"
                @click="retry"
                >重试（恢复到{{ restoreTo }}）</el-button
              ><el-button
                v-if="data.status === 'finalized'"
                :disabled="writePending"
                @click="unlock"
                >解锁返工</el-button
              >
            </div>
            <div class="mt-2 flex gap-2">
              <el-select v-model="rewriteVer" placeholder="重写基线"
                ><el-option
                  v-for="v in versions"
                  :key="v.version"
                  :value="v.version"
                  :label="`v${v.version} ${v.label}`" /></el-select
              ><el-select v-model="viewVer" placeholder="只读回看"
                ><el-option
                  v-for="v in versions"
                  :key="v.version"
                  :value="v.version"
                  :label="`v${v.version} ${v.label}`" /></el-select
              ><el-button @click="viewSnapshot(viewVer)">查看快照</el-button>
            </div>
          </div>
        </div>
      </div>
    </template>
  </template>
  <el-empty v-else-if="validNo && !loading" description="尚无章节数据" />

  <el-dialog v-model="rawDialog" title="细纲原文编辑" width="860px" :close-on-click-modal="false"
    ><el-input v-model="rawText" type="textarea" :rows="22" class="mono" /><template #footer
      ><el-button @click="rawDialog = false">取消</el-button
      ><el-button type="primary" :loading="rawSaving" @click="saveRaw"
        >保存细纲</el-button
      ></template
    ></el-dialog
  >
  <el-dialog v-model="proseDialog" title="人工编辑正文" width="90%" :close-on-click-modal="false"
    ><el-alert
      v-if="proseEditConflict"
      type="warning"
      :closable="false"
      title="章节已更新，未覆盖服务器正文。请复制修改、查看当前版本后合并。"
    /><el-input v-model="proseEditText" type="textarea" :rows="24" class="mono" /><template #footer
      ><el-button @click="proseDialog = false">取消</el-button
      ><el-button
        type="primary"
        :loading="proseSaving"
        :disabled="proseEditConflict"
        @click="saveProseEdit"
        >保存正文</el-button
      ></template
    ></el-dialog
  >
  <el-dialog v-model="snapshotDialog" title="快照回看（只读）" width="860px"
    ><el-skeleton v-if="snapshotLoading" :rows="6" animated />
    <pre v-else class="stream-box">{{ snapshotData?.content }}</pre>
  </el-dialog>
  <el-dialog v-model="correctDialog" title="定稿章字词级订正" width="720px"
    ><el-input
      v-model="correctText"
      type="textarea"
      :rows="8"
      placeholder="旧文 ==>> 新文（每行一条）"
    /><el-input v-model="correctReason" class="mt-2" placeholder="订正理由（必填）" /><template
      #footer
      ><el-button @click="correctDialog = false">取消</el-button
      ><el-button type="primary" :loading="correctSaving" @click="submitCorrect"
        >执行订正</el-button
      ></template
    ></el-dialog
  >
</template>

<style scoped>
.stream-text {
  margin: 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font: inherit;
}
</style>
