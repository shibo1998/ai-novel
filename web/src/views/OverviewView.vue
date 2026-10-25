<script setup lang="ts">
// 书总览（规格04 §1）：档案导航 AI 生成、圣经/文风/台账/角色、章节列表与卷末审计。
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import {
  adoptArchive,
  aiGenerate,
  analyzeStyleF1,
  createCharacter,
  getBible,
  getCharacter,
  getOutlineFull,
  getOverview,
  getPlotPoints,
  getStyle,
  listCharactersFull,
  putBible,
  putCharacter,
  putOutlineFull,
  putPlotPoints,
  putStyleCard,
  putStyleCorpus,
  putStyleEnabled,
  runAudit,
} from '@/api'
import type { Overview } from '@/api/types'
import { useBookStore } from '@/stores/book'
import { parseMdTable, toMdTable } from '@/utils/mdtable'
import { tagType } from '@/utils/status'

const route = useRoute()
const router = useRouter()
const store = useBookStore()

const name = computed(() => String(route.params.name ?? ''))
const ov = ref<Overview | null>(null)
const bible = ref<Record<string, string>>({})
const ppText = ref('')
const outlineText = ref('')
const styleCorpus = ref('')
const styleCard = ref('')
const styleEnabled = ref(false)
const newChar = ref({ name: '', role: '龙套' })
const charRoles = ['龙套', '重要配角', '反派', '女主', '主角']

const PP_COLS = ['编号', '内容', '埋设章', '推进章', '预计回收章', '状态', '备注']
const OC_COLS = ['章', '卷', '本章目标', '冲突来源', '爽点/揭露', '钩子类型', '重要度★']
const G_TARGETS: Record<string, 'bible' | 'characters' | 'outline'> = {
  G1: 'bible',
  G2: 'bible',
  G3: 'characters',
  G4: 'characters',
  G5: 'outline',
}
const G_LABELS: Record<string, string> = {
  G1: 'G1 世界观',
  G2: 'G2 力量体系',
  G3: 'G3 角色卡',
  G4: 'G4 关系网',
  G5: 'G5 全书大纲',
}

// AI 生成预览（生成 → 人工改定 → 采纳落盘，AI 永不静默覆盖）
const aiDialog = ref(false)
const aiText = ref('')
const aiTarget = ref<'bible' | 'characters' | 'outline'>('bible')
const aiPid = ref('')
const aiDigest = ref('')

// 角色编辑
const charDialog = ref(false)
const charEditName = ref('')
const charEditFm = ref('')
const charEditBody = ref('')

const gotoNo = ref(1)
const auditNo = ref<number | undefined>(undefined)

async function load(): Promise<void> {
  store.setCurrentBook(name.value)
  const o = await getOverview(name.value)
  ov.value = o
  bible.value = await getBible(name.value)
  ppText.value = toMdTable(PP_COLS, await getPlotPoints(name.value))
  outlineText.value = toMdTable(OC_COLS, o.outline_rows)
  const st = await getStyle(name.value)
  styleCorpus.value = st.corpus
  styleCard.value = st.card
  styleEnabled.value = st.enabled
}
watch(name, () => void load().catch(() => {}), { immediate: true })

function gotoWorkbench(no: number): void {
  router.push(`/book/${encodeURIComponent(name.value)}/chapter/${no}`)
}

// —— G 链：AI 生成档案 → 预览改定 → 采纳 ——
async function ask(label: string, value = ''): Promise<string> {
  try {
    const r = await ElMessageBox.prompt(label, 'AI 生成档案', { inputValue: value })
    return r.value ?? ''
  } catch {
    return ''
  }
}

async function aiArchive(pid: string): Promise<void> {
  const vars: Record<string, string> = {}
  if (pid === 'G5') {
    vars['目标总章数'] = (await ask('目标总章数', '120')) || '120'
    vars['单章字数区间'] = '2200-2800'
    vars['logline'] = await ask('一句话故事（主角为了X必须做Y但障碍是Z）')
  }
  if (pid === 'G1' || pid === 'G2') {
    try {
      const b = await getBible(name.value)
      vars['既有设定摘要'] = b['基础设定'] ?? ''
      vars['基础设定'] = b['基础设定'] ?? ''
      vars['既有力量体系'] = b['力量体系'] ?? ''
    } catch {
      // 无圣经时留空
    }
    vars['题材'] = '都市'
    vars['故事核'] = await ask('故事核（一句话）')
    vars['对标书'] = ''
  }
  if (pid === 'G3' || pid === 'G4') {
    try {
      const cs = await listCharactersFull(name.value)
      vars['角色卡合集'] = cs.map((c) => c._body ?? '').join('\n\n')
      vars['世界观摘要'] = '见圣经'
    } catch {
      // 无角色时留空
    }
    vars['角色需求清单'] =
      (await ask('角色需求清单（每行：名字+定位+要求）', '林寒+主角+底层逆袭')) || ''
  }
  try {
    const r = await aiGenerate(name.value, pid, vars)
    aiTarget.value = G_TARGETS[pid] ?? 'bible'
    aiPid.value = pid
    aiDigest.value = r.digest
    aiText.value = r.text
    aiDialog.value = true
    ElMessage.success('已生成，请检查修改后再采纳')
  } catch {
    // 拦截器已提示
  }
}

async function adopt(): Promise<void> {
  try {
    const r = await adoptArchive(name.value, aiTarget.value, aiText.value)
    ElMessage.success({ message: `已采纳落盘：${JSON.stringify(r)}`, duration: 8000 })
    aiDialog.value = false
    await load()
  } catch {
    // 拦截器已提示
  }
}

// —— Bible / 台账 / 大纲 ——
async function saveBible(): Promise<void> {
  try {
    await putBible(name.value, bible.value)
    ElMessage.success('圣经已保存')
  } catch {
    // 拦截器已提示
  }
}

async function savePP(): Promise<void> {
  try {
    await putPlotPoints(name.value, parseMdTable(ppText.value))
    ElMessage.success('台账已保存')
  } catch (e) {
    if (e instanceof Error && e.message === '表格格式错误') ElMessage.error('表格格式错误')
    // 其余（后端状态迁移校验）由拦截器提示
  }
}

async function saveOutlineRows(): Promise<void> {
  try {
    const rows = parseMdTable(outlineText.value)
    const od = await getOutlineFull(name.value)
    od.chapter_rows = rows
    await putOutlineFull(name.value, od)
    ElMessage.success('大纲已保存')
    await load()
  } catch (e) {
    if (e instanceof Error && e.message === '表格格式错误') ElMessage.error('表格格式错误')
    // 其余由拦截器提示
  }
}

// —— 角色 ——
async function createChar(): Promise<void> {
  const n = newChar.value.name.trim()
  if (!n) {
    ElMessage.warning('填新角色名')
    return
  }
  try {
    await createCharacter(name.value, n, newChar.value.role)
    ElMessage.success('已建档')
    newChar.value.name = ''
    await load()
  } catch {
    // 拦截器已提示
  }
}

async function editChar(n: string): Promise<void> {
  try {
    const c = await getCharacter(name.value, n)
    charEditName.value = n
    const fm = { ...c }
    delete fm._body
    charEditFm.value = JSON.stringify(fm, null, 2)
    charEditBody.value = c._body ?? ''
    charDialog.value = true
  } catch {
    // 拦截器已提示
  }
}

async function saveChar(): Promise<void> {
  let fm: Record<string, unknown>
  try {
    fm = JSON.parse(charEditFm.value) as Record<string, unknown>
  } catch {
    ElMessage.error('frontmatter 不是合法 JSON')
    return
  }
  fm._body = charEditBody.value
  try {
    await putCharacter(name.value, charEditName.value, fm)
    ElMessage.success('角色卡已保存')
    charDialog.value = false
  } catch {
    // 拦截器已提示
  }
}

// —— 文风系统（M5） ——
async function saveCorpus(): Promise<void> {
  try {
    await putStyleCorpus(name.value, styleCorpus.value)
    ElMessage.success('语料已保存')
  } catch {
    // 拦截器已提示
  }
}

async function analyzeStyle(): Promise<void> {
  if (!styleCorpus.value.trim()) {
    ElMessage.warning('先填语料')
    return
  }
  try {
    const r = await analyzeStyleF1(name.value)
    styleCard.value = r.card
    ElMessage.success('特征卡已生成，请人工改定后保存并启用')
  } catch {
    // 拦截器已提示
  }
}

async function saveCard(): Promise<void> {
  try {
    await putStyleCard(name.value, styleCard.value)
    ElMessage.success('特征卡已保存')
  } catch {
    // 拦截器已提示
  }
}

async function toggleStyle(on: boolean | string | number | undefined): Promise<void> {
  const enabled = Boolean(on)
  try {
    await putStyleEnabled(name.value, enabled)
    ElMessage.success(enabled ? '文风注入已启用（下次生成生效）' : '已停用')
  } catch {
    styleEnabled.value = !enabled // 回滚开关
  }
}

// —— 卷末审计（V6） ——
async function audit(): Promise<void> {
  const n = auditNo.value
  if (!n) {
    ElMessage.warning('填审计截止章号')
    return
  }
  try {
    const r = await runAudit(name.value, n)
    ElMessage.success({
      message: `审计完成：伏笔回收率 ${r.foreshadow_pay_rate}，报告已存 ${r.report_path}`,
      duration: 10000,
    })
  } catch {
    // 拦截器已提示
  }
}
</script>

<template>
  <div class="panel">
    <div class="flex flex-wrap items-center gap-2">
      <span class="text-[15px] font-bold">{{ ov?.meta.title || name }}</span>
      <span class="flex-1"></span>
      <span class="dim">
        全书成本 {{ ((ov?.cost_total.cost_cents ?? 0) / 100).toFixed(2) }} 元 /
        {{ ov?.cost_total.calls ?? 0 }} 次调用
      </span>
      <el-tag v-if="(ov?.open_conflicts ?? 0) > 0" type="danger" size="small">
        待裁决 {{ ov?.open_conflicts }} 条
      </el-tag>
      <el-tag v-else type="success" size="small">裁决队列空</el-tag>
    </div>
    <div class="dim mt-1">
      {{ ov?.meta.genre }} · 目标{{ ((ov?.meta.target_words ?? 0) / 10000).toFixed(0) }}万字 ·
      {{ ov?.meta.platform }} · {{ ov?.meta.status }}
    </div>
  </div>

  <div class="grid grid-cols-1 gap-3 xl:grid-cols-2">
    <div>
      <!-- 档案导航 · AI 生成 -->
      <div class="panel">
        <h3 class="panel-title">档案导航 · AI 生成</h3>
        <div class="flex flex-wrap gap-2">
          <el-button v-for="(label, pid) in G_LABELS" :key="pid" @click="aiArchive(pid)">
            {{ label }}
          </el-button>
        </div>
        <div class="dim mt-2">流程：生成 → diff 预览 → 人工改定 → 采纳落盘（AI 永不静默覆盖）</div>
      </div>

      <!-- 世界观圣经 -->
      <div class="panel">
        <h3 class="panel-title">世界观圣经（固定 H2 节）</h3>
        <div v-for="k in Object.keys(bible)" :key="k" class="mb-2">
          <div class="dim mb-1">## {{ k }}</div>
          <el-input v-model="bible[k]" type="textarea" :rows="4" />
        </div>
        <el-button @click="saveBible">保存圣经</el-button>
      </div>

      <!-- 文风系统 -->
      <div class="panel">
        <h3 class="panel-title">文风系统（M5）</h3>
        <div class="dim">
          把你喜欢的书的正文段落（≥500字，建议 3000-10000 字）贴进语料 → 分析生成特征卡 → 人工改定 →
          启用后每次生成都注入该文风
        </div>
        <div class="dim mt-2 mb-1">语料（你认可的目标文风）</div>
        <el-input
          v-model="styleCorpus"
          type="textarea"
          :rows="6"
          placeholder="粘贴你喜欢的小说正文片段…"
        />
        <div class="mt-2 flex flex-wrap items-center gap-2">
          <el-button @click="saveCorpus">保存语料</el-button>
          <el-button type="primary" @click="analyzeStyle">分析生成特征卡（F1）</el-button>
          <el-switch v-model="styleEnabled" @change="toggleStyle" />
          <span class="dim">启用文风注入</span>
        </div>
        <div class="dim mt-2 mb-1">特征卡（可手改，改完保存）</div>
        <el-input v-model="styleCard" type="textarea" :rows="8" />
        <el-button class="mt-2" @click="saveCard">保存特征卡</el-button>
      </div>

      <!-- 伏笔台账 -->
      <div class="panel">
        <h3 class="panel-title">伏笔台账</h3>
        <div class="dim">状态只允许 planted→building→paid 单向；paid 后不可回退</div>
        <el-input v-model="ppText" type="textarea" :rows="10" class="mono mt-1" />
        <div class="mt-2 flex items-center gap-2">
          <el-button @click="savePP">保存台账</el-button>
          <span class="dim">直接编辑表格文本（Markdown 表格）</span>
        </div>
      </div>

      <!-- 角色档案 -->
      <div class="panel">
        <h3 class="panel-title">角色档案</h3>
        <div class="flex flex-wrap gap-2">
          <el-button v-for="n in ov?.characters ?? []" :key="n" @click="editChar(n)">{{
            n
          }}</el-button>
          <span v-if="!ov?.characters.length" class="dim small">（无角色）</span>
        </div>
        <div class="mt-2 flex flex-wrap items-center gap-2">
          <el-input v-model="newChar.name" placeholder="新角色名" style="width: 130px" />
          <el-select v-model="newChar.role" style="width: 110px">
            <el-option v-for="r in charRoles" :key="r" :label="r" :value="r" />
          </el-select>
          <el-button @click="createChar">建档</el-button>
        </div>
      </div>
    </div>

    <div>
      <!-- 章节列表 -->
      <div class="panel">
        <h3 class="panel-title">章节列表</h3>
        <div class="mb-2 flex flex-wrap items-center gap-2">
          <el-input-number v-model="gotoNo" :min="1" style="width: 100px" />
          <el-button type="primary" @click="gotoWorkbench(gotoNo)">进入工作台</el-button>
          <span style="width: 16px"></span>
          <el-input-number
            v-model="auditNo"
            :min="1"
            placeholder="审计至第N章"
            style="width: 150px"
          />
          <el-button @click="audit">卷末审计（V6）</el-button>
        </div>
        <el-table :data="ov?.chapters ?? []" size="small" max-height="520">
          <el-table-column prop="no" label="章" width="60" />
          <el-table-column label="状态" width="140">
            <template #default="{ row }">
              <el-tag :type="tagType(row.status)" size="small">{{ row.status }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="版本" width="60">
            <template #default="{ row }">v{{ row.current_version }}</template>
          </el-table-column>
          <el-table-column prop="fix_rounds" label="修复" width="55" />
          <el-table-column label="成本" width="90">
            <template #default="{ row }">
              <span :class="{ 'cost-warn': row.cost_over_yuan }">
                {{ (row.cost_cents / 100).toFixed(2) }}元
              </span>
            </template>
          </el-table-column>
          <el-table-column label="" width="90">
            <template #default="{ row }">
              <el-button size="small" @click="gotoWorkbench(row.no)">工作台</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <!-- 大纲（章节总表） -->
      <div class="panel">
        <h3 class="panel-title">大纲（章节总表）</h3>
        <el-table :data="ov?.outline_rows ?? []" size="small" max-height="300">
          <el-table-column prop="章" label="章" width="50" />
          <el-table-column prop="本章目标" label="目标" min-width="140" show-overflow-tooltip />
          <el-table-column prop="冲突来源" label="冲突" min-width="110" show-overflow-tooltip />
          <el-table-column prop="爽点/揭露" label="爽点" min-width="110" show-overflow-tooltip />
          <el-table-column prop="钩子类型" label="钩子" width="80" />
          <el-table-column prop="重要度★" label="★" width="50" />
        </el-table>
        <el-collapse class="mt-2">
          <el-collapse-item title="编辑大纲行（Markdown 表格）">
            <el-input v-model="outlineText" type="textarea" :rows="12" class="mono" />
            <el-button class="mt-2" @click="saveOutlineRows">保存大纲</el-button>
          </el-collapse-item>
        </el-collapse>
      </div>
    </div>
  </div>

  <!-- AI 生成结果预览 -->
  <el-dialog v-model="aiDialog" width="860px" top="4vh" destroy-on-close>
    <template #header>
      <span class="font-bold">AI 生成结果预览（可改后采纳）</span>
      <span class="dim ml-2">
        目标：{{ aiTarget }}（来源 {{ aiPid }} digest {{ aiDigest.slice(0, 12) }}）
      </span>
    </template>
    <el-input v-model="aiText" type="textarea" :rows="22" class="mono" />
    <template #footer>
      <el-button @click="aiDialog = false">取消</el-button>
      <el-button type="primary" @click="adopt">采纳落盘</el-button>
    </template>
  </el-dialog>

  <!-- 角色编辑 -->
  <el-dialog v-model="charDialog" width="760px" top="5vh" destroy-on-close>
    <template #header
      ><span class="font-bold">编辑角色：{{ charEditName }}</span></template
    >
    <div class="dim mb-1">frontmatter（JSON）</div>
    <el-input v-model="charEditFm" type="textarea" :rows="10" class="mono" />
    <div class="dim mt-2 mb-1">正文</div>
    <el-input v-model="charEditBody" type="textarea" :rows="14" />
    <template #footer>
      <el-button @click="charDialog = false">取消</el-button>
      <el-button type="primary" @click="saveChar">保存角色卡</el-button>
    </template>
  </el-dialog>
</template>
