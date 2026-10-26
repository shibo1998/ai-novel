// 路由表 = 规格04 §1 页面清单；hash 模式配合 FastAPI 静态挂载（刷新/直达不 404）。
import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/shelf' },
    { path: '/shelf', component: () => import('@/views/ShelfView.vue') },
    { path: '/book/:name', component: () => import('@/views/OverviewView.vue') },
    // 档案编辑（规格04 §1 独立页面）：左树右编辑器 + 结构化表单 + AI diff 预览。
    { path: '/book/:name/archive', component: () => import('@/views/ArchiveView.vue') },
    { path: '/book/:name/chapter/:no', component: () => import('@/views/WorkbenchView.vue') },
    // 长期记忆状态卡（只读派生视图，规格05 F-05）
    { path: '/book/:name/state', component: () => import('@/views/StateView.vue') },
    { path: '/conflicts', component: () => import('@/views/ConflictsView.vue') },
    { path: '/settings', component: () => import('@/views/SettingsView.vue') },
    { path: '/export', component: () => import('@/views/ExportView.vue') },
  ],
})

export default router
