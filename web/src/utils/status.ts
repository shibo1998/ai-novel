// 状态/级别 → element-plus tag 颜色（沿用旧 UI 的配色语义）。
export type TagType = "success" | "danger" | "warning" | "primary" | "info";

export function tagType(s: string): TagType {
  if (["finalized", "pass", "ok", "final"].includes(s)) return "success";
  if (["block", "bad", "error", "human_review"].includes(s)) return "danger";
  if (["warn", "pending_finalize", "lint_fix", "targeted_fix", "outline_draft", "fix"].includes(s))
    return "warning";
  if (["generating", "llm_review", "mech_check"].includes(s)) return "primary";
  return "info";
}
