// 路由表 = 规格04 §1 页面清单；hash 模式配合 FastAPI 静态挂载（刷新/直达不 404）。
import { createRouter, createWebHashHistory } from 'vue-router'
import { confirmDirtyLeave } from '@/utils/editing'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', redirect: '/shelf' },
    { path: '/shelf', component: () => import('@/views/ShelfView.vue') },
    { path: '/book/:name', component: () => import('@/views/OverviewView.vue') },
    // 规格10：作者主要入口，旧分层规划仍保留在 /planning 高级路径。
    { path: '/book/:name/volumes', component: () => import('@/views/VolumeView.vue') },
    // 档案编辑（规格04 §1 独立页面）：左树右编辑器 + 结构化表单 + AI diff 预览。
    { path: '/book/:name/archive', component: () => import('@/views/ArchiveView.vue') },
    // 内容规划（规格08 §3.3 阶段B）：rolling 书分层规划工作台；支持 node/returnChapter 查询定位。
    { path: '/book/:name/planning', component: () => import('@/views/PlanningView.vue') },
    { path: '/book/:name/chapter/:no', component: () => import('@/views/WorkbenchView.vue') },
    // 长期记忆状态卡（只读派生视图，规格05 F-05）
    { path: '/book/:name/state', component: () => import('@/views/StateView.vue') },
    { path: '/conflicts', component: () => import('@/views/ConflictsView.vue') },
    { path: '/settings', component: () => import('@/views/SettingsView.vue') },
    { path: '/export', component: () => import('@/views/ExportView.vue') },
  ],
})

router.beforeEach(async () => confirmDirtyLeave())

export default router
