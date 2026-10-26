<script setup lang="ts">
// 章节工作台（规格04 §2 核心页）：细纲 → 流式生成 → 机检 → 审校 → 人工关口/定稿 + 装配预览。
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
  getCheckStatus,
  generateOutlineG6,
  getAssemblyPreview,
  getDeslop,
  getDraft,
  getOutlineRaw,
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
  runPolish,
  runReview,
  unlockChapter,
} from '@/api'
import type {
  CheckStatus,
  DeslopResult,
  DraftInfo,
  PreviewResult,
  SnapshotDetail,
  VersionInfo,
  WorkbenchData,
} from '@/api/types'
import { useBookStore } from '@/stores/book'
import { tagType } from '@/utils/status'

const route = useRoute()
const router = useRouter()
const store = useBookStore()

const book = computed(() => String(route.params.name ?? ''))
const no = computed(() => Number(route.params.no ?? 1) || 1)
const data = ref<WorkbenchData | null>(null)
const versions = ref<VersionInfo[]>([])
const rewriteVer = ref<number | null>(null)
const viewVer = ref<number | null>(null)

const promptOptions = [
  { value: '', label: '自动（卷首三章W3 / 其余W1）' },
  { value: 'W2', label: 'W2 对话专场' },
  { value: 'W4', label: 'W4 突破场景' },
  { value: 'W5', label: 'W5 高潮场景' },
]
const promptId = ref('')
const generating = ref(false)
const retrying = ref(false)
const streamText = ref('（生成区）')
let abortCtrl: AbortController | null = null

onUnmounted(() => abortCtrl?.abort())

const rawDialog = ref(false)
const rawText = ref('')
const preview = ref<PreviewResult | null>(null)
// 规格05：机检过期判定 / AI 味客观分 / 断线草稿
const checkStatus = ref<CheckStatus | null>(null)
const deslopRes = ref<DeslopResult | null>(null)
const draft = ref<DraftInfo | null>(null)

// —— 正文按行渲染 + 机检/审校命中跳转（规格04 §2「点击跳转正文位置」）——
// 生成区必须是可定位的：把正文按行拆成 div，机检命中带 line 就直接滚到那一行并高亮 2 秒。
// 流式生成期间显示流式缓冲（streamText），生成结束后以落盘正文为准。
const proseLines = computed(() =>
  (generating.value ? streamText.value : (data.value?.prose?._body ?? streamText.value)).split(
    /\r?\n/,
  ),
)
const hitLine = ref<number | null>(null)
const proseBox = ref<HTMLElement | null>(null)
let hitTimer: number | undefined

function jumpToLine(line: number): void {
  if (!line || line < 1) return
  hitLine.value = line
  void nextTick(() => {
    const el = proseBox.value?.querySelector<HTMLElement>(`[data-line="${line}"]`)
    el?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  })
  if (hitTimer !== undefined) window.clearTimeout(hitTimer)
  hitTimer = window.setTimeout(() => (hitLine.value = null), 2000)
}
onUnmounted(() => {
  if (hitTimer !== undefined) window.clearTimeout(hitTimer)
})

/** 审校 issue 的 location 字段（形如"第3段/第12行/正文开头"）里抽行号，抽不到返回 0。 */
function lineFromLocation(location: string): number {
  const m = /第\s*(\d+)\s*行/.exec(location ?? '')
  return m ? Number(m[1]) : 0
}

// —— 快照回看（规格04 §2「版本列表可回看任意快照」；只读，不触发重写）——
const snapshotDialog = ref(false)
const snapshotLoading = ref(false)
const snapshotData = ref<SnapshotDetail | null>(null)

async function viewSnapshot(version: number | null): Promise<void> {
  if (version == null) {
    ElMessage.warning('先选一个快照版本')
    return
  }
  snapshotLoading.value = true
  snapshotDialog.value = true
  snapshotData.value = null
  try {
    snapshotData.value = await getSnapshot(book.value, no.value, version)
  } catch {
    snapshotDialog.value = false // 拦截器已提示
  } finally {
    snapshotLoading.value = false
  }
}

// —— 人工手改正文（规格03 §2.5 人工关三选项之一）——
// finalized 状态由后端拒绝（400）；前端按钮同时禁用，避免用户白点。
const proseDialog = ref(false)
const proseEditText = ref('')
const proseSaving = ref(false)
// 定稿章订正：每行「旧文 ==>> 新文」。逐条替换、旧文须唯一命中、总改动 ≤5%、强制理由。
const correctDialog = ref(false)
const correctText = ref('')
const correctReason = ref('')
const correctSaving = ref(false)
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

function openProseEdit(): void {
  proseEditText.value = data.value?.prose?._body ?? ''
  proseDialog.value = true
}

async function saveProseEdit(): Promise<void> {
  proseSaving.value = true
  try {
    const r = await putProse(book.value, no.value, proseEditText.value)
    ElMessage.success(`正文已保存，${r.word_count} 字`)
    proseDialog.value = false
    await reload()
  } catch {
    // 拦截器已提示（含 finalized 拒绝）
  } finally {
    proseSaving.value = false
  }
}

// —— 定稿章订正（后端只给 finalized 开这条窄通道）——
// 为什么不用「编辑正文」：那个一律拒绝已定稿章（防随手重写）；也不用「解锁返工」，
// 那要把机检+审校+定稿全部重跑，只为改三个字烧一轮 token。
function openCorrect(): void {
  correctText.value = ''
  correctReason.value = ''
  correctDialog.value = true
}

function parseCorrectEdits(): { old: string; new: string }[] {
  const out: { old: string; new: string }[] = []
  for (const raw of correctText.value.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    const i = line.indexOf('==>>')
    if (i < 0) throw new Error(`格式不对（应为「旧文 ==>> 新文」）：${line.slice(0, 30)}`)
    const old = line.slice(0, i).trim()
    const nw = line.slice(i + 4).trim()
    if (!old) throw new Error(`缺旧文：${line.slice(0, 30)}`)
    out.push({ old, new: nw })
  }
  return out
}

async function submitCorrect(): Promise<void> {
  let edits: { old: string; new: string }[]
  try {
    edits = parseCorrectEdits()
  } catch (e) {
    ElMessage.error((e as Error).message)
    return
  }
  if (!edits.length) {
    ElMessage.error('至少填一条订正')
    return
  }
  if (!correctReason.value.trim()) {
    ElMessage.error('订正必须填写理由，会写进审校记录')
    return
  }
  correctSaving.value = true
  try {
    const r = await correctProse(book.value, no.value, edits, correctReason.value.trim())
    ElMessage.success(
      `已订正 ${r.applied.length} 处 → v${r.version}（改动 ${(r.change_ratio * 100).toFixed(2)}%）`,
    )
    correctDialog.value = false
    await reload()
  } catch {
    // 拦截器已提示（唯一命中 / 5% 上限 / 禁词都在后端拦）
  } finally {
    correctSaving.value = false
  }
}

// —— 一键跑完本章（规格01 §4 章内自动编排）——
// 顺序：机检 → （到 llm_review 则）审校 → （回 mech_check 则）再机检，循环上限 3 轮；
// 任一步进 human_review / error 就停并说明需人工处理。每步后 reload 取最新状态再决定。
const autoRunning = ref(false)
const autoLog = ref<string[]>([])

function autoNote(msg: string): void {
  autoLog.value = [...autoLog.value, `${new Date().toLocaleTimeString()} ${msg}`]
}

const AUTO_TERMINAL = ['pending_finalize', 'human_review', 'finalized', 'error']

async function runAuto(): Promise<void> {
  autoRunning.value = true
  autoLog.value = []
  let rounds = 0
  try {
    let status = data.value?.status ?? ''
    if (status !== 'mech_check') {
      ElMessage.warning(`当前状态 ${status || '未知'} 不能自动编排（需先有正文并进入 mech_check）`)
      return
    }
    while (rounds < 3) {
      rounds++
      autoNote(`第 ${rounds} 轮 · 跑机械检查…`)
      const m = await runMechcheck(book.value, no.value)
      await reload()
      status = m.status
      autoNote(`机检完成 → 状态 ${status}`)
      if (AUTO_TERMINAL.includes(status)) break
      if (status !== 'llm_review') break
      autoNote('跑 LLM 审校（P-V1 五维）…')
      const r = await runReview(book.value, no.value)
      await reload()
      status = r.status
      autoNote(`审校 verdict=${r.verdict} → 状态 ${status}`)
      if (AUTO_TERMINAL.includes(status)) break
      if (status !== 'mech_check') break
    }
    const final = data.value?.status ?? status
    if (final === 'pending_finalize')
      ElMessage.success('自动编排完成：已到待定稿（可人工通读后定稿）')
    else if (final === 'human_review')
      ElMessage.warning('自动编排停止：需人工处理（审校未通过/机检仍阻断）')
    else if (final === 'error')
      ElMessage.error('自动编排停止：状态 error，请点上方「重试」恢复后再试')
    else ElMessage.info(`自动编排停止：当前状态 ${final}（已达轮次上限或状态不适用）`)
  } catch {
    // 拦截器已提示
  } finally {
    autoRunning.value = false
  }
}

const outlineEditable = computed(() =>
  ['awaiting_outline', 'outline_draft', 'error'].includes(data.value?.status ?? ''),
)

const REVIEW_DIMS: [string, string][] = [
  ['ai_flavor', 'AI味'],
  ['plot', '剧情一致性'],
  ['pacing', '节奏爽点'],
  ['prose', '文笔画面'],
  ['hook', '开篇钩子'],
]

async function load(): Promise<void> {
  store.setCurrentBook(book.value)
  data.value = await getWorkbench(book.value, no.value)
  streamText.value = data.value.prose?._body ?? '（生成区）'
  // 三个只读状态并行拉；任一失败都不能挡住工作台（主数据已到手）
  void loadSideState()
  await loadVersions()
}

async function loadSideState(): Promise<void> {
  const b = book.value
  const n = no.value
  const [cs, dr, dl] = await Promise.all([
    getCheckStatus(b, n).catch(() => null),
    getDraft(b, n).catch(() => null),
    getDeslop(b, n).catch(() => null),
  ])
  checkStatus.value = cs
  draft.value = dr
  deslopRes.value = dl
}

/** 未生效项文案：必须点名规则，不能只说"检查异常" */
function ineffectiveText(list: { rule: string; reason: string; detail: string }[]): string {
  const REASON: Record<string, string> = {
    missing_file: '文件缺失',
    empty_file: '文件为空',
    suspiciously_small: '条目过少',
    bad_threshold: '阈值非法',
    disabled: '已关闭',
    unparsed: '本次无法解析',
    crash: '检查器异常',
  }
  return list.map((i) => `${i.rule}（${REASON[i.reason] ?? i.reason}：${i.detail}）`).join('；')
}

async function adoptDraftNow(): Promise<void> {
  const chars = draft.value?.chars ?? 0
  try {
    await ElMessageBox.confirm(
      `采纳草稿会用它覆盖本章正文（${chars} 字），并立刻重跑机检。继续？`,
      '采纳草稿',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    const r = await adoptDraft(book.value, no.value, true)
    ElMessage.success(`已采纳 ${r.chars} 字草稿，状态 → ${r.status}（机检照常执行）`)
    await reload()
    void loadSideState()
  } catch {
    // 拦截器已提示
  }
}

async function discardDraftNow(): Promise<void> {
  try {
    await discardDraft(book.value, no.value)
    ElMessage.success('已丢弃草稿')
    void loadSideState()
  } catch {
    // 拦截器已提示
  }
}

async function rebuildMemory(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '补做记忆卡会重跑 V4（一次模型调用），并先回滚本章已产生的伏笔推进与状态快照。继续？',
      '补做记忆卡',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await rebuildMemoryCard(book.value, no.value)
    ElMessage.success('记忆卡已补做（伏笔/状态不会重复推进）')
    void loadSideState()
  } catch {
    // 拦截器已提示
  }
}
watch(
  [book, no],
  () => {
    abortCtrl?.abort() // 切章/切书时取消旧流，避免旧章内容写进新章视图
    void load().catch(() => {})
  },
  { immediate: true },
)

watch(streamText, () => {
  void nextTick(() => {
    // 流式生成时自动滚底；命中跳转高亮期间不打扰用户阅读
    if (proseBox.value && !hitLine.value) proseBox.value.scrollTop = proseBox.value.scrollHeight
  })
})

async function reload(): Promise<void> {
  try {
    await load()
  } catch {
    // 拦截器已提示
  }
}

// —— error 救援：把章节从 error 恢复到出错前状态（规格01 §4、规格03 §3） ——
// 不改正文、不删版本，只是让状态机走 error --retry--> prev_status，
// 让用户能重新点生成/机检。没有这个入口，error 章只能刷新页面干看。
// 恢复目标由后端记的 prev_status 决定：正文生成中途失败会被记成 outline_locked
// （generating 是瞬态，不能作为停留点），机检/审校失败记各自状态。
// 这里把真实目标显示出来，别让用户猜。
const restoreTo = computed(() => {
  const v = data.value?.row?.['prev_status']
  return typeof v === 'string' && v ? v : '出错前状态'
})

async function retry(): Promise<void> {
  retrying.value = true
  try {
    const r = await retryChapter(book.value, no.value)
    // 回到 outline_locked 说明是正文生成失败：状态机已允许重新生成，提示直接点生成
    ElMessage.success(
      r.status === 'outline_locked'
        ? '已退回到 outline_locked：直接点「开始生成（流式）」重新生成本章'
        : `已恢复到 ${r.status}（可重新执行该步）`,
    )
    await reload()
  } catch {
    // 拦截器已提示
  } finally {
    retrying.value = false
  }
}

function nav(d: number): void {
  router.push(`/book/${encodeURIComponent(book.value)}/chapter/${Math.max(1, no.value + d)}`)
}

// —— ① 细纲（章合同） ——
async function genOutline(): Promise<void> {
  try {
    await generateOutlineG6(book.value, no.value)
    ElMessage.success('细纲已生成（未确认）')
    await reload()
  } catch {
    // 拦截器已提示
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
    ElMessage.success('已锁定')
    await reload()
  } catch {
    // 拦截器已提示
  }
}

async function editRaw(): Promise<void> {
  try {
    rawText.value = (await getOutlineRaw(book.value, no.value)).content
    rawDialog.value = true
  } catch {
    // 拦截器已提示
  }
}

async function saveRaw(): Promise<void> {
  const m = rawText.value.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/)
  if (!m) {
    ElMessage.error('格式：--- frontmatter --- 正文')
    return
  }
  try {
    await putChapterOutline(book.value, no.value, { fm_raw: m[1]!, body: m[2]! })
    ElMessage.success('细纲已保存')
    rawDialog.value = false
    await reload()
  } catch {
    // 拦截器已提示
  }
}

async function saveField(key: string, value: string): Promise<void> {
  const d = data.value
  if (!d?.outline) return
  const fm: Record<string, unknown> = { ...d.outline }
  delete fm._body
  fm[key] = value
  try {
    await putChapterOutline(book.value, no.value, { fm, body: d.outline._body ?? '' })
    ElMessage.success(`已改 ${key} = ${value}`)
  } catch {
    // 拦截器已提示
  }
  await reload()
}

async function saveHook(type: string): Promise<void> {
  const d = data.value
  if (!d?.outline) return
  const fm: Record<string, unknown> = { ...d.outline }
  delete fm._body
  fm.hook = { ...(d.outline.hook ?? {}), type }
  try {
    await putChapterOutline(book.value, no.value, { fm, body: d.outline._body ?? '' })
    ElMessage.success(`钩子类型 → ${type}`)
  } catch {
    // 拦截器已提示
  }
  await reload()
}

// —— ② 正文生成（SSE：fetch 流式读取；协议 data: {json}\n\n，event: done/error） ——
async function generate(): Promise<void> {
  generating.value = true
  streamText.value = ''
  abortCtrl = new AbortController()
  try {
    const res = await fetch(
      `/api/books/${encodeURIComponent(book.value)}/chapters/${no.value}/generate`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(promptId.value ? { prompt_id: promptId.value } : {}),
        signal: abortCtrl.signal, // 切章/离开页面时取消读取（后端会继续生成并正常收尾）
      },
    )
    if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`)
    const reader = res.body.getReader()
    const dec = new TextDecoder()
    let buf = ''
    let finished = false
    while (!finished) {
      const { value, done } = await reader.read()
      if (done) break
      buf += dec.decode(value, { stream: true })
      let idx: number
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const frame = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        let evt = 'message'
        let payload = ''
        for (const line of frame.split('\n')) {
          if (line.startsWith('event: ')) evt = line.slice(7).trim()
          else if (line.startsWith('data: ')) payload += line.slice(6)
        }
        if (evt === 'done') {
          ElMessage.success('生成完成')
          finished = true
          break
        }
        if (evt === 'error') {
          ElMessage.error(`生成失败: ${payload}`)
          finished = true
          break
        }
        try {
          streamText.value += (JSON.parse(payload) as { text?: string }).text ?? ''
        } catch {
          // 忽略非 JSON 帧
        }
      }
    }
    await reload()
  } catch (e) {
    if (e instanceof DOMException && e.name === 'AbortError') {
      ElMessage.info('已取消本页的流式读取，生成仍在后台继续')
    } else {
      ElMessage.error(e instanceof Error ? e.message : String(e))
    }
  } finally {
    generating.value = false
    abortCtrl = null
  }
}

// —— 装配预览 ——
async function loadPreview(): Promise<void> {
  try {
    preview.value = await getAssemblyPreview(book.value, no.value)
  } catch {
    // 拦截器已提示
  }
}

// —— ③ 机械检查 ——
async function mech(): Promise<void> {
  try {
    const r = await runMechcheck(book.value, no.value)
    ElMessage.success(`机械检查完成，状态 → ${r.status}`)
    await reload()
  } catch {
    // 拦截器已提示
  }
}

// —— ④ LLM 审校 ——
async function review(): Promise<void> {
  try {
    const r = await runReview(book.value, no.value)
    ElMessage.success(`审校 verdict=${r.verdict}，状态 → ${r.status}`)
    await reload()
  } catch {
    // 拦截器已提示
  }
}

// —— ⑤ 人工关口 / 定稿 ——
async function fix(): Promise<void> {
  try {
    const r = await runFix(book.value, no.value)
    ElMessage.success(`修复完成 ${r.word_count} 字`)
    await reload()
  } catch {
    // 拦截器已提示
  }
}

async function polish(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '整章润色（V2 去AI味 + V3 复核）：不改剧情人设，字数浮动>10%或机检阻断会自动丢弃。继续？',
      '整章润色',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    const r = await runPolish(book.value, no.value)
    if (r.ok) {
      ElMessage.success({
        message: `润色完成：${r.word_count?.before}→${r.word_count?.after} 字，V3=${r.v3?.verdict}，改动 ${r.changes?.length ?? 0} 处`,
        duration: 8000,
      })
    } else {
      ElMessage.warning(`润色被丢弃：${r.reason}`)
    }
    await reload()
  } catch {
    // 拦截器已提示
  }
}

async function exempt(): Promise<void> {
  let reason = ''
  try {
    const r = await ElMessageBox.prompt(
      '豁免理由（必填，将写入 generation_log 作为合规证据）',
      '豁免',
      { inputValidator: (v: string) => (v?.trim() ? true : '理由必填') },
    )
    reason = r.value ?? ''
  } catch {
    return
  }
  try {
    await runExempt(book.value, no.value, reason)
    ElMessage.success('已豁免 → 待定稿')
    await reload()
  } catch {
    // 拦截器已提示
  }
}

async function backOutline(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '回到 outline_draft 修改细纲？（正文保留，改完需重新确认+生成）',
      '改细纲重写',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await backToOutline(book.value, no.value)
    ElMessage.success('已回到 outline_draft')
    await reload()
  } catch {
    // 拦截器已提示
  }
}

async function finalize(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '第一次确认：定稿将写快照、置 status=final 并触发记忆卡回写。继续？',
      '定稿',
      { type: 'warning' },
    )
    await ElMessageBox.confirm('第二次确认：真的定稿？', '定稿', { type: 'warning' })
  } catch {
    return
  }
  try {
    const r = await finalizeChapter(book.value, no.value)
    ElMessage.success({
      message: `已定稿，快照 v${r.snapshot_version}；回写：${JSON.stringify(r.writeback).slice(0, 200)}`,
      duration: 10000,
    })
    // 细纲说 pay、记忆卡记 advance 时，台账不会推进，下一章会重复回收同一伏笔
    if ((r.foreshadow_drift ?? []).length) {
      ElMessage.warning({
        message: `伏笔漂移：${(r.foreshadow_drift ?? []).join('；')}`,
        duration: 15000,
      })
    }
    await reload()
    void store.refreshConflicts()
  } catch {
    // 拦截器已提示
  }
}

// —— 定稿返工：先解锁，再走手改/修复/重写（规格01 §4 finalized --unlock--> pending_finalize）——
// 定稿后正文受快照保护，直接改会造成「正文已变/快照未变/导出对不上」。
// 解锁只是把状态退回 pending_finalize，不动正文、不动快照、不开新版本。
async function unlock(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '解锁后本章退回「待定稿」，可重新手改正文或定向修复；已产生的快照与版本历史都会保留。确认解锁？',
      '定稿返工',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    const r = await unlockChapter(book.value, no.value)
    ElMessage.success(`已解锁，状态 → ${r.status}（现在可以手改正文/修复，改完重新定稿）`)
    await reload()
  } catch {
    // 拦截器已提示
  }
}

async function loadVersions(): Promise<void> {
  try {
    versions.value = await listVersions(book.value, no.value)
    rewriteVer.value = versions.value.length
      ? versions.value[versions.value.length - 1]!.version
      : null
  } catch {
    // 无快照时静默
  }
}

async function rewrite(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `从 v${rewriteVer.value ?? '当前'} 重写？将开新版本分支，旧版本保留。`,
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
    // 拦截器已提示
  }
}
</script>

<template>
  <div class="panel flex flex-wrap items-center gap-2">
    <el-button @click="nav(-1)">← 上一章</el-button>
    <span class="font-bold">第 {{ no }} 章</span>
    <el-button @click="nav(1)">下一章 →</el-button>
    <span>
      状态
      <el-tag :type="tagType(data?.status ?? '')" size="small">{{ data?.status ?? '…' }}</el-tag>
    </span>
    <span class="dim">版本 {{ data?.row ? 'v' + data.row.current_version : '-' }}</span>
    <span class="dim"
      >本章成本
      {{ data?.cost ? (data.cost.cost_millicents / 100000).toFixed(2) + '元' : '0元' }}</span
    >
    <el-tag v-if="data?.golden3" type="warning" size="small">黄金三章专项</el-tag>
    <span class="flex-1"></span>
    <el-button v-if="data?.status === 'error'" type="warning" :loading="retrying" @click="retry">
      重试（恢复到 {{ restoreTo }}）
    </el-button>
    <el-button @click="reload">刷新</el-button>
    <div v-if="data?.status === 'error'" class="dim mt-1 w-full">
      本章处于 error：上一次生成/检查中途失败（如 LLM
      网络抖动），正文可能是残稿。点「重试」把状态退回出错前的
      {{ restoreTo }}，不改已有正文与快照，之后可重新执行该步；只点「刷新」状态不会变。
    </div>
  </div>

  <!-- 草稿救援：钱已经花了，稿只写了一半（规格05 F-09） -->
  <el-alert
    v-if="draft?.exists && (data?.status === 'error' || data?.status === 'generating')"
    type="warning"
    :closable="false"
    class="mb-2"
    show-icon
  >
    <template #title>
      检测到 {{ draft.chars }} 字草稿（{{ draft.generated_at }}），可采纳为本章正文
      <el-tag v-if="draft.stale" type="danger" size="small" class="ml-2">比正文旧</el-tag>
    </template>
    <div class="flex items-center gap-2">
      <span class="dim">采纳后会立刻重跑机检，不放宽任何门禁。</span>
      <el-button type="warning" size="small" @click="adoptDraftNow">采纳草稿</el-button>
      <el-button size="small" @click="discardDraftNow">丢弃</el-button>
    </div>
  </el-alert>

  <div class="grid grid-cols-1 gap-3 xl:grid-cols-2">
    <div>
      <!-- ① 细纲 -->
      <div class="panel">
        <h3 class="panel-title">① 细纲（章合同）</h3>
        <div class="mb-2 flex flex-wrap gap-2">
          <el-button type="primary" @click="genOutline">生成细纲（P-G6）</el-button>
          <el-button @click="confirmOutlineAct">确认细纲（锁定）</el-button>
          <el-button @click="editRaw">编辑细纲原文</el-button>
        </div>
        <template v-if="data?.outline">
          <div class="text-xs"><b>核心事件：</b>{{ data.outline.core_event }}</div>
          <div class="my-1 flex flex-wrap items-center gap-2 text-xs">
            <span><b>stage</b></span>
            <el-select
              :model-value="data.outline.stage"
              :disabled="!outlineEditable"
              style="width: 90px"
              @change="(v: string) => saveField('stage', v)"
            >
              <el-option
                v-for="s in ['引入', '发展', '转折', '高潮', '收束']"
                :key="s"
                :label="s"
                :value="s"
              />
            </el-select>
            <span><b>情绪</b></span>
            <el-select
              :model-value="data.outline.emotion_task"
              :disabled="!outlineEditable"
              style="width: 80px"
              @change="(v: string) => saveField('emotion_task', v)"
            >
              <el-option v-for="s in ['压', '小扬', '爆']" :key="s" :label="s" :value="s" />
            </el-select>
            <span><b>pov</b> {{ data.outline.pov }}</span>
            <span><b>钩子类型</b></span>
            <el-select
              :model-value="data.outline.hook?.type"
              :disabled="!outlineEditable"
              style="width: 80px"
              @change="(v: string) => saveHook(v)"
            >
              <el-option v-for="s in ['悬念', '选择', '反转']" :key="s" :label="s" :value="s" />
            </el-select>
            <span class="dim">{{ data.outline.hook?.content }}</span>
          </div>
          <div class="text-xs">
            <b>伏笔操作：</b
            ><span class="mono">{{ JSON.stringify(data.outline.foreshadow_ops ?? []) }}</span>
          </div>
          <div class="mt-1 text-xs">
            <b>确认锁：</b
            >{{ data.outline.confirmed ? '✅ 已锁定（改字段请先「改细纲重写」）' : '未确认' }}
          </div>
          <pre class="stream-box mt-2" style="max-height: 260px">{{ data.outline._body }}</pre>
        </template>
        <span v-else class="dim">（未生成细纲）</span>
      </div>

      <!-- ② 正文生成 -->
      <div class="panel">
        <h3 class="panel-title">② 正文生成</h3>
        <div class="flex flex-wrap items-center gap-2">
          <el-button type="primary" :disabled="generating" @click="generate">
            {{ generating ? '生成中…' : '开始生成（流式）' }}
          </el-button>
          <el-select v-model="promptId" style="width: 230px">
            <el-option
              v-for="o in promptOptions"
              :key="o.value"
              :label="o.label"
              :value="o.value"
            />
          </el-select>
          <!-- 人工手改正文（规格03 §2.5 三选项之一）：finalized 由后端 400 拒绝，这里同步禁用 -->
          <el-button :disabled="!canEditProse" @click="openProseEdit">
            {{ data?.status === 'finalized' ? '编辑正文（已定稿，请先解锁）' : '编辑正文' }}
          </el-button>
          <!-- 定稿章专用：逐条替换、唯一命中、≤5%、强制理由；不必解锁重跑全链路 -->
          <el-button v-if="data?.status === 'finalized'" @click="openCorrect">
            订正（字词级）
          </el-button>
        </div>
        <!-- 按行渲染：机检/审校命中可跳到 [data-line] 并高亮（规格04 §2） -->
        <div ref="proseBox" class="prose-scroll mt-2">
          <div
            v-for="(line, idx) in proseLines"
            :key="idx"
            :data-line="idx + 1"
            class="prose-line"
            :class="{ 'hit-active': hitLine === idx + 1 }"
          >
            <span class="line-no">{{ idx + 1 }}</span>
            <span class="line-text">{{ line || ' ' }}</span>
          </div>
        </div>
        <div class="dim mt-1">
          共 {{ proseLines.length }} 行 · 点击右侧机械检查/审校命中项可跳到对应行
        </div>
      </div>

      <!-- 装配预览 -->
      <div class="panel">
        <h3 class="panel-title">装配预览（13 槽位 · 排查跑偏第一入口）</h3>
        <el-button @click="loadPreview">生成装配预览</el-button>
        <template v-if="preview">
          <div class="dim mt-2">
            总估算 {{ preview.total_est_tokens }} tokens / 预算 {{ preview.budget_tokens }} · 已存
            {{ preview.saved_to || '' }}
          </div>
          <el-collapse class="mt-1">
            <el-collapse-item
              v-for="(s, idx) in preview.slots"
              :key="idx"
              :title="`${s.title} · ${s.est_tokens}t${s.truncated ? ' · 已截断' : ''}`"
            >
              <pre class="stream-box">{{ s.content || '（空）' }}</pre>
            </el-collapse-item>
          </el-collapse>
        </template>
      </div>
    </div>

    <div>
      <!-- ③ 机械检查 -->
      <div class="panel">
        <h3 class="panel-title">③ 机械检查（零 token）</h3>
        <div class="mb-2 flex flex-wrap items-center gap-2">
          <el-button type="primary" @click="mech">跑机械检查（含自动修复1轮）</el-button>
          <!-- 章内自动编排（规格01 §4）：机检 → 审校 → 再机检，上限 3 轮，到人工关/待定稿即停 -->
          <el-button :loading="autoRunning" :disabled="autoRunning" @click="runAuto">
            一键跑完本章（机检→审校→再机检）
          </el-button>
          <span class="dim">阻断→自动 P-V5 修复一轮→重查；敏感词直接转人工</span>
        </div>
        <div v-if="autoLog.length" class="dim mb-2" style="max-height: 130px; overflow: auto">
          <div v-for="(l, i) in autoLog" :key="i">{{ l }}</div>
        </div>
        <!-- 未生效必须显式可见：不允许折叠进"通过"（规格05 P-2） -->
        <el-alert
          v-if="(data?.mech?.ineffective ?? []).some((i) => i.blocking !== false)"
          type="error"
          :closable="false"
          class="mb-2"
          show-icon
          title="机检未完整执行，已转人工"
        >
          本次未检查：{{
            ineffectiveText((data?.mech?.ineffective ?? []).filter((i) => i.blocking !== false))
          }}
          —— 修复词表/配置后点「跑机械检查」重跑
        </el-alert>
        <el-alert
          v-else-if="(data?.mech?.ineffective ?? []).length"
          type="info"
          :closable="false"
          class="mb-2"
          show-icon
          title="本次有规则未执行（不影响判定）"
        >
          {{ ineffectiveText(data?.mech?.ineffective ?? []) }}
        </el-alert>
        <div v-if="checkStatus?.stale" class="dim mb-2">
          <el-tag type="warning" size="small">结论已过期</el-tag>
          正文在上次机检后改动过（指纹 {{ checkStatus.current_fingerprint }}）， 请重跑机检再定稿。
        </div>
        <template v-if="data?.mech">
          <div class="mb-1">
            结论：<el-tag :type="tagType(data.mech.verdict)" size="small">{{
              data.mech.verdict
            }}</el-tag>
            （{{ data.mech.issues.length }} 条）
            <span class="dim">· 点条目跳转到正文对应行</span>
          </div>
          <div
            v-for="(i, idx) in data.mech.issues"
            :key="idx"
            class="issue clickable"
            :class="{ blocking: i.level === 'block' }"
            :title="`点击跳转到正文第 ${i.line} 行`"
            @click="jumpToLine(i.line)"
          >
            <b>{{ i.rule }}</b>
            <el-tag :type="tagType(i.level)" size="small">{{ i.level }}</el-tag>
            行{{ i.line }} · {{ i.word }}<br />
            <span class="dim">{{ i.message }}</span>
          </div>
        </template>
        <span v-else class="dim">（未跑）</span>
      </div>

      <!-- ③b AI 味确定性检测（零 token，只报不改） -->
      <div class="panel">
        <h3 class="panel-title">③b AI 味检测（规则集，零 token）</h3>
        <div class="mb-2 flex flex-wrap items-center gap-2">
          <el-button @click="loadSideState">重跑检测</el-button>
          <template v-if="deslopRes && deslopRes.score >= 0">
            <span>干净度</span>
            <el-tag
              :type="
                deslopRes.score >= 80 ? 'success' : deslopRes.score >= 60 ? 'warning' : 'danger'
              "
              size="small"
            >
              {{ deslopRes.score }} / 100
            </el-tag>
            <span class="dim">
              结构性硬伤 {{ deslopRes.hits_count.blocking }} 条 · 提示
              {{ deslopRes.hits_count.advisory }} 条 · {{ deslopRes.word_count }} 字
            </span>
          </template>
          <span v-else-if="deslopRes" class="dim">正文过短，未评分</span>
          <span v-else class="dim">（未检测）</span>
        </div>
        <div
          v-for="(h, i) in (deslopRes?.hits ?? []).slice(0, 12)"
          :key="i"
          class="issue clickable"
          :class="{ blocking: h.level === 'blocking' }"
          :title="`点击跳转到正文第 ${h.line} 行`"
          @click="jumpToLine(h.line)"
        >
          <b>{{ h.rule }}</b>
          <el-tag :type="h.level === 'blocking' ? 'danger' : 'warning'" size="small">
            {{ h.level }}
          </el-tag>
          行{{ h.line }} · {{ h.quote }}<br />
          <span class="dim">{{ h.message }}</span>
        </div>
        <div class="dim mt-2">
          检测器只给客观证据（哪条规则、第几行），改写仍由「整章润色 /
          定向修复」做，改完重新跑机检。
        </div>
      </div>

      <!-- ④ LLM 审校 -->
      <div class="panel">
        <h3 class="panel-title">④ LLM 审校（P-V1 五维）</h3>
        <el-button type="primary" @click="review">跑审校</el-button>
        <template v-if="data?.review">
          <div class="mb-1 mt-2">
            verdict：
            <el-tag
              :type="
                tagType(
                  data.review.verdict === 'pass'
                    ? 'pass'
                    : data.review.verdict === 'fix'
                      ? 'warn'
                      : 'bad',
                )
              "
              size="small"
            >
              {{ data.review.verdict }}
            </el-tag>
          </div>
          <div v-for="[k, t] in REVIEW_DIMS" :key="k" class="my-1 flex items-center gap-2">
            <span class="shrink-0 text-xs" style="width: 70px">{{ t }}</span>
            <el-progress
              :percentage="data.review?.scores?.[k] ?? 0"
              :stroke-width="8"
              class="flex-1"
            />
          </div>
          <div
            v-for="(i, idx) in data.review.issues ?? []"
            :key="idx"
            class="issue"
            :class="{
              blocking: i.severity === 'blocking',
              clickable: lineFromLocation(i.location),
            }"
            :title="
              lineFromLocation(i.location)
                ? `点击跳转到正文第 ${lineFromLocation(i.location)} 行`
                : ''
            "
            @click="jumpToLine(lineFromLocation(i.location))"
          >
            <b>{{ i.category }}</b>
            <el-tag :type="tagType(i.severity)" size="small">{{ i.severity }}</el-tag>
            {{ i.location }}<br />
            引用：<span class="mono">{{ i.quote }}</span
            ><br />
            <span class="dim">{{ i.description }}</span
            ><br />
            建议：{{ i.suggestion }}
          </div>
        </template>
        <span v-else class="dim">（未跑）</span>
      </div>

      <!-- ⑤ 人工关口 / 定稿 -->
      <div class="panel">
        <h3 class="panel-title">⑤ 人工关口 / 定稿</h3>
        <div class="mb-2 flex flex-wrap gap-2">
          <el-button @click="fix">定向修复（P-V5）</el-button>
          <el-button @click="polish">整章润色（P-V2+V3）</el-button>
          <el-button @click="exempt">豁免（必填理由）</el-button>
          <el-button @click="backOutline">改细纲重写</el-button>
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <el-button type="primary" @click="finalize">定稿（二次确认→快照+回写）</el-button>
          <!-- 记忆卡回写失败时定稿仍然成立（不该因回写丢稿），但事实没进长期记忆；
               没有这个入口，那一章的状态/伏笔就永久缺席，下游毫无提示 -->
          <el-button
            v-if="data?.status === 'finalized'"
            type="warning"
            plain
            @click="rebuildMemory"
          >
            补做记忆卡
          </el-button>
          <el-button type="danger" plain @click="rewrite">从版本重写</el-button>
          <el-select v-model="rewriteVer" placeholder="选择快照版本" clearable style="width: 260px">
            <el-option
              v-for="v in versions"
              :key="v.version"
              :value="v.version"
              :label="`v${v.version} ${v.label} ${v.created_at.slice(0, 16)}`"
            />
          </el-select>
          <!-- 定稿返工：解锁后才能手改/修复（定稿后正文受快照保护） -->
          <el-button v-if="data?.status === 'finalized'" type="warning" plain @click="unlock">
            解锁返工（退回待定稿）
          </el-button>
        </div>
        <div v-if="data?.status === 'finalized'" class="dim mt-1">
          本章已定稿，正文受快照保护——「定向修复」「整章润色」「编辑正文」都会被拒绝。
          要返工请先「解锁返工」：状态退回待定稿，快照与版本历史都保留，改完重新定稿即可。
        </div>
        <div class="mt-2 flex flex-wrap items-center gap-2">
          <span class="dim">只读回看（不重写）：</span>
          <el-select
            v-model="viewVer"
            placeholder="选择要回看的版本"
            clearable
            style="width: 260px"
          >
            <el-option
              v-for="v in versions"
              :key="v.version"
              :value="v.version"
              :label="`v${v.version} ${v.label} ${v.created_at.slice(0, 16)}`"
            />
          </el-select>
          <el-button :loading="snapshotLoading" @click="viewSnapshot(viewVer)"
            >查看该版本正文</el-button
          >
        </div>
        <div class="dim mt-2">
          {{ versions.length ? `快照 ${versions.length} 个（定稿历史全保留）` : '（暂无快照）' }}
        </div>
      </div>
    </div>
  </div>

  <!-- 细纲原文编辑 -->
  <el-dialog v-model="rawDialog" width="860px" top="4vh" destroy-on-close>
    <template #header
      ><span class="font-bold">细纲原文（YAML frontmatter + 场景清单）</span></template
    >
    <el-input v-model="rawText" type="textarea" :rows="22" class="mono" />
    <template #footer>
      <el-button @click="rawDialog = false">取消</el-button>
      <el-button type="primary" @click="saveRaw">保存细纲</el-button>
    </template>
  </el-dialog>

  <!-- 快照回看（只读，规格04 §2「版本列表可回看任意快照」）-->
  <el-dialog v-model="snapshotDialog" width="860px" top="4vh" destroy-on-close>
    <template #header>
      <span class="font-bold">快照回看（只读）</span>
      <span v-if="snapshotData" class="dim ml-2">
        v{{ snapshotData.version }} · {{ snapshotData.label }} · {{ snapshotData.created_at }}
      </span>
    </template>
    <div v-if="snapshotLoading" class="dim">加载中…</div>
    <template v-else-if="snapshotData">
      <div class="dim mb-1">
        {{ snapshotData.content.replace(/\s+/g, '').length }} 字 · 这是历史快照，不会改动当前正文
      </div>
      <pre class="stream-box">{{ snapshotData.content }}</pre>
    </template>
    <template #footer>
      <el-button @click="snapshotDialog = false">关闭</el-button>
    </template>
  </el-dialog>

  <!-- 人工手改正文（规格03 §2.5 人工关三选项之一）-->
  <el-dialog v-model="proseDialog" width="90%" top="3vh" destroy-on-close>
    <template #header>
      <span class="font-bold">人工编辑正文 · 第 {{ no }} 章</span>
      <span class="dim ml-2">
        保存后 version 不变、status 保持 draft、word_count 由后端重算（规格03 §2.5）
      </span>
    </template>
    <el-input v-model="proseEditText" type="textarea" :rows="24" class="mono" />
    <template #footer>
      <span class="dim mr-3">
        当前 {{ proseEditText.replace(/\s+/g, '').length }} 字（中文字数按非空白字符计）
      </span>
      <el-button @click="proseDialog = false">取消</el-button>
      <el-button type="primary" :loading="proseSaving" @click="saveProseEdit">保存正文</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="correctDialog" width="720px" top="6vh" destroy-on-close>
    <template #header>
      <span class="font-bold">定稿章订正 · 第 {{ no }} 章</span>
      <span class="dim ml-2">错别字 / 术语旧称 / 事实矛盾，逐条替换并留痕</span>
    </template>
    <div class="dim mb-1">
      每行一条，格式 <code>旧文 ==>> 新文</code>。旧文必须在正文里恰好出现一次； 总改动不得超过全文
      5%（超过请走「解锁返工」）；订正结果另存新版本快照，定稿原文保留。
    </div>
    <el-input
      v-model="correctText"
      type="textarea"
      :rows="8"
      class="mono"
      placeholder="筑基修士 ==>> 凝煞修士"
    />
    <div class="dim mt-2 mb-1">订正理由（必填，写进审校记录）</div>
    <el-input v-model="correctReason" placeholder="例：统一境界口径，炼气/筑基为历史遗留旧称" />
    <template #footer>
      <el-button @click="correctDialog = false">取消</el-button>
      <el-button type="primary" :loading="correctSaving" @click="submitCorrect">执行订正</el-button>
    </template>
  </el-dialog>
</template>
