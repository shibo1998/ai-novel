<script setup lang="ts">
// 设置页（规格04 §1）：API 连接 / 单价 / 阈值调整 / lint 词库 / 系统维护。
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
import type { AppSettings, MechThresholds } from '@/api/types'

const settings = ref<AppSettings | null>(null)
const keyInput = ref('')
const baseUrl = ref('')
const defaultModel = ref('')
const pricesText = ref('')
const connResult = ref<ConnTestResult | null>(null)

// 机械检查阈值（规格03 §2.4 M-05~M-11；settings.py DEFAULTS.mechcheck 九项）
// 这些是判定线与 API 的 data/settings.json 同源，改动会直接影响机检结论。
const TH_COLS: { key: keyof MechThresholds; label: string; step: number; hint: string }[] = [
  { key: 'sentence_comma_max', label: '单句逗号上限', step: 1, hint: 'M-05 一句里逗号最多几个' },
  { key: 'exclaim_per_1k_max', label: '感叹号/千字上限', step: 1, hint: 'M-06' },
  { key: 'ellipsis_per_1k_max', label: '省略号/千字上限', step: 1, hint: 'M-06' },
  {
    key: 'flat_sentence_window',
    label: '平句检测窗口',
    step: 1,
    hint: 'M-07 连续 N 句长度相近视为平',
  },
  {
    key: 'flat_sentence_ratio',
    label: '平句长度差阈值',
    step: 0.01,
    hint: 'M-07 长度差 <20% 视为平句',
  },
  { key: 'paragraph_max_chars', label: '单段字数上限', step: 10, hint: 'M-08' },
  {
    key: 'repeat_para_prefix_n',
    label: '段首重复字数',
    step: 1,
    hint: 'M-09 前 N 字相同的段落判重复',
  },
  {
    key: 'ending_window_chars',
    label: '结尾检查窗口',
    step: 20,
    hint: 'M-10 章末 N 字内不许出现收束套话',
  },
  { key: 'first_person_ratio_max', label: '第一人称占比上限', step: 0.01, hint: 'M-11' },
]
const thresholds = ref<MechThresholds | null>(null)

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
  thresholds.value = { ...(settings.value.mechcheck ?? ({} as MechThresholds)) }
  ftsMode.value = (await getFtsMode()).mode
}
onMounted(() => void load().catch(() => {}))

async function saveProviders(): Promise<void> {
  const key = keyInput.value.trim()
  // 只提交用户真正改过的字段：settings.update 是深合并（PUT 语义给什么覆盖什么），
  // key 留空时干脆不发 api_key 字段，后端自然保留原 key，不依赖掩码串匹配。
  // 掩码占位串（"已配置（留空保持不变…）"）是后端 masked() 的展示产物，绝不能当 key 写回。
  const openai: { base_url: string; api_key?: string } = { base_url: baseUrl.value.trim() }
  if (key && !key.includes('…')) openai.api_key = key
  try {
    await putSettings({ providers: { openai }, default_model: defaultModel.value.trim() })
    keyInput.value = '' // 清空输入框，回到"留空保持不变"语义（也避免密码框长期驻留明文）
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

/** 保存机械检查阈值（PUT 深合并，只送 mechcheck 一个键，不动其他设置）。 */
async function saveThresholds(): Promise<void> {
  if (!thresholds.value) return
  const cleaned: Record<string, number> = {}
  for (const c of TH_COLS) {
    const v = thresholds.value[c.key]
    if (typeof v === 'number' && Number.isFinite(v)) cleaned[c.key] = v
  }
  try {
    await putSettings({ mechcheck: cleaned })
    ElMessage.success('阈值已保存（下一轮机检生效）')
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
          <el-form-item label=" ">
            <span class="dim"
              >留空 = 保持原 key 不变（输入框显示的是掩码占位，不是真实 key）；填了新值才替换</span
            >
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
      <div class="panel">
        <h3 class="panel-title">机械检查阈值（M-05 ~ M-11，规格03 §2.4）</h3>
        <div class="dim mb-2">
          由 data/settings.json 的 mechcheck 对象驱动，改完下一轮机检即生效。小数项如 0.2 / 0.08。
        </div>
        <el-form v-if="thresholds" label-width="150px">
          <el-form-item v-for="c in TH_COLS" :key="c.key" :label="c.label">
            <el-input-number
              :model-value="Number(thresholds[c.key] ?? 0)"
              :step="c.step"
              :min="0"
              :precision="c.step < 1 ? 2 : 0"
              :controls="false"
              style="width: 130px"
              @update:model-value="
                (v: number | undefined) => {
                  if (thresholds) thresholds[c.key] = v ?? 0
                }
              "
            />
            <span class="dim ml-2">{{ c.hint }}</span>
          </el-form-item>
        </el-form>
        <div class="dim mb-2">
          装配预算 {{ settings?.assembly_budget_tokens ?? '…' }} tokens（估算=中文字符×0.75）
        </div>
        <el-button type="primary" @click="saveThresholds">保存阈值</el-button>
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
