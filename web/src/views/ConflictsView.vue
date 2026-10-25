<script setup lang="ts">
// 裁决队列（规格04 §1）：台账冲突三选一裁决，处理后归零。
import { ElMessage } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import { listConflicts, resolveConflictApi } from '@/api'
import type { Conflict } from '@/api/types'
import { useBookStore } from '@/stores/book'

const store = useBookStore()
const items = ref<Conflict[]>([])
const manual = reactive<Record<number, string>>({})

async function load(): Promise<void> {
  items.value = await listConflicts()
  await store.refreshConflicts()
}
onMounted(() => void load().catch(() => {}))

async function resolve(it: Conflict, action: string, manualValue?: unknown): Promise<void> {
  try {
    await resolveConflictApi(it.id, action, manualValue)
    ElMessage.success('已裁决')
    await load()
  } catch {
    // 拦截器已提示
  }
}

function resolveManual(it: Conflict): void {
  const raw = manual[it.id]?.trim()
  if (!raw) {
    ElMessage.warning('填手改值')
    return
  }
  let parsed: unknown = raw
  try {
    parsed = JSON.parse(raw) as unknown
  } catch {
    // 非法 JSON 保留字符串原样提交
  }
  void resolve(it, 'manual', parsed)
}

function j(v: unknown): string {
  return JSON.stringify(v)
}
</script>

<template>
  <h3 class="panel-title">待裁决冲突（非空时禁止开新章细纲）</h3>
  <div v-if="items.length">
    <div v-for="it in items" :key="it.id" class="panel">
      <div class="flex flex-wrap items-center gap-2">
        <el-tag type="danger" size="small">{{ it.kind }}</el-tag>
        <span>第{{ it.chapter_no }}章 · {{ it.book }}</span>
        <span v-if="it.char" class="dim">{{ it.char }}{{ it.field ? '.' + it.field : '' }}</span>
      </div>
      <div class="my-2">{{ it.detail }}</div>
      <div class="dim mono">档案现值：{{ j(it.old) }} → AI 新值：{{ j(it.new) }}</div>
      <div class="mt-2 flex flex-wrap items-center gap-2">
        <el-button type="primary" @click="resolve(it, 'adopt_ai')">采纳AI</el-button>
        <el-button @click="resolve(it, 'keep_archive')">保留档案</el-button>
        <el-input v-model="manual[it.id]" placeholder="手改值（JSON）" style="width: 200px" />
        <el-button @click="resolveManual(it)">手改采纳</el-button>
      </div>
    </div>
  </div>
  <div v-else class="panel dim">队列已清零 ✅</div>
</template>
