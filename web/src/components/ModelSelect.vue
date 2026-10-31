<script setup lang="ts">
// 档位选择器（规格12 §3 / 规格13 §2.2）：先选接口，再选该接口的模型。
// 存进配置的值是 `端点@模型`；默认接口 `openai` 只存模型名，保持旧配置可读。
import { computed, onMounted, ref, watch } from 'vue'

import {
  DEFAULT_PROVIDER,
  getModels,
  getProviders,
  type ProviderOption,
} from '@/utils/modelOptions'

defineProps<{
  placeholder?: string
  disabled?: boolean
  width?: string
}>()

const value = defineModel<string>({ default: '' })

const providers = ref<ProviderOption[]>([])
const options = ref<string[]>([])
const available = ref(false)
const loading = ref(false)
const providerLoading = ref(false)

function split(raw: string): { provider: string; model: string } {
  const text = raw.trim()
  const at = text.indexOf('@')
  if (at > 0) {
    const head = text.slice(0, at).toLowerCase()
    // 只有已配置的接口名才算前缀；否则整串就是默认接口的模型名
    // （中转站存在 gemma4:31b 这类名字，不能让解析器瞎猜）。
    if (!providers.value.length || providers.value.some((p) => p.name === head)) {
      return { provider: head, model: text.slice(at + 1) }
    }
  }
  return { provider: DEFAULT_PROVIDER, model: text }
}

const parsed = computed(() => split(value.value))
const currentProvider = computed(() => parsed.value.provider)

function join(provider: string, model: string): string {
  const m = model.trim()
  if (!m) return ''
  return provider === DEFAULT_PROVIDER ? m : `${provider}@${m}`
}

async function loadModels(provider: string): Promise<void> {
  const key = provider || DEFAULT_PROVIDER
  loading.value = true
  const ids = await getModels(key)
  if (currentProvider.value !== key) return // 切走了就别覆盖当前列表
  options.value = ids
  available.value = ids.length > 0
  loading.value = false
}

// 当前值不在该接口的模型清单里：多半是模型名写错或已下线。不阻断（清单可能不全），
// 但必须提示——否则要等到真正调用时才收到一句含糊的"请求未完成"。
const missing = computed(
  () =>
    available.value && Boolean(parsed.value.model) && !options.value.includes(parsed.value.model),
)

onMounted(async () => {
  providerLoading.value = true
  providers.value = await getProviders()
  providerLoading.value = false
  await loadModels(currentProvider.value)
})

watch(currentProvider, (p) => {
  options.value = []
  available.value = false
  if (p) void loadModels(p)
})

function pickProvider(name: string): void {
  value.value = join(name, parsed.value.model)
}

function pickModel(model: string): void {
  value.value = join(currentProvider.value, model ?? '')
}
</script>

<template>
  <div class="model-select">
    <div class="model-row">
      <el-select
        :model-value="currentProvider"
        :loading="providerLoading"
        :disabled="disabled || providers.length <= 1"
        style="width: 118px"
        placeholder="接口"
        @update:model-value="pickProvider"
      >
        <el-option v-for="p in providers" :key="p.name" :label="p.label" :value="p.name" />
      </el-select>
      <el-select
        :model-value="parsed.model"
        filterable
        allow-create
        default-first-option
        clearable
        :loading="loading"
        :disabled="disabled"
        :placeholder="placeholder ?? '模型名，如 deepseek-chat'"
        :style="{ width: width ?? '220px' }"
        @update:model-value="pickModel"
      >
        <el-option v-for="id in options" :key="id" :label="id" :value="id" />
      </el-select>
    </div>
    <div v-if="missing" class="model-warn">注意：这个接口的清单里没有该模型，调用会失败</div>
    <div v-else-if="!loading && !available" class="dim model-hint">
      该接口未提供模型列表，可直接输入模型名
    </div>
  </div>
</template>

<style scoped>
.model-select {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.model-row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.model-hint {
  font-size: 12px;
}
.model-warn {
  font-size: 12px;
  color: var(--color-danger, #c45656);
}
</style>
