<script setup lang="ts">
// 规格07 UI-28/29：结构化裁决、类型保持、可见项内同书顺序批量与逐项结果。
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox, type CheckboxValueType } from 'element-plus'
import { listConflicts, resolveConflictApi } from '@/api'
import type { Conflict } from '@/api/types'
import { useBookStore } from '@/stores/book'
import { conflictKindLabel } from '@/utils/status'
import {
  allowedActions,
  candidateComplete,
  initialManualValue,
  runConflictBatch,
  validateManualValue,
  type BatchItemResult,
  type ConflictAction,
} from '@/utils/conflicts'
import { reportValidation } from '@/utils/errors'

const router = useRouter()
const store = useBookStore()
const items = ref<Conflict[]>([])
const loading = ref(false)
const failed = ref(false)
const bookFilter = ref('')
const kindFilter = ref('')
const expanded = ref<number[]>([])
const selected = ref<number[]>([])
const manual = reactive<Record<number, unknown>>({})
const busy = reactive<Record<number, boolean>>({})
const results = ref<BatchItemResult[]>([])
const batchRunning = ref(false)
const stopRequested = ref(false)
const batchAction = ref<ConflictAction>('adopt_ai')

interface ManualRecord {
  term: string
  description: string
  chapter_no: number
  event: string
  characters: string[]
  reader_known: boolean
  name: string
  role: string
  status: Record<string, unknown>
}

const books = computed(() => [...new Set(items.value.map((i) => i.book))].sort())
const kinds = computed(() => [...new Set(items.value.map((i) => i.kind))].sort())
const visible = computed(() =>
  items.value.filter(
    (i) =>
      (!bookFilter.value || i.book === bookFilter.value) &&
      (!kindFilter.value || i.kind === kindFilter.value),
  ),
)
const groups = computed(() =>
  kinds.value.map((kind) => ({
    kind,
    count: items.value.filter(
      (i) => i.kind === kind && (!bookFilter.value || i.book === bookFilter.value),
    ).length,
  })),
)
const selectedItems = computed(() => visible.value.filter((i) => selected.value.includes(i.id)))
const selectionError = computed(() =>
  new Set(selectedItems.value.map((i) => i.book)).size > 1 ? '只能批量处理同一本书' : '',
)
const resultMap = computed(() => new Map(results.value.map((r) => [r.id, r])))
watch([bookFilter, kindFilter], () => {
  selected.value = []
  results.value = []
})

async function load(): Promise<void> {
  loading.value = true
  failed.value = false
  try {
    items.value = await listConflicts()
    for (const item of items.value)
      if (!(item.id in manual)) manual[item.id] = initialManualValue(item)
    selected.value = []
    await store.refreshConflicts()
  } catch {
    failed.value = true
  } finally {
    loading.value = false
  }
}
onMounted(() => void load())
function can(item: Conflict, action: ConflictAction): boolean {
  return allowedActions(item).includes(action)
}
function effectLabel(effect?: string): string {
  return (
    {
      applied: '已确认写回并记录裁决',
      kept: '已保留档案，不声称写回修复',
      acknowledged: '已人工确认关闭，不声称自动修改',
    }[effect ?? ''] ?? '已收到成功回执'
  )
}
function targetDescription(item: Conflict): string {
  if (item.kind === 'new_setting') return '目标：术语表；人工术语与说明优先，重名会先展示差异。'
  if (item.kind === 'timeline_candidate')
    return '目标：TIMELINE.md；按手改章号/事件写回，保留角色和读者知情类型。'
  if (item.kind === 'missing_char')
    return '目标：characters/*.md；重名不覆盖，建卡后应用已校验状态增量。'
  if (item.kind === 'unknown_field') return '未知字段不可任意写回；只可人工保留档案/驳回。'
  if (item.field)
    return `目标：角色 ${item.char ?? '未知'} 的 ${item.field} 字段；保持布尔/列表/文本原类型。`
  return '仅说明或无明确可执行候选时，请进入对应档案人工处理。'
}
async function resolve(item: Conflict, action: ConflictAction): Promise<boolean> {
  if (busy[item.id] || !can(item, action)) return false
  const value = action === 'manual' ? manual[item.id] : undefined
  if (action === 'manual') {
    const invalid = validateManualValue(item, value)
    if (invalid) {
      reportValidation(invalid, {
        operation: '裁决手改值',
        book: item.book,
        chapter: item.chapter_no,
      })
      return false
    }
  }
  try {
    await ElMessageBox.confirm(
      `${targetDescription(item)}\n动作：${action === 'adopt_ai' ? '采纳 AI 候选' : action === 'manual' ? '采纳人工值' : '保留档案/人工确认'}。写入与队列回执异常时需核对，取消不产生请求。`,
      '确认单条裁决',
      { type: 'warning' },
    )
  } catch {
    return false
  }
  busy[item.id] = true
  try {
    const response = await resolveConflictApi(item.id, action, value)
    upsertResult({
      id: item.id,
      status: 'success',
      message: effectLabel(response.effect),
      effect: response.effect,
    })
    ElMessage.success(effectLabel(response.effect))
    await load()
    return true
  } catch (error) {
    const status = (error as { response?: { status?: number } }).response?.status
    upsertResult({
      id: item.id,
      status: status ? 'failed' : 'unknown',
      message: status ? '明确失败，条目仍待处理' : '结果待确认，不能自动重试',
    })
    return false
  } finally {
    busy[item.id] = false
  }
}
function upsertResult(result: BatchItemResult): void {
  results.value = [result, ...results.value.filter((r) => r.id !== result.id)]
}
function resultLabel(status?: string): string {
  return (
    {
      success: '已成功',
      failed: '明确失败',
      unknown: '结果待确认',
      skipped: '未执行',
      pending: '等待执行',
    }[status ?? ''] ?? ''
  )
}

async function runBatch(action: ConflictAction): Promise<void> {
  const chosen = selectedItems.value
  if (!chosen.length || selectionError.value || batchRunning.value) return
  // 批量保留仅限有完整可采纳候选、能明确拒绝的项；仅说明/未知类型必须单条人工确认。
  const eligible = chosen.filter(
    (item) =>
      can(item, action) &&
      (action === 'adopt_ai' ? candidateComplete(item) : allowedActions(item).includes('adopt_ai')),
  )
  if (eligible.length !== chosen.length) {
    ElMessage.warning('选择中含不可批量写回/仅说明/未知类型，请取消这些条目后单条处理')
    return
  }
  const book = chosen[0]!.book
  try {
    await ElMessageBox.confirm(
      `书：${book}\n动作：${action === 'adopt_ai' ? '批量采纳候选' : '批量保留档案'}\n条数：${chosen.length}\n类型范围：${[...new Set(chosen.map((i) => conflictKindLabel(i.kind)))].join('、')}\n逐项生效，可能部分成功；停止只停止后续项，不回滚已发项。`,
      '二次确认批量裁决',
      { type: 'warning' },
    )
  } catch {
    return
  }
  batchRunning.value = true
  stopRequested.value = false
  results.value = []
  try {
    const executed = await runConflictBatch(
      chosen,
      action,
      async (item, actualAction) => {
        const response = await resolveConflictApi(item.id, actualAction)
        return { effect: response.effect }
      },
      () => stopRequested.value,
    )
    results.value = executed
    await load()
    // 只回选明确失败/未执行项供重试；成功项和结果未知项不自动重跑。
    const retryable = new Set(
      executed.filter((r) => r.status === 'failed' || r.status === 'skipped').map((r) => r.id),
    )
    selected.value = items.value.filter((item) => retryable.has(item.id)).map((item) => item.id)
    ElMessage.info(
      `批量结束：成功 ${executed.filter((r) => r.status === 'success').length}，明确失败 ${executed.filter((r) => r.status === 'failed').length}，待确认 ${executed.filter((r) => r.status === 'unknown').length}，未执行 ${executed.filter((r) => r.status === 'skipped').length}`,
    )
  } finally {
    batchRunning.value = false
    stopRequested.value = false
  }
}
async function retryBatch(): Promise<void> {
  const retryIds = results.value
    .filter((r) => r.status === 'failed' || r.status === 'skipped')
    .map((r) => r.id)
  if (!retryIds.length) {
    ElMessage.warning('仅明确失败/未执行项可重试；结果待确认项须先核对队列和目标文件')
    return
  }
  await load()
  selected.value = visible.value.filter((i) => retryIds.includes(i.id)).map((i) => i.id)
  await runBatch(batchAction.value)
}
function archive(item: Conflict): void {
  const node =
    item.kind === 'timeline_candidate'
      ? 'meta'
      : item.kind === 'new_setting'
        ? 'bible'
        : item.char
          ? `char:${item.char}`
          : 'outline:rows'
  void router.push(
    `/book/${encodeURIComponent(item.book)}/archive?node=${encodeURIComponent(node)}`,
  )
}
function j(value: unknown): string {
  return JSON.stringify(value, null, 2)
}
function asRecord(value: unknown): ManualRecord {
  return value as ManualRecord
}
function setSelected(id: number, value: CheckboxValueType): void {
  selected.value =
    value === true
      ? [...selected.value, id]
      : selected.value.filter((selectedId) => selectedId !== id)
}
function setAllSelected(value: CheckboxValueType): void {
  selected.value = value === true ? visible.value.map((item) => item.id) : []
}
function booleanValue(id: number): boolean {
  return manual[id] === true
}
function setBoolean(id: number, value: string | number | boolean): void {
  manual[id] = value === true
}
function listValue(id: number): string[] {
  const value = manual[id]
  return Array.isArray(value) ? value.map(String) : []
}
function setList(id: number, value: string[]): void {
  manual[id] = value
}
function textValue(id: number): string {
  return typeof manual[id] === 'string' ? manual[id] : ''
}
function setText(id: number, value: string): void {
  manual[id] = value
}
</script>

<template>
  <div class="panel">
    <h3 class="panel-title">待裁决冲突（只查询未解决项；非空时禁止开新章细纲）</h3>
    <div class="flex flex-wrap items-center gap-2">
      <el-select v-model="bookFilter" clearable placeholder="按书名筛选" style="width: 220px"
        ><el-option v-for="book in books" :key="book" :value="book" :label="book" /></el-select
      ><el-select v-model="kindFilter" clearable placeholder="按类型筛选" style="width: 240px"
        ><el-option
          v-for="kind in kinds"
          :key="kind"
          :value="kind"
          :label="conflictKindLabel(kind)" /></el-select
      ><el-button @click="load">刷新待处理项</el-button>
    </div>
    <div class="mt-2 flex flex-wrap gap-2">
      <el-tag v-for="group in groups" :key="group.kind" @click="kindFilter = group.kind"
        >{{ conflictKindLabel(group.kind) }} {{ group.count }}</el-tag
      >
    </div>
  </div>
  <el-skeleton v-if="loading" :rows="8" animated />
  <el-result
    v-else-if="failed"
    icon="error"
    title="裁决队列加载失败"
    sub-title="不是队列已清空；请重试。"
    ><template #extra><el-button @click="load">重试</el-button></template></el-result
  >
  <template v-else>
    <div v-if="visible.length" class="panel">
      <el-alert v-if="selectionError" type="error" :closable="false" :title="selectionError" />
      <div class="flex flex-wrap items-center gap-2">
        <el-checkbox
          :model-value="selected.length === visible.length && visible.length > 0"
          :indeterminate="selected.length > 0 && selected.length < visible.length"
          @change="setAllSelected"
          >选择当前筛选可见项（{{ selected.length }}）</el-checkbox
        ><el-button
          type="primary"
          :loading="batchRunning && batchAction === 'adopt_ai'"
          :disabled="!selected.length || Boolean(selectionError)"
          @click="
            batchAction = 'adopt_ai'
            runBatch('adopt_ai')
          "
          >批量采纳候选</el-button
        ><el-button
          :loading="batchRunning && batchAction === 'keep_archive'"
          :disabled="!selected.length || Boolean(selectionError)"
          @click="
            batchAction = 'keep_archive'
            runBatch('keep_archive')
          "
          >批量保留档案</el-button
        ><el-button v-if="batchRunning" @click="stopRequested = true">停止发送后续项</el-button
        ><el-button :disabled="batchRunning" @click="retryBatch">重试明确失败 / 未执行项</el-button>
      </div>
      <div class="dim mt-1">网络中断、书占用或结果未知会立即停止后续项；已成功项从重试中移除。</div>
    </div>
    <div v-for="item in visible" :key="item.id" class="panel">
      <div class="flex flex-wrap items-center gap-2">
        <el-checkbox
          :model-value="selected.includes(item.id)"
          @change="setSelected(item.id, $event)"
        /><el-tag type="danger">{{ conflictKindLabel(item.kind) }}</el-tag
        ><span>第 {{ item.chapter_no }} 章 · {{ item.book }}</span
        ><span v-if="item.char" class="dim"
          >{{ item.char }}{{ item.field ? '.' + item.field : '' }}</span
        ><el-tag
          v-if="resultMap.get(item.id)"
          :type="
            resultMap.get(item.id)?.status === 'success'
              ? 'success'
              : resultMap.get(item.id)?.status === 'unknown'
                ? 'warning'
                : 'info'
          "
          >{{ resultLabel(resultMap.get(item.id)?.status) }}</el-tag
        ><span class="flex-1"></span
        ><el-button
          @click="
            expanded = expanded.includes(item.id)
              ? expanded.filter((id) => id !== item.id)
              : [...expanded, item.id]
          "
          >{{ expanded.includes(item.id) ? '收起详情' : '展开详情' }}</el-button
        >
      </div>
      <div class="my-2">{{ item.detail }}</div>
      <div v-if="resultMap.get(item.id)" class="dim">{{ resultMap.get(item.id)?.message }}</div>
      <template v-if="expanded.includes(item.id)">
        <el-alert type="info" :closable="false" class="mb-2" :title="targetDescription(item)" />
        <div class="grid grid-cols-1 gap-2 md:grid-cols-2">
          <div>
            <b>档案原值</b>
            <pre class="diff-box">{{ j(item.old) }}</pre>
          </div>
          <div>
            <b>候选值</b>
            <pre class="diff-box">{{ j(item.new) }}</pre>
          </div>
        </div>
        <div v-if="can(item, 'manual')" class="mt-2">
          <template v-if="item.kind === 'new_setting'"
            ><el-form label-width="90px"
              ><el-form-item label="术语"
                ><el-input v-model="asRecord(manual[item.id]).term" /></el-form-item
              ><el-form-item label="说明"
                ><el-input
                  v-model="asRecord(manual[item.id]).description"
                  type="textarea" /></el-form-item></el-form
          ></template>
          <template v-else-if="item.kind === 'timeline_candidate'"
            ><el-form label-width="110px"
              ><el-form-item label="章号"
                ><el-input-number
                  v-model="asRecord(manual[item.id]).chapter_no"
                  :min="1"
                  :precision="0" /></el-form-item
              ><el-form-item label="事件"
                ><el-input
                  v-model="asRecord(manual[item.id]).event"
                  type="textarea" /></el-form-item
              ><el-form-item label="涉及角色"
                ><el-select
                  v-model="asRecord(manual[item.id]).characters"
                  multiple
                  filterable
                  allow-create
                  default-first-option /></el-form-item
              ><el-form-item label="读者已知"
                ><el-switch
                  v-model="asRecord(manual[item.id]).reader_known" /></el-form-item></el-form
          ></template>
          <template v-else-if="item.kind === 'missing_char'"
            ><el-form label-width="100px"
              ><el-form-item label="角色名"
                ><el-input v-model="asRecord(manual[item.id]).name" /></el-form-item
              ><el-form-item label="定位"
                ><el-select v-model="asRecord(manual[item.id]).role"
                  ><el-option
                    v-for="role in ['主角', '女主', '反派', '重要配角', '龙套']"
                    :key="role"
                    :value="role"
                    :label="role" /></el-select></el-form-item
              ><el-form-item label="初始状态"
                ><el-input
                  :model-value="j(asRecord(manual[item.id]).status)"
                  type="textarea"
                  :rows="5"
                  readonly /></el-form-item></el-form
          ></template>
          <template v-else-if="item.field === 'alive'"
            ><span>存亡（布尔）</span
            ><el-switch
              :model-value="booleanValue(item.id)"
              @update:model-value="setBoolean(item.id, $event)"
          /></template>
          <template v-else-if="['injuries', 'inventory', 'known_info'].includes(item.field ?? '')"
            ><span>字符串列表</span
            ><el-select
              :model-value="listValue(item.id)"
              multiple
              filterable
              allow-create
              default-first-option
              @update:model-value="setList(item.id, $event)"
          /></template>
          <template v-else
            ><span>字段文本值</span
            ><el-input
              :model-value="textValue(item.id)"
              type="textarea"
              @update:model-value="setText(item.id, $event)"
          /></template>
        </div>
      </template>
      <div class="mt-2 flex flex-wrap gap-2">
        <el-button
          type="primary"
          :loading="busy[item.id]"
          :disabled="!can(item, 'adopt_ai')"
          @click="resolve(item, 'adopt_ai')"
          >采纳 AI</el-button
        ><el-button
          v-if="can(item, 'manual')"
          :disabled="!expanded.includes(item.id)"
          @click="resolve(item, 'manual')"
          >手改采纳</el-button
        ><el-button @click="resolve(item, 'keep_archive')">{{
          ['realm_name', 'foreshadow_op'].includes(item.kind)
            ? '已人工处理 / 不采用建议'
            : '保留档案'
        }}</el-button
        ><el-button @click="archive(item)">去对应档案</el-button>
      </div>
    </div>
    <el-empty
      v-if="!visible.length"
      :description="items.length ? '当前筛选无待处理项' : '裁决队列已清空'"
    />
  </template>
</template>
