<script setup lang="ts">
// 书架页（规格04 §1）：新建向导 + 书列表，删除二次确认。
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import {
  attachBook,
  commitImport,
  createBook,
  deleteBook,
  detachBook,
  listBooks,
  previewImport,
} from '@/api'
import type { AttachResult, BookCard, ImportPreview } from '@/api/types'
import ModelSelect from '@/components/ModelSelect.vue'
import { useBookStore } from '@/stores/book'

const router = useRouter()
const store = useBookStore()
const books = ref<BookCard[]>([])
const loading = ref(false)
const loadFailed = ref(false)
const genres = [
  { value: 'urban', label: '都市' },
  { value: 'xuanhuan', label: '玄幻' },
  { value: 'romance', label: '言情' },
  { value: 'suspense', label: '悬疑' },
]
const form = ref({
  name: '',
  genre_template: 'urban',
  target_words: 1_000_000,
  author: '',
  platform: '番茄',
  // 规格08 §3.2：新 Web 建书显式选择规划模式；不勾保持 legacy，旧 API 行为不变
  planning_mode: 'legacy' as 'legacy' | 'rolling',
  // 规格12 §3：规划档（G1-G6/T1/F1）与写作档（W1-W6）分开选，其余三档留空按全局解析
  plan_model: '',
  prose_model: '',
})

// —— 接管已有书稿（规格05 F-07）：原地挂载，不搬迁不复制 ——
const attachDialog = ref(false)
const attachForm = ref({ name: '', path: '', scaffold: true })
const attachResult = ref<AttachResult | null>(null)

async function doAttach(): Promise<void> {
  if (!attachForm.value.name.trim() || !attachForm.value.path.trim()) {
    ElMessage.warning('书名与目录路径都要填')
    return
  }
  try {
    const r = await attachBook({
      name: attachForm.value.name.trim(),
      path: attachForm.value.path.trim(),
      scaffold: attachForm.value.scaffold,
    })
    attachResult.value = r
    ElMessage.success(`已挂载：${r.name}（结构 ${r.detected.layout}）`)
    await load()
  } catch {
    // 拦截器已提示
  }
}

// —— 导入 txt（规格05 F-08）：两段式，预演不写盘 ——
const importDialog = ref(false)
const importBookName = ref('')
const importPath = ref('')
const importMode = ref<'draft' | 'finalized'>('draft')
const importPv = ref<ImportPreview | null>(null)
const importConfirm = ref(false)
const importing = ref(false)

async function doPreview(): Promise<void> {
  if (!importBookName.value || !importPath.value.trim()) {
    ElMessage.warning('先选书、填 txt 路径')
    return
  }
  try {
    importPv.value = await previewImport(importBookName.value, importPath.value.trim())
    importConfirm.value = false
    ElMessage.success(`预演完成：识别 ${importPv.value.chapters.length} 章（未写任何文件）`)
  } catch {
    importPv.value = null
  }
}

async function doCommit(): Promise<void> {
  if (!importPv.value) return
  importing.value = true
  try {
    const r = await commitImport(importBookName.value, {
      file_path: importPv.value.file_path,
      regex: importPv.value.guessed_regex,
      encoding: importPv.value.encoding,
      mode: importMode.value,
      confirm_warnings: importConfirm.value,
    })
    ElMessage.success(`已导入 ${String(r.imported ?? 0)} 章，跳过 ${String(r.skipped ?? 0)} 章`)
    importDialog.value = false
    importPv.value = null
    await load()
  } catch {
    // 拦截器已提示（含缺章未确认的 400）
  } finally {
    importing.value = false
  }
}

async function load(): Promise<void> {
  loading.value = true
  loadFailed.value = false
  try {
    books.value = await listBooks()
    store.reconcileBooks(books.value)
  } catch {
    loadFailed.value = true
  } finally {
    loading.value = false
  }
}
onMounted(() => void load())

function open(name: string): void {
  store.setCurrentBook(name)
  router.push(`/book/${encodeURIComponent(name)}/volumes`)
}

async function create(): Promise<void> {
  const name = form.value.name.trim()
  if (!name) {
    ElMessage.warning('书名必填')
    return
  }
  try {
    // 规格12 §3：建书即选型；留空则不提交该档，交给全局/兜底解析。
    const { plan_model, prose_model, ...rest } = form.value
    const model_stages: Record<string, string> = {}
    if (plan_model.trim()) model_stages.plan = plan_model.trim()
    if (prose_model.trim()) model_stages.prose = prose_model.trim()
    await createBook({ ...rest, name, model_stages })
    ElMessage.success('已创建')
    form.value.name = ''
    await load()
    // 规格10：创建空书后直接进入卷工作台；创建本身仍不调用模型。
    open(name)
  } catch {
    // 拦截器已提示
  }
}

async function del(book: BookCard): Promise<void> {
  const { name, external } = book
  try {
    await ElMessageBox.confirm(
      external
        ? `移出《${name}》只解除挂载，磁盘文件不动。确认？`
        : `删除《${name}》将永久删除目录 books/${name}/（含全部章节与档案），不可恢复。确认？`,
      external ? '移出书架' : '删除书（第一次确认）',
      { type: 'warning' },
    )
    if (!external) {
      await ElMessageBox.confirm(`再次确认：真的删除《${name}》？`, '删除书', { type: 'warning' })
    }
  } catch {
    return // 用户取消
  }
  try {
    if (external) await detachBook(name)
    else await deleteBook(name)
    ElMessage.success(external ? '已移出书架，磁盘文件未删除' : '已删除')
    if (store.currentBook === name) store.clearBook()
    await load()
  } catch {
    // 拦截器已提示
  }
}
</script>

<template>
  <div class="panel">
    <h3 class="panel-title">新建书</h3>
    <el-form inline label-width="auto">
      <el-form-item label="书名">
        <el-input
          v-model="form.name"
          placeholder="书名"
          style="width: 160px"
          @keyup.enter="create"
        />
      </el-form-item>
      <el-form-item label="题材">
        <el-select v-model="form.genre_template" style="width: 100px">
          <el-option v-for="g in genres" :key="g.value" :label="g.label" :value="g.value" />
        </el-select>
      </el-form-item>
      <el-form-item label="目标字数">
        <el-input-number
          v-model="form.target_words"
          :min="10000"
          :step="10000"
          style="width: 140px"
        />
      </el-form-item>
      <el-form-item label="笔名">
        <el-input v-model="form.author" placeholder="可空" style="width: 110px" />
      </el-form-item>
      <el-form-item label="平台">
        <el-input v-model="form.platform" style="width: 90px" />
      </el-form-item>
      <el-form-item label="规划模型">
        <ModelSelect v-model="form.plan_model" width="200px" placeholder="留空按全局解析" />
      </el-form-item>
      <el-form-item label="写作模型">
        <ModelSelect v-model="form.prose_model" width="200px" placeholder="留空按全局解析" />
      </el-form-item>
      <el-form-item label="规划模式">
        <el-switch
          v-model="form.planning_mode"
          active-value="rolling"
          inactive-value="legacy"
          active-text="分层规划"
          inactive-text="旧模式"
        />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="create">创建</el-button>
      </el-form-item>
    </el-form>
  </div>

  <div class="panel">
    <h3 class="panel-title">接管已有书稿（不搬迁、不复制）</h3>
    <div class="flex flex-wrap items-center gap-2">
      <el-button @click="attachDialog = true">挂载已有目录</el-button>
      <el-button @click="importDialog = true">导入 txt</el-button>
      <span class="dim">
        挂载 = 原地接管整个书稿目录（走原有档案结构）；导入 = 把裸 txt 切成章节并建工程。
        两者都不搬文件，挂载前会写一份文件清单备份。
      </span>
    </div>
  </div>

  <el-dialog v-model="attachDialog" title="挂载已有目录" width="600px">
    <el-form label-width="90px">
      <el-form-item label="书架上叫">
        <el-input v-model="attachForm.name" placeholder="书名（不能与已有书重名）" />
      </el-form-item>
      <el-form-item label="目录路径">
        <el-input v-model="attachForm.path" placeholder="如 D://1-work//oh-story-claudecode" />
      </el-form-item>
      <el-form-item label="补齐结构">
        <el-switch v-model="attachForm.scaffold" />
        <span class="dim ml-2">只补缺失文件（圣经/大纲/台账），已存在的文件一个字节都不动</span>
      </el-form-item>
    </el-form>
    <el-alert v-if="attachResult" type="success" :closable="false" show-icon>
      <div>结构：{{ attachResult.detected.layout }}</div>
      <div>
        章节 {{ attachResult.detected.chapters }} 个 · 圣经
        {{ attachResult.detected.has_bible ? '有' : '无（已如实报告）' }}
      </div>
      <div>新建文件：{{ attachResult.scaffolded.join('、') || '（无）' }}</div>
      <div>备份清单：{{ String(attachResult.backup) }}</div>
    </el-alert>
    <template #footer>
      <el-button @click="attachDialog = false">关闭</el-button>
      <el-button type="primary" @click="doAttach">挂载</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="importDialog" title="导入 txt（两段式：预演 → 确认）" width="720px">
    <el-form label-width="90px">
      <el-form-item label="导入到">
        <el-select v-model="importBookName" placeholder="选择一本已有的书" style="width: 220px">
          <el-option v-for="b in books" :key="b.name" :label="b.title || b.name" :value="b.name" />
        </el-select>
      </el-form-item>
      <el-form-item label="文本文件路径">
        <el-input v-model="importPath" placeholder="如 D://1-work//某本//全本.txt" />
      </el-form-item>
      <el-form-item label="导入后状态">
        <el-radio-group v-model="importMode">
          <el-radio value="draft">待定稿（等人确认）</el-radio>
          <el-radio value="finalized">已定稿（历史章）</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item>
        <el-button @click="doPreview">预演（不写任何文件）</el-button>
      </el-form-item>
    </el-form>
    <template v-if="importPv">
      <el-alert
        v-for="(w, i) in importPv.warnings"
        :key="i"
        type="warning"
        :closable="false"
        show-icon
        class="mb-1"
      >
        {{ w.detail }}
      </el-alert>
      <div class="dim mb-1">
        编码 {{ importPv.encoding }} · 共 {{ importPv.total_chars }} 字 ·
        {{ importPv.chapters.length }} 章 · 切章正则 {{ importPv.guessed_regex }}
      </div>
      <el-table :data="importPv.chapters.slice(0, 50)" height="240" size="small">
        <el-table-column prop="no" label="#" width="60" />
        <el-table-column prop="title" label="标题" />
        <el-table-column prop="chars" label="字数" width="90" />
        <el-table-column prop="start_line" label="起始行" width="90" />
      </el-table>
      <el-checkbox v-if="importPv.warnings.length" v-model="importConfirm" class="mt-2">
        我已看过上面的警告，确认继续导入
      </el-checkbox>
    </template>
    <template #footer>
      <el-button @click="importDialog = false">关闭</el-button>
      <el-button
        type="primary"
        :loading="importing"
        :disabled="!importPv || (importPv.warnings.length > 0 && !importConfirm)"
        @click="doCommit"
      >
        确认导入
      </el-button>
    </template>
  </el-dialog>

  <h3 class="panel-title">书列表</h3>
  <el-skeleton v-if="loading" :rows="6" animated />
  <el-result
    v-else-if="loadFailed"
    icon="error"
    title="书架加载失败"
    sub-title="不是空书架，请重试。"
    ><template #extra><el-button @click="load">重试</el-button></template></el-result
  >
  <div v-else-if="books.length" class="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
    <div v-for="b in books" :key="b.name" class="panel mb-0">
      <div class="font-bold">
        {{ b.title || b.name }}
        <el-tag v-if="b.external" size="small" type="info">外挂</el-tag>
      </div>
      <div class="dim mt-1">
        {{ b.genre }} · {{ ((b.target_words ?? 0) / 10000).toFixed(0) }}万字 · {{ b.platform }} ·
        {{ b.status }}
      </div>
      <div class="mt-2 text-xs">
        <template v-if="b.chapters_total"
          >已定稿 {{ b.chapters_done ?? '未知' }} 章 / 已规划 {{ b.chapters_total }} 章</template
        >
        <template v-else-if="b.chapters_total === 0"
          >已定稿 {{ b.chapters_done ?? '未知' }} 章 · 尚未规划总章数</template
        >
        <template v-else>定稿章数 {{ b.chapters_done ?? '未知' }} · 规划章数未知</template>
        · 定稿
        {{
          b.words_done === null || b.words_done === undefined
            ? '未知'
            : (b.words_done / 10000).toFixed(2) + ' 万字'
        }}
      </div>
      <div class="dim mt-1">最后编辑：{{ b.last_edited_at || '未知' }}</div>
      <el-alert
        v-for="warning in b.progress_warnings ?? []"
        :key="warning.code"
        class="mt-1"
        type="warning"
        :closable="false"
        :title="warning.message"
      />
      <div class="mt-2 flex gap-2">
        <el-button type="primary" @click="open(b.name)">打开</el-button>
        <el-button v-if="b.external" type="info" plain @click="del(b)">移出书架</el-button>
        <el-button v-else type="danger" @click="del(b)">删除</el-button>
      </div>
    </div>
  </div>
  <div v-else class="dim">（空，先建书）</div>
</template>
