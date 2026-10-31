<script setup lang="ts">
// 规格07 UI-06/09：只从服务端 chapter_numbers 集合导航，不用 max_chapter 猜测章节存在。
import { computed } from 'vue'
import type { ChapterRow } from '@/api/types'
import { statusLabel } from '@/utils/status'

const props = defineProps<{
  modelValue: number | null
  chapters: number[]
  rows?: ChapterRow[]
  disabled?: boolean
  placeholder?: string
}>()
const emit = defineEmits<{ 'update:modelValue': [number]; select: [number] }>()
const options = computed(() =>
  props.chapters.map((no) => {
    const row = props.rows?.find((r) => r.no === no)
    return { no, label: `第 ${no} 章 · ${row ? statusLabel(row.status) : '待生成细纲'}` }
  }),
)
function change(value: number): void {
  if (!props.chapters.includes(value)) return
  emit('update:modelValue', value)
  emit('select', value)
}
</script>

<template>
  <el-select
    :model-value="modelValue"
    filterable
    :disabled="disabled"
    :placeholder="placeholder ?? '选择章节'"
    class="chapter-select"
    @update:model-value="change"
  >
    <el-option
      v-for="option in options"
      :key="option.no"
      :label="option.label"
      :value="option.no"
    />
  </el-select>
</template>

<style scoped>
.chapter-select {
  min-width: 230px;
  max-width: 100%;
}
</style>
