<script setup lang="ts">
// 规格07 UI-17：总览/档案共用 G 链参数、候选、同基线 diff 与 If-Match 人工采纳。
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  adoptArchive,
  getBibleVersioned,
  getBookConfig,
  getMeta,
  getOutlineVersioned,
  listCharactersVersioned,
  previewOutlineAdopt,
} from '@/api'
import type { OutlinePreview } from '@/api/types'
import DiffViewer from './DiffViewer.vue'
import {
  archiveVariables,
  defaultAIParams,
  G_LABELS,
  G_TARGETS,
  adoptSummary,
  outlineDiffText,
  outlineImpactText,
  validateAIParams,
  type ArchiveAIParams,
} from '@/utils/archiveAI'
import { withTask } from '@/utils/tasks'
import { reportValidation } from '@/utils/errors'
import { toMdTable } from '@/utils/mdtable'

const props = defineProps<{ book: string }>()
const emit = defineEmits<{ adopted: [] }>()
const parameterOpen = ref(false)
const candidateOpen = ref(false)
const loading = ref(false)
const adopting = ref(false)
const pid = ref('G1')
const params = reactive<ArchiveAIParams>(defaultAIParams({}, {}))
const candidate = ref<{ book: string; pid: string; text: string; digest: string } | null>(null)
// 规格08 A-T04：G5 的影响预览由服务端纯计算（真实旧/新全文 + 已有正文真实章号）。
const preview = ref<OutlinePreview | null>(null)
const previewStale = ref(false)
const baseline = ref<{
  book: string
  target: 'bible' | 'characters' | 'outline'
  etag: string
  oldText: string
  writtenRows: number
} | null>(null)

const target = computed(() => G_TARGETS[pid.value] ?? 'bible')
const impact = computed(() => {
  if (target.value === 'bible') return '按候选内容合并 STORY_BIBLE.md 对应节；未提及的人工节保留。'
  if (target.value === 'characters')
    return '按候选新建角色并保留已有角色，写回角色关系；同名角色不被静默覆盖。'
  if (preview.value) return outlineImpactText(preview.value)
  return '替换 OUTLINE.md 的章节总表、卷规划与爽点节奏总谱，并合并 PLOT_POINTS.md 伏笔台账；正文不会随大纲采纳自动重写。'
})
const diffOld = computed(() => {
  const p = preview.value
  if (!p) return baseline.value?.oldText ?? ''
  return outlineDiffText(p.outline_text_old, p.plot_points_text_old, '旧台账')
})
const diffNew = computed(() => {
  const p = preview.value
  if (!p) return candidate.value?.text ?? ''
  return outlineDiffText(p.outline_text_new, p.plot_points_text_new, '新台账')
})

watch(
  () => props.book,
  () => {
    candidate.value = null
    baseline.value = null
    preview.value = null
    previewStale.value = false
    candidateOpen.value = false
    parameterOpen.value = false
  },
)

// 候选被人工编辑后，旧预览基线已不对应当前内容 → 必须重新预览才能采纳（规格08 A-T08 同源纪律）。
watch(
  () => candidate.value?.text,
  () => {
    if (preview.value) previewStale.value = true
  },
)

async function open(pidValue: string): Promise<void> {
  if (loading.value) return
  pid.value = pidValue
  const book = props.book
  loading.value = true
  try {
    const [meta, config] = await Promise.all([getMeta(book), getBookConfig(book)])
    if (book !== props.book) return
    Object.assign(params, defaultAIParams(meta, config))
    parameterOpen.value = true
  } finally {
    loading.value = false
  }
}

async function loadBaseline(book: string, targetValue: 'bible' | 'characters' | 'outline') {
  if (targetValue === 'bible') {
    const r = await getBibleVersioned(book)
    return {
      etag: r.etag,
      oldText: Object.entries(r.data)
        .map(([k, v]) => `## ${k}\n\n${v}`)
        .join('\n\n'),
      writtenRows: 0,
    }
  }
  if (targetValue === 'characters') {
    const r = await listCharactersVersioned(book)
    return {
      etag: r.etag,
      oldText: r.data.map((c) => String(c._body ?? '')).join('\n\n'),
      writtenRows: 0,
    }
  }
  const r = await getOutlineVersioned(book)
  const rows = r.data.chapter_rows ?? []
  // 规格08 A-T04：这里**不再**按表内「状态」列统计已写章数——章节总表七列里没有状态列，
  // 旧实现据此恒得 0。真实章数与受影响范围一律取服务端 /preview/outline 的文件口径。
  return {
    etag: r.etag,
    oldText: [
      '## 章节总表',
      toMdTable(['章', '卷', '本章目标', '冲突来源', '爽点/揭露', '钩子类型', '重要度★'], rows),
      '',
      '## 卷规划',
      JSON.stringify(r.data.volumes ?? [], null, 2),
      '',
      '## 爽点节奏总谱',
      String(r.data._rhythm ?? ''),
    ].join('\n'),
    writtenRows: 0,
  }
}

async function generate(): Promise<void> {
  const error = validateAIParams(pid.value, params)
  if (error) {
    reportValidation(error, { operation: 'AI 生成参数', book: props.book })
    return
  }
  const book = props.book
  const selectedPid = pid.value
  loading.value = true
  try {
    const [bible, characters, base] = await Promise.all([
      getBibleVersioned(book),
      listCharactersVersioned(book),
      loadBaseline(book, G_TARGETS[selectedPid] ?? 'bible'),
    ])
    const baseEtag = base.etag
    if (!baseEtag) throw new Error('服务未返回档案版本，已阻止无保护的生成/采纳')
    const variables = archiveVariables(selectedPid, params, bible.data, characters.data)
    const result = await withTask(
      {
        operation: `${G_LABELS[selectedPid]} 候选生成`,
        book,
        returnPath: `/book/${encodeURIComponent(book)}/archive`,
      },
      (signal) =>
        // genArchive 的纯返回体候选不能承诺断线恢复。
        import('@/utils/archiveAI').then(({ genArchive }) =>
          genArchive(book, selectedPid, variables, signal),
        ),
    )
    if (book !== props.book) return
    candidate.value = { book, pid: selectedPid, text: result.text, digest: result.digest }
    baseline.value = {
      book,
      target: G_TARGETS[selectedPid] ?? 'bible',
      oldText: base.oldText,
      etag: baseEtag,
      writtenRows: base.writtenRows,
    }
    preview.value = null
    previewStale.value = false
    // 规格08 A-T04：大纲候选先取服务端影响预览（纯计算、不写档），失败不阻断候选展示，
    // 但会让下一步"确认采纳"退回通用说明并要求重试预览。
    if ((G_TARGETS[selectedPid] ?? 'bible') === 'outline') {
      try {
        const p = await previewOutlineAdopt(book, result.text)
        if (book === props.book) preview.value = p
      } catch {
        if (book === props.book) preview.value = null
      }
    }
    parameterOpen.value = false
    candidateOpen.value = true
  } finally {
    loading.value = false
  }
}

async function refreshDiff(): Promise<string> {
  const b = baseline.value
  if (!b) return '候选已清除，无需重建差异'
  const current = await loadBaseline(b.book, b.target)
  const etag = current.etag
  if (!etag) throw new Error('当前档案版本不可用')
  baseline.value = { ...b, ...current, etag }
  // 大纲还要按当前候选重算影响预览（旧预览已不适用）
  if (b.target === 'outline' && candidate.value) {
    preview.value = await previewOutlineAdopt(b.book, candidate.value.text)
    previewStale.value = false
  }
  const message = '已按同一当前版本重新展示差异；候选仍保留，请重新人工核对'
  ElMessage.info(message)
  return message
}

async function adopt(): Promise<void> {
  const c = candidate.value
  const b = baseline.value
  if (!c || !b || adopting.value || c.book !== b.book || c.book !== props.book) return
  if (previewStale.value) {
    ElMessage.warning(
      '候选已修改：影响预览与差异对应的是修改前的内容，请先「查看当前文件并重建差异」再采纳',
    )
    return
  }
  const unknown =
    b.target === 'outline' && preview.value !== null && preview.value.written.known === false
  try {
    await ElMessageBox.confirm(
      `${impact.value}${unknown ? '\n\n注意：已有正文章数无法判定，采纳前请先确认章节文件可读。' : ''}\n\n将按预览时的档案版本校验；版本变化会拒绝覆盖。确认采纳？`,
      '确认人工采纳',
      { type: 'warning' },
    )
  } catch {
    return
  }
  adopting.value = true
  try {
    const result = await withTask(
      {
        operation: `${G_LABELS[c.pid]} 人工采纳`,
        book: c.book,
        write: true,
        returnPath: `/book/${encodeURIComponent(c.book)}/archive`,
        confirm: refreshDiff,
      },
      (signal) => adoptArchive(c.book, b.target, c.text, b.etag, signal),
    )
    ElMessage.success(adoptSummary(b.target, result))
    candidateOpen.value = false
    candidate.value = null
    baseline.value = null
    preview.value = null
    previewStale.value = false
    emit('adopted')
  } catch (error) {
    const status = (error as { response?: { status?: number } }).response?.status
    if (status === 412) ElMessage.warning('档案已变化，候选和修改均保留；请重新获取差异后人工核对')
  } finally {
    adopting.value = false
  }
}

defineExpose({ open })
</script>

<template>
  <div class="archive-ai">
    <el-button
      v-for="(label, key) in G_LABELS"
      :key="key"
      :loading="loading && pid === key"
      @click="open(key)"
      >{{ label }}</el-button
    >
    <div class="dim mt-2">
      生成只产生候选并调用模型；耗时取决于模型服务。取消参数或采纳均不写档案。
    </div>

    <el-dialog
      v-model="parameterOpen"
      :title="`${G_LABELS[pid]} · 生成参数`"
      width="680px"
      :close-on-click-modal="false"
    >
      <el-alert
        type="info"
        :closable="false"
        show-icon
        class="mb-2"
        :title="
          pid === 'G5'
            ? '全书范围：将基于圣经与角色生成全书规划候选；采纳仍需人工确认。'
            : target === 'characters'
              ? '角色范围：生成角色/关系候选，不直接建卡。'
              : '世界设定范围：生成 Bible 节候选，不直接覆盖。'
        "
      />
      <el-form label-width="150px">
        <template v-if="pid === 'G1' || pid === 'G2'">
          <el-form-item label="题材（必填）"><el-input v-model="params.genre" /></el-form-item>
          <el-form-item label="故事核（必填）"
            ><el-input v-model="params.story_core" type="textarea"
          /></el-form-item>
          <el-form-item label="对标书"><el-input v-model="params.benchmark" /></el-form-item>
        </template>
        <template v-else-if="pid === 'G3' || pid === 'G4'">
          <el-form-item label="角色需求（必填）"
            ><el-input
              v-model="params.character_requirements"
              type="textarea"
              :rows="6"
              placeholder="每行：名字+定位+要求"
          /></el-form-item>
        </template>
        <template v-else>
          <el-form-item label="一句话故事（必填）"
            ><el-input v-model="params.logline" type="textarea"
          /></el-form-item>
          <el-form-item label="目标总章数"
            ><el-input v-model="params.target_chapters"
          /></el-form-item>
          <el-form-item label="单章字数区间"
            ><el-input v-model="params.chapter_word_range"
          /></el-form-item>
        </template>
      </el-form>
      <template #footer
        ><el-button @click="parameterOpen = false">取消（不调用模型）</el-button
        ><el-button type="primary" :loading="loading" @click="generate"
          >生成候选</el-button
        ></template
      >
    </el-dialog>

    <el-dialog
      v-model="candidateOpen"
      title="AI 候选 · 同基线差异 · 人工采纳"
      width="92%"
      top="3vh"
      :close-on-click-modal="false"
    >
      <template v-if="candidate && baseline">
        <el-alert
          type="warning"
          :closable="false"
          show-icon
          class="mb-2"
          :title="`将写入的实际影响`"
          :description="impact"
        />
        <el-alert
          v-if="previewStale"
          type="error"
          :closable="false"
          show-icon
          class="mb-2"
          title="候选已修改：影响预览与差异已过期，请先「查看当前文件并重建差异」再采纳"
        />
        <div class="dim mb-2">
          候选绑定原书「{{ candidate.book }}」、{{ G_LABELS[candidate.pid] }} 与预览 ETag；digest
          {{ candidate.digest.slice(0, 12) }}
        </div>
        <DiffViewer
          :old-text="diffOld"
          :new-text="diffNew"
          :title="
            target === 'outline'
              ? '当前 OUTLINE + 伏笔台账 → 采纳后将写入的内容（含合并后台账）'
              : '预览基线 → 候选（可编辑）'
          "
        />
        <el-input v-model="candidate.text" type="textarea" :rows="10" class="mono mt-2" />
      </template>
      <template #footer>
        <el-button @click="candidateOpen = false">取消采纳</el-button>
        <el-button @click="refreshDiff">查看当前文件并重建差异</el-button>
        <el-button
          type="primary"
          :loading="adopting"
          :disabled="!candidate || candidate.book !== book || previewStale"
          @click="adopt"
          >确认影响并采纳</el-button
        >
      </template>
    </el-dialog>
  </div>
</template>
