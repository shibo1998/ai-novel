<script setup lang="ts">
// 档案编辑（规格04 §1 M1）：左树右编辑器。
// 规格明确要求的三条：① 左树导航六类档案；② 结构化字段（力量体系表、角色动态状态）用表单；
// ③ AI 生成按钮 → diff 预览 → 采纳。长文本（Bible 正文、角色 H2 节）用 Markdown textarea。
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import MdTableEditor from '@/components/MdTableEditor.vue'
import ModelSelect from '@/components/ModelSelect.vue'
import ArchiveAI from '@/components/ArchiveAI.vue'
import StyleEditor from '@/components/StyleEditor.vue'
import {
  createCharacter,
  getBibleVersioned,
  getBookConfigVersioned,
  getCharacterVersioned,
  getLocationsVersioned,
  getMetaVersioned,
  getOutlineVersioned,
  getPlotPointsVersioned,
  listCharactersVersioned,
  putBible,
  putBookConfig,
  putCharacter,
  putLocations,
  putMeta,
  putOutlineFull,
  putPlotPoints,
} from '@/api'
import type {
  BookConfig,
  BookMeta,
  CharacterData,
  CharacterStatus,
  ModelStage,
  OutlineFull,
} from '@/api/types'
import { MODEL_STAGES } from '@/api/types'
import { useBookStore } from '@/stores/book'
import { registerDirtyGuard } from '@/utils/editing'
import { dumpH2Sections, parseH2Sections } from '@/utils/h2'
import { toMdTable } from '@/utils/mdtable'
import { MODEL_STAGE_HINT, modelStageLabel, tagType } from '@/utils/status'
import type { TableColumn } from '@/components/MdTableEditor.vue'

const route = useRoute()
const router = useRouter()
const store = useBookStore()

const book = computed(() => String(route.params.name ?? ''))

// —— 左侧树：六类档案 ——
type NodeKind =
  | 'meta'
  | 'bible-section'
  | 'character'
  | 'locations'
  | 'plot_points'
  | 'outline-rows'
  | 'outline-volumes'
  | 'outline-rhythm'
  | 'style'

interface TreeNode {
  id: string
  label: string
  kind: NodeKind
  /** bible-section 的节名 / character 的角色名 */
  arg?: string
  children?: TreeNode[]
}

const selectedId = ref('meta')
const treeRef = ref<{ setCurrentKey: (key: string) => void } | null>(null)
const loading = ref(false)
const loadFailed = ref(false)
const saving = ref(false)
const loadError = ref('')
const etags = ref<Record<string, string | null>>({})
const baselineChar = ref('')
const returnChapter = computed(() => {
  const raw = Number(route.query.returnChapter)
  return Number.isInteger(raw) && raw > 0 ? raw : null
})
let loadRequest = 0
let selectedRequest = 0
let pageGeneration = 0

// —— 数据 ——
const meta = ref<BookMeta>({})
const cfg = ref<BookConfig>({})
const bibleSections = ref<{ title: string; content: string }[]>([])
const chars = ref<CharacterData[]>([])
const locations = ref<Record<string, string>[]>([])
const plotPoints = ref<Record<string, string>[]>([])
const outline = ref<OutlineFull>({})

// —— 列定义（必须与后端列名逐字一致）——
// 力量体系 8 列（规格01 §2.2）
const POWER_COLS: TableColumn[] = [
  { key: '大境界', label: '大境界', width: 96 },
  { key: '子境界', label: '子境界', width: 96 },
  { key: '突破条件', label: '突破条件', type: 'textarea' },
  { key: '能力边界(不能做什么)', label: '能力边界(不能做什么)', type: 'textarea' },
  { key: '战力对标', label: '战力对标', type: 'textarea' },
  { key: '寿元', label: '寿元', width: 96 },
  { key: '代价与风险', label: '代价与风险', type: 'textarea' },
  { key: '突破异象', label: '突破异象', type: 'textarea' },
]
// 伏笔台账 7 列（规格01 §2.5）
const PP_COLS: TableColumn[] = [
  { key: '编号', label: '编号', width: 90, placeholder: 'FO-001' },
  { key: '内容', label: '内容', type: 'textarea' },
  { key: '埋设章', label: '埋设章', width: 78, type: 'number' },
  { key: '推进章', label: '推进章', width: 90, placeholder: '7,11' },
  { key: '预计回收章', label: '预计回收章', width: 96 },
  { key: '状态', label: '状态', width: 100, options: ['planted', 'building', 'paid'] },
  { key: '备注', label: '备注', type: 'textarea' },
]
// 地点 5 列（规格01 §2.6）
const LOC_COLS: TableColumn[] = [
  { key: '名称', label: '名称', width: 130 },
  { key: '特征', label: '特征', type: 'textarea' },
  { key: '方位/距离', label: '方位/距离', width: 130 },
  { key: '首现章', label: '首现章', width: 84, type: 'number' },
  { key: '关联剧情', label: '关联剧情', type: 'textarea' },
]
// 章节总表 7 列（规格01 §2.4）
const OC_COLS: TableColumn[] = [
  { key: '章', label: '章', width: 60, type: 'number' },
  { key: '卷', label: '卷', width: 60, type: 'number' },
  { key: '本章目标', label: '本章目标', type: 'textarea' },
  { key: '冲突来源', label: '冲突来源', type: 'textarea' },
  { key: '爽点/揭露', label: '爽点/揭露', type: 'textarea' },
  {
    key: '钩子类型',
    label: '钩子类型',
    width: 100,
    options: ['悬念', '选择', '反转'],
  },
  { key: '重要度★', label: '重要度★', width: 88 },
]
// 卷规划 7 列（规格01 §2.4 volumes 结构：no/name/chapters/core_conflict/goal/climax/hook_out）
const VOL_COLS: TableColumn[] = [
  { key: '卷号', label: '卷号', width: 66 },
  { key: '卷名', label: '卷名', width: 120 },
  { key: '起止章', label: '起止章', width: 100, placeholder: '1-12' },
  { key: '核心冲突', label: '核心冲突', type: 'textarea' },
  { key: '本卷目标', label: '本卷目标', type: 'textarea' },
  { key: '卷末高潮', label: '卷末高潮', type: 'textarea' },
  { key: '卷末钩子(出卷悬念)', label: '卷末钩子(出卷悬念)', type: 'textarea' },
]

// —— 表格行数据（角色状态例外，走表单）——
const powerRows = ref<Record<string, string>[]>([])
const charStatus = ref<CharacterStatus>({})
const charSections = ref<{ title: string; content: string }[]>([])
const locRows = ref<Record<string, string>[]>([])
const ppRows = ref<Record<string, string>[]>([])
const outlineRows = ref<Record<string, string>[]>([])
const volRows = ref<Record<string, string>[]>([])
const rhythmText = ref('')
const bibleText = ref('')

const tree = computed<TreeNode[]>(() => [
  { id: 'meta', label: 'meta.md 书籍信息', kind: 'meta' },
  {
    id: 'bible',
    label: 'STORY_BIBLE.md',
    kind: 'bible-section',
    children: bibleSections.value.map((s) => ({
      id: `bible:${s.title}`,
      label: s.title,
      kind: 'bible-section' as const,
      arg: s.title,
    })),
  },
  {
    id: 'chars',
    label: `角色（${chars.value.length}）`,
    kind: 'character',
    children: chars.value.map((c) => ({
      id: `char:${String(c.name ?? '')}`,
      label: `${String(c.name ?? '')}（${String(c.role ?? '')}）`,
      kind: 'character' as const,
      arg: String(c.name ?? ''),
    })),
  },
  { id: 'locations', label: `地点（${locations.value.length}）`, kind: 'locations' },
  { id: 'plot', label: `伏笔台账（${plotPoints.value.length}）`, kind: 'plot_points' },
  {
    id: 'outline',
    label: 'OUTLINE.md 大纲',
    kind: 'outline-rows',
    children: [
      { id: 'outline:rows', label: '章节总表', kind: 'outline-rows' },
      { id: 'outline:volumes', label: '卷规划', kind: 'outline-volumes' },
      { id: 'outline:rhythm', label: '爽点节奏总谱', kind: 'outline-rhythm' },
    ],
  },
  { id: 'style', label: '文风（M5）', kind: 'style' },
])

/** 展开全部节点（含子节点）做 id → 节点查表；父节点点击时落到第一个子节点。 */
const flatNodes = computed<Map<string, TreeNode>>(() => {
  const map = new Map<string, TreeNode>()
  const walk = (nodes: TreeNode[]): void => {
    for (const n of nodes) {
      map.set(n.id, n)
      if (n.children) walk(n.children)
    }
  }
  walk(tree.value)
  return map
})

const current = computed<TreeNode | null>(() => {
  const node = flatNodes.value.get(selectedId.value)
  if (!node) return null
  // 分组节点（STORY_BIBLE / 角色 / OUTLINE）本身不是可编辑单元，落到第一个子节点
  if (node.children?.length) return node.children[0]!
  return node
})

const currentKind = computed<NodeKind>(() => current.value?.kind ?? 'meta')
const currentTitle = computed(() => current.value?.label ?? '')

/** 力量体系节：把 Markdown 表格解析成行（固定 8 列）。 */
function parseTableRows(text: string, marker: string): Record<string, string>[] {
  const lines = text.split(/\r?\n/).filter((l) => l.trim().startsWith('|'))
  const headerIdx = lines.findIndex((l) => l.includes(marker))
  if (headerIdx < 0) return []
  const cols = lines[headerIdx]!.replace(/^\||\|$/g, '')
    .split('|')
    .map((c) => c.trim())
  const rows: Record<string, string>[] = []
  for (const line of lines.slice(headerIdx + 2)) {
    if (!line.trim().startsWith('|')) break
    const cells = line
      .replace(/^\||\|$/g, '')
      .split('|')
      .map((c) => c.trim())
    const row: Record<string, string> = {}
    cols.forEach((c, i) => {
      row[c] = cells[i] ?? ''
    })
    rows.push(row)
  }
  return rows
}

/** 行数组 → Markdown 表格文本（列名由 TableColumn 决定，顺序一致）。 */
function rowsToText(cols: TableColumn[], rows: Record<string, string>[]): string {
  return toMdTable(
    cols.map((c) => c.key),
    rows,
  )
}

async function load(): Promise<boolean> {
  const targetBook = book.value
  const request = ++loadRequest
  const generation = pageGeneration
  store.setCurrentBook(targetBook)
  loading.value = true
  loadFailed.value = false
  loadError.value = ''
  try {
    const [m, c, b, cs, loc, pp, od] = await Promise.all([
      getMetaVersioned(targetBook),
      getBookConfigVersioned(targetBook),
      getBibleVersioned(targetBook),
      listCharactersVersioned(targetBook),
      getLocationsVersioned(targetBook),
      getPlotPointsVersioned(targetBook),
      getOutlineVersioned(targetBook),
    ])
    if (request !== loadRequest || generation !== pageGeneration || targetBook !== book.value)
      return false
    const versions = {
      meta: m.etag,
      config: c.etag,
      bible: b.etag,
      characters: cs.etag,
      locations: loc.etag,
      plot_points: pp.etag,
      outline: od.etag,
    }
    if (Object.values(versions).some((etag) => !etag))
      throw new Error('服务版本不匹配：档案读取缺少 ETag，已禁用无保护写入')
    etags.value = versions
    meta.value = m.data
    baselineMeta.value = JSON.parse(JSON.stringify(m.data)) as BookMeta
    cfg.value = c.data
    lintWhitelistText.value = (c.data.lint_whitelist ?? []).join('\n')
    baselineConfig.value = JSON.stringify({ cfg: c.data, whitelist: lintWhitelistText.value })
    bibleSections.value = Object.entries(b.data).map(([title, content]) => ({ title, content }))
    chars.value = cs.data
    locations.value = loc.data
    locRows.value = loc.data.map((r) => ({ ...r }))
    plotPoints.value = pp.data
    ppRows.value = pp.data.map((r) => ({ ...r }))
    outline.value = od.data
    outlineRows.value = (od.data.chapter_rows ?? []).map((r) => ({ ...r }))
    rhythmText.value = String(od.data._rhythm ?? '')
    volRows.value = volumesToRows(od.data.volumes ?? [])
    await applyNodeQuery()
    await loadSelected()
    return true
  } catch (error) {
    if (request === loadRequest && targetBook === book.value) {
      loadFailed.value = true
      loadError.value = error instanceof Error ? error.message : '档案加载失败'
    }
    return false
  } finally {
    if (request === loadRequest) loading.value = false
  }
}

function volumesToRows(vols: unknown[]): Record<string, string>[] {
  return vols.map((v) => {
    const o = (v ?? {}) as Record<string, unknown>
    const ch = Array.isArray(o.chapters) ? o.chapters : []
    return {
      卷号: String(o.no ?? ''),
      卷名: String(o.name ?? ''),
      起止章: ch.length >= 2 ? `${ch[0]}-${ch[1]}` : '',
      核心冲突: String(o.core_conflict ?? ''),
      本卷目标: String(o.goal ?? ''),
      卷末高潮: String(o.climax ?? ''),
      '卷末钩子(出卷悬念)': String(o.hook_out ?? ''),
    }
  })
}

function rowsToVolumes(rows: Record<string, string>[]): unknown[] {
  return rows.map((r) => {
    const nums = (r['起止章'] ?? '').match(/\d+/g)?.map(Number) ?? []
    return {
      no: r['卷号'] ?? '',
      name: r['卷名'] ?? '',
      chapters: nums.length >= 2 ? [nums[0], nums[1]] : [1, 1],
      core_conflict: r['核心冲突'] ?? '',
      goal: r['本卷目标'] ?? '',
      climax: r['卷末高潮'] ?? '',
      hook_out: r['卷末钩子(出卷悬念)'] ?? '',
    }
  })
}

/** 切节点时按需加载该节点数据（角色/力量体系懒加载）。 */
async function loadSelected(): Promise<void> {
  const k = currentKind.value
  if (k === 'bible-section' && current.value?.arg === '力量体系') {
    powerRows.value = parseTableRows(bibleTextOf('力量体系'), '大境界')
    bibleText.value = bibleTextOf('力量体系')
    return
  }
  if (k === 'bible-section') {
    bibleText.value = bibleTextOf(current.value?.arg ?? '')
    return
  }
  if (k === 'character') {
    const n = current.value?.arg ?? ''
    if (!n) return
    const targetBook = book.value
    const request = ++selectedRequest
    try {
      const response = await getCharacterVersioned(targetBook, n)
      if (request !== selectedRequest || targetBook !== book.value || current.value?.arg !== n)
        return
      if (!response.etag) throw new Error('角色档案缺少 ETag，已禁用保存')
      etags.value[`character:${n}`] = response.etag
      const c = response.data
      charStatus.value = { ...((c.status ?? {}) as CharacterStatus) }
      const parsed = parseH2Sections(String(c._body ?? ''))
      charSections.value = parsed.sections
      baselineChar.value = JSON.stringify({
        status: charStatus.value,
        sections: charSections.value,
      })
    } catch {
      // 拦截器已提示；保留当前输入，不把失败当空角色。
    }
  }
}

function bibleTextOf(title: string): string {
  return bibleSections.value.find((s) => s.title === title)?.content ?? ''
}

const dirty = computed(() => {
  const k = currentKind.value
  if (k === 'meta') return JSON.stringify(meta.value) !== JSON.stringify(baselineMeta.value)
  if (k === 'bible-section') {
    const title = current.value?.arg ?? ''
    const content = title === '力量体系' ? rowsToText(POWER_COLS, powerRows.value) : bibleText.value
    return content !== bibleTextOf(title)
  }
  if (k === 'character')
    return (
      JSON.stringify({ status: charStatus.value, sections: charSections.value }) !==
      baselineChar.value
    )
  if (k === 'locations') return JSON.stringify(locRows.value) !== JSON.stringify(locations.value)
  if (k === 'plot_points') return JSON.stringify(ppRows.value) !== JSON.stringify(plotPoints.value)
  if (k === 'outline-rows')
    return JSON.stringify(outlineRows.value) !== JSON.stringify(outline.value.chapter_rows ?? [])
  if (k === 'outline-volumes')
    return (
      JSON.stringify(volRows.value) !== JSON.stringify(volumesToRows(outline.value.volumes ?? []))
    )
  if (k === 'outline-rhythm') return rhythmText.value !== String(outline.value._rhythm ?? '')
  return false
})
const baselineMeta = ref<BookMeta>({})
watch(
  meta,
  (value) => {
    if (loading.value) baselineMeta.value = JSON.parse(JSON.stringify(value)) as BookMeta
  },
  { deep: true },
)

async function confirmLeave(): Promise<'save' | 'discard' | 'cancel'> {
  if (!dirty.value && !configDirty.value) return 'discard'
  if (saving.value) return 'cancel'
  try {
    const action = await ElMessageBox.confirm(
      `「${currentTitle.value}」有未保存修改。保存后继续，或放弃修改？`,
      '档案修改未保存',
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
async function selectNode(id: string): Promise<void> {
  if (id === selectedId.value) return
  const decision = await confirmLeave()
  if (decision === 'cancel') {
    treeRef.value?.setCurrentKey(selectedId.value)
    return
  }
  if (decision === 'save' && !(await saveCurrent())) return
  selectedRequest++
  selectedId.value = id
  await loadSelected()
}
async function applyNodeQuery(): Promise<void> {
  const raw = route.query.node
  if (raw === undefined) return
  const id = typeof raw === 'string' ? raw : ''
  const target = flatNodes.value.get(id)
  if (!target) {
    selectedId.value = 'meta'
    ElMessage.warning(`档案节点无效（${id || '空'}），已回到书籍信息`)
  } else {
    selectedId.value = target.children?.length ? target.children[0]!.id : target.id
  }
  await nextTick()
  treeRef.value?.setCurrentKey(selectedId.value)
  document.querySelector('.archive-editor')?.scrollTo({ top: 0 })
}
watch(
  book,
  () => {
    pageGeneration++
    void load()
  },
  { immediate: true },
)
watch(
  () => route.query.node,
  async () => {
    if (loading.value || loadFailed.value) return
    const decision = await confirmLeave()
    if (decision === 'cancel') return
    if (decision === 'save' && !(await saveCurrent())) return
    await applyNodeQuery()
    await loadSelected()
  },
)
const unregisterDirty = registerDirtyGuard(
  async () => {
    const decision = await confirmLeave()
    if (decision === 'save') return (await saveCurrent()) ? 'save' : 'cancel'
    return decision
  },
  () => dirty.value || configDirty.value,
)
onUnmounted(() => {
  pageGeneration++
  loadRequest++
  selectedRequest++
  unregisterDirty()
})

// —— 保存：meta ——
// 规格12 文案中文化：token 仍是 drafting/serializing/done，只换展示名
const META_STATUS = [
  { value: 'drafting', label: '创作中' },
  { value: 'serializing', label: '连载中' },
  { value: 'done', label: '已完结' },
]
async function saveMeta(): Promise<boolean> {
  if (!etags.value.meta || !etags.value.config || saving.value) return false
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm(
      '将保存 meta.md 全部字段，并同步单章字数区间到 config.json；两项使用各自打开时版本校验。',
      '确认保存范围',
      { type: 'warning' },
    )
  } catch {
    return false
  }
  saving.value = true
  try {
    const payload: BookMeta = { ...meta.value }
    meta.value = await putMeta(book.value, payload, etags.value.meta)
    const wr = meta.value.chapter_word_range
    if (Array.isArray(wr) && wr.length === 2)
      await putBookConfig(book.value, { ...cfg.value, chapter_word_range: wr }, etags.value.config)
    ElMessage.success('meta.md 已保存（字数区间已同步到书级配置）')
    await load()
    return true
  } catch {
    return false
  } finally {
    saving.value = false
  }
}

// —— 保存：Bible 单节（力量体系走表格，其余走 Markdown）——
async function saveBibleSection(): Promise<boolean> {
  const title = current.value?.arg ?? ''
  if (!title || !etags.value.bible || saving.value) return false
  if (!dirty.value) return true
  const content = title === '力量体系' ? rowsToText(POWER_COLS, powerRows.value) : bibleText.value
  try {
    await ElMessageBox.confirm(
      `仅修改 Bible「${title}」节；提交整文件时以打开整份资源时的 ETag 防止覆盖其他页修改。`,
      '确认保存差异',
      { type: 'warning' },
    )
  } catch {
    return false
  }
  const all: Record<string, string> = {}
  for (const s of bibleSections.value) all[s.title] = s.content
  all[title] = content
  saving.value = true
  try {
    await putBible(book.value, all, etags.value.bible)
    const local = bibleSections.value.find((s) => s.title === title)
    if (local) local.content = content
    ElMessage.success(`「${title}」已保存`)
    await load()
    return true
  } catch {
    return false
  } finally {
    saving.value = false
  }
}

// —— 保存：角色（动态状态表单 + 其余 H2 节 Markdown）——
const ROLE_OPTIONS = ['主角', '女主', '反派', '重要配角', '龙套']
async function saveCharacter(): Promise<boolean> {
  const name = current.value?.arg ?? ''
  const etag = etags.value[`character:${name}`]
  if (!name || !etag || saving.value) return false
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm(
      `将保存角色「${name}」动态状态与全部 H2 长文本节；按打开角色时 ETag 校验。`,
      '确认角色变更',
      { type: 'warning' },
    )
  } catch {
    return false
  }
  const fm: Record<string, unknown> = {
    name,
    role: String(chars.value.find((c) => String(c.name) === name)?.role ?? '龙套'),
    status: { ...charStatus.value },
    _body: dumpH2Sections(charSections.value),
  }
  saving.value = true
  try {
    await putCharacter(book.value, name, fm as CharacterData, etag)
    ElMessage.success(`角色「${name}」已保存`)
    await load()
    return true
  } catch {
    return false
  } finally {
    saving.value = false
  }
}

const newChar = ref({ name: '', role: '龙套' })
async function createChar(): Promise<void> {
  const n = newChar.value.name.trim()
  if (!n) {
    ElMessage.warning('填新角色名')
    return
  }
  try {
    await createCharacter(book.value, n, newChar.value.role)
    ElMessage.success('已建档')
    newChar.value.name = ''
    await load()
    selectedId.value = `char:${n}`
  } catch {
    // 拦截器已提示
  }
}

// —— 保存：地点 / 伏笔台账 / 大纲 ——
async function saveLocations(): Promise<boolean> {
  if (!etags.value.locations || saving.value) return false
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm(
      `将保存地点档案 ${locRows.value.length} 行；按整个地点文件版本校验。`,
      '确认保存范围',
      { type: 'warning' },
    )
  } catch {
    return false
  }
  saving.value = true
  try {
    await putLocations(book.value, locRows.value, etags.value.locations)
    ElMessage.success('地点档案已保存')
    await load()
    return true
  } catch {
    return false
  } finally {
    saving.value = false
  }
}

async function savePlotPoints(): Promise<boolean> {
  if (!etags.value.plot_points || saving.value) return false
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm(
      `将保存伏笔台账 ${ppRows.value.length} 行，状态迁移仍由后端校验。`,
      '确认保存范围',
      { type: 'warning' },
    )
  } catch {
    return false
  }
  saving.value = true
  try {
    await putPlotPoints(book.value, ppRows.value, etags.value.plot_points)
    ElMessage.success('伏笔台账已保存')
    await load()
    return true
  } catch {
    return false
  } finally {
    saving.value = false
  }
}

async function saveOutline(kind: string): Promise<boolean> {
  if (!etags.value.outline || saving.value) return false
  if (!dirty.value) return true
  try {
    await ElMessageBox.confirm(
      `将保存${kind}；提交保留当前卷规划、章节总表和节奏总谱，ETag 同时覆盖 OUTLINE.md 与 PLOT_POINTS.md。`,
      '确认大纲变更',
      { type: 'warning' },
    )
  } catch {
    return false
  }
  const od: OutlineFull = {
    ...outline.value,
    chapter_rows: outlineRows.value,
    volumes: rowsToVolumes(volRows.value),
    _rhythm: rhythmText.value,
  }
  saving.value = true
  try {
    await putOutlineFull(book.value, od, etags.value.outline)
    ElMessage.success(`${kind}已保存`)
    await load()
    return true
  } catch {
    return false
  } finally {
    saving.value = false
  }
}
const saveOutlineRows = (): Promise<boolean> => saveOutline('章节总表')
const saveVolumes = (): Promise<boolean> => saveOutline('卷规划')
const saveRhythm = (): Promise<boolean> => saveOutline('爽点节奏总谱')

// —— 书级配置（model_stages 五档 / 字数区间 / 修复轮次 / scene_mode / 白名单）——
const lintWhitelistText = ref('')
watch(
  () => cfg.value,
  (c) => {
    lintWhitelistText.value = (c.lint_whitelist ?? []).join('\n')
  },
  { immediate: true, deep: true },
)

const baselineConfig = ref('')
watch(
  [cfg, lintWhitelistText],
  () => {
    if (loading.value)
      baselineConfig.value = JSON.stringify({ cfg: cfg.value, whitelist: lintWhitelistText.value })
  },
  { deep: true, immediate: true },
)
const configDirty = computed(
  () =>
    baselineConfig.value !== JSON.stringify({ cfg: cfg.value, whitelist: lintWhitelistText.value }),
)
async function saveConfig(): Promise<boolean> {
  if (!etags.value.config || saving.value) return false
  if (!configDirty.value) return true
  const payload: BookConfig = {
    ...cfg.value,
    lint_whitelist: lintWhitelistText.value
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter(Boolean),
  }
  try {
    await ElMessageBox.confirm(
      '将保存 config.json 模型档位、字数区间、场景模式与词库白名单。',
      '确认书级配置变更',
      { type: 'warning' },
    )
  } catch {
    return false
  }
  saving.value = true
  try {
    cfg.value = await putBookConfig(book.value, payload, etags.value.config)
    ElMessage.success('书级配置已保存')
    await load()
    return true
  } catch {
    return false
  } finally {
    saving.value = false
  }
}

/** 五档中文用途说明：让"长文通读"这类档位名不用猜（规格12 文案中文化） */
const stageLegend = computed(() =>
  MODEL_STAGES.map((s) => `${modelStageLabel(s)}＝${MODEL_STAGE_HINT[s]}`).join('；'),
)

function setStageModel(stage: ModelStage, value: string): void {
  cfg.value = {
    ...cfg.value,
    model_stages: { ...(cfg.value.model_stages ?? {}), [stage]: value },
  }
}

// 生效来源（规格12 §3）：书级填了就是书级；清空后按全局档位/兜底解析。
// 标签取的是"保存后"的预期来源，未保存时以本地输入为准。
const SOURCE_LABELS: Record<string, string> = {
  book: '书级',
  global_stage: '全局档位',
  fallback: '兜底',
  default: '代码默认',
}
function stageSource(stage: ModelStage): string {
  if ((cfg.value.model_stages?.[stage] ?? '').trim()) return 'book'
  const eff = cfg.value.model_effective?.[stage]
  return eff && eff.source !== 'book' ? eff.source : ''
}
function stageEffectiveModel(stage: ModelStage): string {
  const local = (cfg.value.model_stages?.[stage] ?? '').trim()
  if (local) return local
  const eff = cfg.value.model_effective?.[stage]
  if (!eff) return ''
  // 默认接口只显示模型名；其它接口带上端点，否则看不出走的是哪家中转站。
  return eff.provider && eff.provider !== 'openai' ? `${eff.provider}@${eff.model}` : eff.model
}

async function saveCurrent(): Promise<boolean> {
  if (configDirty.value && !(await saveConfig())) return false
  const kind = currentKind.value
  if (kind === 'meta') return saveMeta()
  if (kind === 'bible-section') return saveBibleSection()
  if (kind === 'character') return saveCharacter()
  if (kind === 'locations') return saveLocations()
  if (kind === 'plot_points') return savePlotPoints()
  if (kind === 'outline-rows') return saveOutlineRows()
  if (kind === 'outline-volumes') return saveVolumes()
  if (kind === 'outline-rhythm') return saveRhythm()
  return true
}
async function saveAndReturn(): Promise<void> {
  if (!returnChapter.value) return
  if (!(await saveCurrent())) return
  await router.push(`/book/${encodeURIComponent(book.value)}/chapter/${returnChapter.value}`)
}
</script>

<template>
  <div class="panel">
    <div class="flex flex-wrap items-center gap-2">
      <el-button @click="router.push(`/book/${encodeURIComponent(book)}`)">← 书总览</el-button>
      <el-button v-if="returnChapter" type="primary" :loading="saving" @click="saveAndReturn"
        >保存并返回第 {{ returnChapter }} 章</el-button
      >
      <span class="text-[15px] font-bold">档案编辑 · {{ meta.title || book }}</span>
      <el-tag v-if="meta.status" :type="tagType(meta.status)" size="small">{{
        meta.status
      }}</el-tag>
      <span class="flex-1"></span>
      <span class="dim"
        >左树选档案 → 右侧编辑 → 保存；结构化字段（力量体系/角色状态/台账/地点/大纲）用表单</span
      >
    </div>
    <ArchiveAI :book="book" @adopted="load" />
  </div>

  <el-skeleton v-if="loading" :rows="12" animated />
  <el-result v-else-if="loadFailed" icon="error" title="档案加载失败" :sub-title="loadError"
    ><template #extra
      ><el-button @click="load">重试</el-button
      ><el-button @click="router.push(`/book/${encodeURIComponent(book)}`)"
        >返回书总览</el-button
      ></template
    ></el-result
  >
  <template v-else>
    <div class="grid grid-cols-1 gap-3 lg:grid-cols-[280px_1fr]">
      <!-- 左树 -->
      <div class="panel archive-tree">
        <el-tree
          ref="treeRef"
          :data="tree"
          node-key="id"
          :props="{ label: 'label', children: 'children' }"
          :default-expanded-keys="['bible', 'chars', 'outline']"
          :current-node-key="selectedId"
          highlight-current
          @node-click="(n: TreeNode) => selectNode(n.id)"
        />
      </div>

      <!-- 右侧编辑器 -->
      <div class="panel archive-editor">
        <h3 class="panel-title">{{ currentTitle }}</h3>

        <!-- meta.md 结构化表单（规格01 §2.1）-->
        <template v-if="currentKind === 'meta'">
          <el-form label-width="150px">
            <el-form-item label="书名">
              <el-input v-model="meta.title" style="width: 320px" />
            </el-form-item>
            <el-form-item label="笔名">
              <el-input v-model="meta.author" style="width: 320px" />
            </el-form-item>
            <el-form-item label="题材">
              <el-input v-model="meta.genre" style="width: 320px" />
            </el-form-item>
            <el-form-item label="标签">
              <el-select
                v-model="meta.tags"
                multiple
                filterable
                allow-create
                default-first-option
                placeholder="回车新增标签"
                style="width: 420px"
              />
            </el-form-item>
            <el-form-item label="目标字数">
              <el-input-number
                :model-value="Number(meta.target_words ?? 0)"
                :min="0"
                :step="100000"
                style="width: 200px"
                @update:model-value="(v: number | undefined) => (meta.target_words = v ?? 0)"
              />
              <span class="dim ml-2"
                >约 {{ ((meta.target_words ?? 0) / 10000).toFixed(0) }} 万字</span
              >
            </el-form-item>
            <el-form-item label="单章字数区间">
              <el-input-number
                :model-value="Number(meta.chapter_word_range?.[0] ?? 2200)"
                :min="0"
                :step="100"
                style="width: 130px"
                @update:model-value="
                  (v: number | undefined) =>
                    (meta.chapter_word_range = [v ?? 2200, meta.chapter_word_range?.[1] ?? 2800])
                "
              />
              <span class="mx-2">—</span>
              <el-input-number
                :model-value="Number(meta.chapter_word_range?.[1] ?? 2800)"
                :min="0"
                :step="100"
                style="width: 130px"
                @update:model-value="
                  (v: number | undefined) =>
                    (meta.chapter_word_range = [meta.chapter_word_range?.[0] ?? 2200, v ?? 2800])
                "
              />
              <span class="dim ml-2">保存时同步写入书级配置（生成时读的是配置文件）</span>
            </el-form-item>
            <el-form-item label="平台">
              <el-input v-model="meta.platform" style="width: 240px" />
            </el-form-item>
            <el-form-item label="对标书">
              <el-input v-model="meta.benchmark_book" style="width: 420px" />
            </el-form-item>
            <el-form-item label="状态">
              <el-select v-model="meta.status" style="width: 160px">
                <el-option
                  v-for="s in META_STATUS"
                  :key="s.value"
                  :label="s.label"
                  :value="s.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="合规声明">
              <el-input
                v-model="meta.compliance_note"
                type="textarea"
                :rows="2"
                style="width: 620px"
              />
            </el-form-item>
            <el-form-item label="正文（自由文本）">
              <el-input v-model="meta._body" type="textarea" :rows="5" style="width: 620px" />
            </el-form-item>
          </el-form>
          <el-button type="primary" @click="saveMeta">保存基础信息</el-button>
        </template>

        <!-- Bible 节：力量体系 = 8 列表单；其余 = Markdown -->
        <template v-else-if="currentKind === 'bible-section'">
          <template v-if="current?.arg === '力量体系'">
            <div class="dim mb-2">
              规格01 §2.2 固定 8
              列表格：大境界｜子境界｜突破条件｜能力边界(不能做什么)｜战力对标｜寿元｜代价与风险｜突破异象。
              装配器从这里抽境界行，境界倒退冲突检测也读它。
            </div>
            <MdTableEditor v-model:rows="powerRows" :columns="POWER_COLS" reorderable />
          </template>
          <template v-else>
            <div class="dim mb-1">## {{ current?.arg }}（Markdown 正文）</div>
            <el-input v-model="bibleText" type="textarea" :rows="22" class="mono" />
          </template>
          <el-button class="mt-2" type="primary" @click="saveBibleSection"
            >保存「{{ current?.arg }}」</el-button
          >
        </template>

        <!-- 角色：动态状态表单 + 其余 H2 节 Markdown -->
        <template v-else-if="currentKind === 'character'">
          <div class="mb-2 flex flex-wrap items-center gap-2">
            <span class="dim">新角色</span>
            <el-input v-model="newChar.name" placeholder="角色名" style="width: 140px" />
            <el-select v-model="newChar.role" style="width: 120px">
              <el-option v-for="r in ROLE_OPTIONS" :key="r" :label="r" :value="r" />
            </el-select>
            <el-button @click="createChar">建档</el-button>
          </div>
          <template v-if="current?.arg">
            <div class="mono mb-1 text-xs">动态状态段（规格01 §2.3，每章由记忆卡回写）</div>
            <el-form label-width="130px">
              <el-form-item label="境界">
                <el-input v-model="charStatus.realm" style="width: 300px" />
              </el-form-item>
              <el-form-item label="位置">
                <el-input v-model="charStatus.location" style="width: 300px" />
              </el-form-item>
              <el-form-item label="情绪">
                <el-input v-model="charStatus.emotion" style="width: 300px" />
              </el-form-item>
              <el-form-item label="存活">
                <el-switch v-model="charStatus.alive" />
                <span class="dim ml-2">关闭 = 已死亡（记忆卡试图复活会进裁决队列）</span>
              </el-form-item>
              <el-form-item label="伤势">
                <el-select
                  v-model="charStatus.injuries"
                  multiple
                  filterable
                  allow-create
                  default-first-option
                  placeholder="回车新增"
                  style="width: 420px"
                />
              </el-form-item>
              <el-form-item label="物品">
                <el-select
                  v-model="charStatus.inventory"
                  multiple
                  filterable
                  allow-create
                  default-first-option
                  placeholder="回车新增"
                  style="width: 420px"
                />
              </el-form-item>
              <el-form-item label="已知信息">
                <el-select
                  v-model="charStatus.known_info"
                  multiple
                  filterable
                  allow-create
                  default-first-option
                  placeholder="回车新增（防提前知情穿帮）"
                  style="width: 420px"
                />
              </el-form-item>
              <el-form-item label="更新章">
                <el-input-number
                  :model-value="Number(charStatus.updated_chapter ?? 0)"
                  :min="0"
                  :controls="false"
                  style="width: 120px"
                  @update:model-value="
                    (v: number | undefined) => (charStatus.updated_chapter = v ?? 0)
                  "
                />
              </el-form-item>
            </el-form>
            <el-divider />
            <div class="dim mb-1">
              其余 H2 节（Markdown）。关系网络由 G4 采纳自动合并写回，也可手改。
            </div>
            <el-collapse>
              <el-collapse-item v-for="(s, i) in charSections" :key="s.title" :title="s.title">
                <el-input v-model="charSections[i]!.content" type="textarea" :rows="6" />
              </el-collapse-item>
            </el-collapse>
            <el-button class="mt-2" type="primary" @click="saveCharacter">
              保存角色「{{ current.arg }}」
            </el-button>
          </template>
          <span v-else class="dim">（左侧选择一个角色）</span>
        </template>

        <!-- 地点 5 列表 -->
        <template v-else-if="currentKind === 'locations'">
          <div class="dim mb-2">
            规格01 §2.6 5 列。装配器的前文检索会读地点名，为空会降低召回质量。
          </div>
          <MdTableEditor v-model:rows="locRows" :columns="LOC_COLS" />
          <el-button class="mt-2" type="primary" @click="saveLocations">保存地点档案</el-button>
        </template>

        <!-- 伏笔台账 7 列表 -->
        <template v-else-if="currentKind === 'plot_points'">
          <div class="dim mb-2">
            规格01 §2.5 7 列。状态只能 planted→building→paid 单向；paid 不得回退（后端校验）。
          </div>
          <MdTableEditor v-model:rows="ppRows" :columns="PP_COLS" />
          <el-button class="mt-2" type="primary" @click="savePlotPoints">保存伏笔台账</el-button>
        </template>

        <!-- 大纲：章节总表 -->
        <template v-else-if="currentKind === 'outline-rows'">
          <div class="dim mb-2">规格01 §2.4 章节总表 7 列。保存时卷规划一并落盘（避免丢卷）。</div>
          <MdTableEditor v-model:rows="outlineRows" :columns="OC_COLS" reorderable />
          <el-button class="mt-2" type="primary" @click="saveOutlineRows">保存章节总表</el-button>
        </template>

        <!-- 大纲：卷规划 -->
        <template v-else-if="currentKind === 'outline-volumes'">
          <div class="dim mb-2">
            规格01 §2.4 frontmatter.volumes：卷号/卷名/起止章/核心冲突/本卷目标/卷末高潮/卷末钩子。
            起止章形如 <span class="mono">1-12</span>。
          </div>
          <MdTableEditor v-model:rows="volRows" :columns="VOL_COLS" reorderable />
          <el-button class="mt-2" type="primary" @click="saveVolumes">保存卷规划</el-button>
        </template>

        <!-- 大纲：爽点节奏总谱 -->
        <template v-else-if="currentKind === 'outline-rhythm'">
          <div class="dim mb-2">
            规格01 §2.4「## 爽点节奏总谱」正文。此前无 UI 入口且保存大纲会把它重置，这里独占编辑。
          </div>
          <el-input v-model="rhythmText" type="textarea" :rows="16" class="mono" />
          <el-button class="mt-2" type="primary" @click="saveRhythm">保存爽点节奏总谱</el-button>
        </template>

        <!-- 文风 M5 -->
        <template v-else-if="currentKind === 'style'">
          <StyleEditor :book="book" />
        </template>
      </div>
    </div>

    <!-- 书级配置（规格01 §1 config.json）：model_stages 五档 / 字数区间 / 修复轮次 / scene_mode / 白名单 -->
    <div class="panel">
      <h3 class="panel-title">书级配置</h3>
      <div class="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <el-form label-width="140px">
          <el-form-item v-for="s in MODEL_STAGES" :key="s" :label="`${modelStageLabel(s)}档模型`">
            <div class="flex flex-col gap-1">
              <div class="flex items-center gap-2">
                <ModelSelect
                  :model-value="cfg.model_stages?.[s] ?? ''"
                  width="260px"
                  placeholder="留空则按全局/兜底解析"
                  @update:model-value="(v: string) => setStageModel(s, v)"
                />
                <el-tag
                  v-if="stageSource(s)"
                  size="small"
                  :type="stageSource(s) === 'book' ? 'primary' : 'info'"
                >
                  {{ SOURCE_LABELS[stageSource(s)] }}
                </el-tag>
              </div>
              <span class="dim text-xs">
                <template v-if="stageEffectiveModel(s)">
                  生效：{{ stageEffectiveModel(s) }}
                  <template v-if="stageSource(s) !== 'book'"
                    >（来自{{ SOURCE_LABELS[stageSource(s)] }}）</template
                  >
                </template>
                <template v-else>留空：保存后按全局档位 → 兜底模型解析</template>
              </span>
            </div>
          </el-form-item>
          <el-form-item label=" ">
            <span class="dim">
              {{ stageLegend }}
            </span>
          </el-form-item>
          <el-form-item label="单章字数区间">
            <el-input-number
              :model-value="Number(cfg.chapter_word_range?.[0] ?? 2200)"
              :min="0"
              :step="100"
              style="width: 130px"
              @update:model-value="
                (v: number | undefined) =>
                  (cfg.chapter_word_range = [v ?? 2200, cfg.chapter_word_range?.[1] ?? 2800])
              "
            />
            <span class="mx-2">—</span>
            <el-input-number
              :model-value="Number(cfg.chapter_word_range?.[1] ?? 2800)"
              :min="0"
              :step="100"
              style="width: 130px"
              @update:model-value="
                (v: number | undefined) =>
                  (cfg.chapter_word_range = [cfg.chapter_word_range?.[0] ?? 2200, v ?? 2800])
              "
            />
          </el-form-item>
          <el-form-item label="审校修复最大轮次">
            <el-input-number
              :model-value="Number(cfg.review_fix_max_rounds ?? 2)"
              :min="0"
              :max="5"
              style="width: 120px"
              @update:model-value="(v: number | undefined) => (cfg.review_fix_max_rounds = v ?? 2)"
            />
            <span class="dim ml-2">规格03 §2.5 默认 2 轮</span>
          </el-form-item>
          <el-form-item label="逐场景生成">
            <el-switch
              :model-value="Boolean(cfg.scene_mode)"
              @update:model-value="(v: boolean | string | number) => (cfg.scene_mode = Boolean(v))"
            />
            <span class="dim ml-2">开启后按场景逐个生成再缝合（每场景独立注意力+字数配额）</span>
          </el-form-item>
        </el-form>
        <div>
          <div class="dim mb-1">
            禁词白名单 lint_whitelist（每行一个词；目标文风作者招牌词豁免 M-02）
          </div>
          <el-input v-model="lintWhitelistText" type="textarea" :rows="10" class="mono" />
        </div>
      </div>
      <el-button class="mt-2" type="primary" @click="saveConfig">保存书级配置</el-button>
    </div>
  </template>
</template>
