<script setup lang="ts">
// 规格06 §7—11：候选只读，所有异步操作绑定固定书、章、会话，不自动写回。
import { computed, onUnmounted, ref, watch } from 'vue'
import { isAxiosError } from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  approvePolishCandidate,
  discardPolishCandidate,
  getPolishCandidate,
  getPolishBlocks,
  listPolishCandidates,
  runPolish,
} from '@/api'
import type {
  PolishPayload,
  PolishResult,
  PolishStatus,
  PolishSummary,
  ProseBlock,
} from '@/api/types'
import DiffViewer from '@/components/DiffViewer.vue'

const props = defineProps<{
  book: string
  no: number
  revision?: number
  status: string
  disabled?: boolean
}>()
const emit = defineEmits<{ applied: []; busy: [value: boolean] }>()
const mode = ref<'auto' | 'selected'>('auto')
const blocks = ref<ProseBlock[]>([])
const blocksRevision = ref<number>()
const start = ref('')
const end = ref('')
const busy = ref('')
const candidates = ref<PolishSummary[]>([])
const candidate = ref<PolishResult | null>(null)
const readError = ref(false)
const conflict = ref(false)
const request = ref<{ book: string; no: number; payload: PolishPayload } | null>(null)
const lastRequestId = ref('')
let epoch = 0
let alive = true
const statusLabels: Record<PolishStatus, string> = {
  generating: '生成中',
  ready: '待人工采纳',
  rejected: '已拒收',
  no_change: '无需修改',
  error: '失败',
  interrupted: '已中断',
  applying: '正在恢复 / 提交',
  applied: '已采纳（非定稿）',
  discarded: '已放弃，保留原稿',
  stale: '已过期',
  recovery_required: '需人工恢复',
}
const styleLabels = {
  improved: '有所改善',
  unchanged: '无明显变化',
  worse: '变差',
  unknown: '无法确定',
}
const eligible = computed(() => ['pending_finalize', 'human_review'].includes(props.status))
const startIndex = computed(() => blocks.value.findIndex((b) => b.id === start.value))
const endIndex = computed(() => blocks.value.findIndex((b) => b.id === end.value))
const selected = computed(() =>
  startIndex.value >= 0 && endIndex.value >= startIndex.value
    ? blocks.value.slice(startIndex.value, endIndex.value + 1).map((b) => b.id)
    : [],
)
const selectionValid = computed(
  () =>
    mode.value === 'auto' || (selected.value.length > 0 && blocksRevision.value === props.revision),
)
const locallyStale = computed(
  () => !!candidate.value && candidate.value.source_revision !== props.revision,
)
const ready = computed(
  () =>
    !!candidate.value &&
    candidate.value.status === 'ready' &&
    candidate.value.ready === true &&
    !candidate.value.applied &&
    !locallyStale.value &&
    !conflict.value &&
    eligible.value &&
    props.revision !== undefined &&
    !!candidate.value.source_fingerprint &&
    !!candidate.value.candidate_fingerprint,
)
const canDiscard = computed(
  () =>
    !!candidate.value?.candidate_id &&
    !candidate.value.applied &&
    !['applied', 'applying', 'generating', 'discarded', 'recovery_required'].includes(
      candidate.value.status,
    ),
)
const checks = computed(() => Object.entries(candidate.value?.checks ?? {}))
const v3Entries = computed(() => Object.entries(candidate.value?.v3 ?? {}))
function display(value: unknown): string {
  if (value === undefined || value === null) return '未提供 / 未执行'
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}
function target() {
  return { book: props.book, no: props.no, epoch }
}
function current(t: ReturnType<typeof target>): boolean {
  return alive && t.epoch === epoch && t.book === props.book && t.no === props.no
}
function setBusy(value: string): void {
  busy.value = value
  emit('busy', !!value)
}

async function refreshList(): Promise<void> {
  if (busy.value) return
  const t = target()
  setBusy('list')
  readError.value = false
  try {
    const rows = await listPolishCandidates(t.book, t.no)
    if (!current(t)) return
    candidates.value = rows
    if (candidate.value?.candidate_id) {
      const detail = await getPolishCandidate(t.book, t.no, candidate.value.candidate_id)
      if (current(t)) {
        candidate.value = detail
        conflict.value = false
      }
    }
  } catch {
    if (current(t)) readError.value = true
  } finally {
    if (current(t)) setBusy('')
  }
}

async function openCandidate(id: string): Promise<void> {
  if (busy.value) return
  const t = target()
  setBusy('detail')
  candidate.value = null
  conflict.value = false
  try {
    const result = await getPolishCandidate(t.book, t.no, id)
    if (current(t)) candidate.value = result
  } catch {
    if (current(t)) readError.value = true
  } finally {
    if (current(t)) setBusy('')
  }
}

async function loadBlocks(): Promise<void> {
  if (busy.value || props.disabled) return
  const t = target()
  setBusy('blocks')
  blocks.value = []
  blocksRevision.value = undefined
  start.value = ''
  end.value = ''
  try {
    const r = await getPolishBlocks(t.book, t.no)
    if (!current(t)) return
    if (r.source_revision !== props.revision || !Array.isArray(r.blocks)) {
      ElMessage.warning('原稿修订与工作台不一致，或缺少服务端段落；请刷新工作台后重新选择')
      return
    }
    blocks.value = r.blocks
    blocksRevision.value = r.source_revision
    start.value = r.blocks[0]?.id ?? ''
    end.value = start.value
  } catch {
    /* HTTP 拦截器已提示；不自行拆段，不能猜测 block_id。 */
  } finally {
    if (current(t)) setBusy('')
  }
}

async function generateCandidate(retry = false): Promise<void> {
  if (busy.value || props.disabled || !eligible.value || props.revision === undefined) return
  const t = target()
  const revision = props.revision
  if (!retry && !selectionValid.value) {
    ElMessage.warning('请读取当前原稿并选择连续段落')
    return
  }
  const previous = request.value
  if (
    retry &&
    (!previous ||
      previous.book !== t.book ||
      previous.no !== t.no ||
      previous.payload.expected_revision !== revision)
  )
    return
  setBusy('confirm')
  try {
    if (!retry) {
      await ElMessageBox.confirm(
        `${candidate.value || previous ? '这是新请求，会使用新 UUID，可能重复产生费用。' : ''}V2 定点编辑 + V3 对照复核会产生模型费用；仅生成只读候选，不覆盖正式正文。继续？`,
        '生成润色候选',
        { type: 'warning' },
      )
    }
    if (!current(t) || props.revision !== revision || props.disabled || !eligible.value) return
    const fixed = retry
      ? previous!
      : {
          book: t.book,
          no: t.no,
          payload: {
            expected_revision: revision,
            client_request_id: crypto.randomUUID(),
            mode: mode.value,
            selected_blocks: mode.value === 'selected' ? [...selected.value] : [],
          },
        }
    request.value = fixed
    lastRequestId.value = fixed.payload.client_request_id
    setBusy('generate')
    const r = await runPolish(fixed.book, fixed.no, fixed.payload)
    if (!current(t)) return
    candidate.value = r
    conflict.value = false
    if (r.status !== 'generating') request.value = null
    candidates.value = [r, ...candidates.value.filter((c) => c.candidate_id !== r.candidate_id)]
    ElMessage.info(
      r.ready && r.status === 'ready'
        ? '候选已就绪，请对照检查后人工采纳；原稿未改变'
        : `原稿未改变：${r.reason || statusLabels[r.status] || r.status}`,
    )
  } catch (e) {
    if (current(t) && busy.value === 'generate') {
      if (isAxiosError(e) && e.response?.status === 409) conflict.value = true
      // 结果不确定时保留同一请求 ID，可查询而非换 ID 再次计费。
    }
  } finally {
    if (current(t)) setBusy('')
  }
}

async function approve(): Promise<void> {
  if (busy.value || props.disabled || !ready.value || !candidate.value) return
  const t = target()
  const c = candidate.value
  setBusy('confirm')
  try {
    await ElMessageBox.confirm(
      '采纳将更新正式正文并使旧机检、审校及豁免失效，状态回到 mech_check；这不是定稿。确认采纳？',
      '人工采纳候选',
      { type: 'warning' },
    )
    if (!current(t) || candidate.value !== c || !ready.value || props.disabled) return
    setBusy('approve')
    const r = await approvePolishCandidate(t.book, t.no, c.candidate_id, {
      expected_revision: c.source_revision,
      source_fingerprint: c.source_fingerprint,
      candidate_fingerprint: c.candidate_fingerprint,
    })
    if (!current(t)) return
    if (!r.applied) {
      conflict.value = true
      ElMessage.warning('服务端未确认应用，请刷新候选与工作台状态')
      return
    }
    candidate.value = { ...c, status: 'applied', ready: false, applied: true, receipt: r.receipt }
    candidates.value = candidates.value.map((row) =>
      row.candidate_id === c.candidate_id ? candidate.value! : row,
    )
    request.value = null
    ElMessage.success('正文已更新，需重新机检和审校；尚未定稿')
    emit('applied')
  } catch (e) {
    if (current(t) && busy.value === 'approve') {
      conflict.value = true
      if (isAxiosError(e) && e.response?.status === 409)
        c.reason = '并发、过期或恢复中：未获采纳确认，请刷新后核对。'
    }
  } finally {
    if (current(t)) setBusy('')
  }
}

async function discard(): Promise<void> {
  if (busy.value || props.disabled || !canDiscard.value || !candidate.value) return
  const t = target()
  const c = candidate.value
  setBusy('discard')
  try {
    const r = await discardPolishCandidate(t.book, t.no, c.candidate_id)
    if (!current(t)) return
    if (!r.ok) {
      ElMessage.warning('放弃尚未确认，请刷新候选')
      return
    }
    candidate.value = r
    candidates.value = candidates.value.map((row) =>
      row.candidate_id === c.candidate_id ? r : row,
    )
    if (r.applied || r.status === 'applied') {
      ElMessage.info('此候选已在另一页面采纳，正式正文已更新；正在刷新工作台')
      emit('applied')
    } else if (r.status === 'discarded') {
      ElMessage.success('已放弃候选，保留原稿（未回滚或修改正文）')
    } else {
      ElMessage.warning(`候选当前状态为 ${statusLabels[r.status]}，请刷新后核对`)
    }
  } catch {
    /* 拦截器已提示。 */
  } finally {
    if (current(t)) setBusy('')
  }
}

watch(
  () => [props.book, props.no],
  () => {
    epoch++
    candidate.value = null
    candidates.value = []
    blocks.value = []
    blocksRevision.value = undefined
    request.value = null
    lastRequestId.value = ''
    conflict.value = false
    setBusy('')
    void refreshList()
  },
  { immediate: true },
)
onUnmounted(() => {
  alive = false
  epoch++
})
</script>

<template>
  <div class="panel">
    <h3 class="panel-title">安全润色候选（V2 + V3，人工采纳前不覆盖原文）</h3>
    <div class="dim mb-2">
      候选只读。额外改字请走「编辑正文」，再生成新候选，不能沿用旧复核结论。
    </div>
    <el-alert
      v-if="!eligible"
      type="info"
      :closable="false"
      class="mb-2"
      title="仅待定稿 / 人工复核阶段可生成或采纳；已定稿请先解锁返工。历史候选仍可只读回看。"
    />
    <div class="mb-2 flex flex-wrap items-center gap-2">
      <el-radio-group v-model="mode" :disabled="!!busy || disabled"
        ><el-radio-button value="auto">自动找问题</el-radio-button
        ><el-radio-button value="selected">选定连续段落</el-radio-button></el-radio-group
      >
      <el-button v-if="mode === 'selected'" :disabled="!!busy || disabled" @click="loadBlocks"
        >读取原稿段落</el-button
      >
      <el-button
        type="primary"
        :loading="busy === 'generate'"
        :disabled="!!busy || disabled || !eligible || revision === undefined || !selectionValid"
        @click="generateCandidate(false)"
        >生成润色候选</el-button
      >
      <el-button :disabled="!!busy" @click="refreshList">刷新候选列表 / 状态</el-button>
    </div>
    <div v-if="mode === 'selected'" class="mb-2">
      <div class="flex flex-wrap items-center gap-2">
        <span>起段</span
        ><el-select v-model="start" style="width: 180px" :disabled="!!busy || disabled"
          ><el-option
            v-for="(b, i) in blocks"
            :key="b.id"
            :value="b.id"
            :label="`第 ${i + 1} 段 · ${b.id}`"
        /></el-select>
        <span>止段</span
        ><el-select v-model="end" style="width: 180px" :disabled="!!busy || disabled"
          ><el-option
            v-for="(b, i) in blocks"
            :key="b.id"
            :value="b.id"
            :label="`第 ${i + 1} 段 · ${b.id}`"
            :disabled="i < startIndex"
        /></el-select>
        <span class="dim"
          >已选 {{ selected.length }} 个连续原稿块 · 修订 {{ blocksRevision ?? '未读取' }}</span
        >
      </div>
      <div v-if="blocks.length" class="mt-2" style="max-height: 260px; overflow: auto">
        <div
          v-for="(b, i) in blocks"
          :key="b.id"
          class="mb-2 rounded p-2"
          :style="selected.includes(b.id) ? 'background: var(--el-color-primary-light-9)' : ''"
        >
          <span class="dim">第 {{ i + 1 }} 段 · {{ b.id }}</span>
          <pre class="whitespace-pre-wrap">{{ b.text }}</pre>
        </div>
      </div>
      <div v-if="blocksRevision !== undefined && blocksRevision !== revision" class="dim">
        原稿修订已改变，请重新读取并选择，不能沿用旧选区。
      </div>
    </div>
    <el-alert
      v-if="request"
      type="warning"
      :closable="false"
      class="mb-2"
      title="请求结果未确认：不要立即发起新付费请求。可先刷新列表或用原 UUID 查询；中断请求不会自动重新调用模型。"
    >
      <div class="dim">
        请求 {{ request.payload.client_request_id }} · 修订 {{ request.payload.expected_revision }}
      </div>
      <el-button
        size="small"
        :disabled="!!busy || disabled || request.payload.expected_revision !== revision"
        @click="generateCandidate(true)"
        >查询同一请求结果（原 ID）</el-button
      >
    </el-alert>
    <div v-if="lastRequestId" class="dim mb-2">本次请求 ID：{{ lastRequestId }}</div>
    <el-alert
      v-if="readError"
      type="warning"
      :closable="false"
      title="候选读取失败，不能视为没有候选；请刷新"
    />
    <div v-if="!candidates.length && !busy" class="dim mb-2">（尚未读取到候选）</div>
    <div class="mb-2 flex flex-wrap gap-2">
      <el-button
        v-for="c in candidates"
        :key="c.candidate_id"
        size="small"
        :disabled="!!busy"
        @click="openCandidate(c.candidate_id)"
        >{{ statusLabels[c.status] || c.status }} · {{ c.candidate_id }} · 修订
        {{ c.source_revision ?? '?' }}</el-button
      >
    </div>
    <template v-if="candidate">
      <div class="my-2 flex flex-wrap items-center gap-2">
        <el-tag :type="ready ? 'success' : 'warning'">{{
          statusLabels[candidate.status] || candidate.status
        }}</el-tag>
        <span
          >字数：{{ candidate.word_count?.before ?? '?' }} →
          {{ candidate.word_count?.after ?? '?' }}</span
        >
        <span v-if="candidate.cost"
          >费用：{{ candidate.cost.calls }} 次调用 /
          {{ (candidate.cost.cost_millicents / 100000).toFixed(5) }} 元（拒收亦计费）</span
        >
        <span v-else class="dim">费用摘要未提供</span>
      </div>
      <el-alert
        v-if="locallyStale && candidate.status === 'ready'"
        type="warning"
        :closable="false"
        class="mb-2"
        title="stale：当前修订不同于候选基线，禁止采纳；两份文本保留"
      />
      <el-alert
        v-if="conflict"
        type="warning"
        :closable="false"
        class="mb-2"
        title="并发 / 应用结果待核对，禁止采纳；请刷新工作台和候选状态。恢复中不得覆盖正文。"
      />
      <el-alert
        v-if="candidate.reason || candidate.reason_code"
        type="warning"
        :closable="false"
        class="mb-2"
        :title="`${candidate.reason_code || ''} ${candidate.reason || ''}`"
      />
      <el-alert
        v-for="(w, i) in candidate.warnings ?? []"
        :key="i"
        type="warning"
        :closable="false"
        class="mb-2"
        :title="display(w)"
      />
      <div class="mb-2 flex flex-wrap gap-2">
        <el-button
          type="primary"
          :loading="busy === 'approve'"
          :disabled="!!busy || disabled || !ready"
          @click="approve"
          >采纳并重新机检</el-button
        >
        <el-button
          :loading="busy === 'discard'"
          :disabled="!!busy || disabled || !canDiscard"
          @click="discard"
          >保留原稿（放弃候选）</el-button
        >
      </div>
      <div class="dim mb-2">
        只有 ready
        且基线一致才允许采纳；拒收、无变化、过期、中断、恢复中均不可绕过。采纳后须重跑机检和审校，不自动定稿。
      </div>
      <h4 class="font-bold">程序保真 / 完整机检（不是语义证明）</h4>
      <div v-if="!checks.length" class="dim">未提供检查证据，不宣称通过</div>
      <el-descriptions v-else :column="1" border size="small" class="my-2"
        ><el-descriptions-item v-for="[key, value] in checks" :key="key" :label="key">
          <pre class="whitespace-pre-wrap">{{ display(value) }}</pre>
        </el-descriptions-item></el-descriptions
      >
      <h4 class="font-bold">V3 语义保真 / 读感（独立于规则分数）</h4>
      <div class="my-1">
        保真复核：{{
          candidate.v3?.fidelity_verdict ?? candidate.v3?.verdict ?? '未执行 / 未提供'
        }}
        · clean：{{ display(candidate.v3?.clean) }} · 读感：{{
          candidate.v3?.style_verdict ? styleLabels[candidate.v3.style_verdict] : '未提供'
        }}
      </div>
      <el-collapse
        ><el-collapse-item title="V3 各维度结论、引用与问题证据"
          ><el-descriptions :column="1" border size="small"
            ><el-descriptions-item v-for="[key, value] in v3Entries" :key="key" :label="key">
              <pre class="whitespace-pre-wrap">{{ display(value) }}</pre>
            </el-descriptions-item></el-descriptions
          ></el-collapse-item
        ></el-collapse
      >
      <h4 class="mt-2 font-bold">逐组修改对照与理由</h4>
      <div v-if="!candidate.edits?.length" class="dim">没有编辑组；无需为完成任务凑修改。</div>
      <el-collapse>
        <el-collapse-item
          v-for="(e, i) in candidate.edits ?? []"
          :key="i"
          :title="`${i + 1}. ${e.start_block}—${e.end_block} · ${e.category} · ${e.reason}`"
        >
          <div class="mb-2">理由：{{ e.reason }}</div>
          <div class="grid grid-cols-1 gap-2 lg:grid-cols-2">
            <div>
              <b>原文</b>
              <pre class="stream-box">{{ e.original }}</pre>
            </div>
            <div>
              <b>候选</b>
              <pre class="stream-box">{{ e.replacement }}</pre>
            </div>
          </div>
          <DiffViewer :old-text="e.original" :new-text="e.replacement" title="本组逐行差异" />
        </el-collapse-item>
      </el-collapse>
      <el-tabs class="mt-2">
        <el-tab-pane label="原稿全文（生成时快照）">
          <pre class="stream-box">{{ candidate.source ?? '（未提供原稿快照）' }}</pre>
        </el-tab-pane>
        <el-tab-pane label="候选全文（只读）">
          <pre class="stream-box">{{ candidate.candidate ?? '（未生成或未提供候选正文）' }}</pre>
        </el-tab-pane>
      </el-tabs>
      <el-collapse
        ><el-collapse-item title="修订基线、指纹与采纳回执">
          <pre class="stream-box">
修订：{{ candidate.source_revision }}
原稿：{{ candidate.source_fingerprint }}
候选：{{ candidate.candidate_fingerprint }}
回执：{{ display(candidate.receipt) }}</pre>
        </el-collapse-item></el-collapse
      >
    </template>
  </div>
</template>
