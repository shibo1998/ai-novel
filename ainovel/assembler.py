"""上下文装配器：13 槽位 + 预算截断 + 预览（规格03 §2.2；槽位表=开发计划.md §2.3 原文）。

13 槽位（开发计划 §2.3）：
  1 system            系统角色（S0）
  2 system_rules      全局硬规则（S1 去 AI 味禁令）
  3 genre             题材适配（S2）
  4 bible_core        Bible 相关条目（≤800 字预算参考）
  5 power_rows        力量体系对应行（≤200 字预算参考）      【永不截断·规格03】
  6 char_cards        出场角色状态卡                        【永不截断·规格03】
  7 volume_rhythm     卷节奏位置 + 本章情绪任务
  8 active_foreshadows 活跃伏笔线程（≤300 字预算参考）
  9 recaps            前情摘要（全局摘要 + 最近 2 章）
 10 fts_recall        前文检索片段（FTS5 召回，≤800 字）     【最先砍·规格03】
 11 prev_ending       上一章末尾原文（衔接锚点，≤300 字）
 12 outline_contract  本章细纲（合同，全量注入）              【永不截断·规格03】
 13 output_rules      输出要求（字数/视角/格式/禁则）

截断顺序（规格03 §2.2，从先砍到后砍）：fts_recall → recaps → bible_core →
active_foreshadows → prev_ending。token 估算 = 非空白中文字符数 × 0.75。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import bible, books, chapters, characters, fts, outline, prompts, settings, summaries

NEVER_TRUNCATE = {"system", "system_rules", "genre", "power_rows", "char_cards",
                  "outline_contract"}
# 规格03 §2.2 原文（从先砍到后砍）：前文检索片段 → 最近章摘要 → Bible 条目。
# 力量体系行、出场角色卡、细纲合同永不截断（已在 NEVER_TRUNCATE）。
# 原实现多砍了 active_foreshadows / prev_ending（规格未授权），
# 也多护了 volume_rhythm / output_rules —— 这两处已按规格原文校正。
CUT_ORDER = ["fts_recall", "recaps", "bible_core"]

SLOT_TITLES = {
    "system": "系统角色", "system_rules": "全局硬规则", "genre": "题材适配",
    "bible_core": "Bible相关条目", "power_rows": "力量体系对应行",
    "char_cards": "出场角色状态卡", "volume_rhythm": "卷节奏位置+情绪任务",
    "active_foreshadows": "活跃伏笔线程", "recaps": "前情摘要",
    "fts_recall": "前文检索片段", "prev_ending": "上一章末尾原文",
    "outline_contract": "本章细纲合同", "output_rules": "输出要求",
}

MAX_FTS_PIECES = 8
PREV_ENDING_CHARS = 400


@dataclass
class Assembly:
    slots: dict[str, str] = field(default_factory=dict)
    est_tokens: dict[str, int] = field(default_factory=dict)
    truncated: set[str] = field(default_factory=set)
    budget_tokens: int = 0
    total_est_tokens: int = 0

    def preview(self) -> dict[str, Any]:
        return {
            "budget_tokens": self.budget_tokens,
            "total_est_tokens": self.total_est_tokens,
            "slots": [
                {"name": name, "title": SLOT_TITLES.get(name, name),
                 "est_tokens": self.est_tokens.get(name, 0),
                 "chars": len(self.slots.get(name, "")),
                 "truncated": name in self.truncated,
                 "content": self.slots.get(name, "")}
                for name in SLOT_TITLES if name in self.slots
            ],
        }

    def context_text(self) -> str:
        """拼装为发给模型的上下文正文（槽 1-3 由 render_system 作为 system 段注入）。"""
        parts = []
        for name in SLOT_TITLES:
            if name in ("system", "system_rules", "genre"):
                continue
            content = self.slots.get(name, "")
            if content:
                parts.append(f"【{SLOT_TITLES[name]}】\n{content}")
        return "\n\n".join(parts)


def est_tokens(text: str) -> int:
    return round(len(re.sub(r"\s+", "", text)) * 0.75)


def _extract_characters(contract: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for scene in contract.get("scenes", []) or []:
        for c in scene.get("chars", []) or []:
            n = c.get("name", "")
            if n and n not in names:
                names.append(n)
    pov = contract.get("pov", "")
    if pov and pov not in names:
        names.insert(0, pov)
    return names


def _keywords(book: str, contract: dict[str, Any], char_names: list[str]) -> list[str]:
    kws: list[str] = list(char_names)
    core = contract.get("core_event", "")
    scene_text = core + " " + " ".join(
        f"{s.get('goal','')}{s.get('conflict','')}{s.get('end_state','')}"
        for s in contract.get("scenes", []) or []
    )
    # 场景地名：从 LOCATIONS 里挑出现在本章文本中的
    try:
        for loc in outline.read_locations(book):
            name = loc.get("名称", "")
            if name and name in scene_text:
                kws.append(name)
    except Exception:
        pass
    # 核心事件关键词：按标点切出的 2-6 字片段
    for seg in re.split(r"[，。！？；、：\s]+", core):
        seg = seg.strip()
        if 2 <= len(seg) <= 6 and not seg.isdigit():
            kws.append(seg)
    seen: set[str] = set()
    out = []
    for k in kws:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out[:12]


def _char_card_text(data: dict[str, Any], state_row: dict[str, Any] | None = None) -> str:
    """角色卡槽：结构化状态优先（来自 chapter_state），无快照时回退 frontmatter。

    注意：**不用状态行替掉角色卡正文**。状态是"现在在哪、伤成什么样"，
    卡正文是"这人是谁、怎么说话"——写作两者都需要，砍掉后者是质量事故。
    """
    if state_row:
        parts = [f"{k}={state_row.get(k)}" for k in
                 ("location", "physical", "mental", "power_level", "arc_stage")
                 if state_row.get(k)]
        st = "、".join(parts) or "（本章无状态变化）"
        st += "　【结构化快照】"
    else:
        status = data.get("status", {})
        st = "、".join(f"{k}={status.get(k, '')}" for k in
                       ("realm", "location", "emotion", "alive") if status.get(k) != "")
        # 没有快照时只有「档案状态」，而档案可能已经更新到很后面的章。
        # 标出更新时间，模型才不会把"全书终态"当成"本章开始时"。
        upd = status.get("updated_chapter")
        if upd:
            st += f"　【档案状态，更新于第{upd}章，可能晚于本章】"
    return f"### {data['name']}（{data.get('role','')}）\n当前状态：{st}\n{data.get('_body','').strip()}"


def assemble(book: str, chapter_no: int) -> Assembly:
    """装配本章上下文（13 槽位）。要求细纲已存在（未确认也可预览）。"""
    cfg = books.load_config(book)
    contract = chapters.read_outline_contract(book, chapter_no)
    char_names = _extract_characters(contract)
    chars_data = {}
    for name in char_names:
        try:
            chars_data[name] = characters.read(book, name)
        except Exception:
            chars_data[name] = None

    # --- 逐槽构建 ---
    slots: dict[str, str] = {}

    # 槽 1-3：系统角色 / 全局硬规则 / 题材适配（真正发给模型时进 system 段）
    slots["system"] = prompts.render("S0", {}, book)[1]
    slots["system_rules"] = prompts.render("S1", _s1_vars(), book)[1]
    try:
        slots["genre"] = prompts.render("S2", {}, book)[1]
    except prompts.PromptError:
        slots["genre"] = ""

    # 槽 4：Bible 相关条目（尾部追加本章知情禁区，规格05 §7.5）
    try:
        b = bible.read(book)
        slots["bible_core"] = "\n\n".join(
            f"### {k}\n{v}" for k, v in b.items() if k in ("基础设定", "势力格局", "历史脉络") and v
        )
    except Exception:
        slots["bible_core"] = "（世界观圣经未建）"
    try:
        from . import timeline as timeline_mod

        constraints = timeline_mod.constraints_for_chapter(book, chapter_no)
        if constraints and not constraints.startswith("（"):
            slots["bible_core"] = (slots["bible_core"] +
                                   f"\n\n【时间线约束】\n{constraints}").strip()
    except Exception:
        pass

    # 槽 5：力量体系对应行
    try:
        rows = bible.power_rows(book)
        slots["power_rows"] = "\n".join(
            " | ".join(f"{c}:{r.get(c, '')}" for c in ("大境界", "子境界", "突破条件", "能力边界(不能做什么)"))
            for r in rows
        ) or "（力量体系未建）"
    except Exception:
        slots["power_rows"] = "（力量体系未建）"

    # 槽 6：出场角色状态卡（含当前状态，永不截断·规格03）
    # F-05：状态部分优先用 chapter_state 的结构化快照（准、短），卡正文照旧保留
    state_rows: dict[str, dict[str, Any]] = {}
    try:
        from . import state as state_mod

        state_rows = state_mod.confirmed_map(book, chapter_no - 1)
    except Exception:
        state_rows = {}
    cards = []
    for name, data in chars_data.items():
        if data is None:
            cards.append(f"### {name}\n（档案缺失——按细纲场景中的 state 字段执行，禁止扩写设定）")
        else:
            cards.append(_char_card_text(data, state_rows.get(name)))
    slots["char_cards"] = "\n\n".join(cards) or "（无出场角色）"

    # 槽 7：卷节奏位置 + 本章情绪任务
    od = outline.read_outline(book)
    vol_line, pos_label = "（卷信息缺失）", ""
    for v in od.get("volumes") or []:
        ch = v.get("chapters") or [0, 0]
        if len(ch) == 2 and ch[0] <= chapter_no <= ch[1]:
            span = max(1, ch[1] - ch[0] + 1)
            ratio = (chapter_no - ch[0] + 1) / span
            pos_label = ("rising_action（铺垫上升期）" if ratio <= 0.4
                         else "midpoint（中段推进期）" if ratio <= 0.75
                         else "climax（高潮趋近期）")
            vol_line = (f"卷{v.get('no')}「{v.get('name')}」[{ch[0]}-{ch[1]}章] 位置：{pos_label}"
                        f"｜核心冲突={v.get('core_conflict', '')}｜本卷目标={v.get('goal', '')}"
                        f"｜卷末高潮={v.get('climax', '')}｜卷末钩子={v.get('hook_out', '')}")
            break
    slots["volume_rhythm"] = (
        f"{vol_line}\n本章 stage={contract.get('stage')}，情绪任务={contract.get('emotion_task')}，"
        f"目的={contract.get('purpose')}\n情感弧线：{contract.get('emotion_arc')}"
    )

    # 槽 8：活跃伏笔线程
    act = outline.active_foreshadows(book)
    slots["active_foreshadows"] = "\n".join(
        f"{r.get('编号')}[{r.get('状态')}] {r.get('内容', '')}（埋设{r.get('埋设章', '')}章，"
        f"推进{r.get('推进章', '') or '—'}，预计回收{r.get('预计回收章', '') or '未定'}）"
        for r in act
    ) or "（无活跃伏笔）"

    # 槽 9：前情摘要（结构化近三章速记优先 + 全局摘要兜底）
    s = summaries.load(book)
    parts = []
    try:
        from . import state as state_mod

        recent_state = state_mod.recaps_text(book, chapter_no)
    except Exception:
        recent_state = ""
    if recent_state:
        parts.append(f"【结构化近三章速记】\n{recent_state}")
    if s.get("cumulative"):
        parts.append(f"【全书累计】{s['cumulative']}")
    recent = "\n".join(f"第{r['no']}章：{r['summary']}" for r in s.get("recent", []))
    if recent:
        parts.append(f"【最近章节】{recent}")
    slots["recaps"] = "\n\n".join(parts) or "（首章，无前情摘要）"

    # 槽 10：前文检索片段（FTS 召回）
    kws = _keywords(book, contract, char_names)
    pieces = fts.search(books.book_id(book), " ".join(kws), top_k=MAX_FTS_PIECES)
    slots["fts_recall"] = "\n".join(f"（第{no}章）{snip}" for no, snip in pieces) or "（无相关前文片段）"

    # 槽 11：上一章末尾原文
    if chapter_no > 1:
        try:
            prev = chapters.read_prose(book, chapter_no - 1)
            slots["prev_ending"] = prev["_body"].strip()[-PREV_ENDING_CHARS:]
        except Exception:
            slots["prev_ending"] = "（上一章正文缺失）"
    else:
        slots["prev_ending"] = "（本章为第一章）"

    # 槽 12：本章细纲合同（全量）
    slots["outline_contract"] = _render_contract(contract)

    # 槽 13：输出要求
    wr = contract.get("word_range") or cfg["chapter_word_range"]
    total = outline.total_chapters(book) or (chapter_no + 9)
    slots["output_rules"] = (
        f"章号：第{chapter_no}章 / 共约{total}章；主视角（pov）：{contract.get('pov')}\n"
        f"字数：{wr[0]}-{wr[1]} 字；格式：直接输出正文，无标题无开场白无 meta，段落 3-5 行；"
        f"章末落在钩子上戛然而止，禁止总结式收尾。"
    )

    # --- 预算截断（规格03 §2.2 顺序） ---
    a = Assembly(slots=slots)
    budget = int(settings.load().get("assembly_budget_tokens", 32000))
    overrides = cfg.get("assembly_budget_overrides", {})
    if "total" in overrides:
        budget = int(overrides["total"])
    a.budget_tokens = budget

    def total_est() -> int:
        return sum(est_tokens(v) for k, v in a.slots.items()
                   if k not in ("system", "system_rules", "genre"))  # 1-3 槽在 system 段另计

    for name in CUT_ORDER:
        if total_est() <= budget:
            break
        content = a.slots.get(name, "")
        if not content:
            continue
        per = overrides.get(name)
        if per is not None and est_tokens(content) <= int(per):
            continue  # 书级给该槽保底预算
        a.slots[name] = _truncate_keep_head(content, max(200, len(content) // 4))
        a.truncated.add(name)
        if total_est() > budget:
            a.slots[name] = ""
    a.total_est_tokens = total_est()
    # 逐槽 token 估算（规格03 §2.2：估算=中文字符数×0.75，记入 preview 供 UI 展示与排查）
    a.est_tokens = {k: est_tokens(v) for k, v in a.slots.items()}
    return a


def _s1_vars() -> dict[str, str]:
    from . import mechcheck

    banned = mechcheck.load_banned_words()
    pats = [p.pattern for p in mechcheck.load_dialog_patterns()]
    return {
        "禁词表": "\n".join(f"- {w}" for w in banned) or "（词库为空）",
        "对话后缀禁式": "\n".join(f"- {p}" for p in pats) or "（无）",
    }


def render_system(book: str, genre: str | None = None, style_card: str | None = None) -> str:
    """W1 的 system message：S0 + S1（词库同源注入）+ S2（都市）+ F2（M5 文风）。"""
    parts = [prompts.render("S0", {}, book)[1],
             prompts.render("S1", _s1_vars(), book)[1]]
    try:
        parts.append(prompts.render("S2", {}, book)[1])
    except prompts.PromptError:
        pass
    if style_card is None:
        # M5：书级启用且特征卡存在时自动注入
        from . import style as style_mod

        if style_mod.enabled(book):
            style_card = style_mod.read_card(book)
    if style_card:
        try:
            parts.append(prompts.render("F2", {"文风特征卡": style_card}, book)[1])
        except prompts.PromptError:
            pass
    return "\n\n".join(parts)


def _truncate_keep_head(text: str, keep_chars: int) -> str:
    keep_chars = max(0, keep_chars)
    if len(text) <= keep_chars:
        return text
    return text[:keep_chars].rsplit("\n", 1)[0] + "\n（……已截断……）"


def _render_contract(contract: dict[str, Any]) -> str:
    lines = [
        f"chapter_no={contract.get('chapter_no')} volume={contract.get('volume')} "
        f"stage={contract.get('stage')} emotion_task={contract.get('emotion_task')}",
        f"core_event：{contract.get('core_event')}",
        f"emotion_arc：{contract.get('emotion_arc')}",
        f"must_include：{contract.get('must_include')}",
        f"hook：{contract.get('hook')}",
        f"foreshadow_ops：{contract.get('foreshadow_ops')}",
        "scenes：",
    ]
    for i, sc in enumerate(contract.get("scenes", []) or [], 1):
        chars = "、".join(f"{c.get('name')}({c.get('state')})" for c in sc.get("chars", []) or [])
        lines.append(
            f"  场景{i}：目标={sc.get('goal')}｜冲突={sc.get('conflict')}｜出场={chars}｜结束状态={sc.get('end_state')}"
        )
    return "\n".join(lines)
