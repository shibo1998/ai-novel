<script setup lang="ts">
// 档案编辑（规格04 §1 M1）：左树右编辑器。
// 规格明确要求的三条：① 左树导航六类档案；② 结构化字段（力量体系表、角色动态状态）用表单；
// ③ AI 生成按钮 → diff 预览 → 采纳。长文本（Bible 正文、角色 H2 节）用 Markdown textarea。
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import MdTableEditor from '@/components/MdTableEditor.vue'
import DiffViewer from '@/components/DiffViewer.vue'
import StyleEditor from '@/components/StyleEditor.vue'
import {
  adoptArchive,
  createCharacter,
  getBible,
  getBookConfig,
  getCharacter,
  getLocations,
  getMeta,
  getOutlineFull,
  getPlotPoints,
  listCharactersFull,
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
import { G_LABELS, G_TARGETS, adoptSummary, genArchive } from '@/utils/archiveAI'
import { dumpH2Sections, parseH2Sections } from '@/utils/h2'
import { toMdTable } from '@/utils/mdtable'
import { tagType } from '@/utils/status'
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

async function load(): Promise<void> {
  store.setCurrentBook(book.value)
  const [m, c, b, cs, loc, pp, od] = await Promise.all([
    getMeta(book.value),
    getBookConfig(book.value),
    getBible(book.value),
    listCharactersFull(book.value),
    getLocations(book.value),
    getPlotPoints(book.value),
    getOutlineFull(book.value),
  ])
  meta.value = m
  cfg.value = c
  bibleSections.value = Object.entries(b).map(([title, content]) => ({ title, content }))
  chars.value = cs
  locations.value = loc
  locRows.value = loc.map((r) => ({ ...r }))
  plotPoints.value = pp
  ppRows.value = pp.map((r) => ({ ...r }))
  outline.value = od
  outlineRows.value = (od.chapter_rows ?? []).map((r) => ({ ...r }))
  rhythmText.value = String(od._rhythm ?? '')
  volRows.value = volumesToRows(od.volumes ?? [])
  await loadSelected()
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
    try {
      const c = await getCharacter(book.value, n)
      charStatus.value = { ...((c.status ?? {}) as CharacterStatus) }
      const parsed = parseH2Sections(String(c._body ?? ''))
      charSections.value = parsed.sections
    } catch {
      // 拦截器已提示
    }
  }
}

function bibleTextOf(title: string): string {
  return bibleSections.value.find((s) => s.title === title)?.content ?? ''
}

watch([book], () => void load().catch(() => {}), { immediate: true })
watch(selectedId, () => void loadSelected().catch(() => {}))

// —— 保存：meta ——
const META_STATUS = ['drafting', 'serializing', 'done']
async function saveMeta(): Promise<void> {
  try {
    const payload: BookMeta = { ...meta.value }
    delete payload._body
    meta.value = await putMeta(book.value, payload)
    // 单章字数区间的真相源是 config.json（管线只读 config，不读 meta），
    // 所以 meta 表单改这一项时必须同步写 config，否则"改了不生效"。
    const wr = meta.value.chapter_word_range
    if (Array.isArray(wr) && wr.length === 2) {
      const cur = await getBookConfig(book.value)
      await putBookConfig(book.value, { ...cur, chapter_word_range: wr })
    }
    ElMessage.success('meta.md 已保存（字数区间已同步到书级配置）')
  } catch {
    // 拦截器已提示
  }
}

// —— 保存：Bible 单节（力量体系走表格，其余走 Markdown）——
async function saveBibleSection(): Promise<void> {
  const title = current.value?.arg ?? ''
  if (!title) return
  const content = title === '力量体系' ? rowsToText(POWER_COLS, powerRows.value) : bibleText.value
  const all: Record<string, string> = {}
  for (const s of bibleSections.value) all[s.title] = s.content
  all[title] = content
  try {
    await putBible(book.value, all)
    const local = bibleSections.value.find((s) => s.title === title)
    if (local) local.content = content
    ElMessage.success(`「${title}」已保存`)
  } catch {
    // 拦截器已提示
  }
}

// —— 保存：角色（动态状态表单 + 其余 H2 节 Markdown）——
const ROLE_OPTIONS = ['主角', '女主', '反派', '重要配角', '龙套']
async function saveCharacter(): Promise<void> {
  const name = current.value?.arg ?? ''
  if (!name) return
  const fm: Record<string, unknown> = {
    name,
    role: String(chars.value.find((c) => String(c.name) === name)?.role ?? '龙套'),
    status: { ...charStatus.value },
    _body: dumpH2Sections(charSections.value),
  }
  try {
    await putCharacter(book.value, name, fm as CharacterData)
    ElMessage.success(`角色「${name}」已保存`)
    await load()
  } catch {
    // 拦截器已提示
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
async function saveLocations(): Promise<void> {
  try {
    await putLocations(book.value, locRows.value)
    ElMessage.success('地点档案已保存')
    locations.value = locRows.value.map((r) => ({ ...r }))
  } catch {
    // 拦截器已提示
  }
}

async function savePlotPoints(): Promise<void> {
  try {
    await putPlotPoints(book.value, ppRows.value)
    ElMessage.success('伏笔台账已保存')
    plotPoints.value = ppRows.value.map((r) => ({ ...r }))
  } catch {
    // 状态迁移非法由拦截器提示
  }
}

async function saveOutlineRows(): Promise<void> {
  try {
    const od: OutlineFull = { ...outline.value }
    delete od._body
    delete od._rhythm
    od.chapter_rows = outlineRows.value
    od.volumes = rowsToVolumes(volRows.value)
    await putOutlineFull(book.value, od)
    ElMessage.success('章节总表已保存（卷规划一并落盘）')
    await load()
  } catch {
    // 拦截器已提示
  }
}

async function saveVolumes(): Promise<void> {
  try {
    const od: OutlineFull = { ...outline.value }
    delete od._body
    delete od._rhythm
    od.chapter_rows = outlineRows.value
    od.volumes = rowsToVolumes(volRows.value)
    await putOutlineFull(book.value, od)
    ElMessage.success('卷规划已保存')
    await load()
  } catch {
    // 拦截器已提示
  }
}

async function saveRhythm(): Promise<void> {
  try {
    const od: OutlineFull = { ...outline.value }
    delete od._body
    od._rhythm = rhythmText.value
    od.chapter_rows = outlineRows.value
    od.volumes = rowsToVolumes(volRows.value)
    await putOutlineFull(book.value, od)
    ElMessage.success('爽点节奏总谱已保存')
    await load()
  } catch {
    // 拦截器已提示
  }
}

// —— 书级配置（model_stages 五档 / 字数区间 / 修复轮次 / scene_mode / 白名单）——
const lintWhitelistText = ref('')
watch(
  () => cfg.value,
  (c) => {
    lintWhitelistText.value = (c.lint_whitelist ?? []).join('\n')
  },
  { immediate: true, deep: true },
)

async function saveConfig(): Promise<void> {
  const payload: BookConfig = {
    ...cfg.value,
    lint_whitelist: lintWhitelistText.value
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter(Boolean),
  }
  try {
    cfg.value = await putBookConfig(book.value, payload)
    ElMessage.success('书级配置已保存')
  } catch {
    // 拦截器已提示
  }
}

function setStageModel(stage: ModelStage, value: string): void {
  cfg.value = {
    ...cfg.value,
    model_stages: { ...(cfg.value.model_stages ?? {}), [stage]: value },
  }
}

// —— AI 生成 → diff 预览 → 采纳（规格02 §4）——
async function ask(label: string, value = ''): Promise<string> {
  try {
    const r = await ElMessageBox.prompt(label, 'AI 生成档案', { inputValue: value })
    return r.value ?? ''
  } catch {
    return ''
  }
}

const aiDialog = ref(false)
const aiLoading = ref(false)
const aiPid = ref('')
const aiText = ref('')
const aiOldText = ref('')
const aiTarget = ref<'bible' | 'characters' | 'outline'>('bible')
const aiDigest = ref('')

/** 采纳目标对应的「当前内容」（diff 的旧侧）。 */
function currentTextForTarget(target: 'bible' | 'characters' | 'outline'): string {
  if (target === 'bible') {
    return bibleSections.value.map((s) => `## ${s.title}\n\n${s.content}`).join('\n\n')
  }
  if (target === 'characters') {
    return chars.value.map((c) => String(c._body ?? '')).join('\n\n')
  }
  return [
    '## 章节总表',
    rowsToText(OC_COLS, outlineRows.value),
    '',
    '## 爽点节奏总谱',
    rhythmText.value,
  ].join('\n')
}

async function aiArchive(pid: string): Promise<void> {
  aiLoading.value = true
  try {
    const r = await genArchive(book.value, pid, ask)
    aiTarget.value = G_TARGETS[pid] ?? 'bible'
    aiPid.value = pid
    aiDigest.value = r.digest
    aiText.value = r.text
    aiOldText.value = currentTextForTarget(aiTarget.value)
    aiDialog.value = true
  } catch {
    // 拦截器已提示
  } finally {
    aiLoading.value = false
  }
}

async function adopt(): Promise<void> {
  try {
    const r = await adoptArchive(book.value, aiTarget.value, aiText.value)
    ElMessage.success({ message: adoptSummary(aiTarget.value, r), duration: 8000 })
    aiDialog.value = false
    await load()
  } catch {
    // 拦截器已提示
  }
}
</script>

<template>
  <div class="panel">
    <div class="flex flex-wrap items-center gap-2">
      <el-button @click="router.push(`/book/${encodeURIComponent(book)}`)">← 书总览</el-button>
      <span class="text-[15px] font-bold">档案编辑 · {{ meta.title || book }}</span>
      <el-tag v-if="meta.status" :type="tagType(meta.status)" size="small">{{
        meta.status
      }}</el-tag>
      <span class="flex-1"></span>
      <span class="dim"
        >左树选档案 → 右侧编辑 → 保存；结构化字段（力量体系/角色状态/台账/地点/大纲）用表单</span
      >
    </div>
    <div class="mt-2 flex flex-wrap items-center gap-2">
      <span class="dim">AI 生成（生成 → diff 预览 → 人工改定 → 采纳落盘）：</span>
      <el-button
        v-for="(label, pid) in G_LABELS"
        :key="pid"
        :loading="aiLoading && aiPid === pid"
        @click="aiArchive(pid)"
      >
        {{ label }}
      </el-button>
      <span class="dim">AI 永不静默覆盖：采纳前先看 diff</span>
    </div>
  </div>

  <div class="grid grid-cols-1 gap-3 lg:grid-cols-[280px_1fr]">
    <!-- 左树 -->
    <div class="panel archive-tree">
      <el-tree
        :data="tree"
        node-key="id"
        :props="{ label: 'label', children: 'children' }"
        :default-expanded-keys="['bible', 'chars', 'outline']"
        :current-node-key="selectedId"
        highlight-current
        @node-click="(n: TreeNode) => (selectedId = n.id)"
      />
    </div>

    <!-- 右侧编辑器 -->
    <div class="panel archive-editor">
      <h3 class="panel-title">{{ currentTitle }}</h3>

      <!-- meta.md 结构化表单（规格01 §2.1）-->
      <template v-if="currentKind === 'meta'">
        <el-form label-width="150px">
          <el-form-item label="书名 title">
            <el-input v-model="meta.title" style="width: 320px" />
          </el-form-item>
          <el-form-item label="笔名 author">
            <el-input v-model="meta.author" style="width: 320px" />
          </el-form-item>
          <el-form-item label="题材 genre">
            <el-input v-model="meta.genre" style="width: 320px" />
          </el-form-item>
          <el-form-item label="标签 tags">
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
          <el-form-item label="目标字数 target_words">
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
            <span class="dim ml-2">保存 meta 时同步写入书级配置（管线读的是 config.json）</span>
          </el-form-item>
          <el-form-item label="平台 platform">
            <el-input v-model="meta.platform" style="width: 240px" />
          </el-form-item>
          <el-form-item label="对标书 benchmark_book">
            <el-input v-model="meta.benchmark_book" style="width: 420px" />
          </el-form-item>
          <el-form-item label="状态 status">
            <el-select v-model="meta.status" style="width: 160px">
              <el-option v-for="s in META_STATUS" :key="s" :label="s" :value="s" />
            </el-select>
          </el-form-item>
          <el-form-item label="合规声明 compliance_note">
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
        <el-button type="primary" @click="saveMeta">保存 meta.md</el-button>
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
            <el-form-item label="境界 realm">
              <el-input v-model="charStatus.realm" style="width: 300px" />
            </el-form-item>
            <el-form-item label="位置 location">
              <el-input v-model="charStatus.location" style="width: 300px" />
            </el-form-item>
            <el-form-item label="情绪 emotion">
              <el-input v-model="charStatus.emotion" style="width: 300px" />
            </el-form-item>
            <el-form-item label="存活 alive">
              <el-switch v-model="charStatus.alive" />
              <span class="dim ml-2">关闭 = 已死亡（记忆卡试图复活会进裁决队列）</span>
            </el-form-item>
            <el-form-item label="伤势 injuries">
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
            <el-form-item label="物品 inventory">
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
            <el-form-item label="已知信息 known_info">
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
            <el-form-item label="更新章 updated_chapter">
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
    <h3 class="panel-title">书级配置（config.json）</h3>
    <div class="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <el-form label-width="140px">
        <el-form-item v-for="s in MODEL_STAGES" :key="s" :label="`${s} 档模型`">
          <el-input
            :model-value="cfg.model_stages?.[s] ?? ''"
            style="width: 320px"
            :placeholder="s === 'plan' ? '如 deepseek-chat' : '如 gemini-2.5-pro'"
            @update:model-value="(v: string) => setStageModel(s, v)"
          />
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
        <el-form-item label="逐场景生成 scene_mode">
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

  <!-- AI 生成 → diff 预览 → 采纳 -->
  <el-dialog v-model="aiDialog" width="92%" top="3vh" destroy-on-close>
    <template #header>
      <span class="font-bold">AI 生成 · diff 预览</span>
      <span class="dim ml-2">
        来源 {{ aiPid }} ｜ 采纳目标 {{ aiTarget }} ｜ digest {{ aiDigest.slice(0, 12) }}
      </span>
    </template>
    <DiffViewer :old-text="aiOldText" :new-text="aiText" title="旧档案 → AI 生成结果" />
    <el-divider />
    <div class="dim mb-1">生成结果（可手改后再采纳；采纳为「合并式」，不删除未提及的人工内容）</div>
    <el-input v-model="aiText" type="textarea" :rows="10" class="mono" />
    <template #footer>
      <el-button @click="aiDialog = false">取消</el-button>
      <el-button type="primary" @click="adopt">采纳落盘</el-button>
    </template>
  </el-dialog>
</template>
