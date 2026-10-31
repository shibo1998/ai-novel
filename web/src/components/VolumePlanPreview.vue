<script setup lang="ts">
// 规格10 §2.3/3：整卷一个候选包；编辑仅存候选，重新摘要后才允许人工确认。
import { computed, ref, watch } from 'vue'
import type { ProductionReview } from '@/api/productionTypes'
import { rangeText } from '@/utils/production'
import DiffViewer from '@/components/DiffViewer.vue'
const props = defineProps<{
  review: ProductionReview
  modelValue: string
  dirty: boolean
  checked: boolean
  disabled: boolean
  saving: boolean
  confirmReady: boolean
}>()
const emit = defineEmits<{
  'update:modelValue': [value: string]
  'update:checked': [value: boolean]
  save: []
  confirm: []
}>()
const outlineNo = ref<number | null>(null)
const outline = computed(
  () =>
    props.review.candidate?.outlines.find((o) => o.no === outlineNo.value) ??
    props.review.candidate?.outlines[0],
)
watch(
  () => props.review.job.id,
  () => {
    outlineNo.value = props.review.candidate?.outlines[0]?.no ?? null
  },
  { immediate: true },
)
const chapterTitle = (fm: Record<string, unknown>): string =>
  typeof fm.title === 'string'
    ? fm.title
    : typeof fm.core_event === 'string'
      ? fm.core_event
      : '章细纲'
</script>

<template>
  <section class="volume-candidate" aria-labelledby="candidate-title">
    <div class="volume-section-heading">
      <div>
        <span class="eyebrow">第二步 · 核对整卷</span>
        <h2 id="candidate-title">本卷全部细纲，等你一次确认</h2>
      </div>
      <span class="volume-state warning">尚未采纳</span>
    </div>
    <p class="volume-lead">
      本批第 {{ rangeText(review.job.numbers) }} 章 · 共
      {{ review.candidate?.outlines.length ?? 0 }}
      份细纲。确认将发布本包规划与必要档案，并逐章锁定细纲、启动正文生成。不会自动定稿。
    </p>
    <p v-for="note in review.candidate?.notes ?? []" :key="note" class="volume-hint">{{ note }}</p>
    <div v-if="outline" class="candidate-reading">
      <label class="volume-field"
        >逐章核对细纲<select v-model.number="outlineNo">
          <option v-for="o in review.candidate?.outlines" :key="o.no" :value="o.no">
            第 {{ o.no }} 章 · {{ chapterTitle(o.fm) }}
          </option>
        </select></label
      >
      <h3>第 {{ outline.no }} 章 · {{ chapterTitle(outline.fm) }}</h3>
      <pre class="candidate-outline prose-body">{{
        outline.body || '（细纲正文为空，请核对）'
      }}</pre>
      <details class="volume-details">
        <summary>本章合同字段（保留原始结构）</summary>
        <pre class="volume-json">{{ JSON.stringify(outline.fm, null, 2) }}</pre>
      </details>
    </div>
    <details class="volume-details">
      <summary>编辑本卷候选 JSON（不写入正式档案）{{ dirty ? ' · 有未保存修改' : '' }}</summary>
      <p class="volume-hint">
        保留结构版本号、章号、文件相对路径与未知字段；不要自行设置“已确认”标记。修改后须保存，服务端会重验范围、更新预览摘要；旧确认失效。
      </p>
      <label class="volume-field"
        >完整候选包<textarea
          class="volume-json-editor"
          :value="modelValue"
          rows="16"
          spellcheck="false"
          :disabled="disabled || saving"
          @input="emit('update:modelValue', ($event.target as HTMLTextAreaElement).value)"
        />
      </label>
    </details>
    <p v-if="dirty" class="volume-alert" role="status">
      候选已修改：当前细纲与文件差异仍是上次服务端预览。请先保存获取新摘要，再重新核对；不能用旧预览开始生成。
    </p>
    <div v-if="dirty" class="volume-actions">
      <el-button type="primary" :loading="saving" :disabled="disabled" @click="emit('save')"
        >保存候选并重新校验</el-button
      >
    </div>
    <details class="volume-details">
      <summary>将写入的文件影响 · {{ review.files.length }} 个文件（展开必要差异）</summary>
      <p v-if="!review.files.length" class="volume-hint">
        未提供文件差异，请核对候选范围；不推断为“无改动”。
      </p>
      <details v-for="file in review.files" :key="file.path" class="volume-details">
        <summary>{{ file.path }}</summary>
        <DiffViewer :old-text="file.before" :new-text="file.after" :title="file.path" />
      </details>
    </details>
    <div class="candidate-confirm">
      <label class="volume-checkbox"
        ><input
          type="checkbox"
          :checked="checked"
          :disabled="disabled || dirty || !review.preview_digest"
          @change="emit('update:checked', ($event.target as HTMLInputElement).checked)"
        /><span>我已核对本卷全部细纲与文件影响，接受本次预览范围</span></label
      >
      <el-button type="primary" :disabled="!confirmReady" @click="emit('confirm')"
        >确认本卷全部细纲并开始生成</el-button
      >
      <p class="volume-hint">
        下一步还有明确二次确认。只在你确认后开始模型调用；等待审核期间不会自动越过人工关。
      </p>
    </div>
  </section>
</template>
