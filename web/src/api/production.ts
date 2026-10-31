// 规格10 §3：仅卷级端点；所有写请求绑定服务端 ETag，不借用单章 tasks 状态。
import { http } from './client'
import type {
  FinalizeReceipt,
  FinalizeRequest,
  ProductionBundle,
  ProductionJob,
  ProductionRequest,
  ProductionReview,
  VolumesResponse,
} from './productionTypes'

const base = (book: string) => `/api/books/${encodeURIComponent(book)}`
const task = (book: string, id: string) => `${base(book)}/production/${encodeURIComponent(id)}`
const config = (book: string, operation: string, etag?: string) => ({
  ...(etag ? { headers: { 'If-Match': etag } } : {}),
  errorContext: { book, operation, write: Boolean(etag), background: !etag },
})
export const productionApi = {
  async volumes(book: string): Promise<VolumesResponse> {
    return (await http.get<VolumesResponse>(`${base(book)}/volumes`, config(book, '读取卷工作台')))
      .data
  },
  async job(book: string, id: string): Promise<ProductionJob> {
    return (await http.get<ProductionJob>(task(book, id), config(book, '核对卷任务'))).data
  },
  async review(book: string, id: string): Promise<ProductionReview> {
    return (
      await http.get<ProductionReview>(`${task(book, id)}/review`, config(book, '读取候选与待审稿'))
    ).data
  },
  async create(book: string, request: ProductionRequest, etag: string): Promise<ProductionJob> {
    return (
      await http.post<ProductionJob>(
        `${base(book)}/production`,
        request,
        config(book, '准备卷候选', etag),
      )
    ).data
  },
  async candidate(
    book: string,
    id: string,
    candidate: ProductionBundle,
    etag: string,
  ): Promise<ProductionReview> {
    return (
      await http.put<ProductionReview>(
        `${task(book, id)}/candidate`,
        { candidate },
        config(book, '保存卷候选', etag),
      )
    ).data
  },
  async confirm(
    book: string,
    id: string,
    preview_digest: string,
    client_request_id: string,
    etag: string,
  ): Promise<ProductionJob> {
    return (
      await http.post<ProductionJob>(
        `${task(book, id)}/confirm`,
        { preview_digest, client_request_id },
        config(book, '人工确认整卷细纲', etag),
      )
    ).data
  },
  async pause(book: string, id: string, etag: string): Promise<ProductionJob> {
    return (
      await http.post<ProductionJob>(
        `${task(book, id)}/pause`,
        {},
        config(book, '请求安全点暂停', etag),
      )
    ).data
  },
  async cancel(book: string, id: string, etag: string): Promise<ProductionJob> {
    return (
      await http.post<ProductionJob>(
        `${task(book, id)}/cancel`,
        {},
        config(book, '停止后续卷生产', etag),
      )
    ).data
  },
  async resume(
    book: string,
    id: string,
    client_request_id: string,
    etag: string,
  ): Promise<ProductionJob> {
    return (
      await http.post<ProductionJob>(
        `${task(book, id)}/resume`,
        { client_request_id },
        config(book, '显式恢复卷生产', etag),
      )
    ).data
  },
  async recover(book: string, id: string, etag: string): Promise<ProductionJob> {
    return (
      await http.post<ProductionJob>(
        `${task(book, id)}/recover`,
        {},
        config(book, '核对并恢复提交回执（不调用模型）', etag),
      )
    ).data
  },
  /** 删除已结束的任务目录（不动章节稿件与生成日志）；运行中的任务服务端会拒绝。 */
  async remove(book: string, id: string): Promise<{ ok: boolean; id: string }> {
    return (
      await http.delete<{ ok: boolean; id: string }>(task(book, id), config(book, '删除卷任务'))
    ).data
  },
  async finalize(
    book: string,
    id: string,
    request: FinalizeRequest,
    etag: string,
  ): Promise<FinalizeReceipt> {
    return (
      await http.post<FinalizeReceipt>(
        `${task(book, id)}/finalize`,
        request,
        config(book, '人工顺序定稿', etag),
      )
    ).data
  },
}
export type ProductionApi = typeof productionApi
