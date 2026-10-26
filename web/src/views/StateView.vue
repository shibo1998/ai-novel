<script setup lang="ts">
// 状态卡（规格05 F-05 §11.3）：只读渲染 books/<书名>/STATE.md。
// 它是 V4 回写后整篇重建的派生文件——手改会被下一次回写覆盖，
// 所以这里只展示、只跳转，不提供编辑入口。
import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { getState } from '@/api'

const route = useRoute()
const router = useRouter()
const book = ref(String(route.params.name ?? ''))
const content = ref('')
const path = ref('')
const rows = ref(0)
const loading = ref(false)

async function load(): Promise<void> {
  if (!book.value) return
  loading.value = true
  try {
    const r = await getState(book.value)
    content.value = r.content
    path.value = r.path
    rows.value = r.rows
  } catch {
    content.value = ''
  } finally {
    loading.value = false
  }
}

watch(
  () => route.params.name,
  () => {
    book.value = String(route.params.name ?? '')
    void load()
  },
  { immediate: true },
)
</script>

<template>
  <div class="panel">
    <div class="flex flex-wrap items-center gap-2">
      <el-button @click="router.push(`/book/${encodeURIComponent(book)}`)">← 回总览</el-button>
      <span class="text-[15px] font-bold">长期记忆状态卡</span>
      <el-tag size="small" type="info">派生自 V4，请勿手改</el-tag>
      <el-tag size="small">结构化状态 {{ rows }} 条</el-tag>
      <span class="flex-1"></span>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>
    <div class="dim mt-1">文件：{{ path || '（尚未生成，定稿任意一章后自动创建）' }}</div>
  </div>

  <div class="panel">
    <pre v-if="content" class="whitespace-pre-wrap text-xs leading-6">{{ content }}</pre>
    <div v-else class="dim">
      （还没有状态卡）定稿任意一章后会由 V4 生成；也可在总览页「批量补做记忆卡」。
    </div>
  </div>
</template>
