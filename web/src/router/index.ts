// 路由表 = 规格04 §1 页面清单；hash 模式配合 FastAPI 静态挂载（刷新/直达不 404）。
import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/shelf' },
    { path: '/shelf', component: () => import('@/views/ShelfView.vue') },
    { path: '/book/:name', component: () => import('@/views/OverviewView.vue') },
    { path: '/book/:name/chapter/:no', component: () => import('@/views/WorkbenchView.vue') },
    { path: '/conflicts', component: () => import('@/views/ConflictsView.vue') },
    { path: '/settings', component: () => import('@/views/SettingsView.vue') },
    { path: '/export', component: () => import('@/views/ExportView.vue') },
  ],
})

export default router
