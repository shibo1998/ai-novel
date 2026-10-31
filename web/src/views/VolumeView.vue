<script setup lang="ts">
// 规格10 §0/3：作者三步卷工作台。旧档案/分层/单章入口保留为高级路径。
import { computed, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useBookStore } from '@/stores/book'
import { useVolumeProduction } from '@/composables/useVolumeProduction'
import { countValid, jobLabel, rangeText } from '@/utils/production'
import { registerDirtyGuard } from '@/utils/editing'
import { tasks } from '@/utils/tasks'
import { statusLabel } from '@/utils/status'
import ProductionProgress from '@/components/ProductionProgress.vue'
import VolumePlanPreview from '@/components/VolumePlanPreview.vue'
import VolumeReviewPanel from '@/components/VolumeReviewPanel.vue'
import StoryIdeaPicker from '@/components/StoryIdeaPicker.vue'

const route = useRoute(),
  router = useRouter(),
  store = useBookStore()
const book = computed(() => String(route.params.name ?? ''))
const production = useVolumeProduction(book)
const {
  model,
  job,
  review,
  volumeNo,
  selectedVolume,
  loading,
  refreshing,
  busy,
  error,
  notice,
  receipt,
  lastChecked,
  candidateText,
  candidateDirty,
  checkedDigest,
  form,
  writeBlocked,
  confirmReady,
  otherOpenJob,
} = production
const chapterNo = ref<number | null>(null)
const confirming = ref(false)
const formOpen = ref(false)
const ideaBlock = ref({ book: '', blocked: false })
const ideasBlocking = computed(() => {
  const sessionPending = tasks.some(
    (task) =>
      task.book === book.value &&
      task.operation === '同题材故事候选' &&
      ['running', 'unknown'].includes(task.phase),
  )
  return sessionPending || (ideaBlock.value.book === book.value && ideaBlock.value.blocked)
})
function setIdeaBlocking(target: string, blocked: boolean): void {
  if (target === book.value) ideaBlock.value = { book: target, blocked }
}
const submittedForm = ref(JSON.stringify(form))
const inputDirty = computed(() => JSON.stringify(form) !== submittedForm.value)
const checked = computed({
  get: () =>
    Boolean(review.value?.preview_digest && checkedDigest.value === review.value.preview_digest),
  set: (v: boolean) => {
    checkedDigest.value = v ? (review.value?.preview_digest ?? '') : ''
  },
})
const base = computed(() => `/book/${encodeURIComponent(book.value)}`)
const mainTitle = computed(() =>
  job.value?.request.mode === 'new_volume' && !job.value.applied.length
    ? '新卷候选 / 本批出稿'
    : selectedVolume.value
      ? `第 ${selectedVolume.value.no} 卷 · ${selectedVolume.value.name || '未命名'}`
      : '第一卷，从一句话开始',
)
const actionLabel = computed(() =>
  form.mode === 'append'
    ? `追加 ${countValid(form.count) ? form.count : 'N'} 章到本卷`
    : form.mode === 'new_volume'
      ? '准备下一卷（独立新卷）'
      : '准备本卷',
)
const steps = computed(() =>
  job.value?.status === 'awaiting_outline_confirmation'
    ? 2
    : job.value && job.value.status !== 'preparing'
      ? 3
      : 1,
)
const chapterRows = computed(() => {
  const rows = new Map((selectedVolume.value?.chapters ?? []).map((c) => [c.no, c]))
  for (const c of review.value?.chapters ?? [])
    rows.set(c.no, { no: c.no, title: c.title, status: c.status, word_count: null })
  return [...rows.values()].sort((a, b) => a.no - b.no)
})
const reviewDisabled = computed(
  () =>
    writeBlocked.value ||
    !job.value ||
    !['awaiting_final_review', 'partially_applied', 'paused'].includes(job.value.status),
)
watch(
  book,
  (b) => {
    store.setCurrentBook(b)
    chapterNo.value = null
    formOpen.value = false
    submittedForm.value = JSON.stringify(form)
    locatedBook = ''
  },
  { immediate: false },
)
watch(
  () => review.value?.job.id,
  () => {
    chapterNo.value = review.value?.chapters[0]?.no ?? null
  },
)
// 直达参数只从已返回服务端集合定位，不猜章/卷编号。
let locatedBook = ''
store.setCurrentBook(book.value)
watch(model, (m) => {
  if (!m || locatedBook === book.value) return
  locatedBook = book.value
  const requestedJob = String(route.query.job ?? '')
  const requestedVolume = Number(route.query.volume)
  if (requestedJob && m.jobs.some((j) => j.id === requestedJob))
    void production.selectJob(requestedJob)
  else if (m.volumes.some((v) => v.no === requestedVolume))
    void production.selectVolume(requestedVolume)
})
async function allowDiscard(): Promise<boolean> {
  if (!candidateDirty.value && !inputDirty.value) return true
  try {
    await ElMessageBox.confirm(
      '当前有未保存的候选修改或尚未提交的故事要求。离开/切换将丢弃这些本地输入，但不会取消后台任务。请先保存候选或复制输入；仍要放弃？',
      '保留输入',
      {
        type: 'warning',
        confirmButtonText: '放弃本地输入并继续',
        cancelButtonText: '返回保留输入',
        closeOnClickModal: false,
      },
    )
    candidateText.value = production.baseline.value
    submittedForm.value = JSON.stringify(form)
    return true
  } catch {
    return false
  }
}
const unregister = registerDirtyGuard(
  async () => ((await allowDiscard()) ? 'discard' : 'cancel'),
  () => candidateDirty.value || inputDirty.value,
)
onUnmounted(unregister)
async function switchVolume(no: number): Promise<void> {
  if (busy.value || confirming.value || !(await allowDiscard())) return
  await production.selectVolume(no)
}
async function switchJob(id: string): Promise<void> {
  if (busy.value || confirming.value || !(await allowDiscard())) return
  await production.selectJob(id)
}
async function prepare(): Promise<void> {
  if (confirming.value || ideasBlocking.value || writeBlocked.value) return
  if (!model.value?.volumes.length && !form.logline.trim()) {
    ElMessage.warning('新书请先填一句话故事')
    return
  }
  if (await production.prepare()) {
    submittedForm.value = JSON.stringify(form)
    formOpen.value = false
  }
}
async function confirmVolume(): Promise<void> {
  if (!confirmReady.value || confirming.value) return
  const b = book.value,
    id = job.value!.id,
    etag = job.value!.etag,
    digest = review.value!.preview_digest
  confirming.value = true
  try {
    await ElMessageBox.confirm(
      `《${model.value?.title || b}》本批第 ${rangeText(job.value!.numbers)} 章，共 ${job.value!.numbers.length} 章。此操作一次确认本卷全部细纲，发布预览里的 ${review.value!.files.length} 个档案/规划文件，锁定细纲并开始逐章生成、检查及有限修复。已有稿不覆盖；最终仍需你通读并人工定稿。确认接受当前范围与正文目标？`,
      '确认本卷全部细纲并开始生成',
      {
        type: 'warning',
        confirmButtonText: '确认全部细纲并开始生成',
        cancelButtonText: '返回继续核对',
        closeOnClickModal: false,
      },
    )
    if (
      b !== book.value ||
      job.value?.id !== id ||
      job.value.etag !== etag ||
      review.value?.preview_digest !== digest ||
      !confirmReady.value
    ) {
      ElMessage.warning('任务或预览已变化，请重新核对；未发出确认请求')
      return
    }
    await production.confirm()
  } catch {
    /* 取消不调用 confirm，更不自动生成。 */
  } finally {
    confirming.value = false
  }
}
async function control(action: 'pause' | 'resume' | 'cancel' | 'recover'): Promise<void> {
  if (confirming.value || writeBlocked.value) return
  const b = book.value,
    id = job.value?.id
  if (action === 'cancel') {
    confirming.value = true
    try {
      await ElMessageBox.confirm(
        '停止后续生产会保留已生成稿件与检查点，不会删除旧稿。已经发出的模型请求可能继续完成及计费，不承诺即时取消。确认停止？',
        '停止后续生产',
        { type: 'warning', confirmButtonText: '停止后续生产', cancelButtonText: '继续等待' },
      )
    } catch {
      return
    } finally {
      confirming.value = false
    }
  }
  if (b === book.value && id === job.value?.id) await production.control(action)
}
async function removeTask(): Promise<void> {
  const current = job.value
  if (!current || confirming.value) return
  const done = current.completed.length
  try {
    await ElMessageBox.confirm(
      `将删除本任务的记录与检查点（候选、准备缓存）。已生成的 ${done} 章正文与生成日志都会保留，但这个任务删除后无法恢复。`,
      '删除任务',
      {
        type: 'warning',
        confirmButtonText: '删除任务',
        cancelButtonText: '保留',
        closeOnClickModal: false,
      },
    )
  } catch {
    return
  }
  if (job.value?.id !== current.id) return
  await production.removeJob()
  // 删完没有其它任务了，直接把「准备本卷」表单展开，省得再找入口。
  if (!job.value) formOpen.value = true
}

function editChapter(no: number): void {
  void router.push(
    `${base.value}/chapter/${no}?volume=${volumeNo.value ?? ''}&productionJob=${job.value?.id ?? ''}`,
  )
}
function readChapter(no: number): void {
  if (review.value?.chapters.some((c) => c.no === no)) chapterNo.value = no
  else editChapter(no)
}
</script>

<template>
  <div class="volume-workspace">
    <nav class="volume-breadcrumb" aria-label="书内导航">
      <router-link to="/shelf">书架</router-link><span aria-hidden="true">/</span
      ><router-link :to="base">{{ model?.title || book }}</router-link
      ><span aria-hidden="true">/</span><span aria-current="page">卷工作台</span>
    </nav>
    <div class="volume-page-heading">
      <div>
        <span class="eyebrow">卷工作台 · 自动出稿，人工把关</span>
        <h1>{{ model?.title || book }}</h1>
      </div>
      <el-button :loading="refreshing" :disabled="!!busy" @click="production.refresh"
        >刷新 / 找回任务</el-button
      >
    </div>
    <ol class="author-steps" aria-label="作者三步">
      <li :class="{ selected: steps === 1 }"><span>一</span>填故事，准备本卷</li>
      <li :class="{ selected: steps === 2 }"><span>二</span>一次确认整卷细纲</li>
      <li :class="{ selected: steps === 3 }"><span>三</span>通读稿件，人工定稿</li>
    </ol>
    <el-skeleton v-if="loading" :rows="8" animated aria-label="正在读取卷与任务" />
    <p v-if="error" class="volume-alert error" role="alert">
      {{ error }}
      <button
        type="button"
        class="volume-text-button"
        :disabled="refreshing || !!busy"
        @click="production.refresh"
      >
        刷新版本与回执（保留输入）
      </button>
    </p>
    <p v-if="notice" class="volume-alert" role="status">{{ notice }}</p>
    <p v-for="w in model?.warnings ?? []" :key="w" class="volume-alert">{{ w }}</p>
    <p v-for="w in review?.warnings ?? []" :key="w" class="volume-alert">{{ w }}</p>
    <div v-if="model && !loading" class="volume-layout">
      <aside class="volume-sidebar" aria-label="卷与章节目录">
        <div class="sidebar-heading">
          <h2>卷 / 章节</h2>
          <span>{{ model.volumes.length }} 卷</span>
        </div>
        <p v-if="!model.volumes.length" class="volume-hint">
          空书尚无卷。创建书本身不调用模型；在右侧准备第一卷。
        </p>
        <div class="volume-nav-list">
          <button
            v-for="(v, i) in model.volumes"
            :key="v.no"
            type="button"
            class="volume-nav-item"
            :class="{ selected: volumeNo === v.no }"
            :style="{ '--stagger': `${Math.min(i, 5) * 32}ms` }"
            :aria-current="volumeNo === v.no ? 'true' : undefined"
            :disabled="!!busy || confirming"
            @click="switchVolume(v.no)"
          >
            <span class="volume-nav-number">{{ String(v.no).padStart(2, '0') }}</span
            ><span
              ><b>{{ v.name || `第 ${v.no} 卷` }}</b
              ><small
                >{{
                  v.chapter_range.length === 2
                    ? `第 ${v.chapter_range[0]}—${v.chapter_range[1]} 章`
                    : '范围尚未规划'
                }}
                · 定稿 {{ v.finalized_count ?? '未知' }}</small
              ></span
            >
          </button>
        </div>
        <div v-if="chapterRows.length" class="chapter-nav-list">
          <button
            v-for="c in chapterRows"
            :key="c.no"
            type="button"
            class="chapter-nav-item"
            :class="{ selected: chapterNo === c.no }"
            :aria-current="chapterNo === c.no ? 'true' : undefined"
            @click="readChapter(c.no)"
          >
            <span class="mono">{{ String(c.no).padStart(2, '0') }}</span
            ><span
              >{{ c.title || `第 ${c.no} 章`
              }}<small
                >{{ statusLabel(c.status)
                }}{{ c.word_count !== null ? ` · ${c.word_count} 字` : '' }}</small
              ></span
            >
          </button>
        </div>
        <details v-if="model.jobs.length" class="volume-details">
          <summary>找回服务端任务 · {{ model.jobs.length }}</summary>
          <label class="volume-field"
            >任务<select
              :value="job?.id ?? ''"
              :disabled="!!busy || confirming"
              @change="switchJob(($event.target as HTMLSelectElement).value)"
            >
              <option value="" disabled>选择已有任务</option>
              <option v-for="j in model.jobs" :key="j.id" :value="j.id">
                {{ jobLabel(j.status) }} · {{ rangeText(j.numbers) }} 章 · {{ j.created_at }}
              </option>
            </select></label
          >
        </details>
        <details class="volume-details">
          <summary>高级入口</summary>
          <nav class="advanced-links" aria-label="高级书内功能">
            <router-link :to="base">书总览 / 状态摘要</router-link
            ><router-link :to="`${base}/archive`">档案编辑（唯一档案编辑器）</router-link
            ><router-link :to="`${base}/planning`">旧分层规划（高级）</router-link
            ><router-link :to="`${base}/state`">状态卡 / 时间线</router-link>
          </nav>
        </details>
      </aside>
      <div class="volume-main">
        <header class="current-volume-heading">
          <h2>{{ mainTitle }}</h2>
          <p v-if="selectedVolume?.goal">{{ selectedVolume.goal }}</p>
          <div v-if="selectedVolume" class="progress-facts">
            <span
              >已规划 <b>{{ selectedVolume.planned_count ?? '未知' }}</b></span
            ><span
              >已有稿 <b>{{ selectedVolume.draft_count ?? '未知' }}</b></span
            ><span
              >已定稿 <b>{{ selectedVolume.finalized_count ?? '未知' }}</b></span
            >
          </div>
        </header>
        <ProductionProgress
          v-if="job"
          :job="job"
          :disabled="writeBlocked || confirming"
          :busy="busy"
          :last-checked="lastChecked"
          @control="control"
          @refresh="production.refresh"
          @remove="removeTask"
        />
        <VolumePlanPreview
          v-if="review?.candidate && job?.status === 'awaiting_outline_confirmation'"
          v-model="candidateText"
          v-model:checked="checked"
          :review="review"
          :dirty="candidateDirty"
          :disabled="!!busy || confirming || production.invalidData.value"
          :saving="busy === 'candidate'"
          :confirm-ready="confirmReady && !confirming"
          @save="production.saveCandidate"
          @confirm="confirmVolume"
        />
        <VolumeReviewPanel
          v-if="
            review &&
            (review.chapters.length ||
              [
                'generating',
                'awaiting_final_review',
                'paused',
                'applied',
                'partially_applied',
                'cancelled',
                'stale',
              ].includes(job?.status ?? ''))
          "
          v-model:chapter-no="chapterNo"
          :review="review"
          :disabled="reviewDisabled"
          :busy="busy === 'finalize'"
          @edit="editChapter"
          @finalize="production.finalize"
        />
        <div v-if="receipt" class="volume-receipt" role="status">
          <h3>{{ receipt.stopped ? '顺序提交已停止' : '本次人工定稿回执' }}</h3>
          <p v-for="r in receipt.results" :key="r.no">
            第 {{ r.no }} 章：{{
              r.applied ? '已定稿 / 写回回执已收' : `未应用 · ${r.error || '请展开结果核对'}`
            }}
          </p>
          <details class="volume-details">
            <summary>逐项完整回执</summary>
            <pre class="volume-json">{{ JSON.stringify(receipt.results, null, 2) }}</pre>
          </details>
        </div>
        <section class="volume-prepare" :class="{ 'prepare-secondary': !!job }">
          <button
            v-if="job"
            type="button"
            class="volume-text-button"
            :aria-expanded="formOpen"
            @click="formOpen = !formOpen"
          >
            {{ formOpen ? '收起下次生产要求' : '准备 / 追加 / 新增下一卷（独立动作）' }}
          </button>
          <template v-if="!job || formOpen">
            <span class="eyebrow">第一步 · 告诉系统你要写什么</span>
            <h2>{{ selectedVolume ? '本卷要求' : '把第一卷交给一个清楚的起点' }}</h2>
            <StoryIdeaPicker
              v-if="!model.volumes.length && !otherOpenJob"
              :key="book"
              :book="book"
              :form="form"
              :disabled="writeBlocked || confirming || !!otherOpenJob"
              @blocking="setIdeaBlocking"
            />
            <form @submit.prevent="prepare">
              <fieldset class="production-mode" :disabled="writeBlocked || !!otherOpenJob">
                <legend>选择范围（不改已有章号）</legend>
                <label
                  ><input v-model="form.mode" type="radio" value="prepare" />准备本卷未写范围</label
                ><label
                  ><input
                    v-model="form.mode"
                    type="radio"
                    value="append"
                    :disabled="!selectedVolume"
                  />本卷追加 N 章</label
                ><label
                  ><input v-model="form.mode" type="radio" value="new_volume" />新增下一卷</label
                >
              </fieldset>
              <p class="volume-hint">
                {{
                  form.mode === 'append'
                    ? '只追加当前卷未来未写章节，当前卷选择不变。若与后卷范围冲突，服务端拒绝；请另选新增下一卷，不挪已有章。'
                    : form.mode === 'new_volume'
                      ? '独立创建下一卷，章号从服务端全局预留范围后开始，不扩展当前卷或重排旧章。'
                      : '复用已有有效档案，只准备未写范围的整卷候选。准备不会自动采纳、确认细纲或定稿。'
                }}
              </p>
              <label class="volume-field"
                >一句话故事<input
                  v-model="form.logline"
                  type="text"
                  placeholder="例如：失去记忆的守城人，在一场大雪中发现自己曾是叛徒。"
                  :disabled="!!busy"
                  :required="!model.volumes.length"
              /></label>
              <div class="production-input-row">
                <label class="volume-field"
                  >本卷要求<textarea
                    v-model="form.requirements"
                    rows="4"
                    placeholder="本卷要达成什么？冲突、人物变化、节奏与收束点……"
                    :disabled="!!busy"
                  /></label
                ><label class="volume-field count-field"
                  >期望章数<input
                    v-model.number="form.count"
                    type="number"
                    min="1"
                    max="50"
                    step="1"
                    :disabled="!!busy"
                    aria-describedby="count-help"
                  /><span id="count-help" class="volume-hint"
                    >1—50 章<br />默认 10 章<br />逐章串行出稿</span
                  ></label
                >
              </div>
              <p v-if="!countValid(form.count)" class="volume-alert">
                期望章数必须为 1—50 的整数。
              </p>
              <p v-if="otherOpenJob" class="volume-hint">
                本书还有未结束任务，请先核对 / 恢复 / 人工审核，或明确停止后续生产，再准备下一批。
              </p>
              <p v-if="ideasBlocking" class="volume-hint" role="status">
                故事候选正在等待、核对或结果未知。请先处理候选等待 /
                显式确认重试，再手动准备本卷；不会并发调用。
              </p>
              <div class="volume-actions">
                <el-button
                  type="primary"
                  native-type="submit"
                  :loading="busy === 'create'"
                  :disabled="
                    ideasBlocking ||
                    writeBlocked ||
                    !!otherOpenJob ||
                    !countValid(form.count) ||
                    (form.mode === 'append' && !selectedVolume)
                  "
                  >{{ actionLabel }}</el-button
                ><span class="volume-hint"
                  >显式启动会调用模型；章号由服务端确定。不是一次超大正文请求。</span
                >
              </div>
            </form>
          </template>
        </section>
      </div>
    </div>
    <div v-else-if="!loading && !model" class="volume-empty">
      <h2>卷工作台尚未读取成功</h2>
      <p>不是空书或“没有任务”。请检查服务版本后刷新；旧书仍可在书总览与既有工作台查看。</p>
      <router-link :to="base">返回书总览</router-link>
    </div>
  </div>
</template>
