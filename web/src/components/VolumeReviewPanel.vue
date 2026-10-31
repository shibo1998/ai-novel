<script setup lang="ts">
// 规格10 §3/5、规格07 UI-14：通读由作者显式标记；正文行号只在显示层映射。
import { computed, nextTick, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { ProductionReview } from '@/api/productionTypes'
import { checkText, continuousSelection, finalizable, rangeText, record } from '@/utils/production'
import { buildProseMapping, paragraphForLine, paragraphForUniqueQuote } from '@/utils/prose'
import { statusLabel } from '@/utils/status'
const props = defineProps<{
  review: ProductionReview
  chapterNo: number | null
  disabled: boolean
  busy: boolean
}>()
const emit = defineEmits<{
  'update:chapterNo': [no: number]
  edit: [no: number]
  finalize: [nos: number[], fingerprints: Record<string, string>]
}>()
const active = computed(
  () => props.review.chapters.find((c) => c.no === props.chapterNo) ?? props.review.chapters[0],
)
const mapping = computed(() => buildProseMapping(active.value?.body ?? ''))
const showLines = ref(false)
const indent = ref(true)
const hitLine = ref<number | null>(null)
const reader = ref<HTMLElement>()
const read = ref<Record<string, string>>({})
const selected = ref<number[]>([])
const confirming = ref(false)
const ready = computed(
  () =>
    !props.disabled &&
    !confirming.value &&
    continuousSelection(props.review.chapters, selected.value, read.value),
)
watch(
  () => props.review.job.id,
  () => {
    read.value = {}
    selected.value = []
  },
  { immediate: true },
)
watch(
  () => props.review.chapters,
  (chapters) => {
    for (const [no, fingerprint] of Object.entries(read.value))
      if (chapters.find((c) => c.no === Number(no))?.fingerprint !== fingerprint)
        delete read.value[no]
    selected.value = selected.value.filter((no) =>
      chapters.some(
        (c) => c.no === no && finalizable(c) && read.value[String(no)] === c.fingerprint,
      ),
    )
  },
  { deep: true },
)
watch(
  () => `${active.value?.no}:${active.value?.fingerprint}`,
  () => {
    hitLine.value = null
    void nextTick(() => reader.value?.scrollTo({ top: 0 }))
  },
)
function markRead(): void {
  const c = active.value
  if (!c?.body || !c.fingerprint) return
  read.value[String(c.no)] = c.fingerprint
}
function choose(no: number, checked: boolean): void {
  if (checked && !selected.value.includes(no)) selected.value.push(no)
  if (!checked) selected.value = selected.value.filter((n) => n !== no)
}
const issues = computed(() => {
  const values = [active.value?.check, active.value?.review].flatMap((result) =>
    Array.isArray(result?.issues) ? result.issues : Array.isArray(result?.hits) ? result.hits : [],
  )
  return values.filter(record)
})
async function jump(issue: Record<string, unknown>): Promise<void> {
  const rawLine = issue.line
  const paragraph =
    typeof rawLine === 'number'
      ? paragraphForLine(mapping.value, rawLine)
      : typeof issue.quote === 'string'
        ? paragraphForUniqueQuote(mapping.value, active.value?.body ?? '', issue.quote)
        : null
  if (!paragraph) {
    ElMessage.info('结果未提供有效行号或唯一引用，未猜测正文位置；请展开原始结果核对。')
    return
  }
  hitLine.value = typeof rawLine === 'number' ? rawLine : paragraph.startLine
  await nextTick()
  reader.value
    ?.querySelector(`[data-start-line="${paragraph.startLine}"]`)
    ?.scrollIntoView({ block: 'center', behavior: 'auto' })
}
function issueText(issue: Record<string, unknown>): string {
  return (
    ['rule', 'category', 'message', 'description', 'quote', 'suggestion']
      .map((key) => (typeof issue[key] === 'string' ? issue[key] : ''))
      .filter(Boolean)
      .join(' · ') || '未知问题结构，请展开原始结果'
  )
}
async function requestFinalize(): Promise<void> {
  if (!ready.value) return
  const id = props.review.job.id,
    etag = props.review.job.etag
  const nos = [...selected.value].sort((a, b) => a - b)
  const fingerprints = Object.fromEntries(nos.map((n) => [String(n), read.value[String(n)]!]))
  confirming.value = true
  try {
    await ElMessageBox.confirm(
      `你将人工定稿第 ${rangeText(nos)} 章（共 ${nos.length} 章）。请确认已经通读当前版本并核对检查与审校。服务端将按本次正文指纹逐章重查、顺序写回正式记忆；发生冲突或写回错误立即停止，未选章节不会定稿。`,
      '确认已通读并定稿所选连续章段',
      {
        type: 'warning',
        confirmButtonText: '确认定稿所选章段',
        cancelButtonText: '返回继续阅读',
        closeOnClickModal: false,
      },
    )
    if (
      props.review.job.id !== id ||
      props.review.job.etag !== etag ||
      !continuousSelection(props.review.chapters, nos, fingerprints) ||
      props.disabled
    ) {
      ElMessage.warning('稿件或任务版本已变化，请重新核对后再定稿')
      return
    }
    emit('finalize', nos, fingerprints)
  } catch {
    /* 取消不发出定稿请求。 */
  } finally {
    confirming.value = false
  }
}
</script>

<template>
  <section class="volume-review" aria-label="待审稿阅读与人工定稿">
    <div class="volume-section-heading">
      <div>
        <span class="eyebrow">第三步 · 通读与定稿</span>
        <h2>读稿是主角，定稿由你决定</h2>
      </div>
      <span class="volume-state warning">本批 {{ review.chapters.length }} 份稿件</span>
    </div>
    <template v-if="active">
      <div class="volume-actions reading-controls">
        <label class="volume-field"
          >当前阅读<select
            :value="active.no"
            @change="emit('update:chapterNo', Number(($event.target as HTMLSelectElement).value))"
          >
            <option v-for="c in review.chapters" :key="c.no" :value="c.no">
              第 {{ c.no }} 章 · {{ c.title || '未命名' }} · {{ statusLabel(c.status) }}
            </option>
          </select></label
        ><el-button @click="emit('edit', active.no)">去既有工作台编辑 / 核对</el-button
        ><label class="volume-checkbox"><input v-model="showLines" type="checkbox" />原始行号</label
        ><label class="volume-checkbox"><input v-model="indent" type="checkbox" />首行缩进</label>
      </div>
      <article
        ref="reader"
        class="volume-manuscript prose-body"
        tabindex="0"
        :aria-label="`第 ${active.no} 章正文阅读`"
      >
        <header>
          <span>待审稿 · {{ statusLabel(active.status) }}</span>
          <h3>第 {{ active.no }} 章　{{ active.title }}</h3>
        </header>
        <div
          v-for="p in mapping.paragraphs"
          :key="p.id"
          class="prose-paragraph"
          :data-start-line="p.startLine"
          :class="{
            indent,
            'hit-active': hitLine !== null && hitLine >= p.startLine && hitLine <= p.endLine,
          }"
        >
          <span
            v-if="hitLine !== null && hitLine >= p.startLine && hitLine <= p.endLine"
            class="hit-line"
            >原始第 {{ hitLine }} 行</span
          ><template v-if="showLines"
            ><span v-for="(line, i) in p.text.split('\n')" :key="i" class="all-lines"
              ><span class="original-line-no">{{ p.startLine + i }}</span
              >{{ line }}</span
            ></template
          ><template v-else>{{ p.text }}</template>
        </div>
        <p v-if="!mapping.paragraphs.length">尚无正文，不可标记通读或定稿。</p>
      </article>
      <p class="volume-hint">
        阅读排版不改变原文；行号含空行，从 1
        起算。本批临时上下文不等于正式记忆；生成完成不等于人工定稿。
      </p>
      <div class="review-checks">
        <div><b>机检：</b>{{ checkText(active.check) }}</div>
        <div><b>审校：</b>{{ checkText(active.review) }}</div>
        <button
          v-for="(issue, i) in issues"
          :key="i"
          type="button"
          class="review-issue"
          @click="jump(issue)"
        >
          {{ issueText(issue) }}
        </button>
        <details class="volume-details">
          <summary>原始检查、审校与修复记录（未知字段不推断通过）</summary>
          <pre class="volume-json">{{
            JSON.stringify({ check: active.check, review: active.review }, null, 2)
          }}</pre>
        </details>
      </div>
      <div class="volume-actions">
        <el-button :disabled="!active.body || !active.fingerprint || disabled" @click="markRead">{{
          read[String(active.no)] === active.fingerprint
            ? '本版本已标记通读'
            : '我已通读本章当前版本'
        }}</el-button
        ><span class="volume-hint">改稿后通读标记失效，需要重新核对。</span>
      </div>
      <details class="volume-details review-selection" open>
        <summary>选择已通读的连续章段 · 已选 {{ selected.length }} 章</summary>
        <div class="review-selection-list">
          <label v-for="c in review.chapters" :key="c.no" class="volume-checkbox"
            ><input
              type="checkbox"
              :checked="selected.includes(c.no)"
              :disabled="disabled || !finalizable(c) || read[String(c.no)] !== c.fingerprint"
              @change="choose(c.no, ($event.target as HTMLInputElement).checked)"
            /><span
              >第 {{ c.no }} 章 · {{ c.title || '未命名' }}
              <small>{{
                c.status === 'finalized'
                  ? '已定稿'
                  : read[String(c.no)] !== c.fingerprint
                    ? '尚未通读当前版本'
                    : finalizable(c)
                      ? '可选择'
                      : '状态或检查尚不可定稿'
              }}</small></span
            ></label
          >
        </div>
      </details>
      <p
        v-if="selected.length && !continuousSelection(review.chapters, selected, read)"
        class="volume-alert"
      >
        所选章号不是连续且已通读的合格章段，请调整选择；不会自动补选未读章节。
      </p>
      <el-button type="primary" :loading="busy" :disabled="!ready" @click="requestFinalize"
        >人工定稿所选 {{ selected.length || '' }} 章（二次确认）</el-button
      >
    </template>
    <p v-else class="volume-hint">
      尚无待审正文。系统将自动出稿、检查与有限修复；若出现暂停原因，请先核对。不会自动标记通读或定稿。
    </p>
  </section>
</template>
