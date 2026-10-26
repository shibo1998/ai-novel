// G 链（G1-G5 档案 AI 生成）的提示词变量装配（规格02 §4）。
// 提示词渲染是严格模式：缺任何 〖变量〗 后端直接拒绝（404/502），
// 所以变量必须按 prompts/generate/G*.md 的清单逐一提供。
import type { AIGenResult } from '@/api'
import { aiGenerate, getBible, listCharactersFull } from '@/api'
import type { CharacterData } from '@/api/types'

/** 各 G 链的采纳目标（决定调 /adopt/{target}）。 */
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

/** 交互式补充单个提示词变量（UI 弹 prompt 输入框）；取消返回空串。 */
export type AskFn = (label: string, value?: string) => Promise<string>

/** 世界观摘要：圣经各节压成「【节名】内容」单行段，跳过空节与自检节。 */
export function bibleSummary(b: Record<string, string>): string {
  const lines = Object.entries(b)
    .filter(([title, content]) => content.trim() && !title.includes('自检'))
    .map(([title, content]) => `【${title}】${content.trim().replace(/\s+/g, ' ').slice(0, 600)}`)
  return lines.length ? lines.join('\n') : '（世界观圣经暂为空：请按题材常识构建）'
}

/** 角色卡摘要：每角色一行「名字（role）：境界/位置/情绪/存亡」关键动态状态。 */
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

/** 读圣经摘要：读失败也返回占位串，保证提示词变量齐全（缺失会被严格渲染拒绝）。 */
export async function bibleDigest(book: string): Promise<string> {
  try {
    return bibleSummary(await getBible(book))
  } catch {
    return '（世界观圣经读取失败）'
  }
}

/** 读角色卡摘要：同上，失败不阻断 G5。 */
export async function charDigest(book: string): Promise<string> {
  try {
    return charSummary(await listCharactersFull(book))
  } catch {
    return '（角色卡读取失败）'
  }
}

/** 按 prompt_id 装配全部变量并调用 AI 生成（G1-G5）。 */
export async function genArchive(book: string, pid: string, ask: AskFn): Promise<AIGenResult> {
  const vars: Record<string, string> = {}
  // G3/G4/G5 的提示词都要真实世界观摘要。G3/G4 早前硬编码 "见圣经"——模型收到的
  // 就是这三个字，等于没给世界观；G5 则因漏传该变量被后端严格渲染直接拒绝（404）。
  if (pid === 'G3' || pid === 'G4' || pid === 'G5') {
    vars['世界观摘要'] = await bibleDigest(book)
  }
  if (pid === 'G5') {
    // G5 必需 5 个变量：世界观摘要 / 角色卡摘要 / logline / 目标总章数 / 单章字数区间
    vars['角色卡摘要'] = await charDigest(book)
    vars['目标总章数'] = (await ask('目标总章数', '120')) || '120'
    vars['单章字数区间'] = '2200-2800'
    vars['logline'] = await ask('一句话故事（主角为了X必须做Y但障碍是Z）')
  }
  if (pid === 'G1' || pid === 'G2') {
    try {
      const b = await getBible(book)
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
      const cs = await listCharactersFull(book)
      vars['角色卡合集'] = cs.map((c) => c._body ?? '').join('\n\n')
    } catch {
      // 无角色时留空
    }
    vars['角色需求清单'] =
      (await ask('角色需求清单（每行：名字+定位+要求）', '林寒+主角+底层逆袭')) || ''
  }
  return aiGenerate(book, pid, vars)
}

/** 采纳结果的「人话摘要」（后端原始响应是 dict，直接 JSON.stringify 给用户看不懂）。 */
export function adoptSummary(target: string, r: Record<string, unknown>): string {
  if (target === 'bible') {
    const updated = Array.isArray(r.updated) ? r.updated.length : 0
    return `圣经已合并：更新 ${updated} 节（其余节保留原内容）`
  }
  if (target === 'characters') {
    const created = Array.isArray(r.created) ? (r.created as string[]) : []
    const kept = Array.isArray(r.kept) ? (r.kept as string[]) : []
    const relations = typeof r.relations_written === 'number' ? r.relations_written : 0
    return `角色卡已落盘：新建 ${created.length} 个${created.length ? `（${created.join('、')}）` : ''}，保留已有 ${kept.length} 个，写回关系行 ${relations} 条`
  }
  const rows = typeof r.chapter_rows === 'number' ? r.chapter_rows : 0
  const vols = typeof r.volumes === 'number' ? r.volumes : 0
  const added = typeof r.foreshadows_added === 'number' ? r.foreshadows_added : 0
  const kept = typeof r.foreshadows_kept === 'number' ? r.foreshadows_kept : 0
  return `大纲已合并：章节 ${rows} 行 / 卷 ${vols} 个；伏笔新增 ${added} 条、保留 ${kept} 条（不删已有台账）`
}
