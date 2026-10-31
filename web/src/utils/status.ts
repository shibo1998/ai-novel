// 规格07 UI-10～12：状态、规则、阶段与冲突类型的中文展示；原 token 仅用于诊断。
export type TagType = 'success' | 'danger' | 'warning' | 'primary' | 'info'

export const STATUS_LABEL: Readonly<Record<string, string>> = {
  awaiting_outline: '待生成细纲',
  outline_draft: '细纲草稿',
  outline_locked: '细纲已锁定',
  generating: '正文生成中',
  mech_check: '机械检查中',
  finalized: '已定稿',
  lint_fix: '自动修复中',
  llm_review: '模型审校中',
  targeted_fix: '定向修复中',
  human_review: '待人工复核',
  pending_finalize: '待定稿',
  error: '出错待恢复',
}

export const RULE_LABEL: Readonly<Record<string, string>> = {
  'M-01': '字数越界（M-01）',
  'M-02': '禁词检查（M-02）',
  'M-03': '对话后缀（M-03）',
  'M-04': '敏感词红线（M-04）',
  'M-05': '单句逗号密度（M-05）',
  'M-06': '感叹号与省略号密度（M-06）',
  'M-07': '连续平句（M-07）',
  'M-08': '超长段落（M-08）',
  'M-09': '段首重复（M-09）',
  'M-10': '章末收束套话（M-10）',
  'M-11': '第一人称占比（M-11）',
  'M-12': 'AI 味参考（M-12）',
  'M-13': '战力倒退（M-13）',
  'M-14': '死者复现（M-14）',
  'M-15': '位置跳跃（M-15）',
  'M-16': '时间线格式（M-16）',
  'M-17': '悬空引用（M-17）',
  'M-18': '开篇衔接（M-18）',
}

export const CONFLICT_KIND_LABEL: Readonly<Record<string, string>> = {
  new_setting: '新设定待补录',
  timeline_candidate: '时间线候选',
  foreshadow_op: '伏笔操作待处理',
  dead_reappear: '死者复现',
  missing_char: '角色缺档',
  realm_regress: '境界倒退',
  realm_name: '境界名称待核对',
  unknown_field: '角色字段待核对',
}

export function statusLabel(token: string | null | undefined): string {
  if (!token) return '状态未知'
  return STATUS_LABEL[token] ?? `未知状态（${token}）`
}

export function ruleLabel(rule: string): string {
  return RULE_LABEL[rule] ?? (rule.startsWith('D-') ? `文风参考（${rule}）` : `未知规则（${rule}）`)
}

export function conflictKindLabel(kind: string): string {
  return CONFLICT_KIND_LABEL[kind] ?? `未知类型（${kind}）`
}

export function levelLabel(level: string | null | undefined): string {
  const labels: Record<string, string> = {
    block: '阻断项',
    blocking: '阻断项',
    warn: '提醒项',
    warning: '提醒项',
    advisory: '参考项',
    pass: '通过',
    fix: '需修复',
    ok: '正常',
    fingerprint: '正文指纹',
    verdict: '结论',
  }
  if (!level) return '级别未知'
  return labels[level] ?? `未知级别（${level}）`
}

export function verdictLabel(verdict: string | null | undefined): string {
  if (!verdict) return '尚无结论'
  return levelLabel(verdict) === `未知级别（${verdict}）`
    ? `未知结论（${verdict}）`
    : levelLabel(verdict)
}

export function stageLabel(stage: string): string {
  const labels: Record<string, string> = {
    'P-G6': '细纲生成（P-G6）',
    'P-V1': '模型审校（P-V1）',
    'P-V2': '审校复核（P-V2）',
    'P-V3': '候选校验（P-V3）',
    'P-V4': '记忆卡回写（P-V4）',
    'P-V5': '定向修复（P-V5）',
    'P-V6': '卷末审计（P-V6）',
  }
  return labels[stage] ?? `阶段 ${stage}`
}

/** 规格12：模型五档的中文名。token（plan/prose/…）仍原样写进 config.json 与诊断日志，
 *  只在界面上中文化——改 token 会破坏既有书配置与接口契约。 */
export const MODEL_STAGE_LABEL: Readonly<Record<string, string>> = {
  plan: '规划',
  prose: '正文',
  polish: '润色',
  review: '审校',
  longctx: '长文通读',
}

export const MODEL_STAGE_HINT: Readonly<Record<string, string>> = {
  plan: '世界观 / 大纲 / 细纲',
  prose: '写章节正文',
  polish: '去 AI 味润色',
  review: '审校与定向修复',
  longctx: '卷末跨章通读',
}

export function modelStageLabel(stage: string): string {
  return MODEL_STAGE_LABEL[stage] ?? stage
}

export function tagType(s: string): TagType {
  if (['finalized', 'pass', 'ok', 'final'].includes(s)) return 'success'
  if (['block', 'blocking', 'bad', 'error', 'human_review'].includes(s)) return 'danger'
  if (['warn', 'pending_finalize', 'lint_fix', 'targeted_fix', 'outline_draft', 'fix'].includes(s))
    return 'warning'
  if (['generating', 'llm_review', 'mech_check'].includes(s)) return 'primary'
  return 'info'
}
