<script setup lang="ts">
import { onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'

import { useBookStore } from '@/stores/book'

const store = useBookStore()
const route = useRoute()
let timer: number | undefined

onMounted(() => {
  void store.refreshConflicts()
  timer = window.setInterval(() => void store.refreshConflicts(), 15000)
})
onUnmounted(() => {
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<template>
  <el-config-provider size="small">
    <header
      class="sticky top-0 z-10 flex items-center gap-3 px-4 py-1"
      style="background: var(--el-bg-color); border-bottom: 1px solid var(--el-border-color-light)"
    >
      <span class="shrink-0 text-[15px] font-bold" style="color: var(--el-color-primary)">
        AI 网文工作台
      </span>
      <el-menu mode="horizontal" router :default-active="route.path" :ellipsis="false">
        <el-menu-item index="/shelf">书架</el-menu-item>
        <el-menu-item index="/conflicts">
          <el-badge :value="store.conflictCount || undefined" :max="99" type="danger">
            裁决队列
          </el-badge>
        </el-menu-item>
        <el-menu-item index="/settings">设置</el-menu-item>
        <el-menu-item index="/export">导出</el-menu-item>
      </el-menu>
      <span v-if="store.currentBook" class="dim shrink-0">当前书：{{ store.currentBook }}</span>
    </header>
    <main class="mx-auto max-w-[1400px] p-4">
      <router-view />
    </main>
  </el-config-provider>
</template>
