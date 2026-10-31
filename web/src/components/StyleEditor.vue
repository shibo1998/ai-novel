<script setup lang="ts">
// 规格06 §11.2：总览和档案共用同一文风资料编辑器。
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { StyleScene } from '@/api/types'
import { useStyleEditor } from '@/composables/useStyleEditor'

const props = defineProps<{ book: string }>()
const {
  state: s,
  corpusDirty,
  cardDirty,
  refresh,
  reset,
  analyze,
  saveCorpus,
  saveCard,
  toggle,
  saveExamples,
  reselectExamples,
} = useStyleEditor(computed(() => props.book))
const start = ref<number>(1)
const end = ref<number>(1)
const scenes = ref<StyleScene[]>(['general'])
watch(
  () => props.book,
  () => {
    start.value = 1
    end.value = 1
    scenes.value = ['general']
  },
)
const sceneLabels: Record<StyleScene, string> = {
  opening: '开篇',
  dialogue: '对白',
  action: '动作',
  reasoning: '判断 / 检修',
  transition: '过渡',
  breakthrough: '突破',
  climax: '高潮',
  general: '通用',
}
const stale = computed(
  () => !!s.value.data && s.value.examplesFingerprint !== s.value.data.corpus_fingerprint,
)
const previewParagraphs = computed(() =>
  (s.value.data?.paragraphs ?? []).filter((p) => p.no >= start.value && p.no <= end.value),
)
const draftStale = computed(
  () => !!s.value.draft && s.value.draft.corpus_fingerprint !== s.value.data?.corpus_fingerprint,
)
function display(value: unknown): string {
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}
function addRange(): void {
  if (!previewParagraphs.value.length || end.value < start.value || !scenes.value.length) {
    ElMessage.warning('请选择存在的连续段落范围和至少一个标签')
    return
  }
  s.value.examples.push({
    start_paragraph: start.value,
    end_paragraph: end.value,
    scene_types: [...scenes.value],
  })
  s.value.examplesDirty = true
}
function discardDraft(): void {
  if (s.value.busy) return
  s.value.draft = null
  s.value.draftText = ''
}
function removeExample(index: number): void {
  if (s.value.busy) return
  s.value.examples.splice(index, 1)
  s.value.examplesDirty = true
}
async function reloadDiscarding(): Promise<void> {
  const b = props.book
  try {
    await ElMessageBox.confirm(
      '将放弃此书本地未保存的语料、卡片、草案和范例修改，重新读取服务器版本。继续？',
      '重新载入',
      { type: 'warning' },
    )
  } catch {
    return
  }
  if (b === props.book) await reset()
}
</script>

<template>
  <div>
    <div class="dim mb-2">
      保存语料 → F1 生成未保存草案 → 人工编辑保存 → 可选确认范例 →
      独立启用。草案不会替换已有人工卡片。
    </div>
    <div class="mb-2 flex flex-wrap items-center gap-2">
      <el-button :disabled="!!s.busy" @click="refresh">刷新资料（保留未保存编辑）</el-button>
      <el-button :disabled="!!s.busy" @click="reloadDiscarding">放弃编辑并重新载入</el-button>
      <span v-if="s.busy" class="dim">{{
        s.busy === 'analyze' ? '正在分析，可能发生模型费用…' : '处理中…'
      }}</span>
    </div>
    <el-alert
      v-if="s.error"
      type="error"
      :closable="false"
      class="mb-2"
      title="文风资料读取失败，请刷新；未保存文本已保留"
    />
    <el-alert
      v-if="s.conflict"
      type="warning"
      :closable="false"
      class="mb-2"
      title="文风资料存在并发冲突，未覆盖服务端；请先复制保留本地编辑，再重新载入"
    />
    <template v-if="s.data">
      <el-alert
        v-for="(w, i) in s.data.warnings ?? []"
        :key="i"
        type="warning"
        :closable="false"
        class="mb-2"
        :title="display(w)"
      />
      <div class="dim mb-1">
        语料（建议使用完整目标文风段落；段落号由服务端解析）{{ corpusDirty ? ' · 未保存' : '' }}
      </div>
      <el-input v-model="s.corpus" type="textarea" :rows="6" :disabled="!!s.busy" />
      <div class="mt-2 flex flex-wrap items-center gap-2">
        <el-button :disabled="!!s.busy || s.conflict || !corpusDirty" @click="saveCorpus"
          >保存语料</el-button
        >
        <el-button
          type="primary"
          :loading="s.busy === 'analyze'"
          :disabled="!!s.busy || corpusDirty || !s.corpus.trim() || !!s.draft || s.error"
          @click="analyze"
          >分析生成草案（F1）</el-button
        >
        <el-switch
          :model-value="s.data.enabled"
          :disabled="!!s.busy"
          @change="(v) => toggle(Boolean(v))"
        />
        <span class="dim">启用已保存文风（独立于保存；关闭后整条链不注入书级文风）</span>
      </div>
      <div class="dim mt-2 mb-1">正式特征卡（人工可改）{{ cardDirty ? ' · 未保存' : '' }}</div>
      <el-input v-model="s.card" type="textarea" :rows="8" :disabled="!!s.busy" />
      <el-button
        class="mt-2"
        :disabled="!!s.busy || s.conflict || !cardDirty"
        @click="saveCard(false)"
        >保存特征卡（不切换启用）</el-button
      >
      <el-collapse class="mt-2">
        <el-collapse-item title="当前已保存的人工特征卡（分析不会覆盖）">
          <pre class="stream-box">{{ s.data.card || '（尚未保存特征卡）' }}</pre>
        </el-collapse-item>
      </el-collapse>
      <div v-if="s.draft" class="mt-2">
        <el-alert
          type="info"
          :closable="false"
          title="F1 未保存草案：只有人工保存后才成为正式卡；启用状态不受分析影响"
        />
        <div class="dim my-1">
          模板 digest：{{ s.draft.digest }} · 来源语料：{{ s.draft.corpus_fingerprint }}
        </div>
        <el-alert
          v-if="draftStale"
          type="warning"
          :closable="false"
          title="草案语料已过期，请重新分析；当前草案仍保留可复制"
        />
        <el-input v-model="s.draftText" type="textarea" :rows="8" :disabled="!!s.busy" />
        <div class="mt-2 flex gap-2">
          <el-button
            type="primary"
            :disabled="!!s.busy || s.conflict || draftStale || cardDirty"
            @click="saveCard(true)"
            >保存草案为正式卡（不自动启用）</el-button
          >
          <el-button :disabled="!!s.busy" @click="discardDraft">放弃草案</el-button>
        </div>
        <div v-if="cardDirty" class="dim">
          正式卡有未保存人工修改，请先保存或复制处理，避免草案覆盖本地修改。
        </div>
      </div>
      <h4 class="mt-3 mb-1 font-bold">人工范例范围与场景标签</h4>
      <el-alert
        v-if="s.data.examples_stale || stale"
        type="warning"
        :closable="false"
        class="mb-2"
        title="旧范例索引已过期，不会套用到新语料位置。请清空待选列表后按当前段落重新选择并保存。"
      />
      <div v-if="!s.data.examples" class="dim mb-2">
        尚无人工范例索引。兼容选例不等于人工确认，具体 legacy / 缺失原因以装配预览为准。
      </div>
      <div
        v-for="(e, i) in s.examples"
        :key="e.id ?? `new-${i}`"
        class="mb-2 flex flex-wrap items-center gap-2"
      >
        <span
          >{{ e.id || '未保存范例' }} · 第 {{ e.start_paragraph }}—{{ e.end_paragraph }} 段</span
        >
        <el-tag v-for="t in e.scene_types" :key="t" size="small"
          >{{ sceneLabels[t] }} / {{ t }}</el-tag
        >
        <el-tag v-if="stale" type="warning" size="small">已过期</el-tag>
        <el-button size="small" :disabled="!!s.busy" @click="removeExample(i)">移除</el-button>
      </div>
      <el-button :disabled="!!s.busy || corpusDirty" @click="reselectExamples"
        >清空待选列表，按当前语料重新选择（保存后生效）</el-button
      >
      <div class="mt-2 flex flex-wrap items-center gap-2">
        <span>起段</span>
        <el-select
          v-model="start"
          style="width: 140px"
          :disabled="!!s.busy || corpusDirty || stale"
        >
          <el-option
            v-for="p in s.data.paragraphs"
            :key="p.id"
            :value="p.no"
            :label="`第 ${p.no} 段`"
          />
        </el-select>
        <span>止段</span>
        <el-select v-model="end" style="width: 140px" :disabled="!!s.busy || corpusDirty || stale">
          <el-option
            v-for="p in s.data.paragraphs"
            :key="p.id"
            :value="p.no"
            :label="`第 ${p.no} 段`"
            :disabled="p.no < start"
          />
        </el-select>
        <el-select v-model="scenes" multiple style="min-width: 240px" :disabled="!!s.busy">
          <el-option
            v-for="(label, key) in sceneLabels"
            :key="key"
            :value="key"
            :label="`${label} / ${key}`"
          />
        </el-select>
        <el-button
          :disabled="!!s.busy || corpusDirty || stale || !previewParagraphs.length || end < start"
          @click="addRange"
          >加入范围</el-button
        >
      </div>
      <div v-if="!stale && !corpusDirty" class="mt-2" style="max-height: 220px; overflow: auto">
        <div v-for="p in previewParagraphs" :key="p.id" class="mb-2">
          <span class="dim">第 {{ p.no }} 段 · {{ p.id }}</span>
          <pre class="whitespace-pre-wrap">{{ p.text }}</pre>
        </div>
      </div>
      <el-button
        class="mt-2"
        type="primary"
        :disabled="!!s.busy || s.conflict || corpusDirty || stale || !s.examplesDirty"
        @click="saveExamples"
        >保存人工范例索引</el-button
      >
      <div class="dim mt-1">标签只用于检索，不是写作配额；语料未保存时不能按旧段落选例。</div>
    </template>
  </div>
</template>
