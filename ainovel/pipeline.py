"""单章闭环管线编排（规格03 §1-§2）。

细纲生成(P-G6) → 人工确认关 → 上下文装配 → 正文生成(P-W1/W3)
  → 机械检查(V1-mech) → [阻断→P-V5→重查] → LLM审校(P-V1)
  → [fix→P-V5→重查重审, ≤2轮] → 人工定稿关 → 快照 → 记忆卡回写(P-V4)
  → (卷末) 跨章审计(P-V6)

状态迁移补充（图未画但语义必需，见 statemachine.py 顶部注释）：
- mech_check 自动修复一轮仍阻断 → human_review
- llm_review verdict=pass → pending_finalize
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Callable

from . import (assembler, bible, books, chapters, characters, conflicts, db,
               fts, frontmatter as FM, llm, mechcheck, outline, prompts,
               settings, statemachine, summaries)

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    pass


class FixOverreach(PipelineError):
    """V5 越权重写：未涉及问题句段的改动率 >10% → 丢弃输出，版本+1 走完整重生成。"""


# ---------------------------------------------------------------------------
# LLM 调用统一入口：渲染 + 计费 + 落 generation_log
# ---------------------------------------------------------------------------

def _call(
    book: str, no: int | None, stage: str, prompt_id: str,
    variables: dict[str, Any], client=None, stream_cb: Callable[[str], None] | None = None,
    extra_instruction: str = "",
) -> tuple[Any, str, str]:
    """返回 (LLMResult, prompt文件元数据, 完整渲染文本)。"""
    cfg = books.load_config(book)
    p, rendered = prompts.render(prompt_id, variables, book)
    model_stage = p.fm.get("model_stage")
    # settings.default_model 一键覆盖全部档位；留空则用书 config 的 model_stages
    model = (settings.load().get("default_model") or ""
             or cfg.get("model_stages", {}).get(model_stage, "deepseek-chat"))
    temperature = float(p.fm.get("temperature", 0.7))
    full_text = rendered + (f"\n\n{extra_instruction}" if extra_instruction else "")
    cli = client or llm.client()
    try:
        res = cli.chat(model=model, system="", user=full_text,
                       temperature=temperature, stream_cb=stream_cb)
        cost = settings.cost_cents(model, res.input_tokens, res.output_tokens)
        db.log_generation(
            book_id=books.book_id(book), chapter_no=no, stage=stage,
            prompt_id=prompt_id, prompt_version=str(p.fm.get("version")),
            prompt_digest=p.digest, model=model,
            input_tokens=res.input_tokens, output_tokens=res.output_tokens,
            cost=cost, status="ok",
        )
        return res, p, full_text
    except Exception as e:
        db.log_generation(
            book_id=books.book_id(book), chapter_no=no, stage=stage,
            prompt_id=prompt_id, prompt_version=str(p.fm.get("version")),
            prompt_digest=p.digest, model=model,
            status="error", error=str(e)[:500],
        )
        raise


def _feedback_instruction(error: str) -> str:
    return (f"【上次输出被程序驳回】\n{error}\n"
            f"请严格按任务要求修正后，重新输出完整结果。")


# ---------------------------------------------------------------------------
# 1. 细纲生成与确认关（规格03 §2.1）
# ---------------------------------------------------------------------------

def generate_outline(book: str, no: int, client=None) -> dict[str, Any]:
    conflicts.guard(book)  # 台账冲突队列非空 → 禁止开下一章细纲
    statemachine.ensure_chapter_row(book, no)
    st = statemachine.current_status(book, no)
    if st == "error":
        statemachine.transition(book, no, "retry")  # 从上次异常自动恢复
        st = statemachine.current_status(book, no)
    if st not in ("awaiting_outline", "outline_draft"):
        raise statemachine.StateError(f"第{no}章状态 {st} 不能生成细纲（需 awaiting_outline/outline_draft）")

    cfg = books.load_config(book)
    row = outline.chapter_row(book, no)
    if row is None:
        raise PipelineError(f"OUTLINE.md 章节总表缺第 {no} 章行，先补全书大纲")

    od = outline.read_outline(book)
    vols = od.get("volumes") or []
    vol = None
    for v in vols:
        ch = v.get("chapters") or [0, 0]
        if len(ch) == 2 and ch[0] <= no <= ch[1]:
            vol = v
            break
    vol_goal = (f"卷{vol.get('no')}「{vol.get('name')}」核心冲突={vol.get('core_conflict')}"
                f"｜目标={vol.get('goal')}｜卷末高潮={vol.get('climax')}") if vol else "（卷信息缺失）"

    prev_hook_content, prev_hook_type, prev_scene_struct = "", "", ""
    if no > 1:
        try:
            prev = chapters.read_outline_contract(book, no - 1)
            prev_hook_content = prev.get("hook", {}).get("content", "")
            prev_hook_type = prev.get("hook", {}).get("type", "")
            prev_scene_struct = "；".join(
                f"{s.get('goal', '')}×{s.get('conflict', '')}" for s in prev.get("scenes", []) or []
            )
        except Exception:
            pass

    char_states = []
    try:
        for name in characters.list_characters(book):
            d = characters.read(book, name)
            if d.get("role") in ("主角", "女主", "反派", "重要配角"):
                st2 = d.get("status", {})
                char_states.append(
                    f"{name}（{d.get('role')}）：境界={st2.get('realm','')}｜位置={st2.get('location','')}"
                    f"｜伤势={st2.get('injuries',[])}｜物品={st2.get('inventory',[])}"
                    f"｜情绪={st2.get('emotion','')}｜存活={st2.get('alive',True)}"
                    f"｜已知={st2.get('known_info',[])}"
                )
    except Exception:
        pass

    active = outline.active_foreshadows(book)
    fo_text = "\n".join(
        f"{r.get('编号')}[{r.get('状态')}] {r.get('内容','')}" for r in active
    ) or "（无活跃伏笔）"

    wr = cfg["chapter_word_range"]
    variables = {
        "全局大纲该章行": " | ".join(f"{k}={v}" for k, v in row.items()),
        "所属卷目标": vol_goal,
        "上一章钩子": prev_hook_content or "（第一章，无上章钩子；用开篇钩子设计）",
        "上一章钩子类型": prev_hook_type or "无",
        "上一章场景结构": prev_scene_struct or "（第一章）",
        "出场角色状态": "\n".join(char_states) or "（暂无角色档案）",
        "活跃伏笔线程": fo_text,
        "章号": str(no),
        "总章数": str(outline.total_chapters(book) or no + 9),
        "字数区间": f"{wr[0]}-{wr[1]}",
    }

    feedback = ""
    fm2: dict[str, Any] | None = None
    body2 = ""
    last_err = ""
    for attempt in range(3):  # 生成 + 纠错回喂 ≤2 次
        res, _p, _t = _call(book, no, "outline", "G6", variables, client=client,
                            extra_instruction=feedback)
        try:
            fm2, body2 = _parse_outline_output(res.text, no)
            _validate_contract_ops(fm2, book, prev_hook_type)
            break
        except (FM.FrontmatterError, PipelineError) as e:
            last_err = str(e)
            _dump_failed_output(book, no, "outline", attempt, res.text)
            feedback = _feedback_instruction(last_err)
    if fm2 is None:
        statemachine.mark_error(book, no, last_err)
        raise PipelineError(f"第{no}章细纲生成 3 次未通过程序校验: {last_err}")

    fm2["word_range"] = wr
    fm2["confirmed"] = False
    chapters.write_outline_contract(book, no, fm2, body2)
    statemachine.transition(book, no, "generate_outline")
    return fm2


def _parse_outline_output(text: str, no: int) -> tuple[dict[str, Any], str]:
    extracted = _extract_contract_block(text)
    if extracted is None:
        raise FM.FrontmatterError(
            "输出中找不到章合同 YAML（必须含 chapter_no 字段；直接以 --- 开头输出，"
            "不要代码块围栏、不要开场白）")
    yaml_text, body = extracted
    fm2, body2 = FM.parse("---\n" + yaml_text + "\n---\n" + body)
    fm2["chapter_no"] = no  # 文件章号即真相
    _coerce_contract(fm2)
    FM.validate(fm2, chapters.OUTLINE_CONTRACT_SCHEMA)
    hook = fm2.get("hook") or {}
    if not isinstance(hook, dict) or hook.get("type") not in ("悬念", "选择", "反转"):
        raise FM.FrontmatterError(f"hook.type 非法: {hook.get('type')!r}（须 悬念|选择|反转）")
    return fm2, body2


def _extract_contract_block(text: str) -> tuple[str, str] | None:
    """从模型输出里宽容提取章合同 YAML 与正文。

    兼容：标准 frontmatter / ```yaml 围栏（带或不带 ---）/ 裸 YAML（chapter_no: 起，
    之后跟 Markdown 正文）。
    """
    # 1) 标准 frontmatter
    m = re.search(r"(?s)\A\s*---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(.*)", text)
    if m and "chapter_no" in m.group(1):
        return m.group(1).strip(), m.group(2).lstrip("\r\n")
    # 2) 围栏代码块
    for fence in re.finditer(r"```[a-zA-Z]*[ \t]*\r?\n(.*?)```", text, re.DOTALL):
        inner = fence.group(1).strip()
        if "chapter_no" not in inner:
            continue
        m2 = re.match(r"(?s)---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(.*)", inner)
        if m2:
            return m2.group(1).strip(), m2.group(2).strip()
        tail = text[fence.end():].lstrip("\r\n")
        return inner, tail
    # 3) 裸 YAML：从 chapter_no: 行（或其上方最近的 --- 行）到首个 Markdown 标题/场景清单
    m3 = re.search(r"(?m)^(---[ \t]*\r?\n)?chapter_no\s*:", text)
    if m3:
        start = m3.start()
        rest = text[start:]
        rest = re.sub(r"\A---[ \t]*\r?\n", "", rest)
        endm = re.search(r"(?m)^(?:#{1,6}[ \t]|={2,}|-{3,}[ \t]*$)", rest)
        if endm:
            return rest[: endm.start()].strip(), rest[endm.start():].strip()
        return rest.strip(), ""
    return None


def _dump_failed_output(book: str, no: int, stage: str, attempt: int, text: str) -> None:
    from . import atomic
    from .paths import data_dir

    d = data_dir() / "failed_outputs"
    d.mkdir(parents=True, exist_ok=True)
    atomic.write_text(d / f"{book}_{no:03d}_{stage}_{attempt}.txt", text)


def _coerce_contract(fm2: dict[str, Any]) -> None:
    """真实模型输出的常见类型偏差就地归一化（word_range 字符串、purpose 逗号串等）。"""
    wr = fm2.get("word_range")
    if isinstance(wr, str):
        nums = [int(x) for x in re.findall(r"\d+", wr)]
        fm2["word_range"] = (nums + nums + [2200, 2800])[:2]
    elif not isinstance(wr, list) or len(wr) != 2:
        fm2["word_range"] = [2200, 2800]
    for key in ("purpose", "must_include"):
        v = fm2.get(key)
        if isinstance(v, str):
            fm2[key] = [x.strip() for x in re.split(r"[,，、;；]", v) if x.strip()]
    if isinstance(fm2.get("foreshadow_ops"), str):
        fm2["foreshadow_ops"] = []


def _validate_contract_ops(fm2: dict[str, Any], book: str, prev_hook_type: str) -> None:
    """校验章合同的伏笔操作与钩子类型（规格02 P-G6 程序校验）。

    plant 也必须校验编号格式：原来 plant 直接 continue，导致任意字符串
    （如"随便写的编号"）都能入台账，后续 apply_foreshadow_ops 的编号匹配全乱。
    """
    hook_type = (fm2.get("hook") or {}).get("type", "")
    if prev_hook_type and hook_type == prev_hook_type:
        raise PipelineError(f"hook.type={hook_type} 与上一章相同，必须更换")
    rows = {r.get("编号"): r for r in outline.read_plot_points(book)}
    for op in fm2.get("foreshadow_ops") or []:
        fo_id = op.get("fo_id", "")
        kind = op.get("op", "")
        if kind == "plant":
            if not re.fullmatch(r"FO-\d{3}", str(fo_id)):
                raise PipelineError(
                    f"plant 的 fo_id 必须为 FO-xxx 三位编号格式，实际 {fo_id!r}")
            if fo_id in rows:
                raise PipelineError(f"{fo_id} 台账已存在，不能重复埋设")
            continue
        row = rows.get(fo_id)
        if row is None:
            raise PipelineError(f"foreshadow_ops 引用不存在的编号 {fo_id}")
        if kind == "pay" and row.get("状态") != "building":
            raise PipelineError(f"{fo_id} 状态为 {row.get('状态')}，pay 要求 building")


def confirm_outline(book: str, no: int) -> None:
    chapters.confirm_outline(book, no)
    statemachine.transition(book, no, "confirm")


# ---------------------------------------------------------------------------
# 2/3. 装配 + 正文生成（规格03 §2.2/§2.3）
# ---------------------------------------------------------------------------

def build_preview(book: str, no: int) -> dict[str, Any]:
    a = assembler.assemble(book, no)
    preview = a.preview()
    path = _save_preview(book, no, preview)
    preview["saved_to"] = str(path)
    return preview


def _save_preview(book: str, no: int, preview: dict[str, Any]):
    from . import atomic
    from .paths import data_dir

    d = data_dir() / "assembly_previews" / book
    d.mkdir(parents=True, exist_ok=True)
    row = statemachine.get_row(book, no)
    path = d / f"{no:03d}_v{row['current_version'] if row else 1}.json"
    atomic.write_text(path, json.dumps(preview, ensure_ascii=False, indent=2))
    return path


def _style_excerpt(book: str, chars: int = 600) -> str:
    """从书级语料随机取一段真实原文做 few-shot 范文（模仿笔触，禁复制内容）。"""
    import random

    from . import style as style_mod

    try:
        corpus = style_mod.read_corpus(book).replace("\u3000", "")
        corpus = re.sub(r"\n{3,}", "\n\n", corpus).strip()
    except Exception:
        return ""
    if len(corpus) < chars * 2:
        return corpus
    start = random.randrange(0, len(corpus) - chars)
    excerpt = corpus[start:start + chars]
    nl = excerpt.find("\n\n")
    if 0 < nl < chars // 2:
        excerpt = excerpt[nl + 2:]
    return excerpt.strip()


def _style_points(book: str) -> str:
    """特征卡浓缩（句法特征+禁忌清单）供 V1 审校参照目标文风。"""
    from . import style as style_mod

    try:
        card = style_mod.read_card(book)
        if not card:
            return "（未启用文风系统）"
        m1 = re.search(r"(?ms)^##\s*句法特征\s*\n(.*?)(?=^##|\Z)", card)
        m2 = re.search(r"(?ms)^##\s*禁忌清单\s*\n(.*?)(?=^##|\Z)", card)
        parts = []
        if m1:
            parts.append("句法特征：" + re.sub(r"\s+", " ", m1.group(1))[:400])
        if m2:
            parts.append("禁忌清单：" + re.sub(r"\s+", " ", m2.group(1))[:300])
        return "\n".join(parts) or "（特征卡为空）"
    except Exception:
        return "（未启用文风系统）"


def generate_prose(book: str, no: int, client=None,
                   stream_cb: Callable[[str], None] | None = None,
                   prompt_id: str | None = None) -> dict[str, Any]:
    """prompt_id 可强制指定场景专项（W2 对话/W4 突破/W5 高潮）；默认自动：
    卷首三章 W3 黄金三章专项，其余 W1 常规。
    书 config scene_mode=true 时逐场景生成再缝合（每场景独立注意力+字数配额）。

    异常处理（规格03 §3）：任何 LLM/装配异常都必须把状态落 error 后原样抛出，
    否则该章会永久卡死在 generating（generating 无 retry 出边，UI 无救援入口）。
    """
    st = statemachine.current_status(book, no)
    if st == "outline_locked":
        statemachine.transition(book, no, "start_generate")
    elif st not in ("generating", "error"):
        raise statemachine.StateError(f"第{no}章状态 {st} 不能生成正文")
    elif st == "error":
        statemachine.transition(book, no, "retry")  # error --retry--> 原状态（→generating）

    try:
        return _generate_prose_inner(book, no, client=client, stream_cb=stream_cb,
                                     prompt_id=prompt_id)
    except statemachine.StateError:
        raise  # 状态机自身的错误不再改写状态（避免掩盖真实原因）
    except Exception as e:
        logger.warning("第%s章正文生成失败，状态落 error 以便重试: %s", no, e)
        statemachine.mark_error(book, no, f"正文生成失败: {e}")
        raise


DRAFT_FLUSH_CHARS = 400  # 半成品落盘节流：每累积约这么多字写一次盘


def _draft_cb(book: str, no: int,
              outer: Callable[[str], None] | None) -> Callable[[str], None]:
    """包一层 stream_cb：转发给调用方的同时，周期性把半成品写盘（规格03 §5）。

    生成中途崩溃/断网时，已生成的部分可从 `<no>_正文.draft.md` 取回，
    重开可提示"继续生成或重来"；正式正文文件全程不被污染。
    """
    state = {"buf": [], "flushed": 0}

    def cb(piece: str) -> None:
        state["buf"].append(piece)
        if outer:
            outer(piece)
        total = sum(len(p) for p in state["buf"])
        if total - state["flushed"] >= DRAFT_FLUSH_CHARS:
            state["flushed"] = total
            try:
                chapters.write_draft(book, no, "".join(state["buf"]))
            except Exception:  # 落盘失败不能影响生成主流程
                logger.debug("第%s章半成品草稿落盘失败（忽略）", no)

    cb.state = state  # type: ignore[attr-defined]
    return cb


def _generate_prose_inner(book: str, no: int, client=None,
                          stream_cb: Callable[[str], None] | None = None,
                          prompt_id: str | None = None) -> dict[str, Any]:
    cfg = books.load_config(book)
    wr = cfg["chapter_word_range"]
    a = assembler.assemble(book, no)
    _save_preview(book, no, a.preview())

    # 半成品草稿：流式途中周期性落盘，崩溃可续（规格03 §5）
    draft_cb = _draft_cb(book, no, stream_cb)
    chapters.clear_draft(book, no)  # 新一轮生成从干净状态开始

    contract = chapters.read_outline_contract(book, no)
    contract_text = assembler._render_contract(contract)
    excerpt = _style_excerpt(book) if cfg.get("style_enabled") else ""

    if prompt_id not in ("W1", "W2", "W3", "W4", "W5"):
        prompt_id = "W3" if no <= 3 else "W1"

    variables: dict[str, Any] = {"系统段": assembler.render_system(book),
                                 "范文样本": excerpt or "（无范文样本）"}
    if prompt_id in ("W1", "W3", "W4", "W5"):
        variables["装配上下文"] = a.context_text()
    variables["章合同"] = contract_text
    variables.update(_scene_special_vars(book, no, contract, prompt_id))
    # 字数区间对所有正文类提示词都是硬约束（W1/W2/W3/W4/W5）：
    # 原实现只给 W1/W3 传，导致场景专项被当作整章调用时没有任何字数约束，
    # 只能靠 M-01 事后兜底，用户看不出为什么生成完就变红。
    variables["字数区间"] = f"{wr[0]}-{wr[1]}"
    if prompt_id == "W1":
        variables["黄金三章附加要求"] = "（本章为黄金三章，严格执行专项标准）" if no <= 3 else ""
    elif prompt_id == "W3":
        variables["章号"] = str(no)

    # 场景级生成：细纲场景逐个生成再缝合（全章注意力分配到每个场景）
    scenes = contract.get("scenes") or []
    if cfg.get("scene_mode") and prompt_id == "W1" and len(scenes) >= 2:
        body, fr = _generate_by_scenes(book, no, variables, wr, scenes,
                                       client=client, stream_cb=draft_cb)
    else:
        res, _p, _t = _call(book, no, "prose", prompt_id, variables, client=client, stream_cb=draft_cb)
        body, fr = res.text.strip(), res.finish_reason
        # 截断/字数越界 → 自动重跑一次并在指令中强调（规格02 P-W1 失败处理）
        if fr == "length" or not _in_range(body, wr):
            emphasis = (f"\n\n【重跑强调】上次输出字数 {mechcheck.count_words(body)} "
                        f"（要求 {wr[0]}-{wr[1]}）或被截断（finish_reason={fr}）。"
                        f"本次必须输出完整正文且字数落在区间内。")
            res2, _p2, _t2 = _call(book, no, "prose", prompt_id, variables, client=client,
                                   stream_cb=draft_cb, extra_instruction=emphasis)
            if _better(res2.text, body, wr) or fr == "length":
                body, fr = res2.text.strip(), res2.finish_reason

    body = body.strip()
    row = statemachine.get_row(book, no)
    version = row["current_version"] if row else 1
    fm = {
        "chapter_no": no, "version": version, "status": "draft",
        "word_count": mechcheck.count_words(body), "finalized_at": None,
    }
    chapters.write_prose(book, no, fm, body + "\n")
    chapters.clear_draft(book, no)  # 成功落正式正文后清掉半成品
    statemachine.transition(book, no, "complete")  # → mech_check
    return {"word_count": fm["word_count"], "version": version, "finish_reason": fr,
            "mode": "scene" if cfg.get("scene_mode") else "whole"}


def _generate_by_scenes(book: str, no: int, base_vars: dict[str, Any], wr: list[int],
                        scenes: list[dict[str, Any]], client=None,
                        stream_cb: Callable[[str], None] | None = None) -> tuple[str, str]:
    """逐场景生成：每场景独立调用（全注意力）+ 字数配额 + 前场景结尾衔接。"""
    n = len(scenes)
    total_mid = (wr[0] + wr[1]) // 2
    per_mid = max(500, total_mid // n)
    # 配额从紧（总和留余量），防止各场景都顶配额导致全章超上限
    per_lo, per_hi = max(400, int(per_mid * 0.8)), int(per_mid * 1.15)
    parts: list[str] = []
    fr = "stop"
    for i, sc in enumerate(scenes, 1):
        chars = "、".join(f"{c.get('name')}({c.get('state')})" for c in sc.get("chars", []) or [])
        task = (
            f"【本场景生成任务】场景 {i}/{n}（只写这一个场景，禁止写到后续场景内容）\n"
            f"目标：{sc.get('goal')}\n冲突：{sc.get('conflict')}\n出场：{chars}\n"
            f"本场景结束状态：{sc.get('end_state')}\n本场景字数：{per_lo}-{per_hi} 字\n"
        )
        if i > 1 and parts:
            task += f"【上一场景结尾（开头直接衔接）】{parts[-1].strip()[-200:]}\n"
        else:
            task += "【本章开头】300 字内承接上一章钩子入题。\n"
        if i == n:
            task += "【收尾要求】本场景是全章末场景，必须落在章合同钩子指向的画面/台词上，戛然而止。\n"
        v = dict(base_vars)
        v["章合同"] = base_vars["章合同"] + "\n\n" + task
        v["字数区间"] = f"{per_lo}-{per_hi}"
        v.setdefault("黄金三章附加要求", "")
        res, _p, _t = _call(book, no, "prose", "W1", v, client=client,
                            stream_cb=stream_cb if i == n else None)
        if res.finish_reason == "length":
            fr = "length"
        parts.append(res.text.strip())
    return "\n\n".join(parts), fr


def _scene_special_vars(book: str, no: int, contract: dict[str, Any], prompt_id: str) -> dict[str, str]:
    """W2/W4/W5 场景专项的专属槽位变量。"""
    if prompt_id not in ("W2", "W4", "W5"):
        return {}
    states: list[str] = []
    for scene in contract.get("scenes", []) or []:
        for c in scene.get("chars", []) or []:
            name = c.get("name", "")
            if not name:
                continue
            try:
                d = characters.read(book, name)
                st = d.get("status", {})
                states.append(f"{name}：境界={st.get('realm','')}｜位置={st.get('location','')}"
                              f"｜伤势={st.get('injuries',[])}｜情绪={st.get('emotion','')}")
            except Exception:
                states.append(f"{name}：（档案缺失，按细纲 state={c.get('state','')} 执行）")
    arc = contract.get("emotion_arc") or {}
    if prompt_id == "W2":
        return {
            "本场景人物状态": "\n".join(states) or "（无）",
            "本场景潜台词要求": f"情绪弧线：{arc.get('from','')}→{arc.get('via','')}→{arc.get('to','')}；"
                              "关键冲突处至少一次答非所问式回避。",
        }
    rows = "\n".join(
        " | ".join(f"{c}:{r.get(c, '')}" for c in ("大境界", "子境界", "突破条件", "能力边界(不能做什么)"))
        for r in bible.power_rows(book)
    ) or "（力量体系未建）"
    fos = "\n".join(
        f"{r.get('编号')}[{r.get('状态')}] {r.get('内容','')}" for r in outline.active_foreshadows(book)
    ) or "（无活跃伏笔）"
    if prompt_id == "W4":
        return {"力量体系相关行": rows,
                "突破代价设定": "以章合同场景冲突与结束状态为准，禁止无代价突破"}
    return {"对抗双方状态与底牌": "\n".join(states) or "（无）", "已埋伏笔清单": fos}


def _in_range(text: str, wr: list[int]) -> bool:
    return wr[0] <= mechcheck.count_words(text) <= wr[1]


def _better(a: str, b: str, wr: list[int]) -> bool:
    mid = (wr[0] + wr[1]) / 2
    return abs(mechcheck.count_words(a) - mid) < abs(mechcheck.count_words(b) - mid)


# ---------------------------------------------------------------------------
# 4. 机械检查 + 自动修复（规格03 §2.4）
# ---------------------------------------------------------------------------

def run_mech_check(book: str, no: int, client=None, auto_fix: bool = True) -> list[mechcheck.Hit]:
    st = statemachine.current_status(book, no)
    if st != "mech_check":
        raise statemachine.StateError(f"第{no}章状态 {st} 不能跑机械检查")
    cfg = books.load_config(book)
    wr = cfg["chapter_word_range"]

    def _record(hits: list[mechcheck.Hit]) -> None:
        db.add_review(book_id=books.book_id(book), chapter_no=no,
                      version=_version(book, no), kind="mech",
                      issues=[h.to_dict() for h in hits],
                      verdict="block" if mechcheck.has_block(hits) else "pass")

    hits = _check(book, no, wr)
    _record(hits)
    if not mechcheck.has_block(hits):
        statemachine.transition(book, no, "pass")  # → llm_review
        return hits
    if mechcheck.has_sensitive(hits) or not auto_fix:
        logger.warning("第%s章 命中敏感词红线，阻断转人工（禁止自动改写）", no)
        statemachine.transition(book, no, "blocked_after_fix")  # → human_review（敏感词禁止自动改写）
        return hits

    logger.info("第%s章 机检阻断 %d 条，自动定向修复一轮", no, len(hits))
    statemachine.transition(book, no, "blocked")  # → lint_fix
    try:
        run_targeted_fix(book, no, hits, client=client)
    except FixOverreach:
        statemachine.transition(book, no, "recheck")       # lint_fix → mech_check
        statemachine.transition(book, no, "blocked_after_fix")  # → human_review（越权→完整重生成由人触发）
        return _check(book, no, wr)
    statemachine.transition(book, no, "recheck")  # → mech_check
    hits2 = _check(book, no, wr)
    _record(hits2)
    if mechcheck.has_block(hits2):
        statemachine.transition(book, no, "blocked_after_fix")  # 仍有阻断 → human_review
    else:
        statemachine.transition(book, no, "pass")  # → llm_review
    return hits2


def _lint_whitelist(book: str) -> list[str]:
    """书级禁词豁免：目标文风作者的招牌词与通用禁词冲突时豁免。"""
    try:
        return list(books.load_config(book).get("lint_whitelist", []))
    except Exception:
        return []


def _check(book: str, no: int, wr: list[int]) -> list[mechcheck.Hit]:
    prose = chapters.read_prose(book, no)
    return mechcheck.check(prose["_body"], word_range=wr, whitelist=_lint_whitelist(book))


def _version(book: str, no: int) -> int:
    row = statemachine.get_row(book, no)
    return row["current_version"] if row else 1


# ---------------------------------------------------------------------------
# P-V5 定向修复 + 越权护栏（规格02 P-V5）
# ---------------------------------------------------------------------------

def format_issues(issues: list[Any]) -> str:
    """把问题清单渲染成 V5 能读懂的文字。

    两种来源字段名不同，必须分别处理：
    - LLM 审校（P-V1）：category/severity/location/quote/description/suggestion
    - 机检（M-xx）：rule/level/line/word/message —— 从 review_records 读回来时
      是这种 dict，若走下面的 else 分支，渲染出来是 `[None/None] 位置：None｜
      原句：None｜问题：None`，模型收到的是一堆 None，于是原样返回原文。
      表现为「定向修复返回 ok、字数一字未变」——功能静默失效且不报错。
    """
    lines = []
    for it in issues:
        if isinstance(it, mechcheck.Hit):
            lines.append(f"- [{it.rule}/{it.level}] 第{it.line}行 ｜命中：{it.word}｜{it.message}｜修改建议：就地改写命中句段")
        elif isinstance(it, dict) and it.get("rule"):
            line_no = it.get("line") or 0
            where = f"第{line_no}行" if line_no else "全章"
            lines.append(
                f"- [{it.get('rule')}/{it.get('level')}] {where} ｜命中：{it.get('word')}"
                f"｜{it.get('message')}｜修改建议：就地改写命中句段，其余段落一字不动"
            )
        else:
            lines.append(
                f"- [{it.get('category')}/{it.get('severity')}] 位置：{it.get('location')}"
                f"｜原句：{it.get('quote')}｜问题：{it.get('description')}｜建议：{it.get('suggestion')}"
            )
    return "\n".join(lines)


def _assert_not_finalized(book: str, no: int, action: str) -> None:
    """定稿守卫：已定稿章节不得被静默改写（规格01 §4、规格03 §2.6、AGENTS.md 红线）。

    定稿即写快照，快照是重写/回滚的唯一依据。若定稿后正文还能被 /fix、/polish、
    /prose 改写，就会出现「正文已变、status 仍 final、快照未变」的三方不一致，
    导出的正文与快照对不上。要返工必须先显式解锁（finalized --unlock--> pending_finalize）。
    """
    st = statemachine.current_status(book, no)
    if st == "finalized":
        raise statemachine.StateError(
            f"第{no}章已定稿，不能{action}；如需返工请先解锁（定稿后正文受快照保护）"
        )


def run_targeted_fix(book: str, no: int, issues: list[Any], client=None) -> str:
    _assert_not_finalized(book, no, "定向修复")
    prose = chapters.read_prose(book, no)
    old_body = prose["_body"]
    cfg = books.load_config(book)
    wr = cfg["chapter_word_range"]
    variables = {
        "正文": old_body,
        "问题清单": format_issues(issues),
        "字数区间": f"{wr[0]}-{wr[1]}",
    }
    res, _p, _t = _call(book, no, "fix", "V5", variables, client=client)
    new_body = res.text.strip() + "\n"
    if _overreach(old_body, new_body, issues):
        logger.warning("第%s章 V5 越权护栏触发：未涉及句段改动率 >10%%，输出已丢弃", no)
        raise FixOverreach("未涉及问题句段的改动率 >10%，本次输出已丢弃")
    fm = dict(prose)
    fm.pop("_body")
    fm["word_count"] = mechcheck.count_words(new_body)
    chapters.write_prose(book, no, fm, new_body)
    return new_body


def _split_quote(q: str) -> list[str]:
    """把审校的 quote 拆成可用于单行匹配的片段。

    LLM 审校的 quote 常写成「句A / 句B / 句C」或「句A……句B」，整串拿去和
    单行比对永远匹配不上，保护集就空了。拆开、各截 12 字再匹配。
    """
    out: list[str] = []
    for frag in re.split(r"[／/｜|、；;\n]+|\.\.\.+|……+", q):
        frag = frag.strip()
        if len(frag) >= 4:
            out.append(frag[:12])
    return out


def _overreach(old: str, new: str, issues: list[Any]) -> bool:
    """未涉及问题句段的改动率超阈值 → 视为越权重写（规格02 P-V5 程序校验）。

    两个坑（2026-10-26 实测第 12 章修复时暴露）：
    1. 机检存进 review_records 的 dict 带的是 `line`，原实现只读 `quote`，
       于是机检的 8 处具体行一处都没被保护，全章都算「未涉及问题句段」。
    2. M-01（字数超限）、M-06（感叹号密度）这类问题是**全章级**的（line=0），
       天然要求通篇调整；按 10% 硬卡必然误杀，表现为「字数超 68 字的极小问题
       永远修不好，只能整章重写」。存在全章级问题时阈值放宽到 35%，
       仍然挡得住真正的整章重写。
    """
    import difflib

    old_lines = old.splitlines()
    new_lines = new.splitlines()
    protected: set[int] = set()
    whole_chapter = False

    def protect(idx: int) -> None:
        for d in (-1, 0, 1):
            i = idx + d
            if 0 <= i < len(old_lines):
                protected.add(i)

    for it in issues:
        if isinstance(it, mechcheck.Hit):
            if it.line > 0:
                protect(it.line - 1)
            else:
                whole_chapter = True
        elif isinstance(it, dict):
            line_no = it.get("line")
            if line_no is None:
                # LLM 审校（P-V1）不提供行号：location 是「全章多处」「章中段落」
                # 这类自然语言，quote 还是「A / B / C」跨多段拼接的串。
                # 程序无法可靠划定"哪些行不该动"，一律按全章级处理（阈值 35%）；
                # 同时尽力把 quote 里能对上的片段保护起来，减少可改范围。
                # 否则 10% 硬卡会让"删掉 11 处多余的比喻"这种正常修复永远失败。
                whole_chapter = True
                q = str(it.get("quote") or "")
                for frag in _split_quote(q):
                    for i, ln in enumerate(old_lines):
                        if frag in ln:
                            protect(i)
                            break
            elif line_no > 0:
                protect(line_no - 1)
            else:
                whole_chapter = True
    # 按行粒度：未被匹配块覆盖的旧行 = 被改动的行
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    matched: set[int] = set()
    for b in sm.get_matching_blocks():
        matched.update(range(b.a, b.a + b.size))
    changed_outside = sum(len(old_lines[i]) for i in range(len(old_lines))
                          if i not in matched and i not in protected)
    total = max(1, sum(len(l) for l in old_lines))
    limit = 0.35 if whole_chapter else 0.10
    return changed_outside / total > limit


# ---------------------------------------------------------------------------
# 5. LLM 审校闭环（规格03 §2.5）
# ---------------------------------------------------------------------------

def build_suspect_list(book: str, no: int) -> str:
    """程序预检疑似冲突清单：角色状态 diff、伏笔编号核对、术语表比对（规格02 P-V1 改造点3）。"""
    lines: list[str] = []
    try:
        contract = chapters.read_outline_contract(book, no)
    except Exception:
        return "（细纲读取失败，无预检）"

    # 角色状态 diff：细纲场景声明 state vs 档案当前状态
    for scene in contract.get("scenes", []) or []:
        for c in scene.get("chars", []) or []:
            name = c.get("name", "")
            want = (c.get("state") or "").strip()
            if not name or not want:
                continue
            try:
                d = characters.read(book, name)
                st = d.get("status", {})
                have = f"境界={st.get('realm','')}｜位置={st.get('location','')}｜伤势={st.get('injuries',[])}"
                lines.append(f"[状态diff] {name}：细纲要求进入状态「{want}」，档案当前：{have}——请核对正文是否完成该状态迁移")
            except Exception:
                lines.append(f"[缺档案] 角色 {name} 在细纲出场但 characters/ 无档案")

    # 伏笔编号核对
    rows = {r.get("编号"): r for r in outline.read_plot_points(book)}
    for op in contract.get("foreshadow_ops") or []:
        fo_id, kind = op.get("fo_id", ""), op.get("op", "")
        row = rows.get(fo_id)
        if row is None and kind != "plant":
            lines.append(f"[伏笔] {fo_id} 被{kind}但台账不存在")
        elif row and kind == "pay" and row.get("状态") != "building":
            lines.append(f"[伏笔] {fo_id} 状态 {row.get('状态')} 不满足 pay 前置（building）")

    # 术语表比对：Bible 术语在正文中未出现（提醒级）
    prose = None
    try:
        prose = chapters.read_prose(book, no)["_body"]
    except Exception:
        pass
    if prose:
        try:
            for term in bible.terms(book):
                if term not in prose:
                    lines.append(f"[术语] 「{term}」为本世界核心术语但正文未出现（若本章应涉及请指出）")
        except Exception:
            pass
    return "\n".join(lines) or "（程序预检未发现疑似冲突）"


RUBRIC_DIMS = ("ai_flavor", "plot", "pacing", "prose", "hook")
RUBRIC_FIX_THRESHOLD = 70  # 规格03 §6：任一维 <70 触发 fix


def _enforce_rubric(result: dict[str, Any]) -> str:
    """程序强制 rubric 守门（规格02 P-V1 改造点5、规格03 §6）。

    规格要求「存在 blocking 或任一维 <70 → fix」，但原实现只信模型自己给的
    verdict：模型若判 pass 却给出低分（放水/自相矛盾），程序直接放行到定稿。
    这里按分数与 blocking 问题重算 verdict，模型只能把结果判得更严、不能更松。
    """
    declared = str(result.get("verdict") or "").strip().lower()
    scores = result.get("scores") or {}
    low = [f"{k}={scores.get(k)}" for k in RUBRIC_DIMS
           if isinstance(scores.get(k), (int, float)) and scores[k] < RUBRIC_FIX_THRESHOLD]
    has_blocking = any(
        str(it.get("severity", "")).lower() == "blocking"
        for it in (result.get("issues") or []) if isinstance(it, dict)
    )

    if not low and not has_blocking:
        return declared or "pass"

    # 有硬伤：declared 若已是 fail 就保持，否则至少降到 fix
    if declared == "fail":
        return "fail"
    if declared == "pass":
        reason = []
        if low:
            reason.append(f"低分维度 {', '.join(low)}（阈值 {RUBRIC_FIX_THRESHOLD}）")
        if has_blocking:
            reason.append("存在 blocking 问题")
        logger.warning("rubric 守门：模型自称 pass，程序强制改判 fix（%s）", "；".join(reason))
        result["rubric_override"] = {
            "declared": declared, "forced": "fix",
            "low_dims": low, "has_blocking": has_blocking,
        }
        return "fix"
    return declared


def run_llm_review(book: str, no: int, client=None) -> dict[str, Any]:
    st = statemachine.current_status(book, no)
    if st != "llm_review":
        raise statemachine.StateError(f"第{no}章状态 {st} 不能跑 LLM 审校")
    cfg = books.load_config(book)
    wr = cfg["chapter_word_range"]
    max_rounds = int(cfg.get("review_fix_max_rounds", 2))
    row = statemachine.get_row(book, no)
    rounds = row["fix_rounds"] if row else 0

    prose = chapters.read_prose(book, no)
    contract = chapters.read_outline_contract(book, no)
    variables = {
        "系统段": assembler.render_system(book),
        "正文": prose["_body"],
        "章合同": assembler._render_contract(contract),
        "疑似冲突清单": build_suspect_list(book, no),
        "台账摘要": "\n".join(
            f"{r.get('编号')}[{r.get('状态')}] {r.get('内容','')}" for r in outline.read_plot_points(book)
        ) or "（台账为空）",
        "字数区间": f"{wr[0]}-{wr[1]}",
        "程序字数": str(mechcheck.count_words(prose["_body"])),
        "文风要点": _style_points(book),
    }

    result: dict[str, Any] | None = None
    feedback = ""
    for _attempt in range(3):  # JSON 解析失败 → 错误回喂重试 ×2
        res, _p, _t = _call(book, no, "review", "V1", variables, client=client,
                            extra_instruction=feedback)
        try:
            result = llm.extract_json(res.text)
            if not isinstance(result, dict) or "verdict" not in result:
                raise llm.LLMError("JSON 缺少 verdict 字段")
            result.setdefault("scores", {})
            result.setdefault("issues", [])
            break
        except llm.LLMError as e:
            feedback = _feedback_instruction(str(e))
    if result is None:
        statemachine.mark_error(book, no, "V1 JSON 解析失败")
        raise PipelineError("V1 审校 JSON 3 次解析失败")

    # 防橡皮章守门：pass + 零问题 + 全维 ≥90 视为放水嫌疑 → 复审一次（规格02 P-V1 举证要求）
    if (result["verdict"] == "pass" and not result["issues"]
            and all((result["scores"].get(k) or 0) >= 90
                    for k in ("ai_flavor", "plot", "pacing", "prose", "hook"))):
        res2, _p2, _t2 = _call(
            book, no, "review", "V1", variables, client=client,
            extra_instruction=(
                "【复审指令】上一轮审校给出了全维≥90且零问题，被系统判定为放水嫌疑。\n"
                "重新逐段排查：副词堆叠、重复句式、无信息量对话、概括式描写、情绪直白说出。\n"
                "至少给出 3 条带原文引用的问题，并按真实水平重新打分。"))
        try:
            second = llm.extract_json(res2.text)
            if isinstance(second, dict) and "verdict" in second:
                second.setdefault("scores", {})
                second.setdefault("issues", [])
                result = second
                result["re_reviewed"] = True
        except llm.LLMError:
            pass  # 复审解析失败则保留首轮结果

    db.add_review(book_id=books.book_id(book), chapter_no=no, version=_version(book, no),
                  kind="llm", scores=result.get("scores"), issues=result.get("issues"),
                  verdict=result.get("verdict"))

    verdict = _enforce_rubric(result)
    result["verdict"] = verdict
    if verdict == "pass":
        statemachine.transition(book, no, "pass")  # → pending_finalize
        return result

    if verdict == "fix" and rounds < max_rounds:
        statemachine.transition(book, no, "fix")  # → targeted_fix
        try:
            run_targeted_fix(book, no, result.get("issues", []), client=client)
        except FixOverreach:
            logger.warning("第%s章 V5 越权护栏触发（审校修复轮），转人工", no)
            statemachine.set_fix_rounds(book, no, rounds + 1)
            statemachine.transition(book, no, "recheck")  # targeted_fix → mech_check
            statemachine.transition(book, no, "blocked_after_fix")  # → human_review
            result["note"] = "V5 越权重写被丢弃，转人工（可触发完整重生成）"
            return result
        statemachine.set_fix_rounds(book, no, rounds + 1)
        statemachine.transition(book, no, "recheck")  # → mech_check
        run_mech_check(book, no, client=client, auto_fix=False)  # 重跑 2.4（阻断即转人工）
        if statemachine.current_status(book, no) == "llm_review":
            return run_llm_review(book, no, client=client)  # 重跑 2.5
        return result

    # fail 或轮次耗尽（含 rubric 守门多次改判 fix 后耗尽的情况）
    event = "fail" if verdict == "fail" else "rounds_exhausted"
    statemachine.transition(book, no, event)  # → human_review
    result["stopped_reason"] = ("轮次耗尽仍未达标" if event == "rounds_exhausted"
                                else "模型判定 fail")
    return result


# ---------------------------------------------------------------------------
# 5.5 整章润色（P-V2 去AI味 + P-V3 复核，M3；规格02 P-V2 改造点）
# ---------------------------------------------------------------------------

def polish(book: str, no: int, client=None) -> dict[str, Any]:
    """V2 整章润色：不改剧情人设；程序校验字数浮动≤10% + 机检全绿，否则丢弃。"""
    st = statemachine.current_status(book, no)
    if st not in ("pending_finalize", "human_review"):
        raise statemachine.StateError(f"第{no}章状态 {st} 不能润色（需 pending_finalize/human_review）")
    cfg = books.load_config(book)
    wr = cfg["chapter_word_range"]
    prose = chapters.read_prose(book, no)
    old_body = prose["_body"]
    old_wc = mechcheck.count_words(old_body)
    banned = "\n".join(f"- {w}" for w in mechcheck.load_banned_words()
                       if w not in _lint_whitelist(book)) or "（词库为空）"
    variables = {
        "系统段": assembler.render_system(book),
        "正文": old_body,
        "禁词表": banned,
        "对话后缀禁式": "\n".join(f"- {p.pattern}" for p in mechcheck.load_dialog_patterns()) or "（无）",
    }
    res, _p, _t = _call(book, no, "polish", "V2", variables, client=client)
    new_body, change_list = _parse_polish(res.text)
    new_wc = mechcheck.count_words(new_body)
    result: dict[str, Any] = {"word_count": {"before": old_wc, "after": new_wc},
                              "changes": change_list}
    if old_wc and abs(new_wc - old_wc) / old_wc > 0.10:
        result.update(ok=False, reason=f"字数浮动 {abs(new_wc - old_wc) / old_wc:.0%} 超 ±10%，输出已丢弃")
        return result
    blocks = [h.to_dict() for h in mechcheck.check(new_body, word_range=wr,
                                                    whitelist=_lint_whitelist(book))
              if h.level == "block"]
    if blocks:
        result.update(ok=False, reason="机械检查仍有阻断，输出已丢弃", blocks=blocks)
        return result

    # V3 AI味自检复核
    res3, _p3, _t3 = _call(book, no, "polish", "V3",
                           {"润色后全文": new_body, "改动清单": "\n".join(change_list),
                            "禁词表": banned}, client=client)
    try:
        v3 = llm.extract_json(res3.text)
    except llm.LLMError:
        v3 = {"clean": None, "verdict": "unknown", "new_issues": []}

    fm = {k: v for k, v in prose.items() if not k.startswith("_")}
    fm["word_count"] = new_wc
    chapters.write_prose(book, no, fm, new_body)
    db.add_review(book_id=books.book_id(book), chapter_no=no, version=fm["version"],
                  kind="polish", issues=change_list, verdict=v3.get("verdict"))
    result.update(ok=True, v3=v3)
    return result


def _parse_polish(text: str) -> tuple[str, list[str]]:
    m = re.search(r"===润色全文===\s*\n(.*?)\n\s*===改动清单===", text, re.DOTALL)
    if not m:
        return text.strip() + "\n", []
    body = m.group(1).strip()
    tail = text[m.end():].strip()
    changes = [ln for ln in tail.splitlines() if ln.strip().startswith("|")]
    return body + "\n", changes


# ---------------------------------------------------------------------------
# 6. 人工关口：豁免 / 定稿 / 重写（规格03 §2.5-§2.6、规格01 §4）
# ---------------------------------------------------------------------------

def exempt(book: str, no: int, reason: str) -> None:
    """豁免必须填理由，写入 generation_log（合规证据链）。"""
    if not reason or not reason.strip():
        raise PipelineError("豁免必须填写理由")
    statemachine.transition(book, no, "approve")  # human_review → pending_finalize
    db.log_generation(book_id=books.book_id(book), chapter_no=no, stage="exempt",
                      status="ok", error=f"豁免理由：{reason.strip()}")


def edit_outline(book: str, no: int) -> None:
    statemachine.transition(book, no, "edit_outline")  # → outline_draft（改细纲）


def human_rewrite(book: str, no: int, base_version: int | None = None) -> int:
    """human_review/finalized → generating，开新 version 分支（不覆盖旧 draft）。

    修复三处数据丢失隐患：
    1. 先校验状态再落盘——原实现是先写正文后迁状态，在非法状态下调用会
       「先毁数据再抛 StateError」，正文已被空 body 覆盖。
    2. base_version=None 时不再写空正文——原实现 body="" 直接覆盖同一路径
       （正文是单文件，路径不含 version），human_review 恰好无快照，一点即清空。
       现在回落到「沿用当前正文」作为新分支的起点。
    3. 快照不存在时在落盘前就抛错，不留下被清空的半成品。
    """
    st = statemachine.current_status(book, no)
    allowed = ("human_review", "finalized")
    if st not in allowed:
        raise statemachine.StateError(
            f"第{no}章状态 {st} 不能开新版本重写（需 {'/'.join(allowed)}）"
        )

    # 1) 先确定新版本的正文来源（任何取数失败都在落盘前抛出）
    if base_version is not None:
        snap = get_snapshot(book, no, base_version)
        if snap is None:
            raise PipelineError(f"快照 v{base_version} 不存在，未做任何改动")
        body = snap
    else:
        # 无指定快照：沿用当前正文开新分支（绝不写空正文）
        try:
            body = chapters.read_prose(book, no)["_body"]
        except Exception:
            body = ""

    # 2) 落盘新版本
    new_version = _next_version(book, no)
    fm = {"chapter_no": no, "version": new_version, "status": "draft",
          "word_count": mechcheck.count_words(body), "finalized_at": None}
    chapters.write_prose(book, no, fm, body)
    statemachine.set_current_version(book, no, new_version)
    statemachine.set_fix_rounds(book, no, 0)
    statemachine.transition(book, no, "rewrite")  # → generating
    return new_version


def _next_version(book: str, no: int) -> int:
    row = statemachine.get_row(book, no)
    cur = row["current_version"] if row else 1
    with db.tx() as conn:
        r2 = conn.execute(
            "SELECT MAX(version) m FROM snapshots WHERE book_id=? AND chapter_no=?",
            (books.book_id(book), no),
        ).fetchone()
    return max(cur, r2["m"] or 0) + 1


def get_snapshot(book: str, no: int, version: int) -> str | None:
    with db.tx() as conn:
        row = conn.execute(
            "SELECT content FROM snapshots WHERE book_id=? AND chapter_no=? AND version=?",
            (books.book_id(book), no, version),
        ).fetchone()
    return row["content"] if row else None


def list_versions(book: str, no: int) -> list[dict[str, Any]]:
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT version, label, created_at FROM snapshots WHERE book_id=? AND chapter_no=? ORDER BY version",
            (books.book_id(book), no),
        ).fetchall()
    return [dict(r) for r in rows]


def finalize(book: str, no: int, client=None) -> dict[str, Any]:
    """定稿 = 二次确认（API 层）→ 快照 + status:final → 记忆卡回写。"""
    st = statemachine.current_status(book, no)
    if st != "pending_finalize":
        raise statemachine.StateError(f"第{no}章状态 {st} 不能定稿")

    prose = chapters.read_prose(book, no)
    fm = {k: v for k, v in prose.items() if not k.startswith("_")}
    fm["status"] = "final"
    fm["finalized_at"] = datetime.now().isoformat(timespec="seconds")
    chapters.write_prose(book, no, fm, prose["_body"])
    statemachine.transition(book, no, "finalize")  # → finalized

    version = fm["version"]
    db.add_snapshot(book_id=books.book_id(book), chapter_no=no, version=version,
                    label=f"定稿 {fm['word_count']}字", content=prose["_body"])

    result = {"snapshot_version": version, "writeback": None, "audit": None}
    try:
        result["writeback"] = writeback_memory(book, no, client=client)
    except Exception as e:
        # 记忆卡失败不回滚定稿，但标记待重试
        statemachine.mark_error(book, no, f"记忆卡回写失败: {e}")
        result["writeback"] = {"error": str(e)}
        return result

    result["audit"] = _maybe_audit(book, no, client=client)
    return result


# ---------------------------------------------------------------------------
# 7. 记忆卡回写（规格03 §2.7）
# ---------------------------------------------------------------------------

def writeback_memory(book: str, no: int, client=None) -> dict[str, Any]:
    prose = chapters.read_prose(book, no)
    variables = {
        "正文": prose["_body"],
        "章号": str(no),
        "现有角色名清单": "\n".join(characters.list_characters(book)) or "（无）",
        "活跃伏笔编号清单": "\n".join(
            f"{r.get('编号')}[{r.get('状态')}]" for r in outline.active_foreshadows(book)
        ) or "（无）",
    }
    result: dict[str, Any] | None = None
    feedback = ""
    for _attempt in range(3):
        res, _p, _t = _call(book, no, "memory", "V4", variables, client=client,
                            extra_instruction=feedback)
        try:
            result = llm.extract_json(res.text)
            result["chapter_no"] = no
            FM.validate(result, chapters.MEMORY_SCHEMA)
            break
        except (llm.LLMError, FM.FrontmatterError) as e:
            feedback = _feedback_instruction(str(e))
    if result is None:
        raise PipelineError("V4 记忆卡 JSON 3 次解析失败")

    body = f"{result.get('event_summary','')}（第{no}章记忆卡）\n"
    chapters.write_memory(book, no, result, body)

    applied, conflict_list = characters.apply_status_updates(book, no, result.get("char_updates", []))
    if conflict_list:
        conflicts.enqueue(conflict_list)

    fo_errors, fo_warns = outline.apply_foreshadow_ops(book, no, result.get("foreshadow_ops", []))
    if fo_errors:
        conflicts.enqueue([
            characters.Conflict("foreshadow_op", book, no, err, None, None)
            for err in fo_errors
        ])

    for nc in result.get("new_chars", []):
        name = nc.get("name", "")
        if name and name not in characters.list_characters(book):
            characters.create(book, name, role="龙套")
    for loc in result.get("new_locations", []):
        if isinstance(loc, str) and loc:
            outline.upsert_location(book, loc, chapter_no=no)
    if result.get("new_settings_pending"):
        conflicts.enqueue([
            characters.Conflict("new_setting", book, no, f"待补录设定：{s}", None, None)
            for s in result["new_settings_pending"]
        ])

    summaries.push(book, no, result.get("event_summary", ""))
    fts.index_chapter(books.book_id(book), no, prose["_body"])

    return {
        "applied_chars": applied,
        "conflicts": [c.to_dict() for c in conflict_list] + [{"detail": e} for e in fo_errors],
        "warnings": fo_warns,
        "event_summary": result.get("event_summary", ""),
    }


# ---------------------------------------------------------------------------
# 8. 卷末审计（规格03 §2.8，M3）
# ---------------------------------------------------------------------------

def _volume_last_chapter(book: str, no: int) -> bool:
    od = outline.read_outline(book)
    for v in od.get("volumes") or []:
        ch = v.get("chapters") or [0, 0]
        if len(ch) == 2 and ch[0] <= no <= ch[1]:
            return no == ch[1]
    return False


def _maybe_audit(book: str, no: int, client=None) -> dict[str, Any] | None:
    if not _volume_last_chapter(book, no):
        return None
    return audit_volume(book, no, client=client)


def audit_volume(book: str, last_chapter: int, client=None) -> dict[str, Any]:
    od = outline.read_outline(book)
    vol = None
    for v in od.get("volumes") or []:
        ch = v.get("chapters") or [0, 0]
        if len(ch) == 2 and ch[0] <= last_chapter <= ch[1]:
            vol = v
            break
    vol_no = vol.get("no") if vol else "?"
    first = (vol.get("chapters")[0] if vol else 1)

    all_prose = []
    for n in range(first, last_chapter + 1):
        try:
            all_prose.append(chapters.read_prose(book, n)["_body"])
        except Exception:
            continue
    arcs = []
    for name in characters.list_characters(book):
        d = characters.read(book, name)
        if d.get("role") in ("主角", "女主", "反派"):
            m = re.search(r"(?ms)^##\s*弧光预设\s*\n(.*?)(?=^##\s|\Z)", d.get("_body", ""))
            arcs.append(f"{name}: {(m.group(1).strip() if m else '（未填）')}")
    variables = {
        "系统段": assembler.render_system(book),
        "本卷全部正文": "\n\n".join(all_prose)[:120_000],
        "伏笔台账全表": "\n".join(
            f"{r.get('编号')}[{r.get('状态')}] {r.get('内容','')}（埋设{r.get('埋设章','')}→回收{r.get('预计回收章','')}）"
            for r in outline.read_plot_points(book)
        ) or "（台账为空）",
        "主要角色弧光预设": "\n".join(arcs) or "（无）",
        "世界观圣经": "\n".join(f"## {k}\n{v}" for k, v in bible.read(book).items()),
    }
    res, _p, _t = _call(book, last_chapter, "audit", "V6", variables, client=client)

    # 程序算伏笔回收率（规格03 §2.8：程序算，LLM 解读）
    rows = outline.read_plot_points(book)
    total = len(rows)
    paid = sum(1 for r in rows if r.get("状态") == "paid")
    rate = f"{paid}/{total}" if total else "0/0"

    from . import atomic
    from .paths import book_dir

    report = f"# 卷{vol_no} 审计报告\n\n伏笔回收率（程序计算）：{rate}\n\n{res.text}\n"
    d = book_dir(book) / "audits"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"卷{vol_no}_审计.md"
    atomic.write_text(path, report)

    # 审计结果入库（规格01 §3 review_records 的 kind 含 'audit'；
    # 原实现只写文件不入库，UI 无法按章/版本检索审计结论）
    db.add_review(book_id=books.book_id(book), chapter_no=last_chapter,
                  version=_version(book, last_chapter), kind="audit",
                  scores={"foreshadow_paid": paid, "foreshadow_total": total},
                  issues=[{"category": "audit_report", "severity": "info",
                           "location": str(path), "quote": "",
                           "description": f"卷{vol_no} 审计报告", "suggestion": ""}],
                  verdict="pass")
    return {"report_path": str(path), "foreshadow_pay_rate": rate}
