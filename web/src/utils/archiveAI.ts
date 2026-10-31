// G 链共用参数装配（规格07 UI-17）：取消由表单层处理，空值不再被默认值吞掉。
// 规格08 A-T04：G5 影响预览的中文摘要与 diff 文本组装也在这里，避免两个入口各写一份。
import type { AIGenResult } from '@/api'
import { aiGenerate } from '@/api'
import type { BookConfig, BookMeta, CharacterData, OutlinePreview } from '@/api/types'

export const G_TARGETS: Record<string, 'bible' | 'characters' | 'outline'> = {
  G1: 'bible',
  G2: 'bible',
  G3: 'characters',
  G4: 'characters',
  G5: 'outline',
}
export const G_LABELS: Record<string, string> = {
  G1: 'G1 世界观',
  G2: 'G2 力量体系',
  G3: 'G3 角色卡',
  G4: 'G4 关系网',
  G5: 'G5 全书大纲',
}
export interface ArchiveAIParams {
  genre: string
  story_core: string
  benchmark: string
  character_requirements: string
  logline: string
  target_chapters: string
  chapter_word_range: string
}

export function defaultAIParams(meta: BookMeta, config: BookConfig): ArchiveAIParams {
  const range = config.chapter_word_range ?? meta.chapter_word_range ?? [2200, 2800]
  return {
    genre: meta.genre ?? '',
    story_core: '',
    benchmark: meta.benchmark_book ?? '',
    character_requirements: '',
    logline: meta.logline ? String(meta.logline) : '',
    target_chapters: '120',
    chapter_word_range: `${range[0] ?? 2200}-${range[1] ?? 2800}`,
  }
}

export function validateAIParams(pid: string, params: ArchiveAIParams): string | null {
  if ((pid === 'G1' || pid === 'G2') && (!params.genre.trim() || !params.story_core.trim()))
    return '题材与故事核必填'
  if ((pid === 'G3' || pid === 'G4') && !params.character_requirements.trim())
    return '角色需求清单必填'
  if (pid === 'G5') {
    if (!params.logline.trim()) return '一句话故事必填'
    const chapters = Number(params.target_chapters)
    if (!Number.isInteger(chapters) || chapters <= 0) return '目标总章数须为正整数'
    if (!/^\d+\s*-\s*\d+$/.test(params.chapter_word_range)) return '单章字数区间格式应为 2200-2800'
  }
  return null
}

export function bibleSummary(b: Record<string, string>): string {
  const lines = Object.entries(b)
    .filter(([title, content]) => content.trim() && !title.includes('自检'))
    .map(([title, content]) => `【${title}】${content.trim().replace(/\s+/g, ' ').slice(0, 600)}`)
  return lines.length ? lines.join('\n') : '（世界观圣经暂为空：请按题材常识构建）'
}
export function charSummary(cs: CharacterData[]): string {
  const lines = cs.map((c) => {
    const st = (c.status ?? {}) as Record<string, unknown>
    const brief = [
      st.realm ? `境界 ${String(st.realm)}` : '',
      st.location ? `位置 ${String(st.location)}` : '',
      st.emotion ? `情绪 ${String(st.emotion)}` : '',
      st.alive === false ? '已死亡' : '',
    ].filter(Boolean)
    return `- ${String(c.name ?? '')}（${String(c.role ?? '')}）${brief.length ? '：' + brief.join(' / ') : ''}`
  })
  return lines.length ? lines.join('\n') : '（暂无角色卡）'
}

export function archiveVariables(
  pid: string,
  params: ArchiveAIParams,
  bible: Record<string, string>,
  characters: CharacterData[],
): Record<string, string> {
  const vars: Record<string, string> = {}
  if (['G3', 'G4', 'G5'].includes(pid)) vars['世界观摘要'] = bibleSummary(bible)
  if (pid === 'G5') {
    vars['角色卡摘要'] = charSummary(characters)
    vars['目标总章数'] = params.target_chapters
    vars['单章字数区间'] = params.chapter_word_range
    vars['logline'] = params.logline
  }
  if (pid === 'G1' || pid === 'G2') {
    vars['既有设定摘要'] = bible['基础设定'] ?? ''
    vars['基础设定'] = bible['基础设定'] ?? ''
    vars['既有力量体系'] = bible['力量体系'] ?? ''
    vars['题材'] = params.genre
    vars['故事核'] = params.story_core
    vars['对标书'] = params.benchmark
  }
  if (pid === 'G3' || pid === 'G4') {
    vars['角色卡合集'] = characters.map((c) => c._body ?? '').join('\n\n')
    vars['角色需求清单'] = params.character_requirements
  }
  return vars
}

export async function genArchive(
  book: string,
  pid: string,
  variables: Record<string, string>,
  signal?: AbortSignal,
): Promise<AIGenResult> {
  return aiGenerate(book, pid, variables, signal)
}

export function adoptSummary(target: string, r: Record<string, unknown>): string {
  if (target === 'bible')
    return `圣经已合并：更新 ${Array.isArray(r.updated) ? r.updated.length : 0} 节（其余节保留）`
  if (target === 'characters') {
    const created = Array.isArray(r.created) ? (r.created as string[]) : []
    const kept = Array.isArray(r.kept) ? (r.kept as string[]) : []
    return `角色卡已落盘：新建 ${created.length} 个${created.length ? `（${created.join('、')}）` : ''}，保留已有 ${kept.length} 个，写回关系 ${Number(r.relations_written ?? 0)} 条`
  }
  return `大纲已采纳：章节 ${Number(r.chapter_rows ?? 0)} 行 / 卷 ${Number(r.volumes ?? 0)} 个；伏笔新增 ${Number(r.foreshadows_added ?? 0)} 条、保留 ${Number(r.foreshadows_kept ?? 0)} 条；正文未重写`
}

/** 章号列表压成区间文本：7-12 / 1、3、7-9（规格08 A-T04 展示用）。 */
export function compressNumbers(numbers: number[]): string {
  if (!numbers.length) return '无'
  const sorted = [...numbers].sort((a, b) => a - b)
  const parts: string[] = []
  let start = sorted[0]
  let prev = sorted[0]
  for (const n of sorted.slice(1)) {
    if (n === prev + 1) {
      prev = n
      continue
    }
    parts.push(start === prev ? String(start) : `${start}-${prev}`)
    start = n
    prev = n
  }
  parts.push(start === prev ? String(start) : `${start}-${prev}`)
  return parts.join('、')
}

/**
 * G5 影响预览的中文摘要（规格08 A-T04）。
 * 关键：未知一律说"无法判定"，绝不显示成 0，避免作者把"查不了"当成"没影响"。
 */
export function outlineImpactText(p: OutlinePreview): string {
  const lines: string[] = []
  lines.push(
    `替换 OUTLINE.md 章节总表/卷规划/爽点节奏（${p.chapter_rows} 行、${p.volumes} 卷），并合并 PLOT_POINTS.md（新增 ${p.foreshadows_added} 条、保留 ${p.foreshadows_kept} 条）。`,
  )
  if (p.written.known) {
    const affected = p.affected_total === null ? '无法判定' : `${p.affected_total} 章`
    lines.push(
      `已有正文 ${p.written.total} 章（${compressNumbers(p.written.numbers)}）；本次替换范围内命中 ${affected}：${compressNumbers(p.affected_numbers)}。正文不会随大纲采纳自动重写。`,
    )
  } else {
    lines.push('已有正文章数无法判定（章节文件读取失败），不要据此认为"没有影响"；修复后再采纳。')
  }
  lines.push(
    `新增章号：${compressNumbers(p.added_numbers)}｜删除章号：${compressNumbers(p.removed_numbers)}｜内容变化：${compressNumbers(p.changed_numbers)}`,
  )
  if (!p.old_readable) lines.push('旧 OUTLINE.md 读取失败，差异与旧章号无法判定。')
  if (!p.plot_readable) lines.push('旧 PLOT_POINTS.md 读取失败，伏笔基线无法判定。')
  for (const w of p.warnings) lines.push(`警告：${w}`)
  return lines.join('\n')
}

/** 预览里的旧/新全文（含伏笔台账）拼成 diff 文本，标记缺失以免被当成"删空了"。 */
export function outlineDiffText(
  outlineText: string,
  plotText: string | null,
  label: string,
): string {
  return `${outlineText.trim()}\n\n## 伏笔台账\n\n${plotText === null ? `（${label}：无法判定）` : plotText.trim()}`
}
