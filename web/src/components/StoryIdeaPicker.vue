<script setup lang="ts">
// 规格11 §4：首卷的可选灵感入口；题材只读、三卡同字段对比，选用不启动生产。
import { computed, onUnmounted, watch } from 'vue'
import type { StoryIdeaForm } from '@/api/storyIdeas'
import { useStoryIdeas } from '@/composables/useStoryIdeas'
import { storyTextLength } from '@/utils/storyIdeas'
const props = defineProps<{ book: string; form: StoryIdeaForm; disabled: boolean }>()
const emit = defineEmits<{ blocking: [book: string, blocked: boolean] }>()
const ideas = useStoryIdeas(
  computed(() => props.book),
  props.form,
  computed(() => props.disabled),
)
const {
  context,
  batch,
  preferences,
  contextLoading,
  pending,
  modelWaiting,
  selecting,
  selected,
  error,
  notice,
  unknown,
  prepareBlocked,
  stale,
  canGenerate,
  canSelect,
  elapsed,
} = ideas
watch(prepareBlocked, (value) => emit('blocking', props.book, value), {
  immediate: true,
  flush: 'sync',
})
onUnmounted(() => emit('blocking', props.book, false))
</script>

<template>
  <section
    class="story-idea-picker"
    aria-label="首卷同题材故事候选"
    :aria-busy="pending || selecting"
  >
    <header class="volume-section-heading">
      <div>
        <span class="eyebrow">可选灵感 · 不改正式书稿</span>
        <h3>没想法？AI 给我 3 个故事方案</h3>
      </div>
      <span class="volume-state">{{
        context
          ? `当前题材：${context.genre}`
          : contextLoading
            ? '正在读取建书题材…'
            : '题材尚未读取'
      }}</span>
    </header>
    <p class="volume-hint">
      在建书时选定的题材内，比较不同主角与冲突。无需先填一句话故事；也可以跳过，直接手填下方表单。
    </p>
    <p v-if="context && !context.eligible" class="volume-alert" role="status">
      {{ context.reason || '本书已不适用首卷故事候选；请继续现有卷与任务。' }}
    </p>
    <template v-if="!context || context.eligible">
      <label class="volume-field story-preferences"
        >创意偏好 <span class="volume-hint">可选；留空也能生成</span
        ><textarea
          v-model="preferences"
          rows="2"
          maxlength="2000"
          placeholder="例如：偏轻松、小人物主角，不想写系统流。"
          :disabled="pending || selecting || disabled"
          aria-describedby="story-preferences-help"
        /><span id="story-preferences-help" class="volume-hint"
          >{{ storyTextLength(preferences) }} / 2000 字 · 题材来自本书配置，不在这里切换</span
        ></label
      >
      <div class="volume-actions">
        <el-button
          type="primary"
          data-action="generate"
          :disabled="!canGenerate"
          @click="ideas.generate"
          >{{
            pending
              ? '正在核对 / 等待…'
              : unknown
                ? '确认再次付费并重试'
                : batch
                  ? '换一批 · 再调用一次'
                  : 'AI 给我 3 个故事方案'
          }}</el-button
        >
        <el-button v-if="modelWaiting" data-action="stop" @click="ideas.stop"
          >停止等待（不取消模型）</el-button
        >
        <span class="volume-hint">一次点击调用一次模型，可能产生费用；不会自动准备本卷。</span>
      </div>
    </template>
    <p v-if="pending" class="volume-alert" role="status">
      {{
        contextLoading
          ? '正在核对题材与上下文版本。'
          : modelWaiting
            ? `等待模型响应 · 已等待 ${elapsed} 秒。`
            : '正在等待你确认再次调用；不会自动重发。'
      }}
      <template v-if="modelWaiting">{{
        elapsed >= 300
          ? '等待较久，建议检查模型服务。'
          : elapsed >= 60
            ? '仍在等待响应，耗时取决于模型服务。'
            : '不保证耗时，也不显示虚构进度。'
      }}</template>
    </p>
    <p v-if="error" class="volume-alert error" role="alert">
      {{ error }}
      <button
        type="button"
        class="volume-text-button"
        :disabled="contextLoading || pending || selecting"
        @click="ideas.refreshContext"
      >
        重新读取题材与适用范围（不调用模型）
      </button>
    </p>
    <p v-if="notice" class="volume-alert" role="status">{{ notice }}</p>
    <p v-else-if="unknown" class="volume-alert" role="status">
      模型可能仍在执行并产生费用；候选无法恢复或取回。再次调用需确认可能重复计费，准备本卷暂不可用。
    </p>
    <p v-if="stale" class="volume-alert">
      旧卡仅供阅读：书的上下文已变化或不再适用，不能选用。手填内容保留。
    </p>
    <div v-if="batch" :key="batch.request_id" class="story-idea-grid">
      <article
        v-for="(idea, index) in batch.ideas"
        :key="index"
        class="story-idea-card"
        :class="{ selected: selected === index, stale }"
        :style="{ '--stagger': `${index * 40}ms` }"
        :aria-label="`故事方案 ${index + 1}：${idea.title}`"
      >
        <header>
          <span class="eyebrow">方案 {{ String(index + 1).padStart(2, '0') }} · 暂定故事名</span>
          <h4>{{ idea.title }}</h4>
        </header>
        <dl>
          <div>
            <dt>一句话故事</dt>
            <dd class="story-logline">{{ idea.logline }}</dd>
          </div>
          <div>
            <dt>简介</dt>
            <dd>{{ idea.synopsis }}</dd>
          </div>
          <div>
            <dt>主角</dt>
            <dd>{{ idea.protagonist }}</dd>
          </div>
          <div>
            <dt>核心冲突</dt>
            <dd>{{ idea.core_conflict }}</dd>
          </div>
          <div>
            <dt>创作卖点</dt>
            <dd>{{ idea.selling_point }}</dd>
          </div>
          <div>
            <dt>首卷目标</dt>
            <dd>{{ idea.volume_goal }}</dd>
          </div>
          <div>
            <dt>卷末钩子</dt>
            <dd>{{ idea.volume_hook }}</dd>
          </div>
        </dl>
        <footer>
          <el-button
            data-action="select"
            :disabled="!canSelect"
            :aria-pressed="selected === index"
            @click="ideas.select(index)"
            >{{
              selecting ? '正在核对选用…' : selected === index ? '已填入下方表单' : '选用这个故事'
            }}</el-button
          >
        </footer>
      </article>
    </div>
    <p v-if="batch" class="volume-hint">
      卖点是创作描述，不代表热门榜单或市场验证。候选只在当前页面，刷新不承诺恢复；正式书名、题材与期望章数保持不变。
    </p>
    <p v-if="!batch && !pending && context?.eligible" class="story-idea-empty">
      还没有候选。先让 AI 提供起点，或直接写下你的故事，两条路都由你决定。
    </p>
    <button
      v-if="!context && !error && !contextLoading"
      type="button"
      class="volume-text-button"
      @click="ideas.refreshContext"
    >
      读取本书题材与适用范围
    </button>
  </section>
</template>
