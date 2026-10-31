<script setup lang="ts">
// 规格07 UI-23/24：跨页会话任务条与错误记录；不是服务端任务队列。
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { confirmTask, stopWaiting, taskElapsed, tasks } from '@/utils/tasks'
import { copyError, errorRecords } from '@/utils/errors'

const router = useRouter()
const expanded = ref(false)
const activeCount = computed(
  () => tasks.filter((t) => t.phase === 'running' || t.phase === 'unknown').length,
)

function phaseLabel(phase: string): string {
  return (
    { running: '等待中', unknown: '结果待确认', completed: '已完成', failed: '已失败' }[phase] ??
    '未知状态'
  )
}
function waitingHint(seconds: number): string {
  if (seconds >= 300) return '已等待超过 5 分钟，建议检查模型服务；不伪造进度百分比'
  if (seconds >= 60) return '仍在等待响应'
  return ''
}
async function copy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('排障信息已复制')
  } catch {
    ElMessage.warning('浏览器未允许剪贴板访问，请手动选择记录复制')
  }
}
</script>

<template>
  <div v-if="activeCount || errorRecords.length" class="session-center">
    <button class="session-summary" type="button" @click="expanded = !expanded">
      任务 {{ activeCount }} · 错误 {{ errorRecords.length }} {{ expanded ? '收起' : '展开' }}
    </button>
    <div v-if="expanded" class="session-details">
      <section v-if="tasks.length">
        <h4>本次会话任务</h4>
        <div v-for="task in tasks" :key="task.id" class="session-item">
          <div>
            <b>{{ task.operation }}</b>
            {{ task.book || '全局' }}{{ task.chapter ? ` · 第${task.chapter}章` : '' }} ·
            {{ phaseLabel(task.phase) }} · {{ taskElapsed(task) }}秒
          </div>
          <div class="dim">{{ task.result }} {{ waitingHint(taskElapsed(task)) }}</div>
          <div class="mt-1 flex gap-2">
            <el-button v-if="task.phase === 'running'" size="small" @click="stopWaiting(task.id)"
              >停止等待</el-button
            >
            <el-button v-if="task.phase === 'unknown'" size="small" @click="confirmTask(task.id)"
              >确认结果</el-button
            >
            <el-button v-if="task.returnPath" size="small" @click="router.push(task.returnPath)"
              >回到原目标</el-button
            >
          </div>
        </div>
      </section>
      <section v-if="errorRecords.length">
        <h4>本次会话错误记录（最多 20 条，刷新清空）</h4>
        <div v-for="error in errorRecords" :key="error.id" class="session-item">
          <div>
            <b>{{ error.operation }}</b> · {{ error.code }} · {{ error.time }} · 共
            {{ error.count }} 次
          </div>
          <div>{{ error.message }}</div>
          <div class="dim">下一步：{{ error.action }}</div>
          <el-button size="small" @click="copy(copyError(error))">复制排障信息</el-button>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.session-center {
  position: fixed;
  right: 16px;
  bottom: 16px;
  z-index: 3000;
  max-width: min(680px, 90vw);
}
.session-summary {
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  padding: 8px 12px;
  background: var(--el-bg-color-overlay);
  color: var(--el-text-color-primary);
  cursor: pointer;
}
.session-details {
  max-height: 60vh;
  overflow: auto;
  margin-bottom: 8px;
  padding: 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 6px;
  background: var(--el-bg-color-overlay);
  box-shadow: var(--el-box-shadow);
}
.session-item {
  padding: 8px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
  font-size: 12px;
}
</style>
