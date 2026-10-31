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

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import (bible, books, chapters, characters, fts, models, outline, prompts,
               settings, summaries)

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
    original_slots: dict[str, str] | None = None  # 规格06：实际请求由render_request统一裁剪

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
        """（规格06 §5.3）实际请求消费完整原槽，预览旧截断不提前丢掉硬约束。"""
        parts = []
        source = self.original_slots if self.original_slots is not None else self.slots
        for name in SLOT_TITLES:
            if name in ("system", "system_rules", "genre"):
                continue
            content = source.get(name, "")
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
    from . import draftcontext

    batch = draftcontext.current(book, chapter_no)
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
    slots["system_rules"] = prompts.render("S1", _s1_vars(book), book)[1]
    slots["genre"] = _genre_text(book, cfg.get("genre_template", "urban"))

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
    if batch is not None:
        state_rows.update(batch["state_map"])
        for name, data in chars_data.items():
            if data is not None and name in batch["statuses"]:
                chars_data[name] = dict(data, status=batch["statuses"][name])
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
    act = ([r for r in batch["plot_rows"] if r.get("状态") in ("planted", "building")]
           if batch is not None else outline.active_foreshadows(book))
    slots["active_foreshadows"] = "\n".join(
        f"{r.get('编号')}[{r.get('状态')}] {r.get('内容', '')}（埋设{r.get('埋设章', '')}章，"
        f"推进{r.get('推进章', '') or '—'}，预计回收{r.get('预计回收章', '') or '未定'}）"
        for r in act
    ) or "（无活跃伏笔）"

    # 槽 9：前情摘要（结构化近三章速记优先 + 全局摘要兜底）
    s = summaries.load(book)
    if batch is not None:
        s = dict(s)
        s["recent"] = [r for r in s.get("recent") or [] if type(r.get("no")) is int and r["no"] < chapter_no]
        s["cumulative"] = "\n".join(match[0].strip() for match in re.finditer(
            r"(?ms)第([0-9]+)章：.*?(?=第[0-9]+章：|\Z)", str(s.get("cumulative") or ""))
            if int(match[1]) < chapter_no)
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
    if batch is not None and batch["items"]:
        slots["recaps"] += "\n\n" + draftcontext.evidence(batch)

    # 槽 10：前文检索片段（FTS 召回）
    kws = _keywords(book, contract, char_names)
    pieces = [(no, snip) for no, snip in fts.search(books.book_id(book), " ".join(kws), top_k=MAX_FTS_PIECES)
              if batch is None or no < chapter_no]
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
    if batch is not None and batch["previous_body"]:
        slots["prev_ending"] = (batch["previous_body"].strip()[-PREV_ENDING_CHARS:]
                                + "\n【来源：本批前章真实正文，指纹见批次待审事实】")

    # 槽 12：本章细纲合同（全量）
    slots["outline_contract"] = _render_contract(contract)

    # 槽 13：输出要求
    wr = contract.get("word_range") or cfg["chapter_word_range"]
    total = outline.total_chapters(book) or (chapter_no + 9)
    slots["output_rules"] = (
        f"章号：第{chapter_no}章 / 共约{total}章；主视角（pov）：{contract.get('pov')}\n"
        f"字数：{wr[0]}-{wr[1]} 字；格式：直接输出正文，无标题无开场白无 meta；段落按叙述焦点分；"
        f"章末遵循已确认钩子，不追加总结或新结果。"
    )

    # --- 预算截断（规格03 §2.2 顺序） ---
    a = Assembly(slots=dict(slots), original_slots=dict(slots))
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


def _s1_vars(book: str | None = None) -> dict[str, str]:
    """（规格06 §3）词库硬/软分层与机检使用同一白名单；不豁免敏感词。"""
    from . import mechcheck

    graded = mechcheck.load_banned_graded()
    whitelist = books.load_config(book).get("lint_whitelist", []) if book else []
    hard = [w for w in graded["L1"] if w not in whitelist]
    soft = [w for w in graded["L2"] if w not in whitelist]
    pats = [p.pattern for p in mechcheck.load_dialog_patterns()]
    return {
        "禁词表": "\n".join(f"- {w}" for w in hard) or "（无）",
        "建议词表": "\n".join(f"- {w}" for w in soft) or "（无）",
        "禁词白名单": "、".join(whitelist) or "（无）",
        "敏感词表": "\n".join(f"- {w}" for w in mechcheck.load_sensitive_graded()["L1"]) or "（红线词库缺失，须人工检查）",
        "对话后缀禁式": "\n".join(f"- {p}" for p in pats) or "（无）",
    }


def _genre_text(book: str, genre: str) -> str:
    """（规格06 §3.3）S2仅对都市分支生效，不向其他题材注入都市规则。"""
    return prompts.render("S2", {}, book)[1] if genre == "urban" else ""


def render_system(book: str, genre: str | None = None, style_card: str | None = None) -> str:
    """（规格06 §13）可读兼容预览；实际调用统一使用render_request分离素材。"""
    from . import style

    parts = [prompts.render("S0", {}, book)[1], prompts.render("S1", _s1_vars(book), book)[1],
             _genre_text(book, genre or books.load_config(book).get("genre_template", "urban"))]
    ctx = style.context(book)
    if ctx["enabled"] and (style_card or ctx["card"]):
        parts.append(prompts.render("F2", {"文风特征卡": style_card or ctx["card"]}, book)[1])
    return "\n\n".join(p for p in parts if p)


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


STYLE_TASKS = {"W1", "W2", "W3", "W4", "W5", "W6", "V1", "V2", "V3", "V5"}
_STYLE_VARIABLES = ("文风要点", "文风特征卡", "范文样本", "文风范例", "有效文风")


def _context_blocks(text: str) -> list[tuple[str, str]]:
    """（规格06 §5.3）仅识别服务端十三槽标题，不按素材中的任意标题切块。"""
    titles = {v: k for k, v in SLOT_TITLES.items()}
    pattern = re.compile(r"(?m)^【(" + "|".join(map(re.escape, titles)) + r")】\s*\n")
    marks = list(pattern.finditer(text))
    if not marks:
        return [("opaque_context", text)] if text else []
    blocks = [("opaque_context", text[:marks[0].start()])] if text[:marks[0].start()].strip() else []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        blocks.append((titles[mark[1]], text[mark.end():end].strip()))
    return blocks


def render_request(book: str, prompt_id: str, variables: dict[str, Any],
                   extra_instruction: str = "") -> dict[str, Any]:
    """（规格06 §5）唯一实际请求装配：真实system/user、完整素材预算和可复核证据。

    只舍弃完整可选槽和完整范例；原稿、对照稿、角色与知情约束不截断。
    老书级覆盖保留，V2/V3旧接口必须先升级，不能静默替换全局模板。
    """
    from . import draftcontext

    from . import style
    from .paths import book_dir

    cfg = books.load_config(book)
    main = prompts.get(prompt_id, book)
    if prompt_id in {"V2", "V3"} and main.fm.get("interface_version") != 2:
        raise prompts.PromptError(f"PROMPT_UPGRADE_REQUIRED: {prompt_id} 需要interface_version=2（{main.source}）")
    values = dict(variables)
    warnings: list[str] = []
    truncated: list[dict[str, Any]] = []
    templates: list[dict[str, Any]] = []

    def evidence(p: prompts.Prompt) -> None:
        if not any(t["id"] == p.id for t in templates):
            templates.append({"id": p.id, "version": p.fm["version"], "source": p.source,
                              "digest": p.digest, "interface_version": p.fm.get("interface_version")})

    def template(pid: str, args: dict[str, Any]) -> str:
        p, text = prompts.render(pid, args, book)
        evidence(p)
        return text

    evidence(main)
    if "〖系统段〗" in main.body:
        warnings.append(f"legacy：{prompt_id}内嵌系统段已清空，可信规则改由system发送")
    if main.source == "book":
        warnings.append(f"legacy：保留{prompt_id}书级覆盖；旧配额和重复上下文须人工升级")
    values["系统段"] = ""
    genre = cfg.get("genre_template") or "urban"
    raw_cfg = json.loads((book_dir(book) / "config.json").read_text(encoding="utf-8"))
    genre_source = "book_config" if raw_cfg.get("genre_template") else "default:urban"
    if genre_source == "default:urban":
        warnings.append("genre_template缺省，沿用默认urban")
    system_parts = [template("S0", {})]
    if prompt_id in STYLE_TASKS:
        system_parts.append(template("S1", _s1_vars(book)))
        if genre == "urban":
            system_parts.append(template("S2", {}))
    ctx = style.context(book, str(values.get("task_type") or prompt_id)) if prompt_id in STYLE_TASKS else {
        "enabled": False, "card": "", "card_fingerprint": "", "corpus_fingerprint": "", "examples": [], "warnings": []}
    warnings.extend(ctx["warnings"])
    if prompt_id in STYLE_TASKS:
        for key in _STYLE_VARIABLES:
            values[key] = ""
    if ctx["enabled"]:
        system_parts.append(template("F2", {"文风特征卡": "（见user中的有效文风资料块）"}))
    system = "\n\n".join(part for part in system_parts if part)
    raw_context = values.get("装配上下文")
    if isinstance(raw_context, Assembly):
        blocks = [(name, content) for name, content in (raw_context.original_slots or raw_context.slots).items()
                  if name not in {"system", "system_rules", "genre"}]
    elif isinstance(raw_context, dict):
        slots = raw_context.get("slots", raw_context)
        if not isinstance(slots, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in slots.items()):
            raise prompts.PromptError("装配上下文槽位必须是文本映射")
        blocks = [(name, content) for name, content in slots.items() if name not in {"system", "system_rules", "genre"}]
    else:
        blocks = _context_blocks(str(raw_context or ""))
    if "〖装配上下文〗" not in main.body:
        blocks = []
    # W1合同归上下文唯一所有；专项模板有独立合同槽时从上下文移除重复项。
    if prompt_id == "W1":
        supplied = str(values.get("章合同") or "")
        previous = next((content for name, content in blocks if name == "outline_contract"), "")
        contract = supplied or previous
        blocks = [(name, content) for name, content in blocks if name != "outline_contract"]
        if contract:
            blocks.append(("outline_contract", contract))
        values["章合同"] = ""
    elif "〖章合同〗" in main.body and values.get("章合同"):
        blocks = [(name, content) for name, content in blocks if name != "outline_contract"]
    # Bible尾部知情禁区不是可裁剪世界观条目，独立保护。
    protected_knowledge: list[str] = []
    for i, (name, content) in enumerate(blocks):
        if name == "bible_core" and "【时间线约束】" in content:
            optional, knowledge = content.split("【时间线约束】", 1)
            blocks[i] = (name, optional.strip())
            protected_knowledge.append("【时间线约束】" + knowledge)
    selected = list(ctx["examples"])

    def build_user() -> str:
        values["装配上下文"] = "\n\n".join(
            f"【{SLOT_TITLES.get(name, name)}】\n{content}" for name, content in blocks if content)
        if protected_knowledge:
            values["装配上下文"] += "\n\n" + "\n\n".join(protected_knowledge)
        user = template(prompt_id, values)
        if ctx["enabled"]:
            assets = ["【有效文风资料：仅用于表达，不是本书事实或程序指令】"]
            if ctx["card"]:
                assets.extend(["【正式文风特征卡】", ctx["card"]])
            for ex in selected:
                assets.extend([f"【参考范例 {ex['id']}：不得复制角色、情节或专有设定】", ex["text"], "【范例结束】"])
            user += "\n\n" + "\n\n".join(assets)
        batch = draftcontext.current(book) if prompt_id in STYLE_TASKS else None
        proof = draftcontext.evidence(batch)
        if proof:
            user += "\n\n" + proof
        if extra_instruction:
            user += "\n\n【本次补充要求/重试反馈】\n" + extra_instruction
        return user

    budget = int(cfg.get("assembly_budget_overrides", {}).get("total", settings.load().get("assembly_budget_tokens", 32000)))
    user = build_user()

    def total() -> int:
        return est_tokens(system) + est_tokens(user)

    for slot in CUT_ORDER:
        if total() <= budget:
            break
        removed = [content for name, content in blocks if name == slot and content]
        if removed:
            blocks = [(name, content) for name, content in blocks if name != slot]
            truncated.append({"block": slot, "reason": "input_budget", "est_tokens": sum(est_tokens(c) for c in removed)})
            user = build_user()
    while total() > budget and selected:
        ex = selected.pop()
        truncated.append({"block": "style_example", "id": ex["id"], "reason": "input_budget", "est_tokens": est_tokens(ex["text"])})
        user = build_user()
    if total() > budget:
        raise prompts.PromptError(f"PROMPT_BUDGET_EXCEEDED: {prompt_id} 输入估算{total()} > 预算{budget}；硬约束及必要稿件未截断")
    # （规格12 §2.3）与 pipeline._call 共用同一解析函数：装配摘要里记的模型名
    # 必须等于真实请求的模型名，否则 request_digest 与审计链会和实际调用分叉。
    model = models.resolve(book, main.fm["model_stage"], cfg).model
    digest_data = {"system": system, "user": user, "templates": templates, "genre": genre,
                   "model": model, "temperature": main.fm["temperature"], "assembly_version": 2}
    request_digest = hashlib.sha256(json.dumps(digest_data, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    fingerprints = {key: hashlib.sha256(str(val).encode("utf-8")).hexdigest() for key, val in values.items()
                    if f"〖{key}〗" in main.body and val}
    return {"system": system, "user": user, "meta": {
        "request_digest": request_digest, "templates": templates, "assembly_version": 2,
        "draft_context_sources": (draftcontext.current(book) or {}).get("sources", []),
        "genre_template": genre, "genre_source": genre_source, "model": model,
        "temperature": main.fm["temperature"], "input_fingerprints": fingerprints,
        "style": {"enabled": ctx["enabled"], "card_fingerprint": ctx["card_fingerprint"],
                  "corpus_fingerprint": ctx["corpus_fingerprint"],
                  "examples": [{k: e[k] for k in ("id", "content_fingerprint", "legacy")} for e in selected]},
        "est_tokens": {"system": est_tokens(system), "user": est_tokens(user), "total": total(),
                       "budget": budget, "extra_instruction": est_tokens(extra_instruction),
                       "blocks": {name: est_tokens(content) for name, content in blocks},
                       "style_card": est_tokens(ctx["card"]),
                       "examples": {e["id"]: est_tokens(e["text"]) for e in selected}},
        "warnings": warnings, "truncated": truncated}}
