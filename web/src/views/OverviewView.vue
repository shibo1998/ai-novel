<script setup lang="ts">
// 规格07 UI-20/21/25/27：总览只读摘要与唯一导航；手工编辑统一在档案页。
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getBible,
  getOverview,
  getOverdueForeshadows,
  getPlotPoints,
  getState,
  getTimeline,
  listAudits,
  listMemoryPending,
  rebuildMemoryBatch,
  runAudit,
} from '@/api'
import type { AuditReport, MemoryPendingItem, Overview, StateData, TimelineData } from '@/api/types'
import { useBookStore } from '@/stores/book'
import { statusLabel, tagType } from '@/utils/status'
import { withTask } from '@/utils/tasks'
import ArchiveAI from '@/components/ArchiveAI.vue'
import ChapterSelect from '@/components/ChapterSelect.vue'

const route = useRoute()
const router = useRouter()
const store = useBookStore()
const name = computed(() => String(route.params.name ?? ''))
const ov = ref<Overview | null>(null)
const bible = ref<Record<string, string>>({})
const plotPoints = ref<Record<string, string>[]>([])
const loading = ref(false)
const failed = ref(false)
const auxiliaryFailed = ref(false)
let loadRequest = 0
const selectedChapter = ref<number | null>(null)
const overdue = ref({ chapter: 0, grace: 30, overdue: [] as string[] })
const memPending = ref<MemoryPendingItem[]>([])
const timelineData = ref<TimelineData | null>(null)
const stateData = ref<StateData | null>(null)
const memLoading = ref(false)
const memFailed = ref(false)
const memBusy = ref(false)
const audits = ref<AuditReport[]>([])
const auditFailed = ref(false)
const auditNo = ref<number | null>(null)
const auditView = ref<AuditReport>()
const auditDialog = ref(false)

const progressText = computed(() => {
  const o = ov.value
  if (!o) return ''
  const done =
    o.chapters_done === null || o.chapters_done === undefined ? '未知' : `${o.chapters_done} 章`
  const total =
    o.chapters_total === 0
      ? '尚未规划总章数'
      : o.chapters_total === null || o.chapters_total === undefined
        ? '规划章数未知'
        : `已规划 ${o.chapters_total} 章`
  const words =
    o.words_done === null || o.words_done === undefined
      ? '未知'
      : `${(o.words_done / 10000).toFixed(2)} 万字`
  const targetWords = o.meta.target_words ?? 0
  const target = targetWords > 0 ? `${(targetWords / 10000).toFixed(1)} 万字` : '目标字数未知'
  return `已定稿 ${done} / ${total} · 定稿字数 ${words} / 目标 ${target}`
})
const chapterPercent = computed(() => {
  const o = ov.value
  if (!o || !o.chapters_total || o.chapters_done === null || o.chapters_done === undefined)
    return null
  return Math.max(0, Math.min(100, Math.round((o.chapters_done / o.chapters_total) * 100)))
})
const wordPercent = computed(() => {
  const o = ov.value
  const target = o?.meta.target_words ?? 0
  if (!o || !target || o.words_done === null || o.words_done === undefined) return null
  return Math.max(0, Math.min(100, Math.round((o.words_done / target) * 100)))
})
const unfinishedPlanned = computed(() => {
  const o = ov.value
  if (!o) return null
  return o.chapter_numbers.filter((no) => {
    const row = o.chapters.find((r) => r.no === no)
    return row?.status !== 'finalized'
  }).length
})

function archive(node: string): void {
  void router.push(
    `/book/${encodeURIComponent(name.value)}/archive?node=${encodeURIComponent(node)}`,
  )
}
function workbench(no: number): void {
  void router.push(`/book/${encodeURIComponent(name.value)}/chapter/${no}`)
}
async function loadMemory(): Promise<void> {
  const book = name.value
  memLoading.value = true
  memFailed.value = false
  try {
    const [pending, timeline, state] = await Promise.all([
      listMemoryPending(book),
      getTimeline(book),
      getState(book),
    ])
    if (book !== name.value) return
    memPending.value = pending.chapters
    timelineData.value = timeline
    stateData.value = state
  } catch {
    if (book === name.value) memFailed.value = true
  } finally {
    if (book === name.value) memLoading.value = false
  }
}
async function loadAudits(): Promise<void> {
  try {
    audits.value = await listAudits(name.value)
    auditFailed.value = false
  } catch {
    auditFailed.value = true
  }
}
async function load(): Promise<void> {
  const book = name.value
  const request = ++loadRequest
  loading.value = true
  failed.value = false
  auxiliaryFailed.value = false
  ov.value = null
  store.setCurrentBook(book)
  try {
    const overview = await getOverview(book)
    if (request !== loadRequest || book !== name.value) return
    if (!Array.isArray(overview.chapter_numbers))
      throw new Error('服务版本不匹配：总览缺少 chapter_numbers，已禁用章节写入导航')
    ov.value = overview
    selectedChapter.value = overview.chapter_numbers.at(-1) ?? null
    void loadMemory()
    void loadAudits()
    try {
      const [b, pp, od] = await Promise.all([
        getBible(book),
        getPlotPoints(book),
        getOverdueForeshadows(book),
      ])
      if (request !== loadRequest || book !== name.value) return
      bible.value = b
      plotPoints.value = pp
      overdue.value = od
    } catch {
      if (request === loadRequest) auxiliaryFailed.value = true
    }
  } catch {
    if (request === loadRequest && book === name.value) failed.value = true
  } finally {
    if (request === loadRequest) loading.value = false
  }
}
watch(name, () => void load(), { immediate: true })

async function rebuildAllMemory(): Promise<void> {
  if (!memPending.value.length) return
  try {
    await ElMessageBox.confirm(
      `将逐章重跑 V4 补做 ${memPending.value.length} 章记忆卡，每章调用模型。继续？`,
      '批量补做记忆卡',
      { type: 'warning' },
    )
  } catch {
    return
  }
  const book = name.value
  memBusy.value = true
  try {
    const result = await withTask(
      {
        operation: '批量补做记忆卡',
        book,
        write: true,
        returnPath: `/book/${encodeURIComponent(book)}`,
      },
      (signal) => rebuildMemoryBatch(book, undefined, signal),
    )
    ElMessage.success(
      `补做完成：成功 ${result.done.length} 章，明确失败 ${result.failed.length} 章`,
    )
    await load()
  } finally {
    memBusy.value = false
  }
}
async function audit(): Promise<void> {
  const no = auditNo.value
  const book = name.value
  if (!no || !ov.value?.chapter_numbers.includes(no)) {
    ElMessage.warning('审计截止章号必须来自章节集合')
    return
  }
  try {
    const result = await withTask(
      { operation: '卷末审计', book, chapter: no, write: true },
      (signal) => runAudit(book, no, signal),
    )
    ElMessage.success(`审计完成：伏笔回收率 ${String(result.foreshadow_pay_rate)}`)
    await loadAudits()
  } catch {
    /* 统一错误记录 */
  }
}
</script>

<template>
  <el-skeleton v-if="loading" :rows="10" animated />
  <el-result
    v-else-if="failed"
    icon="error"
    title="书总览加载失败"
    sub-title="当前不是空书；请重试或返回书架，失败期间不显示旧书编辑入口。"
  >
    <template #extra
      ><el-button type="primary" @click="load">重试</el-button
      ><el-button @click="router.push('/shelf')">返回书架</el-button></template
    >
  </el-result>
  <template v-else-if="ov">
    <div class="panel">
      <div class="flex flex-wrap items-center gap-2">
        <el-button @click="router.push('/shelf')">← 书架</el-button>
        <span class="text-[15px] font-bold">{{ ov.meta.title || name }}</span>
        <el-tag :type="ov.open_conflicts ? 'danger' : 'success'" size="small">{{
          ov.open_conflicts ? `待裁决 ${ov.open_conflicts} 条` : '裁决队列空'
        }}</el-tag>
        <span class="flex-1"></span
        ><el-button type="primary" @click="router.push(`/book/${encodeURIComponent(name)}/volumes`)"
          >卷工作台 / 自动出稿</el-button
        >
        <el-button @click="archive('meta')">档案编辑</el-button>
        <el-button plain @click="router.push(`/book/${encodeURIComponent(name)}/planning`)"
          >旧分层规划（高级）</el-button
        >
      </div>
      <div class="dim mt-1">
        {{ ov.meta.genre }} · 目标 {{ ((ov.meta.target_words ?? 0) / 10000).toFixed(1) }} 万字 ·
        {{ ov.meta.platform }}
      </div>
      <div class="mt-2 font-bold">{{ progressText }}</div>
      <div class="mt-1 flex flex-wrap items-center gap-3">
        <span v-if="unfinishedPlanned !== null">规划内未定稿 {{ unfinishedPlanned }} 章</span>
        <el-progress
          v-if="chapterPercent !== null"
          :percentage="chapterPercent"
          style="width: 220px"
        />
        <el-progress v-if="wordPercent !== null" :percentage="wordPercent" style="width: 220px" />
      </div>
      <el-alert
        v-for="warning in ov.progress_warnings ?? []"
        :key="warning.code"
        type="warning"
        :closable="false"
        :title="warning.message"
        class="mt-1"
      />
      <div class="dim mt-1">
        全书成本 {{ (ov.cost_total.cost_millicents / 100000).toFixed(2) }} 元 /
        {{ ov.cost_total.calls }} 次调用 · 最后编辑 {{ ov.last_edited_at || '未知' }}
      </div>
    </div>

    <div class="panel">
      <h3 class="panel-title">章节导航</h3>
      <el-alert
        v-if="ov.planning_error"
        type="warning"
        :closable="false"
        class="mb-2"
        :title="`大纲读取失败：${ov.planning_error.message}；列表可能不完整，可查看已有章或去档案修复。`"
      />
      <div v-if="ov.chapter_numbers.length" class="flex flex-wrap items-center gap-2">
        <ChapterSelect
          v-model="selectedChapter"
          :chapters="ov.chapter_numbers"
          :rows="ov.chapters"
          @select="workbench"
        />
        <el-button
          type="primary"
          :disabled="!selectedChapter"
          @click="selectedChapter && workbench(selectedChapter)"
          >进入工作台</el-button
        >
      </div>
      <el-empty v-else description="尚无章节"
        ><el-button type="primary" @click="router.push(`/book/${encodeURIComponent(name)}/volumes`)"
          >去卷工作台准备第一卷</el-button
        ><el-button @click="archive('outline:rows')">高级：手工规划大纲</el-button></el-empty
      >
    </div>

    <div class="grid grid-cols-1 gap-3 xl:grid-cols-2">
      <div>
        <div class="panel">
          <h3 class="panel-title">档案导航 · 共用 AI 候选</h3>
          <ArchiveAI :book="name" @adopted="load" />
        </div>
        <div class="panel">
          <h3 class="panel-title">世界观圣经（只读摘要）</h3>
          <el-alert
            v-if="auxiliaryFailed"
            type="error"
            :closable="false"
            title="档案摘要读取失败；主总览仍可用，请重试。"
          />
          <template v-else
            ><div v-for="(content, title) in bible" :key="title" class="mb-2">
              <el-button link type="primary" @click="archive(`bible:${title}`)">{{
                title
              }}</el-button>
              <div class="dim summary-lines">
                {{ content.split(/\r?\n/).slice(0, 3).join('\n') || '（空节）' }}
              </div>
            </div>
            <div class="dim">
              共 {{ Object.keys(bible).length }} 节；编辑请进入对应档案节点。
            </div></template
          >
        </div>
        <div class="panel">
          <h3 class="panel-title">伏笔台账（只读摘要）</h3>
          <div class="dim">共 {{ plotPoints.length }} 条</div>
          <div
            v-for="row in plotPoints.slice(0, 3)"
            :key="String(row['编号'])"
            class="summary-lines"
          >
            {{ row['编号'] }} · {{ row['内容'] }}
          </div>
          <el-button link type="primary" @click="archive('plot')">进入伏笔台账编辑</el-button>
        </div>
        <div class="panel">
          <h3 class="panel-title">角色档案</h3>
          <el-button
            v-for="character in ov.characters"
            :key="character"
            link
            type="primary"
            @click="archive(`char:${character}`)"
            >{{ character }}</el-button
          ><span v-if="!ov.characters.length" class="dim">（无角色）</span>
        </div>
      </div>
      <div>
        <div class="panel">
          <h3 class="panel-title">章节列表</h3>
          <el-table :data="ov.chapters" size="small" max-height="480">
            <el-table-column prop="no" label="章" width="60" /><el-table-column
              label="状态"
              min-width="130"
              ><template #default="{ row }"
                ><el-tag :type="tagType(row.status)" :title="row.status">{{
                  statusLabel(row.status)
                }}</el-tag></template
              ></el-table-column
            >
            <el-table-column label="版本" width="70"
              ><template #default="{ row }">v{{ row.current_version }}</template></el-table-column
            ><el-table-column label="成本" width="90"
              ><template #default="{ row }"
                >{{ (row.cost_millicents / 100000).toFixed(2) }}元</template
              ></el-table-column
            ><el-table-column width="90"
              ><template #default="{ row }"
                ><el-button @click="workbench(row.no)">工作台</el-button></template
              ></el-table-column
            >
          </el-table>
        </div>
        <div class="panel">
          <h3 class="panel-title">大纲（只读）</h3>
          <el-table :data="ov.outline_rows" size="small" max-height="300"
            ><el-table-column prop="章" label="章" width="60" /><el-table-column
              prop="本章目标"
              label="目标"
              min-width="160"
              show-overflow-tooltip /><el-table-column
              prop="冲突来源"
              label="冲突"
              min-width="130"
              show-overflow-tooltip /></el-table
          ><el-button class="mt-2" @click="archive('outline:rows')"
            >唯一编辑入口：章节总表</el-button
          >
        </div>
      </div>
    </div>

    <div class="panel">
      <h3 class="panel-title">长期记忆健康度</h3>
      <el-skeleton v-if="memLoading" :rows="2" animated /><el-alert
        v-else-if="memFailed"
        type="error"
        :closable="false"
        title="健康度读取失败，状态未知，不视为健康。"
        ><el-button @click="loadMemory">重试</el-button></el-alert
      >
      <div v-else class="flex flex-wrap items-center gap-3">
        <span>待补记忆卡 {{ memPending.length }}</span
        ><span>结构化状态 {{ stateData?.rows ?? 0 }} 条</span
        ><span>状态卡提议 {{ stateData?.proposed_fingerprint ? '待确认' : '无' }}</span
        ><span
          >时间线
          {{
            !timelineData?.exists ? '未建立' : timelineData.issues.length ? '格式有误' : '正常'
          }}</span
        ><el-button :loading="memBusy" :disabled="!memPending.length" @click="rebuildAllMemory"
          >批量补做记忆卡</el-button
        ><el-button @click="router.push(`/book/${encodeURIComponent(name)}/state`)"
          >状态卡 / 时间线</el-button
        >
      </div>
    </div>
    <div class="panel">
      <h3 class="panel-title">卷末审计</h3>
      <ChapterSelect
        v-model="auditNo"
        :chapters="ov.chapter_numbers"
        :rows="ov.chapters"
        placeholder="审计截止章"
      /><el-button class="ml-2" @click="audit">卷末审计（V6）</el-button>
      <el-alert
        v-if="auditFailed"
        type="warning"
        :closable="false"
        title="审计报告列表读取失败；主总览仍可用。"
        ><el-button @click="loadAudits">重试</el-button></el-alert
      >
      <el-button
        v-for="report in audits"
        :key="report.file"
        class="mt-2"
        @click="
          auditView = report
          auditDialog = true
        "
        >{{ report.file }}</el-button
      ><span v-if="!audits.length && !auditFailed" class="dim ml-2">尚无审计报告</span>
    </div>
  </template>
  <el-empty v-else description="尚无总览数据" />
  <el-dialog v-model="auditDialog" title="卷末审计报告（只读）" width="860px">
    <pre class="stream-box">{{ auditView?.content }}</pre>
  </el-dialog>
</template>

<style scoped>
.summary-lines {
  white-space: pre-wrap;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
