<script setup lang="ts">
// 规格06 §5、§11.3：实际请求与旧槽位预览分开展示，不把组装片段冒充已发送请求。
import { computed } from 'vue'
import type { PreviewResult } from '@/api/types'
const props = defineProps<{ preview: PreviewResult }>()
const requests = computed(() =>
  props.preview.actual_requests?.length
    ? props.preview.actual_requests
    : props.preview.latest_request
      ? [props.preview.latest_request]
      : [],
)
function display(value: unknown): string {
  return value == null
    ? '（未提供）'
    : typeof value === 'string'
      ? value
      : JSON.stringify(value, null, 2)
}
</script>

<template>
  <div>
    <div class="dim mt-2">
      总输入估算 {{ preview.total_est_tokens }} tokens / 预算
      {{ preview.budget_tokens }}（工程估算，非供应商精确值） · {{ preview.saved_to || '' }}
    </div>
    <el-alert
      v-for="(w, i) in preview.warnings ?? []"
      :key="i"
      type="warning"
      :closable="false"
      class="my-2"
      :title="display(w)"
    />
    <el-alert
      v-if="!requests.length"
      type="info"
      :closable="false"
      class="my-2"
      title="暂无实际请求快照（旧日志可能没有）；下方槽位不是实际 system / user 调用证据。"
    />
    <el-collapse v-else class="mt-2">
      <el-collapse-item
        v-for="r in requests"
        :key="r.request_id"
        :title="`实际请求 · ${r.request_id}`"
      >
        <el-alert
          v-for="(w, i) in r.meta?.warnings ?? []"
          :key="i"
          type="warning"
          :closable="false"
          class="mb-2"
          :title="display(w)"
        />
        <h4 class="font-bold">实际系统提示词</h4>
        <pre class="stream-box">{{ r.system || '（空）' }}</pre>
        <h4 class="mt-2 font-bold">实际用户消息</h4>
        <pre class="stream-box">{{ r.user || '（空）' }}</pre>
        <h4 class="mt-2 font-bold">输入词元估算 / 各块预算</h4>
        <pre class="stream-box">{{
          display(r.meta?.est_tokens ?? r.meta?.token_estimates ?? r.meta?.tokens)
        }}</pre>
        <h4 class="mt-2 font-bold">参与模板（编号 / 版本 / 来源 / 摘要）</h4>
        <pre class="stream-box">{{ display(r.meta?.templates) }}</pre>
        <h4 class="mt-2 font-bold">有效文风 / 开关 / 特征指纹 / 范例引用</h4>
        <pre class="stream-box">{{ display(r.meta?.style) }}</pre>
        <el-collapse
          ><el-collapse-item title="完整请求元数据（题材来源、兼容 legacy 警告、完整块截断与摘要）">
            <pre class="stream-box">{{ display(r.meta) }}</pre>
          </el-collapse-item></el-collapse
        >
      </el-collapse-item>
    </el-collapse>
    <el-collapse class="mt-2">
      <el-collapse-item
        v-for="(s, i) in preview.slots ?? []"
        :key="i"
        :title="`${s.title} · ${s.est_tokens}t${s.truncated ? ' · 已截断' : ''}`"
      >
        <pre class="stream-box">{{ s.content || '（空）' }}</pre>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>
