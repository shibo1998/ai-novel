<script setup lang="ts">
// 逐行 diff 预览（规格02 §4：生成 → UI diff 预览 → 人工改定 → 采纳）。
// 新增行绿底、删除行红底+删除线；默认只显示有变更的段落（±3 行上下文），
// 可切「全部行」看完整对比。
import { computed, ref, watch } from 'vue'

import type { DiffLine } from '@/utils/diff'
import { diffLines, diffStat } from '@/utils/diff'

const props = withDefaults(
  defineProps<{
    oldText: string
    newText: string
    /** 上下文行数（默认 3） */
    context?: number
    /** 标题 */
    title?: string
  }>(),
  { context: 3, title: 'diff 预览' },
)

const showAll = ref(false)
const onlyChanged = ref(true)

const all = computed<DiffLine[]>(() => diffLines(props.oldText, props.newText))
// 编辑内容变化时回到「只看变更」，避免用户在看全量时被新结果淹没
watch(
  () => props.newText,
  () => {
    showAll.value = false
  },
)

const stat = computed(() => diffStat(all.value))

const visible = computed<DiffLine[]>(() => {
  if (showAll.value || !onlyChanged.value) return all.value
  const lines = all.value
  const keep = new Set<number>()
  lines.forEach((l, i) => {
    if (l.kind === 'same') return
    for (
      let k = Math.max(0, i - props.context);
      k <= Math.min(lines.length - 1, i + props.context);
      k++
    ) {
      keep.add(k)
    }
  })
  return lines.filter((_, i) => keep.has(i))
})
</script>

<template>
  <div>
    <div class="mb-1 flex flex-wrap items-center gap-2">
      <span class="dim">{{ title }}</span>
      <el-tag type="success" size="small">+{{ stat.added }} 行</el-tag>
      <el-tag type="danger" size="small">-{{ stat.removed }} 行</el-tag>
      <span class="dim">相同 {{ stat.unchanged }} 行</span>
      <span class="flex-1"></span>
      <el-switch v-model="showAll" active-text="显示全部行" />
    </div>
    <div class="diff-box">
      <div v-if="!visible.length" class="dim">（新旧内容完全相同）</div>
      <div v-for="(l, i) in visible" :key="`${i}-${l.kind}`" class="diff-line" :class="l.kind">
        <span class="diff-no">{{ l.oldLine ?? '' }}</span>
        <span class="diff-no">{{ l.newLine ?? '' }}</span>
        <span class="diff-sign">{{ l.kind === 'add' ? '+' : l.kind === 'del' ? '-' : ' ' }}</span>
        <span class="diff-text">{{ l.text || ' ' }}</span>
      </div>
    </div>
  </div>
</template>
