// 规格10 §3/5：服务端任务为唯一事实，1000ms 串行轮询；离开仅停止查询，不取消后台。
import { computed, onUnmounted, reactive, ref, watch, type Ref } from 'vue'
import { productionApi, type ProductionApi } from '@/api/production'
import type {
  FinalizeReceipt,
  ProductionJob,
  ProductionMode,
  ProductionReview,
  VolumesResponse,
} from '@/api/productionTypes'
import {
  assertJob,
  assertReview,
  assertVolumes,
  canCancel,
  canRemove,
  canResume,
  continuousSelection,
  countValid,
  isRunning,
  JOB_LABELS,
  parseBundle,
} from '@/utils/production'
import { parseError } from '@/utils/errors'

export function useVolumeProduction(book: Ref<string>, api: ProductionApi = productionApi) {
  const model = ref<VolumesResponse | null>(null)
  const job = ref<ProductionJob | null>(null)
  const review = ref<ProductionReview | null>(null)
  const volumeNo = ref<number | null>(null)
  const loading = ref(false)
  const refreshing = ref(false)
  const busy = ref('')
  const error = ref('')
  const conflict = ref(false)
  const invalidData = ref(false)
  const uncertain = ref(false)
  const notice = ref('')
  const receipt = ref<FinalizeReceipt | null>(null)
  /** 本页最后一次成功核对服务端的时刻：用来区分"在跑"与"卡死"。 */
  const lastChecked = ref('')
  const candidateText = ref('')
  const baseline = ref('')
  const checkedDigest = ref('')
  const form = reactive({
    mode: 'prepare' as ProductionMode,
    count: 10,
    logline: '',
    requirements: '',
  })
  const candidateDirty = computed(() => candidateText.value !== baseline.value)
  const selectedVolume = computed(
    () => model.value?.volumes.find((v) => v.no === volumeNo.value) ?? null,
  )
  const knownJob = computed(() => !job.value || Boolean(JOB_LABELS[job.value.status]))
  const writeBlocked = computed(() =>
    Boolean(
      busy.value ||
      refreshing.value ||
      invalidData.value ||
      conflict.value ||
      uncertain.value ||
      !knownJob.value,
    ),
  )
  const confirmReady = computed(
    () =>
      !writeBlocked.value &&
      job.value?.status === 'awaiting_outline_confirmation' &&
      Boolean(
        review.value?.candidate &&
        review.value.preview_digest &&
        checkedDigest.value === review.value.preview_digest &&
        !candidateDirty.value &&
        review.value.job.etag === job.value.etag,
      ),
  )
  const otherOpenJob = computed(() =>
    model.value?.jobs.find((j) => !['applied', 'cancelled'].includes(j.status)),
  )
  let generation = 0
  let readSequence = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  let disposed = false
  let restoreActiveJob = true
  // 失败后保留相同内容的幂等请求 ID；不会自动重试付费动作。
  const intents = new Map<string, { signature: string; id: string }>()
  const valid = (g: number, b: string) => !disposed && g === generation && b === book.value
  // 轮询每秒一次，内容没变就不替换对象：否则整页每秒重渲染一次（列表闪、滚动跳），
  // 用户看到的就是"页面一直在刷新"。
  const sameJson = (a: unknown, b: unknown): boolean => JSON.stringify(a) === JSON.stringify(b)
  function requestId(action: string, payload: unknown): string {
    const signature = JSON.stringify(payload)
    const old = intents.get(action)
    if (old?.signature === signature) return old.id
    const id = crypto.randomUUID()
    intents.set(action, { signature, id })
    return id
  }
  function stopPolling(): void {
    if (timer !== undefined) clearTimeout(timer)
    timer = undefined
  }
  function schedule(): void {
    stopPolling()
    if (!disposed && isRunning(job.value) && !error.value)
      timer = setTimeout(() => void refresh(), 1000)
  }
  function fail(e: unknown, write = false): void {
    const parsed = parseError(e)
    error.value = parsed.message
    if (parsed.status === 412 || parsed.status === 409) {
      conflict.value = true
      checkedDigest.value = ''
      error.value += '。输入与候选已保留；请刷新版本、合并候选并重新核对后显式重试。'
    } else if (write && !parsed.status) {
      uncertain.value = true
      error.value += '。请求是否已生效未知；先刷新服务端回执，不自动重发或宣称取消已发模型请求。'
    }
    if (e instanceof Error && e.message.startsWith('服务版本或任务数据不完整'))
      invalidData.value = true
    stopPolling()
  }
  function acceptJob(j: ProductionJob): void {
    if (job.value?.etag !== j.etag) checkedDigest.value = ''
    job.value = j
    if (model.value) {
      const i = model.value.jobs.findIndex((row) => row.id === j.id)
      if (i >= 0) model.value.jobs[i] = j
      else model.value.jobs.unshift(j)
    }
  }
  function acceptReview(r: ProductionReview, force = false): void {
    const wasDirty = candidateDirty.value
    const changed = review.value?.preview_digest !== r.preview_digest
    review.value = r
    acceptJob(r.job)
    if (changed || force) checkedDigest.value = ''
    if (!wasDirty || force) {
      candidateText.value = r.candidate ? JSON.stringify(r.candidate, null, 2) : ''
      baseline.value = candidateText.value
    }
  }
  function matchingJobs(no: number | null): ProductionJob[] {
    const v = model.value?.volumes.find((row) => row.no === no)
    return (model.value?.jobs ?? [])
      .filter(
        (j) =>
          no === null ||
          j.request.volume_no === no ||
          (v &&
            j.numbers.some(
              (n) =>
                v.chapters.some((c) => c.no === n) ||
                (v.chapter_range.length === 2 &&
                  n >= v.chapter_range[0] &&
                  n <= v.chapter_range[1]),
            )),
      )
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
  }
  async function refresh(): Promise<void> {
    if (refreshing.value || busy.value || disposed) return
    const g = generation,
      b = book.value,
      seq = ++readSequence,
      id = job.value?.id
    refreshing.value = true
    error.value = ''
    stopPolling()
    try {
      const m = await api.volumes(b)
      if (!valid(g, b) || seq !== readSequence) return
      assertVolumes(m, b)
      if (!sameJson(m, model.value)) model.value = m
      if (volumeNo.value === null && !id) volumeNo.value = m.volumes[0]?.no ?? null
      const next = id
        ? m.jobs.find((j) => j.id === id)
        : (matchingJobs(volumeNo.value).find((j) => !['applied', 'cancelled'].includes(j.status)) ??
          (restoreActiveJob
            ? m.jobs.find((j) => !['applied', 'cancelled'].includes(j.status))
            : undefined) ??
          matchingJobs(volumeNo.value)[0])
      if (next || id) {
        const targetId = next?.id ?? id!
        const j = await api.job(b, targetId)
        if (!valid(g, b) || seq !== readSequence) return
        assertJob(j, b, targetId)
        if (!sameJson(j, job.value)) acceptJob(j)
        if (restoreActiveJob || volumeNo.value === null) {
          const requested = j.request.volume_no
          const target =
            m.volumes.find((v) => v.no === requested) ??
            m.volumes.find((v) =>
              j.numbers.some(
                (n) =>
                  v.chapter_range.length === 2 &&
                  n >= v.chapter_range[0] &&
                  n <= v.chapter_range[1],
              ),
            )
          if (target) volumeNo.value = target.no
        }
        restoreActiveJob = false
        // 运行时也展示已经落盘的稿件；等待人工阶段不再自动轮询。
        const r = await api.review(b, targetId)
        if (!valid(g, b) || seq !== readSequence) return
        assertReview(r, b, targetId)
        if (!sameJson(r, review.value)) acceptReview(r)
      }
      invalidData.value = false
      conflict.value = false
      uncertain.value = false
      lastChecked.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    } catch (e) {
      if (valid(g, b) && seq === readSequence) {
        invalidData.value = true
        fail(e)
      }
    } finally {
      if (valid(g, b) && seq === readSequence) {
        loading.value = false
        refreshing.value = false
        schedule()
      }
    }
  }
  async function selectVolume(no: number | null): Promise<void> {
    if (busy.value || candidateDirty.value) return
    restoreActiveJob = false
    generation++
    readSequence++
    stopPolling()
    refreshing.value = false
    volumeNo.value = no
    job.value = null
    review.value = null
    candidateText.value = ''
    baseline.value = ''
    checkedDigest.value = ''
    receipt.value = null
    form.mode = 'prepare'
    await refresh()
  }
  async function selectJob(id: string): Promise<void> {
    if (busy.value || candidateDirty.value || !model.value?.jobs.some((j) => j.id === id)) return
    generation++
    readSequence++
    stopPolling()
    refreshing.value = false
    job.value = model.value.jobs.find((j) => j.id === id)!
    review.value = null
    candidateText.value = ''
    baseline.value = ''
    checkedDigest.value = ''
    receipt.value = null
    const no = job.value.request.volume_no
    if (typeof no === 'number' && model.value.volumes.some((v) => v.no === no)) volumeNo.value = no
    await refresh()
  }
  async function write(
    action: string,
    operation: (
      b: string,
      j: ProductionJob | null,
    ) => Promise<ProductionJob | ProductionReview | FinalizeReceipt>,
  ): Promise<boolean> {
    if (writeBlocked.value) return false
    const g = generation,
      b = book.value,
      j = job.value
    busy.value = action
    error.value = ''
    notice.value = ''
    stopPolling()
    readSequence++
    try {
      const result = await operation(b, j)
      if (!valid(g, b)) return false
      if ('results' in result) {
        assertJob(result.job, b, j?.id)
        if (!Array.isArray(result.results) || typeof result.stopped !== 'boolean')
          throw new Error('服务版本或任务数据不完整：定稿回执缺失')
        receipt.value = result
        acceptJob(result.job)
        notice.value = result.stopped
          ? '顺序定稿已停止；已成功章保留，请查看逐章回执，不会自动重试。'
          : '本次人工定稿回执已收到；未选章节仍待作者审核。'
      } else if ('candidate' in result) {
        assertReview(result, b, j!.id)
        acceptReview(result, true)
        notice.value =
          '候选已保存，服务端已重新校验并更新摘要。请重新通读影响并勾选核对；保存不等于确认。'
      } else {
        assertJob(result, b, action === 'create' ? undefined : j?.id)
        acceptJob(result)
        if (action === 'pause' || action === 'cancel')
          notice.value =
            '服务端已收到安全检查点请求。已发出的模型调用可能继续完成及计费；以服务端阶段为准。'
        else notice.value = '请求已被服务端接受，不代表全部完成；等待服务端检查点。'
      }
      intents.delete(action)
      return true
    } catch (e) {
      if (valid(g, b)) fail(e, true)
      return false
    } finally {
      if (valid(g, b)) {
        busy.value = ''
        schedule()
      }
    }
  }
  async function prepare(): Promise<boolean> {
    if (!countValid(form.count)) {
      error.value = '期望章数必须是 1—50 的整数'
      return false
    }
    if (!model.value || otherOpenJob.value || (form.mode === 'append' && !selectedVolume.value))
      return false
    const payload = {
      mode: form.mode,
      count: form.count,
      logline: form.logline,
      requirements: form.requirements,
      ...(form.mode !== 'new_volume' && volumeNo.value !== null
        ? { volume_no: volumeNo.value }
        : {}),
    }
    const etag = model.value.etag
    const ok = await write('create', (b) =>
      api.create(b, { ...payload, client_request_id: requestId('create', payload) }, etag),
    )
    if (ok) await refresh()
    return ok
  }
  async function saveCandidate(): Promise<boolean> {
    if (!job.value || job.value.status !== 'awaiting_outline_confirmation') return false
    let candidate
    try {
      candidate = parseBundle(candidateText.value)
    } catch (e) {
      error.value = e instanceof Error ? e.message : '候选 JSON 无效'
      return false
    }
    return write('candidate', (b, j) => api.candidate(b, j!.id, candidate, j!.etag))
  }
  async function confirm(): Promise<boolean> {
    if (!confirmReady.value) return false
    const digest = review.value!.preview_digest
    const ok = await write('confirm', (b, j) =>
      api.confirm(b, j!.id, digest, requestId('confirm', { id: j!.id, digest }), j!.etag),
    )
    if (ok) await refresh()
    return ok
  }
  async function control(action: 'pause' | 'resume' | 'cancel' | 'recover'): Promise<boolean> {
    if (
      !job.value ||
      (action === 'resume' && !canResume(job.value)) ||
      (action === 'pause' && !isRunning(job.value)) ||
      (action === 'cancel' && !canCancel(job.value)) ||
      (action === 'recover' && job.value.status !== 'recovery_required')
    )
      return false
    const ok = await write(action, (b, j) =>
      action === 'resume'
        ? api.resume(b, j!.id, requestId('resume', { id: j!.id, etag: j!.etag }), j!.etag)
        : api[action](b, j!.id, j!.etag),
    )
    if (ok) await refresh()
    return ok
  }
  /** 删除已结束的任务（规格10 追加）：清掉半途任务，回到"可重新准备"的状态。 */
  async function removeJob(): Promise<boolean> {
    if (!job.value || !canRemove(job.value)) return false
    const id = job.value.id
    busy.value = 'remove'
    error.value = ''
    notice.value = ''
    stopPolling()
    readSequence++
    try {
      await api.remove(book.value, id)
      job.value = null
      review.value = null
      candidateText.value = ''
      baseline.value = ''
      checkedDigest.value = ''
      receipt.value = null
      notice.value = '任务已删除；已生成的章节正文与生成日志都保留在书里'
      await refresh()
      return true
    } catch (e) {
      fail(e, true)
      return false
    } finally {
      busy.value = ''
    }
  }

  async function finalize(ns: number[], read: Record<string, string>): Promise<boolean> {
    if (
      !job.value ||
      !review.value ||
      !['awaiting_final_review', 'partially_applied', 'paused'].includes(job.value.status) ||
      !continuousSelection(review.value.chapters, ns, read)
    ) {
      error.value = '只能定稿已通读、指纹未变且待定稿的连续章段'
      return false
    }
    const chapter_nos = [...ns].sort((a, b) => a - b)
    const fingerprints = Object.fromEntries(chapter_nos.map((n) => [String(n), read[String(n)]!]))
    const payload = { chapter_nos, fingerprints, confirmed: true as const }
    const ok = await write('finalize', (b, j) =>
      api.finalize(
        b,
        j!.id,
        { ...payload, client_request_id: requestId('finalize', { id: j!.id, ...payload }) },
        j!.etag,
      ),
    )
    if (ok) await refresh()
    return ok
  }
  watch(candidateText, () => {
    checkedDigest.value = ''
  })
  watch(
    book,
    () => {
      generation++
      readSequence++
      stopPolling()
      model.value = null
      job.value = null
      review.value = null
      volumeNo.value = null
      candidateText.value = ''
      baseline.value = ''
      checkedDigest.value = ''
      receipt.value = null
      form.mode = 'prepare'
      form.count = 10
      form.logline = ''
      form.requirements = ''
      busy.value = ''
      refreshing.value = false
      error.value = ''
      notice.value = ''
      conflict.value = false
      uncertain.value = false
      invalidData.value = false
      intents.clear()
      loading.value = true
      restoreActiveJob = true
      void refresh()
    },
    { immediate: true },
  )
  onUnmounted(() => {
    disposed = true
    generation++
    stopPolling()
  })
  return {
    model,
    job,
    review,
    volumeNo,
    selectedVolume,
    loading,
    refreshing,
    busy,
    error,
    conflict,
    invalidData,
    uncertain,
    notice,
    receipt,
    lastChecked,
    removeJob,
    candidateText,
    baseline,
    candidateDirty,
    checkedDigest,
    form,
    writeBlocked,
    confirmReady,
    otherOpenJob,
    refresh,
    selectVolume,
    selectJob,
    prepare,
    saveCandidate,
    confirm,
    control,
    finalize,
    stopPolling,
  }
}
