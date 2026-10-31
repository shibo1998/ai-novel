// 规格10 §3：卷生产独立契约；检查/档案内部结构不臆测，不引入第二套编辑器。
export type ProductionMode = 'prepare' | 'append' | 'new_volume'
export interface ProductionRequest {
  mode: ProductionMode
  count: number
  volume_no?: number
  logline: string
  requirements: string
  client_request_id: string
}
export interface ProductionJob {
  id: string
  book: string
  status: string
  phase: string
  numbers: number[]
  current_chapter: number | null
  completed: number[]
  applied: number[]
  reason: string | null
  request: Record<string, unknown>
  created_at: string
  updated_at: string
  etag: string
  preview_digest?: string
  interrupted?: boolean
}
export interface VolumeChapter {
  no: number
  title: string
  status: string
  word_count: number | null
}
export interface Volume {
  no: number
  name: string
  goal: string
  chapter_range: [number, number] | []
  planned_count: number | null
  draft_count: number | null
  finalized_count: number | null
  chapters: VolumeChapter[]
}
export interface VolumesResponse {
  book: string
  title: string
  etag: string
  volumes: Volume[]
  jobs: ProductionJob[]
  warnings: string[]
}
export interface ProductionBundle {
  schema_version: 1
  outline: { fm: Record<string, unknown>; rows: unknown[]; rhythm: unknown }
  files: Record<string, string>
  outlines: Array<{ no: number; fm: Record<string, unknown>; body: string }>
  notes: string[]
}
export interface ReviewChapter {
  no: number
  title: string
  body: string
  fingerprint: string
  outline_fingerprint: string
  status: string
  check: Record<string, unknown> | null
  review: Record<string, unknown> | null
}
export interface ProductionReview {
  job: ProductionJob
  candidate: ProductionBundle | null
  preview_digest: string
  files: Array<{ path: string; before: string; after: string }>
  chapters: ReviewChapter[]
  warnings: string[]
}
export interface FinalizeReceipt {
  job: ProductionJob
  results: Array<{ no: number; applied: boolean; result?: Record<string, unknown>; error?: string }>
  stopped: boolean
}
export interface FinalizeRequest {
  chapter_nos: number[]
  fingerprints: Record<string, string>
  client_request_id: string
  confirmed: true
}
