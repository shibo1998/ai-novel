<script setup lang="ts">
// 规格10 §3：阶段、原因与章号只取服务端回执；不将生成完成称为定稿。
import { computed } from 'vue'
import type { ProductionJob } from '@/api/productionTypes'
import {
  canCancel,
  canRemove,
  canResume,
  isRunning,
  jobLabel,
  phaseLabel,
  rangeText,
  SLOW_PHASES,
} from '@/utils/production'
const props = defineProps<{
  job: ProductionJob
  disabled: boolean
  busy: string
  lastChecked?: string
}>()
defineEmits<{
  control: [action: 'pause' | 'resume' | 'cancel' | 'recover']
  refresh: []
  remove: []
}>()
const remaining = computed(() => props.job.numbers.filter((n) => !props.job.completed.includes(n)))
const percent = computed(() =>
  props.job.numbers.length
    ? Math.round((props.job.completed.length / props.job.numbers.length) * 100)
    : 0,
)
</script>

<template>
  <section class="production-progress" aria-label="本批当前任务">
    <details
      class="progress-disclosure"
      :open="
        isRunning(job) ||
        ['paused', 'error', 'interrupted', 'stale', 'recovery_required'].includes(job.status)
      "
    >
      <summary>
        {{ jobLabel(job.status) }} · 已出稿并检查 {{ job.completed.length }}/{{
          job.numbers.length
        }}
        · 已人工定稿 {{ job.applied.length }}（展开任务）
      </summary>
      <div class="volume-section-heading">
        <div>
          <span class="eyebrow">本批当前任务</span>
          <h2>{{ jobLabel(job.status) }}</h2>
        </div>
        <span
          class="volume-state"
          :class="{ warning: !['applied', 'cancelled'].includes(job.status) }"
          >{{ phaseLabel(job.phase) }}</span
        >
      </div>
      <div class="progress-facts">
        <span
          >本批章号 <b>{{ rangeText(job.numbers) }}</b></span
        ><span
          >当前章
          <b>{{ job.current_chapter ? `第 ${job.current_chapter} 章` : '无当前章' }}</b></span
        ><span
          >已出稿并检查 <b>{{ job.completed.length }}/{{ job.numbers.length }}</b></span
        ><span
          >已人工定稿 <b>{{ job.applied.length }}</b></span
        ><span v-if="lastChecked"
          >本页最后核对 <b class="mono">{{ lastChecked }}</b></span
        >
      </div>
      <p v-if="isRunning(job) && SLOW_PHASES.has(job.phase)" class="volume-hint">
        正在等待模型返回：单次调用可能要几分钟，阶段名不变不代表卡死。页面每秒核对服务端，模型只在服务端调用，浏览器不会重复发请求。
      </p>
      <div
        class="volume-meter"
        role="progressbar"
        :aria-valuenow="percent"
        aria-valuemin="0"
        aria-valuemax="100"
        aria-label="本批出稿并检查进度"
      >
        <span :style="{ width: `${percent}%` }" />
      </div>
      <p v-if="job.reason" class="volume-alert" role="status">
        <b>服务端原因：</b>{{ job.reason }}
      </p>
      <p v-if="job.status === 'cancelled' && !job.completed.length" class="volume-alert">
        这个任务已停止，且还没有产出任何稿件。它不会自动恢复；可以「删除此任务」清掉，
        然后重新填要求准备本卷——删除只清任务记录与检查点，不动书里的正文。
      </p>
      <p v-if="job.status === 'stale' || job.status === 'recovery_required'" class="volume-alert">
        已有稿件保留。请核对变更或去高级工作台修复；本页不会自动改细纲、覆盖后续稿或放行定稿。
      </p>
      <div class="volume-actions">
        <el-button
          v-if="isRunning(job)"
          :disabled="disabled"
          :loading="busy === 'pause'"
          @click="$emit('control', 'pause')"
          >在安全点暂停</el-button
        >
        <el-button
          v-if="canResume(job)"
          type="primary"
          :disabled="disabled"
          :loading="busy === 'resume'"
          @click="$emit('control', 'resume')"
          >核对后继续 / 重试</el-button
        >
        <el-button
          v-if="job.status === 'recovery_required'"
          type="primary"
          :disabled="disabled"
          :loading="busy === 'recover'"
          @click="$emit('control', 'recover')"
          >核对并恢复提交（不调用模型）</el-button
        >
        <el-button :disabled="!!busy" @click="$emit('refresh')">刷新服务端回执</el-button>
        <el-button
          v-if="canCancel(job)"
          type="danger"
          plain
          :disabled="disabled"
          :loading="busy === 'cancel'"
          @click="$emit('control', 'cancel')"
          >停止后续生产</el-button
        >
        <el-button
          v-if="canRemove(job)"
          type="danger"
          text
          :disabled="disabled"
          :loading="busy === 'remove'"
          @click="$emit('remove')"
          >删除此任务</el-button
        >
      </div>
      <p class="volume-hint">
        {{
          isRunning(job)
            ? '每秒核对服务端阶段；关闭或离开页面不会取消后台任务。'
            : '当前不自动轮询。刷新可核对最新结果；恢复必须由你显式发起。'
        }}
        暂停或停止只阻止后续调度，已发出的模型请求可能继续完成及计费。
      </p>
      <details class="volume-details">
        <summary>任务范围与检查点</summary>
        <dl class="volume-definition">
          <div>
            <dt>已出稿并检查</dt>
            <dd>{{ rangeText(job.completed) }}</dd>
          </div>
          <div>
            <dt>尚未完成检查 / 未执行</dt>
            <dd>{{ rangeText(remaining) }}（当前章可能正在执行）</dd>
          </div>
          <div>
            <dt>已人工定稿</dt>
            <dd>{{ rangeText(job.applied) }}</dd>
          </div>
          <div>
            <dt>更新于</dt>
            <dd>{{ job.updated_at }}</dd>
          </div>
          <div>
            <dt>任务编号</dt>
            <dd class="mono">{{ job.id }}</dd>
          </div>
        </dl>
      </details>
    </details>
  </section>
</template>
