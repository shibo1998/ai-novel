// 规格06 §4、§11.2：两处入口共用书级编辑会话；刷新不覆盖未保存的人工文本。
import { computed, reactive, watch, type ComputedRef } from 'vue'
import { isAxiosError } from 'axios'
import { ElMessage } from 'element-plus'

import {
  analyzeStyleF1,
  getStyle,
  putStyleCard,
  putStyleCorpus,
  putStyleEnabled,
  putStyleExamples,
} from '@/api'
import type { StyleAnalysis, StyleData, StyleExampleInput } from '@/api/types'

interface StyleSession {
  data: StyleData | null
  corpus: string
  card: string
  draft: StyleAnalysis | null
  draftText: string
  examples: StyleExampleInput[]
  examplesDirty: boolean
  examplesFingerprint: string
  busy: string
  conflict: boolean
  error: boolean
  request: number
}

const sessions = new Map<string, StyleSession>()
function session(book: string): StyleSession {
  let s = sessions.get(book)
  if (!s) {
    s = reactive<StyleSession>({
      data: null,
      corpus: '',
      card: '',
      draft: null,
      draftText: '',
      examples: [],
      examplesDirty: false,
      examplesFingerprint: '',
      busy: '',
      conflict: false,
      error: false,
      request: 0,
    })
    sessions.set(book, s)
  }
  return s
}

function applyData(s: StyleSession, data: StyleData): void {
  const old = s.data
  const dirtyCorpus = !!old && s.corpus !== old.corpus
  const dirtyCard = !!old && s.card !== old.card
  s.conflict ||=
    !!old &&
    ((dirtyCard && old.card_fingerprint !== data.card_fingerprint) ||
      (dirtyCorpus && old.corpus_fingerprint !== data.corpus_fingerprint))
  if (!dirtyCorpus) s.corpus = data.corpus
  if (!dirtyCard) s.card = data.card
  if (!s.examplesDirty) {
    s.examples = (data.examples?.examples ?? []).map((e) => ({
      id: e.id,
      start_paragraph: e.start_paragraph,
      end_paragraph: e.end_paragraph,
      scene_types: [...e.scene_types],
    }))
    s.examplesFingerprint = data.examples?.corpus_fingerprint ?? data.corpus_fingerprint
  }
  s.data = data
  s.error = false
}

export function useStyleEditor(book: ComputedRef<string>) {
  const state = computed(() => session(book.value))
  const corpusDirty = computed(() => state.value.corpus !== state.value.data?.corpus)
  const cardDirty = computed(() => state.value.card !== state.value.data?.card)

  async function refresh(): Promise<void> {
    const b = book.value
    const s = session(b)
    if (s.busy) return
    s.busy = 'load'
    const request = ++s.request
    try {
      const data = await getStyle(b)
      if (request !== s.request) return
      applyData(s, data)
    } catch {
      s.error = true
    } finally {
      if (request === s.request) s.busy = ''
    }
  }

  async function perform(
    kind: string,
    action: (s: StyleSession, b: string) => Promise<void>,
  ): Promise<void> {
    const b = book.value
    const s = session(b)
    if (s.busy || !s.data || s.error) return
    s.busy = kind
    try {
      await action(s, b)
    } catch (e) {
      if (isAxiosError(e) && e.response?.status === 409) s.conflict = true
      // HTTP 拦截器已提示，保留全部编辑文本与草案。
    } finally {
      s.busy = ''
    }
  }

  async function saveCorpus(): Promise<void> {
    await perform('corpus', async (s, b) => {
      if (!s.data || s.conflict) return
      const text = s.corpus
      const r = await putStyleCorpus(b, text, s.data.corpus_fingerprint)
      if (!r.concurrency_protected) ElMessage.warning('服务端未确认并发保护，请升级后端')
      s.data = {
        ...s.data,
        corpus: text,
        corpus_fingerprint: r.fingerprint,
        paragraphs: [],
        examples_stale: !!s.data.examples && s.data.examples.corpus_fingerprint !== r.fingerprint,
      }
      s.error = true
      // 新段落只使用服务端解析结果；读取失败时不能拿旧段落套新指纹。
      const fresh = await getStyle(b)
      applyData(s, fresh)
      ElMessage.success('语料已保存，正式特征卡与启用开关未改变')
    })
  }

  async function analyze(): Promise<void> {
    if (corpusDirty.value || !state.value.corpus.trim()) {
      ElMessage.warning('请先保存非空语料，再生成草案')
      return
    }
    if (state.value.draft) {
      ElMessage.warning('已有未保存草案，请先保存或明确放弃，再重新分析')
      return
    }
    await perform('analyze', async (s, b) => {
      const r = await analyzeStyleF1(b)
      if (!r.ok || r.applied !== false) {
        ElMessage.warning('未获得有效的未应用草案，请刷新文风资料')
        return
      }
      s.draft = r
      s.draftText = r.card
      ElMessage.success('草案已暂存；未覆盖正式特征卡，也未改变启用状态')
    })
  }

  async function saveCard(fromDraft = false): Promise<void> {
    await perform(fromDraft ? 'draft' : 'card', async (s, b) => {
      if (!s.data || s.conflict) return
      const text = fromDraft ? s.draftText : s.card
      const fingerprint = fromDraft ? s.draft?.card_fingerprint : s.data.card_fingerprint
      if (fingerprint === undefined) return
      if (fromDraft && s.draft?.corpus_fingerprint !== s.data.corpus_fingerprint) {
        ElMessage.warning('草案来源语料已变化，请重新分析；草案内容已保留')
        return
      }
      const r = await putStyleCard(b, text, fingerprint)
      if (!r.concurrency_protected) ElMessage.warning('服务端未确认并发保护，请升级后端')
      s.data = { ...s.data, card: text, card_fingerprint: r.fingerprint }
      s.card = text
      if (fromDraft) {
        s.draft = null
        s.draftText = ''
      }
      ElMessage.success('特征卡已保存；启用状态未改变')
    })
  }

  async function toggle(enabled: boolean): Promise<void> {
    await perform('toggle', async (s, b) => {
      await putStyleEnabled(b, enabled)
      if (s.data) s.data.enabled = enabled
      ElMessage.success(
        enabled ? '已启用已保存的文风资料（未保存草案不生效）' : '已停用书级文风注入',
      )
    })
  }

  async function saveExamples(): Promise<void> {
    await perform('examples', async (s, b) => {
      if (
        !s.data ||
        s.conflict ||
        s.corpus !== s.data.corpus ||
        s.examplesFingerprint !== s.data.corpus_fingerprint
      )
        return
      const index = await putStyleExamples(
        b,
        s.examplesFingerprint,
        s.examples.map((e) => ({
          ...(e.id ? { id: e.id } : {}),
          start_paragraph: e.start_paragraph,
          end_paragraph: e.end_paragraph,
          scene_types: [...e.scene_types],
        })),
      )
      s.data.examples = index
      s.data.examples_stale = false
      s.examples = index.examples.map((e) => ({ ...e, scene_types: [...e.scene_types] }))
      s.examplesFingerprint = index.corpus_fingerprint
      s.examplesDirty = false
      ElMessage.success('人工范例索引已保存')
    })
  }

  function reselectExamples(): void {
    const s = state.value
    if (!s.data || s.busy) return
    s.examples = []
    s.examplesFingerprint = s.data.corpus_fingerprint
    s.examplesDirty = true
  }

  async function reset(): Promise<void> {
    const s = state.value
    if (s.busy) return
    // 用户明确放弃本地编辑后才允许重新建立保存基线。
    s.data = null
    s.draft = null
    s.draftText = ''
    s.examplesDirty = false
    s.conflict = false
    await refresh()
  }

  watch(book, () => void refresh(), { immediate: true })
  return {
    state,
    corpusDirty,
    cardDirty,
    refresh,
    reset,
    analyze,
    saveCorpus,
    saveCard,
    toggle,
    saveExamples,
    reselectExamples,
  }
}
