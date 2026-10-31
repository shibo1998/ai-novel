// 规格11 §2/4：严格返回体验证、确定性本地表单映射，不渲染 HTML、不改变书名/题材/章数。
import type {
  StoryIdea,
  StoryIdeaForm,
  StoryIdeasContext,
  StoryIdeasResult,
} from '@/api/storyIdeas'

const limits: Record<keyof StoryIdea, number> = {
  title: 80,
  logline: 400,
  synopsis: 1200,
  protagonist: 400,
  core_conflict: 800,
  selling_point: 800,
  volume_goal: 800,
  volume_hook: 800,
}
export function storyIdeasError(message: string): Error & { code: string } {
  return Object.assign(new Error(message), { code: 'STORY_IDEAS_INVALID' })
}
const record = (v: unknown): v is Record<string, unknown> =>
  Boolean(v && typeof v === 'object' && !Array.isArray(v))
const nonempty = (v: unknown): v is string => typeof v === 'string' && Boolean(v.trim())
export const storyTextLength = (v: string): number => Array.from(v).length
export function assertStoryContext(
  value: unknown,
  book: string,
): asserts value is StoryIdeasContext {
  if (
    !record(value) ||
    value.schema_version !== 1 ||
    value.book !== book ||
    typeof value.genre_template !== 'string' ||
    !['urban', 'xuanhuan', 'romance', 'suspense'].includes(value.genre_template) ||
    !nonempty(value.genre) ||
    !nonempty(value.etag) ||
    typeof value.eligible !== 'boolean' ||
    typeof value.reason !== 'string'
  ) {
    throw storyIdeasError(
      '故事候选上下文不完整或版本未知；请检查书的题材配置后重新读取，不会默认题材。',
    )
  }
}
export function assertStoryResult(
  value: unknown,
  context: StoryIdeasContext,
): asserts value is StoryIdeasResult {
  if (
    !record(value) ||
    value.schema_version !== 1 ||
    value.book !== context.book ||
    value.genre_template !== context.genre_template ||
    value.genre !== context.genre ||
    value.context_etag !== context.etag ||
    !nonempty(value.request_id) ||
    !Array.isArray(value.ideas) ||
    value.ideas.length !== 3
  ) {
    throw storyIdeasError('故事候选返回体不完整、题材或版本不匹配；未接收可选用的新卡片。')
  }
  const seen = new Set<string>()
  for (const idea of value.ideas) {
    if (
      !record(idea) ||
      Object.entries(limits).some(
        ([key, max]) => !nonempty(idea[key]) || storyTextLength(String(idea[key])) > max,
      )
    ) {
      throw storyIdeasError('故事候选字段缺失、为空或超出长度；未接收可选用的新卡片。')
    }
    const normalized = String(idea.logline)
      .normalize('NFKC')
      .replace(/[\s\p{P}\p{S}]/gu, '')
      .toLowerCase()
    if (!normalized || seen.has(normalized))
      throw storyIdeasError('故事候选的一句话故事重复或无有效内容；未接收新卡片。')
    seen.add(normalized)
  }
}
export function storyIdeaToForm(idea: StoryIdea): StoryIdeaForm {
  return {
    logline: idea.logline,
    requirements: [
      `简介：${idea.synopsis}`,
      `主角：${idea.protagonist}`,
      `核心冲突：${idea.core_conflict}`,
      `创作卖点：${idea.selling_point}`,
      `首卷目标：${idea.volume_goal}`,
      `卷末钩子：${idea.volume_hook}`,
    ].join('\n\n'),
  }
}
