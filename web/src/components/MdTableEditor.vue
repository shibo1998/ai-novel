<script setup lang="ts">
// 通用 Markdown 表格行编辑表格（信息密度优先：单元格直接 el-input，无弹窗）。
// 用于力量体系表(8列)/伏笔台账(7列)/地点(5列)/大纲章节总表(7列)/卷规划(7列)。
export interface TableColumn {
  /** 列名 = Markdown 表头（必须与后端 dump_table 的列名逐字一致） */
  key: string
  label: string
  /** 列宽（px）；不传则自适应 */
  width?: number
  /** 下拉选项（提供则渲染 el-select） */
  options?: string[]
  /** 输入类型：text（默认）/ number / textarea */
  type?: 'text' | 'number' | 'textarea'
  placeholder?: string
}

const props = withDefaults(
  defineProps<{
    rows: Record<string, string>[]
    columns: TableColumn[]
    /** 是否允许上下移动/插入行（默认允许增删行） */
    reorderable?: boolean
  }>(),
  { reorderable: false },
)

const emit = defineEmits<{ 'update:rows': [rows: Record<string, string>[]] }>()

function clone(): Record<string, string>[] {
  return props.rows.map((r) => ({ ...r }))
}

function addRow(): void {
  const blank: Record<string, string> = {}
  for (const c of props.columns) blank[c.key] = ''
  emit('update:rows', [...clone(), blank])
}

function delRow(idx: number): void {
  const next = clone()
  next.splice(idx, 1)
  emit('update:rows', next)
}

function insertRow(idx: number): void {
  const blank: Record<string, string> = {}
  for (const c of props.columns) blank[c.key] = ''
  const next = clone()
  next.splice(idx + 1, 0, blank)
  emit('update:rows', next)
}

function moveRow(idx: number, delta: number): void {
  const target = idx + delta
  if (target < 0 || target >= props.rows.length) return
  const next = clone()
  const [item] = next.splice(idx, 1)
  if (item) next.splice(target, 0, item)
  emit('update:rows', next)
}

function setCell(idx: number, key: string, value: string): void {
  const next = clone()
  if (next[idx]) next[idx]![key] = value
  emit('update:rows', next)
}
</script>

<template>
  <div>
    <el-table :data="rows" size="small" border max-height="420" class="w-full">
      <el-table-column type="index" label="#" width="42" />
      <el-table-column
        v-for="c in columns"
        :key="c.key"
        :label="c.label"
        :width="c.width"
        :min-width="c.width ? undefined : 120"
      >
        <template #default="{ row, $index }">
          <el-select
            v-if="c.options"
            :model-value="row[c.key] ?? ''"
            size="small"
            filterable
            allow-create
            default-first-option
            style="width: 100%"
            @update:model-value="(v: string) => setCell($index, c.key, v ?? '')"
          >
            <el-option v-for="o in c.options" :key="o" :label="o" :value="o" />
          </el-select>
          <el-input-number
            v-else-if="c.type === 'number'"
            :model-value="Number(row[c.key] || 0)"
            size="small"
            :controls="false"
            style="width: 100%"
            @update:model-value="
              (v: number | undefined) => setCell($index, c.key, v == null ? '' : String(v))
            "
          />
          <el-input
            v-else-if="c.type === 'textarea'"
            :model-value="row[c.key] ?? ''"
            type="textarea"
            :rows="1"
            autosize
            :placeholder="c.placeholder"
            @update:model-value="(v: string) => setCell($index, c.key, v)"
          />
          <el-input
            v-else
            :model-value="row[c.key] ?? ''"
            size="small"
            :placeholder="c.placeholder"
            @update:model-value="(v: string) => setCell($index, c.key, v)"
          />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="130" fixed="right">
        <template #default="{ $index }">
          <el-button size="small" text @click="insertRow($index)">插入</el-button>
          <el-button size="small" text @click="delRow($index)">删</el-button>
          <template v-if="reorderable">
            <el-button size="small" text :disabled="$index === 0" @click="moveRow($index, -1)">
              ↑
            </el-button>
            <el-button
              size="small"
              text
              :disabled="$index === rows.length - 1"
              @click="moveRow($index, 1)"
            >
              ↓
            </el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>
    <div class="mt-2">
      <el-button size="small" @click="addRow">+ 新增行</el-button>
      <span class="dim ml-2">{{ rows.length }} 行</span>
    </div>
  </div>
</template>
