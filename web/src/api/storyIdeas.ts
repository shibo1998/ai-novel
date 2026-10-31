// 规格11 §2/3：候选为纯返回体，不复用卷任务版本或客户端题材。
import { http } from './client'

export interface StoryIdea {
  title: string
  logline: string
  synopsis: string
  protagonist: string
  core_conflict: string
  selling_point: string
  volume_goal: string
  volume_hook: string
}
export interface StoryIdeasContext {
  schema_version: 1
  book: string
  genre_template: string
  genre: string
  eligible: boolean
  reason: string
  etag: string
}
export interface StoryIdeasResult {
  schema_version: 1
  book: string
  genre_template: string
  genre: string
  context_etag: string
  request_id: string
  ideas: [StoryIdea, StoryIdea, StoryIdea]
}
export interface StoryIdeasRequest {
  preferences: string
  previous_loglines: string[]
}
export interface StoryIdeaForm {
  logline: string
  requirements: string
}
const base = (book: string) => `/api/books/${encodeURIComponent(book)}/story-ideas`
export const storyIdeasApi = {
  async context(book: string, signal?: AbortSignal): Promise<StoryIdeasContext> {
    const response = await http.get<StoryIdeasContext>(`${base(book)}/context`, {
      signal,
      errorContext: { book, operation: '读取故事候选题材与版本', background: true, write: false },
    })
    const etag = response.headers?.etag
    if (etag && etag !== response.data.etag)
      throw Object.assign(new Error('故事上下文响应版本不一致，请重新读取'), {
        code: 'STORY_IDEAS_INVALID',
      })
    return response.data
  },
  async generate(
    book: string,
    request: StoryIdeasRequest,
    etag: string,
    signal: AbortSignal,
  ): Promise<StoryIdeasResult> {
    return (
      await http.post<StoryIdeasResult>(
        `${base(book)}/generate`,
        {
          preferences: request.preferences,
          previous_loglines: [...request.previous_loglines],
        },
        {
          headers: { 'If-Match': etag },
          timeout: 0,
          signal,
          errorContext: { book, operation: '同题材故事候选', write: false },
        },
      )
    ).data
  },
}
export type StoryIdeasApi = typeof storyIdeasApi
