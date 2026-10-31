<script setup lang="ts">
// 设置页（规格04 §1）：API 连接 / 单价 / 阈值调整 / lint 词库 / 系统维护。
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, onUnmounted, ref } from 'vue'

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
import type { AppSettings, MechThresholds, ModelStage } from '@/api/types'
import { MODEL_STAGES } from '@/api/types'
import { modelStageLabel } from '@/utils/status'
import { resetModelCaches } from '@/utils/modelOptions'
import ModelSelect from '@/components/ModelSelect.vue'
import { registerDirtyGuard } from '@/utils/editing'
import { hasPendingWrite } from '@/utils/tasks'

const settings = ref<AppSettings | null>(null)

/** 一个可编辑的接口行（规格13 §3）。api_key 留空 = 保持原密钥不变。 */
interface ProviderRow {
  name: string
  label: string
  base_url: string
  api_key: string
  has_key: boolean
  removed?: boolean
  isNew?: boolean
}
const providers = ref<ProviderRow[]>([])
const newProviderName = ref('')
const fallbackModel = ref('')
// 全局五档默认（规格12 §2.2）：留空即该档继续向下探到兜底模型
const stageDefaults = ref<Partial<Record<ModelStage, string>>>({})
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
const loadedWhich = ref('')
const lintEtag = ref<string | null>(null)
const lintBaseline = ref('')
const lintLoading = ref(false)
const lintSaving = ref(false)
const lintError = ref('')
let lintRequest = 0
let lintGeneration = 0
const lintDirty = computed(
  () => Boolean(loadedWhich.value) && lintText.value !== lintBaseline.value,
)
const lintTargetLabel = computed(
  () => lintOptions.find((o) => o.value === lintWhich.value)?.label ?? lintWhich.value,
)
const ftsMode = ref('')
const rebuildResult = ref('')
const rebuildBusy = ref(false)

async function load(): Promise<void> {
  settings.value = await getSettings()
  const raw = settings.value.providers ?? {}
  providers.value = Object.keys(raw)
    .sort()
    .map((name) => ({
      name,
      label: raw[name]?.label ?? '',
      base_url: raw[name]?.base_url ?? '',
      api_key: '',
      has_key: Boolean(raw[name]?.has_key),
    }))
  fallbackModel.value = settings.value.fallback_model ?? ''
  stageDefaults.value = { ...(settings.value.model_defaults ?? {}) }
  pricesText.value = JSON.stringify(settings.value.prices ?? {}, null, 2)
  thresholds.value = { ...(settings.value.mechcheck ?? ({} as MechThresholds)) }
  ftsMode.value = (await getFtsMode()).mode
}
onMounted(() => {
  void load().catch(() => {})
  void loadLint('banned_words')
})

function addProvider(): void {
  const name = newProviderName.value.trim().toLowerCase()
  if (!/^[a-z][a-z0-9_-]{0,23}$/.test(name)) {
    ElMessage.warning('接口名要用小写字母开头，只含小写字母/数字/_/-，最长 24 字符')
    return
  }
  if (providers.value.some((p) => p.name === name)) {
    ElMessage.warning('接口名已存在')
    return
  }
  providers.value.push({ name, label: '', base_url: '', api_key: '', has_key: false, isNew: true })
  newProviderName.value = ''
}

function removeProvider(row: ProviderRow): void {
  if (row.name === 'openai') {
    ElMessage.warning('默认接口不能删除；它会兜住所有没写接口前缀的模型值')
    return
  }
  if (row.isNew) {
    providers.value = providers.value.filter((p) => p !== row)
    return
  }
  row.removed = true
}

async function saveProviders(): Promise<void> {
  const payload: Record<string, { base_url: string; label?: string; api_key?: string }> = {}
  const removals: string[] = []
  for (const row of providers.value) {
    if (row.removed) {
      removals.push(row.name)
      continue
    }
    const entry: { base_url: string; label?: string; api_key?: string } = {
      base_url: row.base_url.trim(),
      label: row.label.trim(),
    }
    // 只提交用户真正改过的密钥：留空 = 保持原密钥。掩码占位串绝不能写回。
    const key = row.api_key.trim()
    if (key && !key.includes('…')) entry.api_key = key
    payload[row.name] = entry
  }
  if (!Object.keys(payload).length) {
    ElMessage.warning('至少要保留一个接口')
    return
  }
  try {
    await putSettings({
      providers: payload,
      remove_providers: removals,
      fallback_model: fallbackModel.value.trim(),
      model_defaults: stageDefaults.value,
    })
    resetModelCaches() // 接口变了，选择器的缓存必须失效
    ElMessage.success('已保存')
    await load()
  } catch {
    // 拦截器已提示
  }
}

async function testConn(row: ProviderRow): Promise<void> {
  connResult.value = null
  try {
    connResult.value = await testConnection({ provider: row.name })
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

async function confirmLintLeave(): Promise<'save' | 'discard' | 'cancel'> {
  if (!lintDirty.value) return 'discard'
  if (lintSaving.value) return 'cancel'
  try {
    const result = await ElMessageBox.confirm(
      `${lintTargetLabel.value}有未保存修改。保存后继续，或放弃修改？`,
      '词库修改未保存',
      {
        type: 'warning',
        distinguishCancelAndClose: true,
        confirmButtonText: '保存后继续',
        cancelButtonText: '放弃修改',
        showClose: true,
      },
    )
    return result === 'confirm' ? 'save' : 'discard'
  } catch (action) {
    return action === 'cancel' ? 'discard' : 'cancel'
  }
}
const unregisterDirty = registerDirtyGuard(
  async () => {
    const decision = await confirmLintLeave()
    if (decision === 'save') return (await saveLint()) ? 'save' : 'cancel'
    return decision
  },
  () => lintDirty.value,
)
onUnmounted(unregisterDirty)

/** 只有捕获的 which + 代次 + 请求序号仍匹配时才允许响应改界面。 */
async function loadLint(which = lintWhich.value, force = false): Promise<boolean> {
  if (!force && lintSaving.value) return false
  const request = ++lintRequest
  const generation = lintGeneration
  lintWhich.value = which
  loadedWhich.value = ''
  lintEtag.value = null
  lintLoading.value = true
  lintError.value = ''
  try {
    const response = await getLint(which)
    if (request !== lintRequest || generation !== lintGeneration || which !== lintWhich.value)
      return false
    if (!response.data.exists)
      throw new Error('词库文件缺失；请按项目维护方式恢复种子文件，不能当作空词库覆盖')
    if (!response.etag) throw new Error('服务未返回 ETag，已禁用无版本保护的词库保存')
    loadedWhich.value = which
    lintText.value = response.data.content
    lintBaseline.value = response.data.content
    lintEtag.value = response.etag
    return true
  } catch (error) {
    if (request === lintRequest && generation === lintGeneration && which === lintWhich.value) {
      lintError.value =
        error instanceof Error ? error.message : '词库加载失败，可重试；原输入仍可复制'
    }
    return false
  } finally {
    if (request === lintRequest && generation === lintGeneration) lintLoading.value = false
  }
}

async function switchLint(value: string): Promise<void> {
  if (value === lintWhich.value) return
  const decision = await confirmLintLeave()
  if (decision === 'cancel') return
  if (decision === 'save' && !(await saveLint())) return
  lintGeneration++
  lintRequest++
  lintWhich.value = value
  lintText.value = ''
  lintBaseline.value = ''
  await loadLint(value, true)
}

async function saveLint(): Promise<boolean> {
  const which = loadedWhich.value
  const etag = lintEtag.value
  if (
    !which ||
    which !== lintWhich.value ||
    !etag ||
    lintLoading.value ||
    lintSaving.value ||
    lintError.value
  )
    return false
  if (!lintDirty.value) return true
  try {
    await ElMessageBox.confirm(
      `将保存「${which}」的词库内容（${lintBaseline.value.length} → ${lintText.value.length} 字符）。其他词库不受影响。`,
      '确认词库变更',
      { type: 'warning' },
    )
  } catch {
    return false
  }
  const text = lintText.value
  const generation = lintGeneration
  lintSaving.value = true
  try {
    await putLint(which, text, etag)
    if (generation !== lintGeneration || which !== lintWhich.value) return true
    // 成功回执后重新读取同一资源获取新 ETag；旧响应无权清空新目标草稿。
    lintBaseline.value = text
    await loadLint(which, true)
    ElMessage.success('词库已保存')
    return true
  } catch {
    return false // 412 保留 text/etag，绝不偷偷刷新基线重试
  } finally {
    lintSaving.value = false
  }
}

async function rebuild(): Promise<void> {
  if (rebuildBusy.value || hasPendingWrite('')) return
  try {
    await ElMessageBox.confirm(
      '根据活动书稿重建章节/状态及全文索引；不改书稿文件，保留现有生成日志、快照和审校记录；不能从文件恢复已经丢失的运行历史。',
      '确认重建索引',
      { type: 'warning' },
    )
  } catch {
    return
  }
  rebuildBusy.value = true
  const started = performance.now()
  try {
    const r = await rebuildDb()
    const elapsed = ((performance.now() - started) / 1000).toFixed(1)
    rebuildResult.value = `书 ${String(r.books ?? 0)} 本 · 章节 ${String(r.chapters ?? 0)} 章 · 全文索引 ${String(r.fts_reindexed ?? '未知')} · 停用书名 ${String(r.dropped ?? 0)} · 请求耗时 ${elapsed} 秒`
    ElMessage.success('重建完成')
  } catch {
    // 保留上次结果；错误记录给出“等待已有任务完成”等下一步。
  } finally {
    rebuildBusy.value = false
  }
}
</script>

<template>
  <div class="grid grid-cols-1 gap-3 lg:grid-cols-2">
    <div>
      <div class="panel">
        <h3 class="panel-title">接口连接（OpenAI 兼容协议，官方与中转站通用）</h3>
        <div
          v-for="row in providers"
          :key="row.name"
          class="provider-card"
          :class="{ 'provider-removed': row.removed }"
        >
          <div class="provider-head">
            <b class="mono">{{ row.name }}</b>
            <el-input v-model="row.label" placeholder="显示名（可空）" style="width: 150px" />
            <el-tag v-if="row.name === 'openai'" size="small" type="info">默认接口</el-tag>
            <el-tag v-if="row.removed" size="small" type="danger">保存后删除</el-tag>
            <span class="provider-spacer" />
            <el-button size="small" :disabled="row.removed" @click="testConn(row)"
              >测试连接</el-button
            >
            <el-button size="small" :disabled="row.name === 'openai'" @click="removeProvider(row)">
              删除
            </el-button>
          </div>
          <div class="provider-fields">
            <label>接口地址</label>
            <el-input v-model="row.base_url" style="width: 340px" placeholder="到 /v1 为止" />
            <label>密钥</label>
            <el-input
              v-model="row.api_key"
              type="password"
              show-password
              style="width: 300px"
              :placeholder="row.has_key ? '已配置（留空保持不变…）' : '未配置'"
            />
          </div>
        </div>
        <div class="flex flex-wrap items-center gap-2 mt-2">
          <el-input
            v-model="newProviderName"
            placeholder="新接口名，如 backup"
            style="width: 170px"
            @keyup.enter="addProvider"
          />
          <el-button @click="addProvider">添加接口</el-button>
          <el-button type="primary" @click="saveProviders">保存连接</el-button>
          <span v-if="connResult" class="text-xs">
            <el-tag :type="connResult.ok ? 'success' : 'danger'" size="small">
              {{ connResult.ok ? '连通' : '失败' }}
            </el-tag>
            {{ connResult.provider ? `[${connResult.provider}] ` : ''
            }}{{ connResult.ok ? `${connResult.model}：${connResult.reply}` : connResult.error }}
          </span>
        </div>
        <el-form label-width="auto" class="mt-3">
          <el-form-item label="兜底模型">
            <ModelSelect v-model="fallbackModel" width="340px" />
          </el-form-item>
          <el-form-item label=" ">
            <span class="dim"
              >只在书级档位与下面的全局档位都没配时才用。它<strong>不再</strong>覆盖各书配置文件里的档位设置。</span
            >
          </el-form-item>
          <el-form-item
            v-for="s in MODEL_STAGES"
            :key="`global-${s}`"
            :label="`全局${modelStageLabel(s)}档`"
          >
            <ModelSelect
              :model-value="stageDefaults[s] ?? ''"
              width="300px"
              placeholder="留空则用兜底模型"
              @update:model-value="(v: string) => (stageDefaults[s] = v)"
            />
          </el-form-item>
        </el-form>
        <div class="dim mt-1">
          接口地址填到 /v1 为止（例：https://your-relay.com/v1），不要带 /chat/completions。
          档位里选哪个接口就走哪套 URL 和密钥；密钥只存在本机 data/settings.json，不会写进书目录。
        </div>
      </div>
      <div class="panel">
        <h3 class="panel-title">单价（分 / 百万词元）</h3>
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
        <h3 class="panel-title">词库</h3>
        <div class="flex flex-wrap gap-2">
          <el-select
            :model-value="lintWhich"
            style="width: 230px"
            :disabled="lintSaving"
            @update:model-value="switchLint"
          >
            <el-option v-for="o in lintOptions" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <el-button :loading="lintLoading" @click="loadLint()">重新载入</el-button>
          <el-button
            type="primary"
            :loading="lintSaving"
            :disabled="lintLoading || !loadedWhich || !lintEtag || Boolean(lintError)"
            @click="saveLint"
            >保存</el-button
          >
        </div>
        <el-alert v-if="lintError" type="error" :closable="false" class="mt-2" :title="lintError"
          ><el-button @click="loadLint(lintWhich, true)">重试</el-button></el-alert
        >
        <el-input
          v-model="lintText"
          type="textarea"
          :rows="14"
          class="mono mt-2"
          :disabled="lintLoading || lintSaving || Boolean(lintError)"
        />
        <div class="dim mt-1">
          {{
            lintDirty
              ? '有未保存修改；切换或离开前会确认。'
              : loadedWhich
                ? `已载入 ${loadedWhich}，ETag 已绑定`
                : '尚未成功载入，禁止保存'
          }}
        </div>
      </div>
      <div class="panel">
        <h3 class="panel-title">系统维护</h3>
        <div class="flex flex-wrap items-center gap-2">
          <el-button :loading="rebuildBusy" :disabled="hasPendingWrite('')" @click="rebuild"
            >从文件重建 SQLite 索引</el-button
          >
          <span v-if="hasPendingWrite('')" class="dim"
            >本会话有结果待确认的全局写任务，暂不可重建。</span
          >
          <span class="dim">{{ rebuildResult }}</span>
        </div>
        <div class="dim mt-1">FTS 模式：{{ ftsMode || '…' }}（trigram 不可用时自动 bigram）</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.provider-card {
  border: 1px solid var(--color-border-tertiary, #dcdfe6);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 8px;
}
.provider-removed {
  opacity: 0.55;
}
.provider-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.provider-spacer {
  flex: 1;
}
.provider-fields {
  display: grid;
  grid-template-columns: 80px auto;
  align-items: center;
  gap: 6px 10px;
  margin-top: 8px;
}
.provider-fields label {
  font-size: 13px;
  color: var(--color-text-secondary, #606266);
}
</style>
