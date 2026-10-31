<script setup lang="ts">
// 规格07 UI-16：只读 Markdown 子集；模板插值默认转义，不执行 HTML/脚本。
import { computed } from 'vue'

const props = defineProps<{ text: string }>()
type Block =
  | { type: 'heading'; level: number; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'list'; items: string[] }
  | { type: 'table'; columns: string[]; rows: string[][] }

function cells(line: string): string[] {
  return line
    .replace(/^\s*\||\|\s*$/g, '')
    .split('|')
    .map((v) => v.trim())
}
function separator(line: string): boolean {
  const cs = cells(line)
  return cs.length > 0 && cs.every((v) => /^:?-{3,}:?$/.test(v))
}
const blocks = computed<Block[]>(() => {
  const lines = props.text.replace(/\r\n?/g, '\n').split('\n')
  const out: Block[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i] ?? ''
    if (!line.trim()) {
      i++
      continue
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(line)
    if (heading) {
      out.push({ type: 'heading', level: heading[1]!.length, text: heading[2] ?? '' })
      i++
      continue
    }
    if (line.trim().startsWith('|') && i + 1 < lines.length && separator(lines[i + 1] ?? '')) {
      const columns = cells(line)
      const rows: string[][] = []
      i += 2
      while (i < lines.length && (lines[i] ?? '').trim().startsWith('|')) {
        const row = cells(lines[i] ?? '')
        rows.push(columns.map((_, index) => row[index] ?? ''))
        i++
      }
      out.push({ type: 'table', columns, rows })
      continue
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = []
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i] ?? '')) {
        items.push((lines[i] ?? '').replace(/^\s*[-*+]\s+/, ''))
        i++
      }
      out.push({ type: 'list', items })
      continue
    }
    const paragraph = [line]
    i++
    while (
      i < lines.length &&
      (lines[i] ?? '').trim() &&
      !/^(#{1,6})\s+|^\s*[-*+]\s+|^\s*\|/.test(lines[i] ?? '')
    ) {
      paragraph.push(lines[i] ?? '')
      i++
    }
    out.push({ type: 'paragraph', text: paragraph.join('\n') })
  }
  return out
})
</script>

<template>
  <div class="md-view">
    <template v-for="(block, index) in blocks" :key="index">
      <component :is="`h${Math.min(block.level, 6)}`" v-if="block.type === 'heading'">{{
        block.text
      }}</component>
      <p v-else-if="block.type === 'paragraph'">{{ block.text }}</p>
      <ul v-else-if="block.type === 'list'">
        <li v-for="(item, i) in block.items" :key="i">{{ item }}</li>
      </ul>
      <div v-else class="md-table-scroll">
        <el-table :data="block.rows" size="small">
          <el-table-column
            v-for="(column, i) in block.columns"
            :key="column"
            :label="column"
            :min-width="140"
          >
            <template #default="{ row }"
              ><span class="md-cell">{{ row[i] }}</span></template
            >
          </el-table-column>
        </el-table>
      </div>
    </template>
  </div>
</template>

<style scoped>
.md-view {
  font-size: 14px;
  line-height: 1.75;
}
.md-view p {
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.md-table-scroll {
  max-width: 100%;
  overflow-x: auto;
}
.md-cell {
  white-space: normal;
  overflow-wrap: anywhere;
}
</style>
