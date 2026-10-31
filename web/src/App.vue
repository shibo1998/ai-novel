<script setup lang="ts">
// 规格07 UI-07/08：深链菜单归属与可恢复当前书导航。
import { computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { getOverview, listBooks } from '@/api'
import { useBookStore } from '@/stores/book'
import SessionCenter from '@/components/SessionCenter.vue'

const store = useBookStore()
const route = useRoute()
const router = useRouter()
let timer: number | undefined
const activeMenu = computed(() => {
  if (route.path.startsWith('/book/')) return '/shelf'
  if (['/shelf', '/settings', '/conflicts', '/export'].includes(route.path)) return route.path
  return ''
})
const bookPath = (book: string, suffix = '') => `/book/${encodeURIComponent(book)}${suffix}`

async function continueWriting(book: string): Promise<void> {
  store.setCurrentBook(book)
  try {
    const overview = await getOverview(book)
    if (!Array.isArray(overview.chapter_numbers)) {
      ElMessage.warning('服务版本缺少章节集合，已返回总览；请升级后端后再恢复写作')
      await router.push(bookPath(book))
      return
    }
    const recent = store.recentChapter(book)
    const no =
      recent && overview.chapter_numbers.includes(recent) ? recent : overview.chapter_numbers.at(-1)
    await router.push(no ? bookPath(book, `/chapter/${no}`) : bookPath(book))
  } catch {
    // 错误记录提供返回路径；不构造可能无效的章号。
  }
}
async function bookCommand(command: string): Promise<void> {
  const [action, book] = command.split('|') as [string, string]
  if (action === 'recent') {
    await continueWriting(book)
    return
  }
  const current = store.currentBook
  if (action === 'overview') await router.push(bookPath(current))
  if (action === 'volumes') await router.push(bookPath(current, '/volumes'))
  if (action === 'write') await continueWriting(current)
  if (action === 'archive') await router.push(bookPath(current, '/archive'))
  if (action === 'planning') await router.push(bookPath(current, '/planning'))
  if (action === 'state') await router.push(bookPath(current, '/state'))
}
onMounted(() => {
  void store.refreshConflicts()
  void listBooks()
    .then((books) => store.reconcileBooks(books))
    .catch(() => {})
  timer = window.setInterval(() => void store.refreshConflicts(), 15000)
})
onUnmounted(() => {
  if (timer !== undefined) window.clearInterval(timer)
})
</script>

<template>
  <!-- :locale 统一中文（规格08：确认框按钮曾显示英文 Cancel/OK） -->
  <el-config-provider size="small" :locale="zhCn">
    <header class="sticky top-0 z-10 flex items-center gap-3 px-4 py-1 app-header">
      <span class="shrink-0 text-[15px] font-bold app-brand"
        ><span class="ink-seal" aria-hidden="true">墨</span>网文工作台</span
      >
      <el-menu
        :key="activeMenu"
        mode="horizontal"
        router
        :default-active="activeMenu"
        :ellipsis="false"
      >
        <el-menu-item index="/shelf">书架</el-menu-item>
        <el-menu-item index="/conflicts"
          ><el-badge :value="store.conflictCount || undefined" :max="99" type="danger"
            >裁决队列</el-badge
          ></el-menu-item
        >
        <el-menu-item index="/settings">设置</el-menu-item>
        <el-menu-item index="/export">导出</el-menu-item>
      </el-menu>
      <el-dropdown v-if="store.currentBook" @command="bookCommand">
        <el-button text type="primary">当前书：{{ store.currentBook }}</el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="overview">书总览</el-dropdown-item>
            <el-dropdown-item command="volumes">卷工作台 / 找回生产任务</el-dropdown-item>
            <el-dropdown-item command="write">继续写作</el-dropdown-item>
            <el-dropdown-item command="archive">档案编辑</el-dropdown-item>
            <el-dropdown-item command="planning">旧分层规划（高级）</el-dropdown-item>
            <el-dropdown-item command="state">状态卡</el-dropdown-item>
            <el-dropdown-item
              v-for="book in store.recentBooks"
              :key="book"
              :command="`recent|${book}`"
              divided
              >最近：{{ book }}</el-dropdown-item
            >
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-button v-if="!activeMenu" @click="router.push('/shelf')">返回书架</el-button>
    </header>
    <main class="mx-auto max-w-[1400px] p-4"><router-view /></main>
    <SessionCenter />
  </el-config-provider>
</template>

<style scoped>
.app-header {
  background: var(--el-bg-color);
  border-bottom: 1px solid var(--el-border-color-light);
}
.app-brand {
  color: var(--el-color-primary);
}
.ink-seal {
  display: inline-grid;
  place-items: center;
  width: 28px;
  height: 28px;
  margin-right: 8px;
  border-radius: 4px;
  background: #c43d2a;
  color: #fff7ed;
  font:
    18px SimSun,
    serif;
}
.app-header {
  min-height: 56px;
  flex-wrap: wrap;
}
@media (max-width: 680px) {
  .app-header {
    position: static;
    gap: 4px;
  }
  .app-brand {
    width: 100%;
  }
  .app-header :deep(.el-menu) {
    min-width: 0;
  }
  .app-header :deep(.el-menu-item) {
    padding: 0 12px;
  }
}
</style>
