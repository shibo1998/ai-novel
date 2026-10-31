<script setup lang="ts">
// 内容规划页（规格08 §3.3 阶段B）：全书→卷→单元→章纲批次的滚动规划工作台。
// - rolling 书的唯一分层编辑实现；legacy 档案编辑仍在档案页（本页只读+启用入口）。
// - 就绪状态全部展示服务端计算结果；生成/预览/采纳走 /planning/* 端点（强 If-Match）。
// - 候选可编辑；编辑后旧预览作废，必须重新预览（preview_digest 校验兜底）。
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { isAxiosError } from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  adoptPlanning,
  enablePlanning,
  generatePlanning,
  getPlanningVersioned,
  previewPlanning,
} from '@/api'
import type {
  PlanningLayer,
  PlanningModel,
  PlanningPreview,
  PlanningScope,
  PlanningTarget,
} from '@/api/types'
import DiffViewer from '@/components/DiffViewer.vue'
import {
  PLANNING_SCOPE_LABELS,
  PLANNING_STATUS_LABEL,
  PLANNING_STATUS_TAG,
  planningImpactText,
  planningLayerLine,
  planningNextActionText,
  planningNextBatch,
  planningNodeKey,
  planningProjectionText,
} from '@/utils/planning'
import { withTask } from '@/utils/tasks'

interface Session {
  book: string
  scope: PlanningScope
  target: PlanningTarget
  content: string
  inputVersion: string
  promptVersion: number | null
}

interface TreeNode {
  key: string
  label: string
  scope: PlanningScope | null
  target: PlanningTarget | null
  layer?: PlanningLayer
  protectedNos?: number[]
}

const route = useRoute()
const router = useRouter()
const name = computed(() => String(route.params.name ?? ''))
const nodeQuery = computed(
  () => (Array.isArray(route.query.node) ? route.query.node[0] : route.query.node) ?? '',
)
const returnChapter = computed(() => {
  const raw = Array.isArray(route.query.returnChapter)
    ? route.query.returnChapter[0]
    : route.query.returnChapter
  return /^\d+$/.test(String(raw ?? '')) ? Number(raw) : null
})

const model = ref<PlanningModel | null>(null)
const etag = ref('')
const loading = ref(false)
const failed = ref(false)
let loadRequest = 0

const session = ref<Session | null>(null)
const preview = ref<PlanningPreview | null>(null)
let previewedContent = ''
const requirements = ref('')
const generating = ref(false)
const previewing = ref(false)
const adopting = ref(false)
const inputChanged = ref(false)

const statusTag = computed(() => (model.value?.estimates.source === 'unknown' ? 'danger' : 'info'))

function statusTagOf(layer: PlanningLayer): 'primary' | 'success' | 'warning' | 'info' | 'danger' {
  return PLANNING_STATUS_TAG[layer.status] ?? 'info'
}

function statusLabelOf(layer: PlanningLayer): string {
  return PLANNING_STATUS_LABEL[layer.status] ?? layer.status
}

/** 左侧树：全书 → 卷 → 单元 → 已采纳章纲批次 */
const treeData = computed<TreeNode[]>(() => {
  const m = model.value
  if (!m || m.mode !== 'rolling' || !m.tree?.volumes) return []
  const layers = m.readiness.layers
  const covered = m.tree.chapter_numbers ?? []
  const nodes: TreeNode[] = [
    {
      key: 'global',
      label: `全书粗纲${m.tree.chapter_rows ? ` · 已采纳章纲 ${m.tree.chapter_rows} 行` : ''}`,
      scope: 'global',
      target: { volume_no: null, unit_id: null, chapters: null },
      layer: layers.global,
    },
  ]
  for (const vol of m.tree.volumes) {
    const volNo = Number(vol.no)
    const volLayer = layers.volumes.find((l) => String(l.no) === String(vol.no))
    const node: TreeNode = {
      key: `volume:${vol.no}`,
      label: `卷${vol.no} ${vol.name || '（未命名）'} · ${vol.detail_level === 'expanded' ? '已展开' : '粗方向'}`,
      scope: 'volume',
      target: { volume_no: volNo, unit_id: null, chapters: null },
      layer: volLayer,
    }
    const children: TreeNode[] = []
    for (const unit of vol.units) {
      const unitLayer = layers.units.find((l) => String(l.id) === unit.id)
      children.push({
        key: `unit:${unit.id}`,
        label: `${unit.name || unit.id}（章 ${unit.chapters[0] ?? '?'}-${unit.chapters[1] ?? '?'}）· ${unit.detail_level === 'expanded' ? '已细化' : '骨架'}`,
        scope: 'unit',
        target: { volume_no: volNo, unit_id: unit.id, chapters: null },
        layer: unitLayer,
      })
      for (const batch of layers.chapters.filter((b) => b.unit_id === unit.id)) {
        children.push({
          key: String(batch.key),
          label: `章纲批次 ${batch.from}-${batch.to}`,
          scope: 'chapters',
          target: {
            volume_no: volNo,
            unit_id: unit.id,
            chapters: [Number(batch.from), Number(batch.to)],
          },
          layer: batch,
          protectedNos: (m.protected_numbers ?? []).filter(
            (no) => Number(batch.from) <= no && no <= Number(batch.to),
          ),
        })
      }
      const next = planningNextBatch(unit.chapters, covered)
      if (unit.detail_level === 'expanded' && next) {
        children.push({
          key: `next:${unit.id}`,
          label: `下一批次 ${next[0]}-${next[1]}（待生成）`,
          scope: 'chapters',
          target: { volume_no: volNo, unit_id: unit.id, chapters: next },
        })
      }
    }
    node.label += ` · ${children.filter((c) => c.scope === 'unit').length} 单元`
    nodes.push({ ...node, label: node.label, children } as TreeNode & { children: TreeNode[] })
  }
  return nodes
})

const selected = computed<TreeNode | null>(() => {
  const key = nodeQuery.value
  if (!key) return treeData.value[0] ?? null
  const walk = (nodes: TreeNode[]): TreeNode | null => {
    for (const n of nodes) {
      if (n.key === key) return n
      const child = walk((n as TreeNode & { children?: TreeNode[] }).children ?? [])
      if (child) return child
    }
    return null
  }
  return walk(treeData.value)
})

/** 当前会话是否与选中节点一致（切书/切节点后旧会话作废） */
const sessionMatches = computed(() => {
  const s = session.value
  const sel = selected.value
  if (!s || !sel || !sel.scope || !sel.target) return false
  if (s.book !== name.value) return false
  return (
    planningNodeKey(s.scope, s.target) === planningNodeKey(sel.scope, sel.target) ||
    (sel.key.startsWith('next:') && s.scope === 'chapters')
  )
})

/** 候选被编辑后旧预览作废（规格08 §3.1：preview_digest 校验的服务端兜底） */
const candidateDirty = computed(() =>
  Boolean(preview.value && session.value && session.value.content !== previewedContent),
)

const adoptDisabled = computed(
  () => !preview.value || candidateDirty.value || adopting.value || !sessionMatches.value,
)

watch(name, () => {
  session.value = null
  preview.value = null
  requirements.value = ''
  inputChanged.value = false
  void load()
})
onMounted(() => {
  void load()
})
watch(selected, (node) => {
  // 切节点即弃当前会话（旧响应不串书、不串层）
  session.value = null
  preview.value = null
  inputChanged.value = false
  void node
})

async function load(): Promise<void> {
  const book = name.value
  const request = ++loadRequest
  loading.value = true
  failed.value = false
  model.value = null
  try {
    const versioned = await getPlanningVersioned(book)
    if (request !== loadRequest || book !== name.value) return
    model.value = versioned.data
    etag.value = versioned.etag ?? ''
  } catch {
    if (request === loadRequest && book === name.value) failed.value = true
  } finally {
    if (request === loadRequest) loading.value = false
  }
}

function selectNode(node: TreeNode): void {
  void router.replace({
    query: { ...route.query, node: node.key === 'global' ? undefined : node.key },
  })
}

async function enable(): Promise<void> {
  const book = name.value
  try {
    await ElMessageBox.confirm(
      '启用内容规划只添加空规划元数据：不改任何旧稿、不改章纲/正文/细纲/伏笔，也不自动生成或采纳。之后全书大纲的唯一编辑入口转到本页（分层人工采纳）。确认启用？',
      '启用内容规划（分层滚动）',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    model.value = await enablePlanning(book, etag.value)
    const versioned = await getPlanningVersioned(book)
    if (book === name.value) {
      model.value = versioned.data
      etag.value = versioned.etag ?? ''
    }
    ElMessage.success('已启用内容规划：旧稿全部保留')
  } catch {
    /* 拦截器已提示 */
  }
}

async function generate(): Promise<void> {
  const sel = selected.value
  const book = name.value
  if (!sel?.scope || !sel.target || generating.value) return
  generating.value = true
  preview.value = null
  inputChanged.value = false
  try {
    const result = await withTask(
      {
        operation: `规划候选生成（${PLANNING_SCOPE_LABELS[sel.scope]}）`,
        book,
        write: false,
        returnPath: `/book/${encodeURIComponent(book)}/planning`,
      },
      (signal) =>
        generatePlanning(
          book,
          sel.scope as PlanningScope,
          sel.target as PlanningTarget,
          requirements.value,
          etag.value,
          signal,
        ),
    )
    if (book !== name.value) return
    session.value = {
      book,
      scope: result.scope,
      target: result.target,
      content: result.content,
      inputVersion: result.input_version,
      promptVersion: result.prompt_version,
    }
    ElMessage.success(`候选已生成（G5 v${result.prompt_version}）；预览影响后人工采纳`)
  } catch {
    /* 拦截器/任务条已提示 */
  } finally {
    if (book === name.value) generating.value = false
  }
}

function errorCode(error: unknown): { status: number; code: string } {
  if (isAxiosError(error)) {
    const detail = (error.response?.data as { detail?: { code?: string } })?.detail
    return {
      status: error.response?.status ?? 0,
      code: typeof detail?.code === 'string' ? detail.code : '',
    }
  }
  return { status: 0, code: '' }
}

async function runPreview(rebase = false): Promise<void> {
  const s = session.value
  const book = name.value
  if (!s || previewing.value) return
  previewing.value = true
  try {
    const result = await previewPlanning(
      book,
      s.scope,
      s.target,
      s.content,
      s.inputVersion,
      etag.value,
      rebase,
    )
    if (book !== name.value) return
    preview.value = result
    previewedContent = s.content
    inputChanged.value = false
    if (result.baseline_changed) {
      ElMessage.warning('基线已变：预览按当前版本重建，原候选 input_version 已保留')
    }
  } catch (error) {
    const { status, code } = errorCode(error)
    if (status === 409 && code === 'PLANNING_INPUT_CHANGED') {
      inputChanged.value = true
      ElMessage.warning('输入版本已变化：候选保留；点「按当前版本重建差异」后才能继续')
    }
  } finally {
    if (book === name.value) previewing.value = false
  }
}

async function adopt(): Promise<void> {
  const s = session.value
  const p = preview.value
  const book = name.value
  if (!s || !p || adopting.value) return
  try {
    await ElMessageBox.confirm(
      `${planningImpactText(p.impact)}\n将按预览版本与 preview_digest 校验；版本或候选变化会拒绝写入。确认人工采纳？`,
      '确认采纳（写入 OUTLINE.md）',
      { type: 'warning' },
    )
  } catch {
    return
  }
  adopting.value = true
  try {
    const result = await withTask(
      {
        operation: `规划人工采纳（${PLANNING_SCOPE_LABELS[s.scope]}）`,
        book,
        write: true,
        returnPath: `/book/${encodeURIComponent(book)}/planning`,
      },
      (signal) =>
        adoptPlanning(
          book,
          s.scope,
          s.target,
          s.content,
          s.inputVersion,
          p.preview_digest,
          etag.value,
          signal,
        ),
    )
    if (book !== name.value) return
    const bits = [
      result.added_numbers.length ? `新增章行 ${result.added_numbers.join('、')}` : '',
      result.changed_numbers.length ? `改动 ${result.changed_numbers.length} 行` : '',
      result.units_added.length ? `新单元 ${result.units_added.length} 个` : '',
    ]
      .filter(Boolean)
      .join('；')
    ElMessage.success(`已采纳${bits ? `：${bits}` : '（内容与凭据无变化，不重复写盘）'}`)
    session.value = null
    preview.value = null
    await load()
  } catch (error) {
    const { status, code } = errorCode(error)
    if (status === 412) ElMessage.warning('档案版本已变化，候选与输入保留；请刷新后重新预览')
    else if (status === 409 && code === 'PLANNING_PREVIEW_CHANGED')
      ElMessage.warning('候选与预览不一致（可能被编辑过）；请重新预览')
  } finally {
    if (book === name.value) adopting.value = false
  }
}

const layerOfSelected = computed(() => selected.value?.layer)
const nodeStatus = computed(() => {
  const layer = layerOfSelected.value
  if (!layer) return null
  return { status: layer.status, label: PLANNING_STATUS_LABEL[layer.status] ?? layer.status }
})

function backToWorkbench(): void {
  const no = returnChapter.value
  if (no) void router.push(`/book/${encodeURIComponent(name.value)}/chapter/${no}`)
  else void router.push(`/book/${encodeURIComponent(name.value)}`)
}
</script>

<template>
  <el-skeleton v-if="loading" :rows="10" animated />
  <el-result
    v-else-if="failed"
    icon="error"
    title="内容规划加载失败"
    sub-title="读取失败期间不显示可写入口；请重试或返回总览。"
  >
    <template #extra
      ><el-button type="primary" @click="load">重试</el-button
      ><el-button @click="backToWorkbench">返回</el-button></template
    >
  </el-result>

  <!-- legacy：只读说明 + 显式启用 -->
  <template v-else-if="model?.mode === 'legacy'">
    <div class="panel">
      <div class="flex flex-wrap items-center gap-2">
        <el-button @click="backToWorkbench">← 返回</el-button>
        <el-button type="primary" @click="router.push(`/book/${encodeURIComponent(name)}/volumes`)"
          >卷工作台（作者三步）</el-button
        >
        <span class="text-[15px] font-bold">内容规划</span>
        <el-tag size="small" type="info">旧模式（未启用）</el-tag>
      </div>
      <el-alert
        type="info"
        :closable="false"
        class="mt-2"
        title="本书仍是旧模式：全书大纲在档案页整表编辑，G5 全量生成/采纳照旧可用。"
      />
      <div class="dim mt-2">
        启用内容规划（分层滚动）= 分层「全书粗纲 → 卷纲 → 剧情单元 → 章纲批次」滚动展开 +
        每层人工采纳。只添加空规划元数据，不改任何旧稿；不启用则一切照旧。
      </div>
      <div class="mt-2">
        <el-button type="primary" @click="enable">启用内容规划（保留全部旧稿）</el-button>
      </div>
    </div>
  </template>

  <!-- invalid：无法判定，只读 -->
  <template v-else-if="model?.mode === 'invalid'">
    <div class="panel">
      <div class="flex flex-wrap items-center gap-2">
        <el-button @click="backToWorkbench">← 返回</el-button>
        <el-button type="primary" @click="router.push(`/book/${encodeURIComponent(name)}/volumes`)"
          >卷工作台</el-button
        >
        <span class="text-[15px] font-bold">内容规划</span>
        <el-tag size="small" type="danger">无法判定</el-tag>
      </div>
      <el-alert
        type="error"
        :closable="false"
        class="mt-2"
        :title="`规划元数据读取失败（${model.readiness.code ?? '未知'}）：${model.readiness.message ?? ''}`"
      />
      <div class="dim mt-2">
        损坏期间本页只读；请修复大纲文件的规划字段后再刷新。不显示"零规划已成功"。
      </div>
    </div>
  </template>

  <template v-else-if="model">
    <div class="panel">
      <div class="flex flex-wrap items-center gap-2">
        <el-button @click="backToWorkbench">← 返回</el-button>
        <el-button type="primary" @click="router.push(`/book/${encodeURIComponent(name)}/volumes`)"
          >卷工作台</el-button
        >
        <span class="text-[15px] font-bold">内容规划 · {{ name }}</span>
        <el-tag size="small" type="success">分层滚动</el-tag>
        <el-tag size="small" :type="statusTag"
          >估算 {{ model.estimates.estimated_chapters ?? '未知' }} 章</el-tag
        >
        <span class="flex-1"></span>
        <el-button @click="load">刷新</el-button>
      </div>
      <div class="dim mt-1">
        目标 {{ ((model.estimates.target_words ?? 0) / 10000).toFixed(1) }} 万字 · 单章
        {{ model.estimates.chapter_word_range?.[0] ?? '?' }}-{{
          model.estimates.chapter_word_range?.[1] ?? '?'
        }}
        字 · {{ model.estimates.message }}（估算仅供参考，不作为进度分母）
      </div>
      <div class="mt-1 font-bold">下一步：{{ planningNextActionText(model) }}</div>
      <el-alert
        v-for="warning in model.warnings"
        :key="warning"
        type="warning"
        :closable="false"
        :title="warning"
        class="mt-1"
      />
      <div v-if="model.unassigned_numbers.length" class="dim mt-1">
        未映射单元的章行：{{
          model.unassigned_numbers.join('、')
        }}（需人工映射或另开新卷，不猜分卷）
      </div>
      <div v-if="model.protected_numbers.length" class="dim mt-1">
        受保护章（已写正文/记忆卡/已确认细纲）：{{ model.protected_numbers.join('、') }} ——
        规划采纳不得改其章行
      </div>
    </div>

    <div class="planning-grid">
      <div class="panel">
        <h3 class="panel-title">规划树（全书 → 卷 → 单元 → 章纲批次）</h3>
        <el-tree
          :data="treeData"
          node-key="key"
          :default-expand-all="true"
          :current-node-key="nodeQuery || 'global'"
          highlight-current
          @node-click="selectNode"
        >
          <template #default="{ data }">
            <span class="flex items-center gap-1">
              <span>{{ data.label }}</span>
              <el-tag v-if="data.layer" :type="statusTagOf(data.layer)" size="small">{{
                statusLabelOf(data.layer)
              }}</el-tag>
            </span>
          </template>
        </el-tree>
      </div>

      <div class="panel">
        <template v-if="selected && selected.scope && selected.target">
          <div class="flex flex-wrap items-center gap-2">
            <span class="text-[15px] font-bold">{{ selected.label }}</span>
            <el-tag v-if="nodeStatus" :type="PLANNING_STATUS_TAG[nodeStatus.status]" size="small">{{
              nodeStatus.label
            }}</el-tag>
          </div>
          <div class="dim mt-1">{{ planningLayerLine(layerOfSelected) }}</div>
          <div v-if="layerOfSelected?.accepted_at" class="dim">
            采纳于 {{ layerOfSelected.accepted_at }}（来源：{{
              layerOfSelected.source === 'manual' ? '人工' : 'AI 候选人工确认'
            }}）
          </div>
          <div v-if="selected.protectedNos?.length" class="mt-1">
            <el-tag
              v-for="no in selected.protectedNos"
              :key="no"
              type="warning"
              size="small"
              class="mr-1"
              >第 {{ no }} 章受保护</el-tag
            >
          </div>

          <el-divider />

          <div class="mb-2">
            <div class="mb-1 font-bold">
              生成候选（G5 按「{{ PLANNING_SCOPE_LABELS[selected.scope] }}」作用域）
            </div>
            <el-input
              v-model="requirements"
              type="textarea"
              :rows="2"
              placeholder="作者补充要求（可空）：本层特别要照顾的点"
            />
          </div>
          <div class="mb-2 flex flex-wrap gap-2">
            <el-button type="primary" :loading="generating" @click="generate">生成候选</el-button>
            <el-button v-if="session && !preview" @click="runPreview(false)">预览影响</el-button>
            <el-button v-if="inputChanged" type="warning" @click="runPreview(true)"
              >按当前版本重建差异</el-button
            >
          </div>

          <template v-if="session">
            <div class="dim mb-1">
              候选（可编辑；G5 v{{ session.promptVersion }} · 绑定 {{ session.book }} 与
              input_version）。 编辑后旧预览作废，须重新预览。
            </div>
            <el-input v-model="session.content" type="textarea" :rows="10" class="mono" />
            <el-button
              class="mt-2"
              :disabled="!candidateDirty && !!preview"
              :loading="previewing"
              @click="runPreview(false)"
            >
              {{ preview ? '重新预览' : '预览影响' }}
            </el-button>

            <template v-if="preview">
              <el-divider />
              <el-alert
                type="warning"
                :closable="false"
                class="mb-2"
                :title="planningImpactText(preview.impact)"
              />
              <el-alert
                v-for="warning in preview.warnings"
                :key="warning"
                type="info"
                :closable="false"
                :title="warning"
                class="mb-2"
              />
              <DiffViewer
                :old-text="planningProjectionText(preview.old_projection)"
                :new-text="planningProjectionText(preview.new_projection)"
                title="当前正式数据 → 采纳后（投影）"
              />
              <div class="mt-2 flex flex-wrap gap-2">
                <el-button
                  type="primary"
                  :disabled="adoptDisabled"
                  :loading="adopting"
                  @click="adopt"
                  >确认影响并采纳</el-button
                >
                <el-button
                  @click="
                    session = null
                    preview = null
                  "
                  >放弃候选</el-button
                >
              </div>
              <div v-if="candidateDirty" class="dim mt-1">
                候选已被编辑：旧预览不可直接采纳（服务端也会拒绝），请重新预览。
              </div>
            </template>
          </template>
        </template>
        <el-empty v-else description="选择左侧树节点开始规划" />
      </div>
    </div>
  </template>
  <el-empty v-else description="尚无规划数据" />
</template>

<style scoped>
.planning-grid {
  display: grid;
  grid-template-columns: minmax(280px, 1fr) minmax(360px, 1.4fr);
  gap: 12px;
  align-items: start;
}
@media (max-width: 900px) {
  .planning-grid {
    grid-template-columns: 1fr;
  }
}
</style>
