<script setup lang="ts">
// 设置页（规格04 §1）：API 连接 / 单价 / lint 词库 / 系统维护。
import { ElMessage } from 'element-plus'
import { onMounted, ref } from 'vue'

import {
  getFtsMode,
  getLint,
  getSettings,
  putLint,
  putSettings,
  rebuildDb,
  testConnection,
} from '@/api'
import type { ConnTestResult } from '@/api'
import type { AppSettings } from '@/api/types'

const settings = ref<AppSettings | null>(null)
const keyInput = ref('')
const baseUrl = ref('')
const defaultModel = ref('')
const pricesText = ref('')
const connResult = ref<ConnTestResult | null>(null)

const lintOptions = [
  { value: 'banned_words', label: '禁词表（M-02）' },
  { value: 'sensitive_words', label: '敏感词红线（M-04）' },
  { value: 'dialog_suffix', label: '对话后缀禁式（M-03）' },
]
const lintWhich = ref('banned_words')
const lintText = ref('')
const ftsMode = ref('')
const rebuildResult = ref('')

async function load(): Promise<void> {
  settings.value = await getSettings()
  const p = settings.value.providers?.openai ?? {}
  baseUrl.value = p.base_url ?? ''
  defaultModel.value = settings.value.default_model ?? ''
  pricesText.value = JSON.stringify(settings.value.prices ?? {}, null, 2)
  ftsMode.value = (await getFtsMode()).mode
}
onMounted(() => void load().catch(() => {}))

async function saveProviders(): Promise<void> {
  try {
    const cur = await getSettings()
    const key = keyInput.value.trim()
    // 输入框留空或仍是掩码占位则保留原 key
    const apiKey = !key || key.includes('…') ? (cur.providers?.openai?.api_key ?? '') : key
    await putSettings({
      providers: { openai: { api_key: apiKey, base_url: baseUrl.value.trim() } },
      default_model: defaultModel.value.trim(),
    })
    ElMessage.success('已保存')
    await load()
  } catch {
    // 拦截器已提示
  }
}

async function testConn(): Promise<void> {
  connResult.value = null
  try {
    connResult.value = await testConnection()
  } catch {
    // 拦截器已提示
  }
}

async function savePrices(): Promise<void> {
  let prices: unknown
  try {
    prices = JSON.parse(pricesText.value) as unknown
  } catch {
    ElMessage.error('单价不是合法 JSON')
    return
  }
  try {
    await putSettings({ prices })
    ElMessage.success('已保存')
  } catch {
    // 拦截器已提示
  }
}

async function loadLint(): Promise<void> {
  try {
    lintText.value = (await getLint(lintWhich.value)).content
  } catch {
    // 拦截器已提示
  }
}

async function saveLint(): Promise<void> {
  try {
    await putLint(lintWhich.value, lintText.value)
    ElMessage.success('词库已保存')
  } catch {
    // 拦截器已提示
  }
}

async function rebuild(): Promise<void> {
  try {
    const r = await rebuildDb()
    rebuildResult.value = JSON.stringify(r)
    ElMessage.success('重建完成')
  } catch {
    // 拦截器已提示
  }
}
</script>

<template>
  <div class="grid grid-cols-1 gap-3 lg:grid-cols-2">
    <div>
      <div class="panel">
        <h3 class="panel-title">API 连接（OpenAI 兼容协议，官方/中转站通用）</h3>
        <el-form label-width="auto">
          <el-form-item label="API key">
            <el-input
              v-model="keyInput"
              type="password"
              show-password
              :placeholder="settings?.providers?.openai?.api_key || '未配置'"
              style="width: 300px"
            />
          </el-form-item>
          <el-form-item label="base_url">
            <el-input
              v-model="baseUrl"
              style="width: 360px"
              placeholder="到 /v1 为止，如 https://your-relay.com/v1"
            />
          </el-form-item>
          <el-form-item label="默认模型">
            <el-input
              v-model="defaultModel"
              style="width: 360px"
              placeholder="留空则用各书 config.json 的 model_stages"
            />
          </el-form-item>
        </el-form>
        <div class="flex flex-wrap items-center gap-2">
          <el-button type="primary" @click="saveProviders">保存连接</el-button>
          <el-button @click="testConn">测试连接</el-button>
          <span v-if="connResult" class="text-xs">
            <el-tag :type="connResult.ok ? 'success' : 'danger'" size="small">
              {{ connResult.ok ? 'OK' : '失败' }}
            </el-tag>
            {{ connResult.ok ? `${connResult.model}：${connResult.reply}` : connResult.error }}
          </span>
        </div>
        <div class="dim mt-1">
          base_url 填到 /v1 为止（例：https://your-relay.com/v1），不要带 /chat/completions
        </div>
      </div>
      <div class="panel">
        <h3 class="panel-title">单价（分/百万token）</h3>
        <el-input v-model="pricesText" type="textarea" :rows="8" class="mono" />
        <el-button class="mt-2" @click="savePrices">保存单价</el-button>
      </div>
    </div>
    <div>
      <div class="panel">
        <h3 class="panel-title">lint 词库</h3>
        <div class="flex flex-wrap gap-2">
          <el-select v-model="lintWhich" style="width: 230px">
            <el-option v-for="o in lintOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <el-button @click="loadLint">载入</el-button>
          <el-button type="primary" @click="saveLint">保存</el-button>
        </div>
        <el-input v-model="lintText" type="textarea" :rows="14" class="mono mt-2" />
      </div>
      <div class="panel">
        <h3 class="panel-title">系统维护</h3>
        <div class="flex flex-wrap items-center gap-2">
          <el-button @click="rebuild">从文件重建 SQLite 索引</el-button>
          <span class="dim mono">{{ rebuildResult }}</span>
        </div>
        <div class="dim mt-1">FTS 模式：{{ ftsMode || '…' }}（trigram 不可用时自动 bigram）</div>
      </div>
    </div>
  </div>
</template>
