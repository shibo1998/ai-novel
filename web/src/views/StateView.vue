<script setup lang="ts">
// 状态卡提议人工确认（规格05 F-05 / C-05）与双时间线维护（规格05 F-06）。
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { approveState, discardState, getState, getTimeline, putTimeline } from '@/api'
import type { MechHit, StateData, TimelineData, TimelineRow } from '@/api/types'
import DiffViewer from '@/components/DiffViewer.vue'
import MdTableEditor from '@/components/MdTableEditor.vue'
import type { TableColumn } from '@/components/MdTableEditor.vue'

const route = useRoute()
const router = useRouter()
const book = computed(() => String(route.params.name ?? ''))
const state = ref<StateData | null>(null)
const stateLoading = ref(false)
const stateFailed = ref(false)
const stateBusy = ref(false)
const timeline = ref<TimelineData | null>(null)
const timelineLoading = ref(false)
const timelineFailed = ref(false)
const timelineSaving = ref(false)
const truthRows = ref<TimelineRow[]>([])
const releasedRows = ref<TimelineRow[]>([])
const timelineIssues = ref<MechHit[]>([])
let stateRequest = 0
let timelineRequest = 0

const TRUTH_COLS: TableColumn[] = [
  { key: '编号', label: '编号', width: 110, placeholder: 'T-001' },
  { key: '章', label: '章', width: 80 },
  { key: '事件', label: '事件', type: 'textarea' },
  { key: '涉及角色', label: '涉及角色' },
  { key: '读者已知', label: '读者已知', width: 110, options: ['是', '否'] },
  { key: '知情角色', label: '知情角色' },
]
const RELEASED_COLS: TableColumn[] = [
  { key: '章', label: '章', width: 80 },
  { key: '本章向读者释放的真相', label: '本章向读者释放的真相', placeholder: 'T-001' },
]

async function loadState(): Promise<void> {
  const b = book.value
  const request = ++stateRequest
  state.value = null
  stateFailed.value = false
  stateLoading.value = true
  try {
    const r = await getState(b)
    if (request === stateRequest && b === book.value) state.value = r
  } catch {
    if (request === stateRequest && b === book.value) stateFailed.value = true
  } finally {
    if (request === stateRequest) stateLoading.value = false
  }
}

async function loadTimeline(): Promise<void> {
  const b = book.value
  const request = ++timelineRequest
  timeline.value = null
  timelineFailed.value = false
  timelineLoading.value = true
  truthRows.value = []
  releasedRows.value = []
  timelineIssues.value = []
  try {
    const r = await getTimeline(b)
    if (request !== timelineRequest || b !== book.value) return
    timeline.value = r
    truthRows.value = r.truth.map((row) => ({ ...row }))
    releasedRows.value = r.released.map((row) => ({ ...row }))
    timelineIssues.value = r.issues
  } catch {
    if (request === timelineRequest && b === book.value) timelineFailed.value = true
  } finally {
    if (request === timelineRequest) timelineLoading.value = false
  }
}

async function load(): Promise<void> {
  if (!book.value) return
  await Promise.all([loadState(), loadTimeline()])
}

async function handleProposal(approve: boolean): Promise<void> {
  const snapshot = state.value
  const b = book.value
  if (!snapshot?.proposed_fingerprint || stateBusy.value || stateLoading.value) return
  if (approve && snapshot.proposed_stale) return
  stateBusy.value = true
  try {
    await ElMessageBox.confirm(
      approve
        ? '确认已核对提议内容与差异？采纳将替换已确认状态卡，供后续章节装配使用。'
        : '确认丢弃这份待确认提议？已确认状态卡不会被修改。',
      approve ? '采纳状态卡提议' : '丢弃状态卡提议',
      { type: 'warning' },
    )
    if (b !== book.value || state.value !== snapshot) return
    if (approve) {
      await approveState(b, snapshot.proposed_fingerprint, snapshot.accepted_fingerprint)
    } else {
      await discardState(b, snapshot.proposed_fingerprint)
    }
    ElMessage.success(approve ? '已采纳状态卡提议' : '已丢弃提议，已确认状态卡未变')
    if (b === book.value) await loadState()
  } catch {
    // 取消不操作；过期冲突由拦截器提示，刷新后重新核对，绝不自动重试采纳。
  } finally {
    stateBusy.value = false
  }
}

async function saveTimeline(): Promise<void> {
  if (!timeline.value || timelineSaving.value || timelineLoading.value) return
  const b = book.value
  timelineSaving.value = true
  try {
    const r = await putTimeline(b, truthRows.value, releasedRows.value, timeline.value.fingerprint)
    if (b !== book.value) return
    timelineIssues.value = r.issues
    if (!r.ok) {
      ElMessage.warning('时间线校验未通过，未覆盖文件；请按问题列表修正后再保存')
      return
    }
    ElMessage.success('双时间线已保存')
    await loadTimeline()
  } catch {
    // 拦截器已提示；保留编辑内容与原指纹，校验失败或并发冲突时不刷新覆盖。
  } finally {
    timelineSaving.value = false
  }
}

watch(book, () => void load(), { immediate: true })
</script>

<template>
  <div class="panel">
    <div class="flex flex-wrap items-center gap-2">
      <el-button @click="router.push(`/book/${encodeURIComponent(book)}`)">← 回总览</el-button>
      <span class="text-[15px] font-bold">长期记忆状态卡</span>
      <el-tag size="small" type="info">V4 提议，人工确认后生效</el-tag>
      <el-tag v-if="state" size="small">结构化状态 {{ state.rows }} 条</el-tag>
      <span class="flex-1"></span>
      <el-button
        :loading="stateLoading || timelineLoading"
        :disabled="stateBusy || timelineSaving"
        @click="load"
      >
        刷新
      </el-button>
    </div>
    <div class="dim mt-1">文件：{{ state?.path || 'STATE.md' }}</div>
  </div>

  <div class="panel">
    <h3 class="panel-title">已确认状态卡</h3>
    <div v-if="stateLoading" class="dim">加载中…</div>
    <el-alert v-else-if="stateFailed" type="error" :closable="false" show-icon>
      状态卡读取失败，请刷新重试，不能据此判断是否有待确认提议。
    </el-alert>
    <template v-else-if="state">
      <pre v-if="state.content" class="whitespace-pre-wrap text-xs leading-6">{{
        state.content
      }}</pre>
      <div v-else class="dim">（尚无已确认状态卡）请先定稿生成提议，核对后采纳。</div>
    </template>
  </div>

  <div v-if="state" class="panel">
    <h3 class="panel-title">待确认提议</h3>
    <template v-if="state.proposed_fingerprint">
      <el-alert v-if="state.proposed_stale" type="warning" :closable="false" show-icon class="mb-2">
        提议已过期，不能采纳；请丢弃并重新生成提议后核对。
      </el-alert>
      <pre class="whitespace-pre-wrap text-xs leading-6">{{ state.proposed_content }}</pre>
      <DiffViewer
        :old-text="state.content"
        :new-text="state.proposed_content"
        title="已确认状态卡 → 待确认提议"
      />
      <div class="mt-2 flex gap-2">
        <el-button
          type="primary"
          :loading="stateBusy"
          :disabled="stateBusy || state.proposed_stale"
          @click="handleProposal(true)"
        >
          核对并采纳
        </el-button>
        <el-button :disabled="stateBusy" @click="handleProposal(false)">丢弃提议</el-button>
      </div>
    </template>
    <div v-else class="dim">（没有待确认提议）</div>
  </div>

  <div class="panel">
    <div class="flex flex-wrap items-center gap-2">
      <h3 class="panel-title">双时间线维护</h3>
      <span class="flex-1"></span>
      <el-button
        type="primary"
        :loading="timelineSaving"
        :disabled="!timeline || timelineLoading || timelineSaving"
        @click="saveTimeline"
      >
        保存双时间线
      </el-button>
    </div>
    <div v-if="timelineLoading" class="dim">加载中…</div>
    <el-alert v-else-if="timelineFailed" type="error" :closable="false" show-icon>
      时间线读取失败，请刷新重试；读取成功前不能保存，避免误覆盖。
    </el-alert>
    <template v-else-if="timeline">
      <div class="dim mb-2">
        文件：{{ timeline.path }}{{ timeline.exists ? '' : '（未建立，保存后创建）' }}。 编号格式
        T-001；角色名须与档案一致，多个角色用逗号分隔。服务端校验失败不覆盖原文件。
      </div>
      <el-form :disabled="timelineSaving">
        <h4 class="mb-1 font-bold">作者真相（故事世界实际发生）</h4>
        <MdTableEditor v-model:rows="truthRows" :columns="TRUTH_COLS" />
        <h4 class="mb-1 mt-3 font-bold">读者已知（按章释放）</h4>
        <MdTableEditor v-model:rows="releasedRows" :columns="RELEASED_COLS" />
      </el-form>
      <div v-if="timelineIssues.length" class="mt-2">
        <div v-for="(issue, i) in timelineIssues" :key="i" class="dim">
          {{ issue.rule }} · {{ issue.level }} · 行{{ issue.line }}：{{ issue.message }}
        </div>
      </div>
    </template>
  </div>
</template>
