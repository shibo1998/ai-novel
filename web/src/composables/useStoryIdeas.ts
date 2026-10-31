// 规格11 §4/5：原书/请求/输入/上下文绑定；候选只在本页，重试必须显式接受再次计费。
import { computed, h, onUnmounted, ref, watch, type Ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import {
  storyIdeasApi,
  type StoryIdeaForm,
  type StoryIdeasApi,
  type StoryIdeasContext,
  type StoryIdeasResult,
} from '@/api/storyIdeas'
import {
  assertStoryContext,
  assertStoryResult,
  storyIdeaToForm,
  storyIdeasError,
  storyTextLength,
} from '@/utils/storyIdeas'
import { parseError, recordError } from '@/utils/errors'
import { stopWaiting, tasks, taskElapsed, withTask, type TaskRecord } from '@/utils/tasks'

const operation = '同题材故事候选'
const unknownNotice =
  '已停止等待或网络结果未知。模型可能仍在执行并产生费用；未接收的候选无法恢复或取回。不会自动重发；再次调用需明确确认可能重复计费。'
export function useStoryIdeas(
  book: Ref<string>,
  form: StoryIdeaForm,
  disabled: Ref<boolean>,
  api: StoryIdeasApi = storyIdeasApi,
) {
  const context = ref<StoryIdeasContext | null>(null)
  const batch = ref<StoryIdeasResult | null>(null)
  const preferences = ref('')
  const contextLoading = ref(false)
  const pending = ref(false)
  const modelWaiting = ref(false)
  const selecting = ref(false)
  const selected = ref<number | null>(null)
  const error = ref('')
  const notice = ref('')
  const activeTask = ref<TaskRecord | null>(null)
  const unresolved = computed(() =>
    tasks.filter(
      (t) => t.book === book.value && t.operation === operation && t.phase === 'unknown',
    ),
  )
  const sessionRunning = computed(() =>
    tasks.some((t) => t.book === book.value && t.operation === operation && t.phase === 'running'),
  )
  const unknown = computed(() => unresolved.value.length > 0)
  const prepareBlocked = computed(
    () => pending.value || selecting.value || unknown.value || sessionRunning.value,
  )
  const stale = computed(() =>
    Boolean(
      batch.value &&
      (!context.value?.eligible ||
        batch.value.context_etag !== context.value.etag ||
        batch.value.genre_template !== context.value.genre_template),
    ),
  )
  const canGenerate = computed(
    () =>
      !pending.value &&
      !selecting.value &&
      !sessionRunning.value &&
      !contextLoading.value &&
      !disabled.value &&
      Boolean(context.value?.eligible) &&
      storyTextLength(preferences.value) <= 2000,
  )
  const canSelect = computed(() =>
    Boolean(
      batch.value &&
      context.value?.eligible &&
      !stale.value &&
      !prepareBlocked.value &&
      !contextLoading.value &&
      !disabled.value,
    ),
  )
  const elapsed = computed(() => (activeTask.value ? taskElapsed(activeTask.value) : 0))
  let generation = 0,
    actionSequence = 0,
    readSequence = 0,
    inputRevision = 0,
    candidateRevision = 0,
    contextRevision = 0,
    disabledRevision = 0
  let disposed = false
  const reads = new Set<AbortController>()
  const valid = (g: number, b: string) => !disposed && generation === g && book.value === b
  watch(
    () => JSON.stringify(form),
    () => {
      inputRevision++
      selected.value = null
    },
    { flush: 'sync' },
  )
  watch(
    preferences,
    () => {
      inputRevision++
    },
    { flush: 'sync' },
  )
  watch(
    batch,
    () => {
      candidateRevision++
    },
    { deep: true, flush: 'sync' },
  )
  watch(
    context,
    () => {
      contextRevision++
    },
    { deep: true, flush: 'sync' },
  )
  watch(
    disabled,
    () => {
      disabledRevision++
    },
    { flush: 'sync' },
  )
  function fail(e: unknown, b: string, recorded = false): void {
    const parsed = parseError(e)
    error.value = parsed.message
    if (parsed.status === 409) error.value += '。本书忙或已不适用；保留手填与旧卡，请核对当前任务。'
    if (parsed.status === 412 || parsed.status === 428) {
      context.value = null
      error.value += '。请重新读取故事上下文，旧卡不再直接选用。'
    }
    // Axios 已记录响应/网络错误；本地契约校验和离线替身错误统一补记。
    if (
      !recorded &&
      !(e as { response?: unknown; config?: unknown })?.response &&
      !(e as { config?: unknown })?.config
    ) {
      recordError(e, { operation, book: b, write: false })
    }
  }
  async function readContext(g: number, b: string): Promise<StoryIdeasContext | null> {
    const controller = new AbortController(),
      seq = ++readSequence
    reads.add(controller)
    contextLoading.value = true
    try {
      const next = await api.context(b, controller.signal)
      if (!valid(g, b) || controller.signal.aborted || seq !== readSequence) return null
      assertStoryContext(next, b)
      context.value = next
      return next
    } catch (e) {
      if (valid(g, b) && seq === readSequence) context.value = null
      throw e
    } finally {
      reads.delete(controller)
      if (valid(g, b) && seq === readSequence) contextLoading.value = false
    }
  }
  async function refreshContext(): Promise<void> {
    if (disposed || pending.value || selecting.value || contextLoading.value) return
    const g = generation,
      b = book.value
    error.value = ''
    try {
      await readContext(g, b)
    } catch (e) {
      if (valid(g, b)) {
        context.value = null
        fail(e, b)
      }
    }
  }
  function requireEligible(next: StoryIdeasContext): void {
    if (!next.eligible)
      throw storyIdeasError(next.reason || '本书已不适用首卷故事候选；请保留原故事并继续现有任务。')
  }
  async function generate(): Promise<void> {
    if (!canGenerate.value) return
    const g = generation,
      b = book.value,
      seq = ++actionSequence,
      input = inputRevision,
      lock = disabledRevision
    const previous = batch.value?.ideas.map((v) => v.logline) ?? []
    const pref = preferences.value
    const stillValid = () =>
      valid(g, b) &&
      seq === actionSequence &&
      !disabled.value &&
      lock === disabledRevision &&
      input === inputRevision
    const attempt = { task: null as TaskRecord | null, awaitingModel: false, failed: false }
    pending.value = true
    error.value = ''
    notice.value = ''
    try {
      if (unknown.value) {
        await ElMessageBox.confirm(unknownNotice, '再次调用可能重复付费', {
          type: 'warning',
          confirmButtonText: '接受可能重复费用，再调用一次',
          cancelButtonText: '不重试，保留当前内容',
          closeOnClickModal: false,
        })
        if (!stillValid()) {
          notice.value = '书或输入已变化，未再次调用。请重新核对后显式重试。'
          return
        }
      }
      const before = await readContext(g, b)
      if (!before || !stillValid()) return
      requireEligible(before)
      // 只有再次付费确认通过且准备真正发起时，解除旧未知的本书阻断；不伪称取回旧结果。
      for (const task of unresolved.value) {
        task.phase = 'failed'
        task.result = '作者明确接受可能重复费用并再次调用；旧候选无法恢复。'
      }
      const next = await withTask(
        { operation, book: b, write: false, returnPath: `/book/${encodeURIComponent(b)}/volumes` },
        async (signal, task) => {
          activeTask.value = task
          attempt.task = activeTask.value
          attempt.awaitingModel = true
          modelWaiting.value = true
          try {
            const value = await api.generate(
              b,
              { preferences: pref, previous_loglines: previous },
              before.etag,
              signal,
            )
            if (signal.aborted)
              throw Object.assign(new Error('已停止等待'), { code: 'ERR_CANCELED' })
            attempt.awaitingModel = false
            assertStoryResult(value, before)
            return value
          } finally {
            if (valid(g, b) && seq === actionSequence) modelWaiting.value = false
          }
        },
      ).catch((e: unknown) => {
        attempt.failed = true
        const parsed = parseError(e)
        // 即使已经切书/卸载，原书未知任务仍留在会话中；纯返回体不借写档旗标。
        if (
          attempt.task &&
          attempt.awaitingModel &&
          !parsed.status &&
          parsed.code !== 'STORY_IDEAS_INVALID'
        ) {
          attempt.task.phase = 'unknown'
          attempt.task.result = unknownNotice
        }
        throw e
      })
      if (!stillValid()) {
        if (valid(g, b))
          notice.value = '等待期间输入已变化，新候选未接收；旧卡与手填内容保留。不会自动重新调用。'
        return
      }
      const after = await readContext(g, b)
      if (!after || !stillValid()) return
      requireEligible(after)
      if (after.etag !== before.etag)
        throw storyIdeasError('生成期间书的上下文已变化，未接收新候选；旧卡与手填内容保留。')
      assertStoryResult(next, after)
      batch.value = next
      selected.value = null
      notice.value = '已收到 3 个故事方案。选用只填写下方两项；你可以修改，再手动准备本卷。'
    } catch (e) {
      if (!valid(g, b) || seq !== actionSequence) return
      // 正常关闭确认框不是失败，更不自动重发。
      if (e === 'cancel' || e === 'close') {
        if (unknown.value) notice.value = unknownNotice
        return
      }
      const parsed = parseError(e)
      if (attempt.task?.phase === 'unknown') {
        notice.value = unknownNotice
        if (parsed.code !== 'ERR_CANCELED') error.value = parsed.message
      } else fail(e, b, attempt.failed)
    } finally {
      if (valid(g, b) && seq === actionSequence) pending.value = false
    }
  }
  async function select(index: number): Promise<void> {
    if (!canSelect.value || !Number.isInteger(index) || !batch.value?.ideas[index]) return
    const g = generation,
      b = book.value,
      seq = ++actionSequence,
      input = inputRevision,
      candidate = candidateRevision,
      lock = disabledRevision
    const source = batch.value,
      signature = JSON.stringify(source),
      mapped = storyIdeaToForm(source.ideas[index]!)
    const stillValid = () =>
      valid(g, b) &&
      seq === actionSequence &&
      !disabled.value &&
      disabledRevision === lock &&
      inputRevision === input &&
      candidateRevision === candidate &&
      JSON.stringify(batch.value) === signature
    selecting.value = true
    error.value = ''
    notice.value = ''
    try {
      const before = await readContext(g, b)
      if (!before || !stillValid()) return
      requireEligible(before)
      assertStoryResult(source, before)
      const version = contextRevision
      if (form.logline.length || form.requirements.length) {
        const preview = h('div', { class: 'story-idea-replace-preview' }, [
          h('p', '只替换以下两项本地输入；书名、题材与期望章数不变。不写档、不自动准备本卷。'),
          ...(['logline', 'requirements'] as const).map((key) =>
            h('section', [
              h('h4', key === 'logline' ? '一句话故事' : '本卷要求'),
              h('small', '当前输入'),
              h('pre', form[key] || '（空）'),
              h('small', '将替换为'),
              h('pre', mapped[key]),
            ]),
          ),
        ])
        await ElMessageBox.confirm(preview, '选用故事 · 核对替换内容', {
          type: 'warning',
          confirmButtonText: '仅替换这两项输入',
          cancelButtonText: '取消，保留原输入',
          closeOnClickModal: false,
        })
        if (!stillValid() || contextRevision !== version) {
          notice.value = '确认期间输入、候选或上下文已变化，未覆盖。请重新选用并核对。'
          return
        }
        const after = await readContext(g, b)
        if (!after || !stillValid()) return
        requireEligible(after)
        assertStoryResult(source, after)
      }
      if (!stillValid()) return
      form.logline = mapped.logline
      form.requirements = mapped.requirements
      selected.value = index
      notice.value = '已填入一句话故事与本卷要求，未启动生产。可继续手改；准备本卷仍由你手动点击。'
    } catch (e) {
      if (valid(g, b) && seq === actionSequence && e !== 'cancel' && e !== 'close') fail(e, b)
    } finally {
      if (valid(g, b) && seq === actionSequence) selecting.value = false
    }
  }
  function stop(): void {
    if (activeTask.value?.phase !== 'running') return
    stopWaiting(activeTask.value.id)
    activeTask.value.result = unknownNotice
    notice.value = unknownNotice
  }
  function invalidate(): void {
    generation++
    actionSequence++
    readSequence++
    if (activeTask.value?.phase === 'running') {
      stopWaiting(activeTask.value.id)
      activeTask.value.result = unknownNotice
    }
    for (const controller of reads) controller.abort()
    reads.clear()
    pending.value = false
    modelWaiting.value = false
    selecting.value = false
    contextLoading.value = false
  }
  watch(
    book,
    () => {
      invalidate()
      context.value = null
      batch.value = null
      preferences.value = ''
      selected.value = null
      activeTask.value = null
      error.value = ''
      notice.value = unknown.value ? unknownNotice : ''
      void refreshContext()
    },
    { immediate: true, flush: 'sync' },
  )
  onUnmounted(() => {
    disposed = true
    invalidate()
  })
  return {
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
    generate,
    select,
    stop,
    refreshContext,
  }
}
