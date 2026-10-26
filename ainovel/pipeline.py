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


class FixIneffective(FixOverreach):
    """V5 修完「点名要改的那句」一个字没动（程序保真校验兜底）。

    与 FixOverreach 同一处置（输出不可用 → 转人工），所以直接继承：
    既有救援分支不用改就能正确接住。但日志与措辞必须区分——把它当成
    「越权」会掩盖真正的原因（模型偷懒不改），下次还会照样发生。

    实测来源：第 4 章 V5 跑完，审校点名的 4 条（含禁用词「骤然」）全部原样
    留在正文里，而 pipeline 一路当成「修复成功」继续往下走，白烧了一轮审校。
    """


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
        cost_mc = settings.cost_millicents(model, res.input_tokens, res.output_tokens)
        db.log_generation(
            book_id=books.book_id(book), chapter_no=no, stage=stage,
            prompt_id=prompt_id, prompt_version=str(p.fm.get("version")),
            prompt_digest=p.digest, model=model,
            input_tokens=res.input_tokens, output_tokens=res.output_tokens,
            cost_millicents=cost_mc, status="ok",
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
    prev_ending = ""
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
        # 只给「钩子一句话」不够：细纲作者不知道上一章**停在哪**，就会凭空换场。
        # 第 5→6 章实测：上章收在沉降口，第 6 章细纲直接写「烂尾楼六层」，
        # 正文照抄 → 空间断档，读者要自己脑补一层楼的距离。
        try:
            prev_ending = chapters.read_prose(book, no - 1)["_body"].strip()[-600:]
        except Exception:
            prev_ending = ""

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
        "上一章结尾原文": prev_ending or "（第一章 / 上一章正文缺失）",
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
            _validate_outline_banned(fm2, book)
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


def _outline_prose_seed(fm: dict[str, Any]) -> str:
    """细纲里会被正文继承的那部分文字（钩子、场景目标/冲突、必含项）。"""
    parts = [
        str(fm.get("purpose") or ""),
        str(fm.get("core_event") or ""),
        " ".join(str(x) for x in (fm.get("must_include") or [])),
        str((fm.get("hook") or {}).get("content") or ""),
    ]
    for s in fm.get("scenes") or []:
        parts.append(str(s.get("goal") or ""))
        parts.append(str(s.get("conflict") or ""))
        parts.append(str(s.get("end_state") or ""))
    return "\n".join(parts)


def _validate_outline_banned(fm: dict[str, Any], book: str) -> None:
    """细纲含禁词 → 当场回喂重写。

    第 12 章实测：「云雾缭绕」先出现在细纲的 hook 里，W1 生成正文时原样继承，
    机检 M-02 判 block，再走 V5 定向修复——同一处问题白烧两次调用，还占掉
    一次修复轮次。禁词源头在 G6，就该在 G6 拦。
    """
    text = _outline_prose_seed(fm)
    if not text:
        return
    whitelist = _lint_whitelist(book)
    hits = sorted({w for w in mechcheck.load_banned_words()
                   if w not in whitelist and w and w in text})
    if hits:
        raise PipelineError(
            "细纲含禁词：" + "、".join(hits[:8])
            + f"（共 {len(hits)} 个）——这些表述会被正文原样继承并触发机检阻断，请换写法"
        )


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


def _deslop_brief(body: str, limit: int = 8) -> str:
    """给 V1/V2 的检测器摘要：分数 + 前 N 条命中（带行号，便于定点改）。"""
    from . import deslop

    try:
        res = deslop.analyze(body)
    except Exception as e:  # 参数写坏等情况：如实告知模型"这次没有客观依据"
        return f"（检测器未执行：{e}）"
    if res.score < 0:
        return "（正文过短，检测器未评分）"
    head = f"干净度 {res.score}/100（越高越干净；<60 建议润色，<45 且有结构性硬伤则阻断）"
    if not res.hits:
        return head + "\n（无规则命中）"
    return head + "\n" + res.top_hits(limit)


def _timeline_constraints(book: str, no: int) -> str:
    """本章出场角色的知情禁区（只注入相关条目，不注入全表）。"""
    try:
        from . import timeline

        return timeline.constraints_for_chapter(book, no)
    except Exception:
        return "（未建立时间线）"


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
DRAFT_MIN_ADOPT_CHARS = 500  # 低于此值大概率只是开头，采纳要显式 force


def draft_info(book: str, no: int) -> dict[str, Any]:
    """草稿状态（规格05 F-09）。不存在时返回 exists=False，不是 404——前端好处理。"""
    path = chapters.draft_path(book, no)
    if not path.exists():
        return {"exists": False, "chars": None, "generated_at": None,
                "preview": None, "stale": None}
    text = chapters.read_draft(book, no)
    stale = False
    try:
        prose_path = chapters.prose_path(book, no)
        if prose_path.exists():
            stale = path.stat().st_mtime < prose_path.stat().st_mtime
    except OSError:
        stale = False
    return {
        "exists": True,
        "chars": mechcheck.count_words(text),
        "generated_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        "preview": text.strip()[:200],
        "stale": stale,
    }


def _clean_draft_text(text: str) -> str:
    """去掉草稿可能残留的 frontmatter 与首尾空行。"""
    t = text.replace("\r\n", "\n").strip()
    if t.startswith("---"):
        m = re.match(r"^---\s*\n.*?\n---\s*\n?", t, re.DOTALL)
        if m:
            t = t[m.end():]
    return t.strip()


def adopt_draft(book: str, no: int, force: bool = False) -> dict[str, Any]:
    """把半成品草稿采纳为正式正文，然后**照常过机检**。

    草稿是半成品，更可能字数不足或残缺——正因如此不能跳过 M-01。
    """
    st = statemachine.current_status(book, no)
    if st == "generating":
        # 用户主动中断后再采纳：先把状态落到 error，才有 adopt_draft 出边
        statemachine.transition(book, no, "fail")
        st = "error"
    if st != "error":
        raise statemachine.StateError(
            f"第{no}章状态 {st} 不能采纳草稿（需 error/generating，先中断或等失败）")

    path = chapters.draft_path(book, no)
    if not path.exists():
        raise FileNotFoundError(f"第{no}章没有草稿")
    info = draft_info(book, no)
    if info["stale"] and not force:
        raise PipelineError("草稿比正文旧（正文后来改过），采纳会覆盖新内容；确认要覆盖请带 force")
    body = _clean_draft_text(chapters.read_draft(book, no))
    chars = mechcheck.count_words(body)
    if chars == 0:
        raise PipelineError("草稿为空，无法采纳")
    if chars < DRAFT_MIN_ADOPT_CHARS and not force:
        raise PipelineError(
            f"草稿只有 {chars} 字（<{DRAFT_MIN_ADOPT_CHARS}），大概率只是开头；"
            "确认要采纳请带 force")

    try:
        prose = chapters.read_prose(book, no)
        fm = {k: v for k, v in prose.items() if not k.startswith("_")}
    except Exception:
        row = statemachine.get_row(book, no)
        fm = {"chapter_no": no, "version": (row or {}).get("current_version", 1),
              "status": "draft", "word_count": 0, "finalized_at": None}
    fm.update(status="draft", word_count=chars,
              draft_adopted_at=datetime.now().isoformat(timespec="seconds"))
    chapters.write_prose(book, no, fm, body + "\n")
    chapters.clear_draft(book, no)
    db.log_generation(book_id=books.book_id(book), chapter_no=no,
                      stage="draft_adopt", status="ok", error=f"采纳草稿 {chars} 字")
    statemachine.transition(book, no, "adopt_draft")  # → mech_check
    return {"ok": True, "chars": chars,
            "status": statemachine.current_status(book, no)}


def discard_draft(book: str, no: int) -> dict[str, Any]:
    chapters.clear_draft(book, no)
    return {"ok": True, "status": statemachine.current_status(book, no)}


DRAFT_MIN_ADOPT_CHARS = 500  # 低于此值大概率只是开头，采纳要显式 force


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
    # 只给区间，模型容易顶到上限再冲出去；给「目标值」才收得住
    variables["目标字数"] = str((wr[0] + wr[1]) // 2)
    variables["开篇承接点"] = (
        str(contract.get("opening_bridge") or "").strip()
        or "（细纲未写承接点：按「上一章末尾原文」自己接，必须交代位置与时间）"
    )
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
    # 生成/重跑都越界时，用 W6 做确定性收敛（最多 2 轮，方向不对就停）
    fit_log: list[dict[str, Any]] = []
    if not _in_range(body, wr):
        body, fit_log = fit_length(book, no, body, wr, client=client)
        if fit_log:
            logger.info("第%s章 生成后字数收敛 %s", no, fit_log)
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
            "mode": "scene" if cfg.get("scene_mode") else "whole",
            "length_fit": fit_log}


def _generate_by_scenes(book: str, no: int, base_vars: dict[str, Any], wr: list[int],
                        scenes: list[dict[str, Any]], client=None,
                        stream_cb: Callable[[str], None] | None = None) -> tuple[str, str]:
    """逐场景生成：每场景独立调用（全注意力）+ 字数配额 + 前场景结尾衔接。"""
    n = len(scenes)
    total_mid = (wr[0] + wr[1]) // 2
    per_mid = max(500, total_mid // n)
    parts: list[str] = []
    used = 0  # 已写出的字数：后面场景按剩余预算收紧，单个场景超了不会把全章拖爆
    fr = "stop"
    for i, sc in enumerate(scenes, 1):
        chars = "、".join(f"{c.get('name')}({c.get('state')})" for c in sc.get("chars", []) or [])
        left = n - i + 1
        remain = max(600, wr[1] - 200 - used)   # 留 200 字给收尾钩子
        sc_lo = max(400, int(remain / left * 0.8))
        sc_hi = max(500, int(remain / left * 1.05))
        task = (
            f"【本场景生成任务】场景 {i}/{n}（只写这一个场景，禁止写到后续场景内容）\n"
            f"目标：{sc.get('goal')}\n冲突：{sc.get('conflict')}\n出场：{chars}\n"
            f"本场景结束状态：{sc.get('end_state')}\n"
            f"本场景字数：{sc_lo}-{sc_hi} 字（全章还剩约 {remain} 字额度，后续还有 {left - 1} 个场景）\n"
        )
        if i > 1 and parts:
            task += f"【上一场景结尾（开头直接衔接）】{parts[-1].strip()[-200:]}\n"
        else:
            task += "【本章开头】300 字内承接上一章钩子入题。\n"
        if i == n:
            task += "【收尾要求】本场景是全章末场景，必须落在章合同钩子指向的画面/台词上，戛然而止。\n"
        v = dict(base_vars)
        v["章合同"] = base_vars["章合同"] + "\n\n" + task
        v["字数区间"] = f"{sc_lo}-{sc_hi}"
        v["目标字数"] = str((sc_lo + sc_hi) // 2)
        v.setdefault("黄金三章附加要求", "")
        res, _p, _t = _call(book, no, "prose", "W1", v, client=client,
                            stream_cb=stream_cb if i == n else None)
        if res.finish_reason == "length":
            fr = "length"
        part = res.text.strip()
        parts.append(part)
        used += mechcheck.count_words(part)
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


LENGTH_FIT_MAX_ROUNDS = 2
LENGTH_FIT_RATIO = 0.97


def fit_length(book: str, no: int, body: str, wr: list[int],
               client=None) -> tuple[str, list[dict[str, Any]]]:
    """把正文收进字数区间（确定性收敛，不指望模型一次写准）。

    为什么单独一条通道：M-01 超字数原本走 V5「定向修复」，而 V5 的纪律是
    「只改问题句段、其余一字不动」——它天生不适合整章删减，实测经常原样返回，
    然后一路当成「修过了」，白烧一轮审校（详见 FixIneffective）。

    这里用 W6 明确「只删不加 / 只加不删 + 目标字数」，并且：
    - 每轮只在**方向正确**（超了就变短、少了就变长）且**没把正文删残**时才采纳；
    - 模型摆烂就直接停，不无限重试烧 token；
    - 最多 LENGTH_FIT_MAX_ROUNDS 轮，仍不达标就如实返回，交给 M-01 阻断。
    """
    lo, hi = wr
    log: list[dict[str, Any]] = []
    for _ in range(LENGTH_FIT_MAX_ROUNDS):
        n = mechcheck.count_words(body)
        if lo <= n <= hi:
            break
        shrink = n > hi
        target = int(hi * LENGTH_FIT_RATIO) if shrink else (lo + hi) // 2
        try:
            res, _p, _t = _call(book, no, "fit_length", "W6", {
                "正文": body,
                "当前字数": str(n),
                "目标字数": str(target),
                "增减字数": str(abs(n - target)),
                "字数区间": f"{lo}-{hi}",
                "方向": "压缩" if shrink else "扩写",
                # 只说"压缩"模型会顺手润色成更长；必须点明这是删字任务
                "方向说明": ("这是删字任务：删掉约 %d 字的内容冗余，不是重写、不是润色、不许增句。"
                          % (n - target)) if shrink else (
                          "这是补字任务：补足约 %d 字的具体细节，不许新增情节。" % (target - n)),
            }, client=client)
        except Exception as e:
            logger.warning("第%s章 长度收敛调用失败，保留原稿: %s", no, e)
            break
        cand = _clean_draft_text(res.text)
        cw = mechcheck.count_words(cand)
        log.append({"dir": "shrink" if shrink else "expand",
                    "from": n, "to": cw, "target": target})
        improved = (cw < n) if shrink else (cw > n)
        # 低于下限 40% 说明它直接删了半章——宁可不采纳
        if not improved or cw < max(300, int(lo * 0.4)):
            logger.warning("第%s章 W6 输出不可用（%d→%d 字），保留原稿", no, n, cw)
            break
        body = cand
        if lo <= cw <= hi:
            break
    return body, log


def _better(a: str, b: str, wr: list[int]) -> bool:
    mid = (wr[0] + wr[1]) / 2
    return abs(mechcheck.count_words(a) - mid) < abs(mechcheck.count_words(b) - mid)


# ---------------------------------------------------------------------------
# 4. 机械检查 + 自动修复（规格03 §2.4）
# ---------------------------------------------------------------------------

def run_mech_check(book: str, no: int, client=None,
                   auto_fix: bool = True) -> mechcheck.CheckResult:
    """机械检查（规格05 §2.5）：返回带生效状态的结论，三态驱动流转。

    关键：`not_effective`（有 block 级规则没查成，如禁词表被删）**不得**当成通过。
    词表清空时旧实现返回空命中 → 整章直接进 llm_review，属于"假绿"。
    """
    st = statemachine.current_status(book, no)
    if st != "mech_check":
        raise statemachine.StateError(f"第{no}章状态 {st} 不能跑机械检查")
    cfg = books.load_config(book)
    wr = cfg["chapter_word_range"]

    def _record(res: mechcheck.CheckResult) -> None:
        db.add_review(book_id=books.book_id(book), chapter_no=no,
                      version=_version(book, no), kind="mech",
                      issues=[h.to_dict() for h in res.hits],
                      verdict=res.verdict,
                      fingerprint=res.fingerprint,
                      ineffective=[i.to_dict() for i in res.ineffective])
        db.set_check_status(book_id=books.book_id(book), chapter_no=no,
                            fingerprint=res.fingerprint, status=res.verdict)

    res = _check(book, no, wr)
    _record(res)
    if res.verdict == "not_effective":
        logger.warning("第%s章 机检未完整执行（%s），转人工，禁止推进到审校",
                       no, "、".join(f"{i.rule}:{i.reason}" for i in res.ineffective))
        statemachine.transition(book, no, "blocked_after_fix")  # → human_review
        return res
    if res.verdict == "pass":
        statemachine.transition(book, no, "pass")  # → llm_review
        return res

    hits = res.hits
    # 只卡字数时先走「长度收敛」，不走 V5：V5 的纪律是「只改问题句段、其余一字不动」，
    # 它天生不适合整章删减（实测原样返回，然后白烧一轮审校）。
    if auto_fix and res.blocks and all(h.rule == "M-01" for h in res.blocks) \
            and not mechcheck.has_sensitive(hits):
        prose = chapters.read_prose(book, no)
        new_body, fit_log = fit_length(book, no, prose["_body"], wr, client=client)
        if fit_log and new_body != prose["_body"]:
            fm = {k: v for k, v in prose.items() if k != "_body"}
            fm["word_count"] = mechcheck.count_words(new_body)
            chapters.write_prose(book, no, fm, new_body)
            logger.info("第%s章 字数收敛 %s", no, fit_log)
            res = _check(book, no, wr)
            _record(res)
            if res.verdict == "pass":
                statemachine.transition(book, no, "pass")  # → llm_review
                return res
            hits = res.hits

    if mechcheck.has_sensitive(hits) or not auto_fix:
        logger.warning("第%s章 命中敏感词红线，阻断转人工（禁止自动改写）", no)
        statemachine.transition(book, no, "blocked_after_fix")  # → human_review（敏感词禁止自动改写）
        return res

    logger.info("第%s章 机检阻断 %d 条，自动定向修复一轮", no, len(hits))
    statemachine.transition(book, no, "blocked")  # → lint_fix
    try:
        run_targeted_fix(book, no, hits, client=client)
    except FixOverreach:
        statemachine.transition(book, no, "recheck")       # lint_fix → mech_check
        statemachine.transition(book, no, "blocked_after_fix")  # → human_review（越权→完整重生成由人触发）
        res = _check(book, no, wr)
        _record(res)
        return res
    statemachine.transition(book, no, "recheck")  # → mech_check
    res2 = _check(book, no, wr)
    _record(res2)
    if res2.verdict != "pass":
        statemachine.transition(book, no, "blocked_after_fix")  # 仍有阻断/未生效 → human_review
    else:
        statemachine.transition(book, no, "pass")  # → llm_review
    return res2


def _lint_whitelist(book: str) -> list[str]:
    """书级禁词豁免：目标文风作者的招牌词与通用禁词冲突时豁免。"""
    try:
        return list(books.load_config(book).get("lint_whitelist", []))
    except Exception:
        return []


def _check(book: str, no: int, wr: list[int] | None) -> mechcheck.CheckResult:
    prose = chapters.read_prose(book, no)
    ctx = None
    try:
        from . import state as state_mod

        ctx = state_mod.mech_context(book, no)
        ctx["timeline_hits"] = timeline_validate(book, prose["_body"])
    except Exception as e:  # 上下文取不到 → 让 M-13~M-17 如实报"未查"，不当成通过
        logger.warning("第%s章状态/时间线上下文读取失败: %s", no, e)
        ctx = None
    return mechcheck.check_full(prose["_body"], word_range=wr,
                                whitelist=_lint_whitelist(book), context=ctx)


def timeline_validate(book: str, body: str = "") -> list[dict[str, Any]]:
    from . import timeline

    return timeline.validate(book, body)


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
    missed = _fix_missed(new_body, issues)
    text_total = len(_single_sentence_quotes(issues))
    if missed and text_total and len(missed) >= text_total:
        logger.warning("第%s章 V5 未改到任何点名句段（%d/%d 原样保留），输出已丢弃：%s",
                       no, len(missed), text_total,
                       "｜".join(m[:18] for m in missed[:3]))
        raise FixIneffective(
            f"V5 点名要改的 {text_total} 处句段一处未动，本次输出已丢弃")
    if missed:
        logger.warning("第%s章 V5 有 %d/%d 处点名句段未改（已接受其余修复）：%s",
                       no, len(missed), text_total, "｜".join(m[:18] for m in missed[:3]))
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


_TEXT_ISSUE_CATEGORIES = frozenset(
    {"ai_flavor", "prose", "style", "wording", "sentence", "word", "dialogue"}
)


def _single_sentence_quotes(issues: list[Any]) -> list[str]:
    """取出「单句」类的文字问题原句（判「改没改」只用这一种）。

    只取单句，是因为多句引用的 quote 无法用字符串比对判断是否改到位：
    审校常把 2-3 句一起引用，修复只要改其中 1 句就算完成，其余原样保留是
    **正确**的，硬判「没改」必然误报。单句引用没有这个歧义：它原样还在，
    就是没改。

    机检条目（dict 带 rule / Hit 带 word）不走这里——它们的处置由
    重跑机检后的命中数决定，比字符串比对可靠。
    """
    out: list[str] = []
    for it in issues or []:
        if not isinstance(it, dict) or it.get("rule"):
            continue
        if str(it.get("category") or "").lower() not in _TEXT_ISSUE_CATEGORIES:
            continue
        quote = str(it.get("quote") or "").strip()
        if len(quote) < 8:
            continue
        # 先按句号/感叹号/分号切，**不做长度过滤**再数句数：短句（"第一句"）被过滤掉
        # 会让三句引用退化成一个「句子」，反而被当成单句参与判定（实测踩过）。
        parts = [s for s in re.split(r"[。！？!?…；;]+", quote) if s.strip()]
        if len(parts) != 1 or len(parts[0].strip()) < 8:
            continue
        out.append(quote)
    return out


def _fix_missed(new: str, issues: list[Any]) -> list[str]:
    """修复后仍原样保留的单句问题——识别「V5 说改好了、其实没改」。

    只比句子主体前 10 字：结尾标点（。→；、！→。）常被顺手改掉，
    连标点一起比会把「改了标点没改内容」当成已修复。
    """
    missed: list[str] = []
    for quote in _single_sentence_quotes(issues):
        body = next((s.strip() for s in re.split(r"[。！？!?…；;]+", quote) if s.strip()), "")
        if body and body[:10] in new:
            missed.append(quote)
    return missed


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

    # 术语表比对（双向）—— 旧实现只查「术语表里的词没在正文出现」：
    # 33 条术语逐条报一遍，全是噪声，而真正该查的「正文新造的名词有没有定义」
    # 没人看。于是审校把 NH-01/NH-02（休眠舱活体编号，术语表已列）报成
    # FO-013「LT77-SQ82」的冲突，浪费了一轮修复额度。
    prose = None
    try:
        prose = chapters.read_prose(book, no)["_body"]
    except Exception:
        pass
    if prose:
        try:
            lines.extend(_term_crosscheck(book, prose, contract))
        except Exception:
            pass
    return "\n".join(lines) or "（程序预检未发现疑似冲突）"


# 专名候选：【方括号术语】、【编号】（NH-01 / LT77-SQ82）、中文专名+建制后缀
_BRACKET_TERM_RE = re.compile(r"【([^】\n]{2,20})】")
_CODE_TERM_RE = re.compile(r"[A-Z]{2,}[A-Z0-9]*(?:-[A-Z0-9]+)+|[A-Z]{2,}\d+[A-Z0-9]*")
_SUFFIX_TERM_RE = re.compile(r"(集团|门阀|协议|阁|司|堂|局|井|仓|峰|城|院|所|部|队|锁|令|丹|符|阵)")
# 中文无词边界，正向取字必然粘连成「没有太昊集团」「那是太昊门阀」，剥掉这些停用字
_STOP_CHARS = set("的了是在把被让给和与及又就都也还不没有这那个些里上下中外内之其他她它我你们"
                  "着过得地很更最并或但却才只已将正便从向对于为以所由至到")


def _trim_stop_prefix(s: str) -> str:
    i = 0
    while i < len(s) - 1 and s[i] in _STOP_CHARS:
        i += 1
    return s[i:]


def _proper_noun_counts(text: str) -> dict[str, int]:
    """抽出正文里"像专名"的串及其出现次数（不判对错，交给下面的定义集过滤）。

    中文专名靠后缀反向定位再向前取 4 字，然后剥停用前缀——正向正则会把
    「没有太昊集团」整串当成一个名字，比不查还糟。
    """
    counts: dict[str, int] = {}
    high: set[str] = set()  # 高置信候选：方括号标注 / 带编号，出现一次也算数

    def bump(k: str) -> None:
        counts[k] = counts.get(k, 0) + 1

    for m in _BRACKET_TERM_RE.finditer(text):
        k = m.group(1).strip()
        bump(k)
        high.add(k)
    for m in _CODE_TERM_RE.finditer(text):
        if re.search(r"\d", m.group(0)):  # 纯字母缩写（NH、AI）不是设定编号
            bump(m.group(0))
            high.add(m.group(0))
    for m in _SUFFIX_TERM_RE.finditer(text):
        # 从后缀往前扫到非汉字边界（标点/换行）为止，最多 4 字：
        # 固定取 4 字会跨标点粘连，句首专名又会因为前面是标点而整条丢掉
        i = m.start()
        j = i
        while j > 0 and i - j < 4 and "\u4e00" <= text[j - 1] <= "\u9fa5":
            j -= 1
        head = text[j:i]
        if not head:
            continue
        cand = _trim_stop_prefix(head) + m.group(1)
        if len(cand) >= 3:
            bump(cand)
    # 中文后缀词是弱信号，只出现一次多半是行文巧合（「里的水比井」），要求复现才算设定
    return {k: n for k, n in counts.items()
            if n >= 2 or k in high or any(not ("\u4e00" <= ch <= "\u9fa5") for ch in k)}


def _term_crosscheck(book: str, prose: str, contract: dict[str, Any],
                     limit: int = 20) -> list[str]:
    """术语双向核对：①细纲点名却缺席 ②正文新造却无定义。

    方向二才是真问题所在——它是术语一致性检查唯一能提前发现「AI 自己造了个
    新名词、圣经里没有」的地方；方向一必须收窄到「细纲点了名」，否则整本术语表
    逐条报缺席，噪声大到没人看。
    """
    out: list[str] = []
    try:
        defined = list(bible.terms(book))
    except Exception:
        defined = []
    known: set[str] = set()
    try:
        known.update(characters.read_all(book).keys())
    except Exception:
        pass
    try:
        for r in outline.read_locations(book):
            nm = str(r.get("名称") or "").strip()
            if nm:
                known.add(nm)
    except Exception:
        pass

    def is_defined(cand: str) -> bool:
        if cand in known:
            return True
        if any(cand in t or t in cand for t in defined):
            return True
        # 同族专名：前两字命中任一已定义词（太昊集团 ← 太昊生命医疗集团）
        if len(cand) >= 2:
            head = cand[:2]
            return any(head in t or t in head for t in (*defined, *known))
        return False

    # 方向一：细纲点名的术语，正文里没有
    contract_text = "\n".join(str(v) for v in (
        contract.get("purpose"),
        contract.get("core_event"),
        " ".join(str(x) for x in (contract.get("must_include") or [])),
        " ".join(f"{s.get('goal') or ''} {s.get('conflict') or ''}"
                 for s in (contract.get("scenes") or [])),
        str((contract.get("hook") or {}).get("content") or ""),
    ))
    for term in defined:
        if term and term in contract_text and term not in prose:
            out.append(f"[术语·缺席] 「{term}」被细纲点名，正文却未出现——确认设定没被写丢")

    # 方向二：正文出现、却查不到定义的新名词
    unknown = {k: n for k, n in _proper_noun_counts(prose).items() if not is_defined(k)}
    for cand, n in sorted(unknown.items(), key=lambda kv: -kv[1])[:limit]:
        out.append(
            f"[术语·新名词] 「{cand}」正文出现 {n} 次但术语表无定义"
            f"——若属设定请补录进 STORY_BIBLE 术语表，若是笔误请统一写法"
        )
    return out


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
        "检测器结果": _deslop_brief(prose["_body"]),
        "时间线约束": _timeline_constraints(book, no),
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
        "检测器命中": _deslop_brief(old_body),
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


PROSE_CORRECT_MAX_RATIO = 0.05  # 订正上限：超过 5% 就是重写


def correct_prose(book: str, no: int, edits: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    """定稿后的字词级订正通道（错别字 / 术语旧称 / 事实矛盾）。

    为什么需要单独一条：`PUT /prose` 一律拒绝 finalized（防随手重写已定稿章，
    AGENTS.md 红线 3），`human_rewrite` 又要把章打回 generating（机检、审校、
    定稿全部重跑，还得重烧 token）。结果定稿章里一个错别字、一处旧称没有合规
    通道，只能忍着——真实书第 8/10 章至今留着「筑基修士」「练气一层」。

    通道刻意做窄：
    - 逐条 old→new，old 必须在正文中**恰好出现一次**（0 次=没找到，≥2 次=会误伤）
    - 总改动量 ≤5%，超过就是重写，请走解锁返工
    - 不得引入新禁词
    - 强制理由，写进 review_records 与 generation_log
    - 定稿原文留在旧快照里，订正结果另存新版本快照
    """
    import difflib

    st = statemachine.current_status(book, no)
    try:
        prose = chapters.read_prose(book, no)
    except Exception as e:
        raise PipelineError(f"读不到第{no}章正文：{e}")
    if st != "finalized" and prose.get("status") != "final":
        raise PipelineError(
            f"第{no}章状态 {st} 尚未定稿——未定稿章节请用「编辑正文」整篇改"
        )
    reason = (reason or "").strip()
    if not reason:
        raise PipelineError("订正必须填写理由（会写进审校记录，事后要能解释改了什么）")
    if not edits:
        raise PipelineError("没有要订正的内容")

    body = prose["_body"]
    whitelist = _lint_whitelist(book)
    banned = [w for w in mechcheck.load_banned_words() if w and w not in whitelist]
    new_body = body
    applied: list[dict[str, str]] = []
    for e in edits:
        old = str(e.get("old") or "")
        new = str(e.get("new") or "")
        if not old:
            raise PipelineError("订正项的 old 不能为空")
        if old == new:
            raise PipelineError(f"订正项新旧相同：「{old[:30]}」")
        cnt = new_body.count(old)
        if cnt != 1:
            raise PipelineError(
                f"「{old[:40]}」在正文出现 {cnt} 次，必须恰好 1 次才能安全替换"
                f"（0 次=没找到，多次=会误伤别的句子，请带足上下文")
        introduced = [w for w in banned if w in new and w not in old]
        if introduced:
            raise PipelineError(f"订正文本引入禁词：{'、'.join(introduced[:5])}")
        new_body = new_body.replace(old, new, 1)
        applied.append({"old": old, "new": new})

    ratio = 1 - difflib.SequenceMatcher(None, body, new_body, autojunk=False).ratio()
    if ratio > PROSE_CORRECT_MAX_RATIO:
        raise PipelineError(
            f"改动量 {ratio:.1%} 超过 {PROSE_CORRECT_MAX_RATIO:.0%} 上限——"
            f"这是重写而不是订正，请走「解锁返工」"
        )

    new_version = _next_version(book, no)
    fm = {k: v for k, v in prose.items() if not k.startswith("_")}
    fm["version"] = new_version
    fm["word_count"] = mechcheck.count_words(new_body)
    fm["status"] = "final"
    fm["corrected_at"] = datetime.now().isoformat(timespec="seconds")
    chapters.write_prose(book, no, fm, new_body)
    statemachine.set_current_version(book, no, new_version)
    bid = books.book_id(book)
    db.add_snapshot(book_id=bid, chapter_no=no, version=new_version,
                    label=f"订正 {len(applied)} 处", content=new_body)
    db.add_review(book_id=bid, chapter_no=no, version=new_version,
                  kind="polish", issues=applied, verdict="correct")
    db.log_generation(book_id=bid, chapter_no=no, stage="correct",
                      status="ok", error=f"订正理由：{reason}")
    return {"version": new_version, "applied": applied, "reason": reason,
            "change_ratio": round(ratio, 4)}


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


_NON_REALM_LABELS = frozenset(
    {"凡人", "普通人", "常人", "无", "未知", "不明", "-", "—", "？", "?"}
)


def _ledger_reflects(book: str, no: int, fo_id: str, op: str) -> bool:
    """台账里是否已经体现了「第 no 章执行了 op」。

    漂移判定必须看**台账**而不是只看记忆卡：第 6 章实测——细纲声明 advance
    FO-007，记忆卡漏记，但台账的 `推进章` 早就有 6（前一版记忆卡推进的）。
    这时再报「漏执行」就是假警报；假警报多了，人就不看了。
    """
    try:
        from . import outline

        row = next((r for r in outline.read_plot_points(book)
                    if str(r.get("编号") or "") == fo_id), None)
    except Exception:
        return False
    if row is None:
        return False
    if op == "plant":
        return str(row.get("埋设章") or "").strip() == str(no)
    if op == "advance":
        parts = [p.strip() for p in str(row.get("推进章") or "").split(",") if p.strip()]
        return str(no) in parts
    if op == "pay":
        return str(row.get("状态") or "") == "paid" and f"第{no}章" in str(row.get("备注") or "")
    return False


def foreshadow_drift(book: str, no: int) -> list[str]:
    """细纲声明的伏笔操作 vs 实际执行（记忆卡 + 台账）。

    第 11 章实测：细纲写 pay FO-001，但正文没真正回收，V4 抽出来是 advance，
    台账停在 building。设计上「以记忆卡为准」是对的——正文写了什么才算数。
    问题在于两边不一致时没有任何提示，于是第 12 章又安排了一次回收，
    读者会看到同一个伏笔被回收两遍。这里把差异摆到定稿返回里。

    判定口径：记忆卡记了 **或** 台账已经体现，都算「执行过」。只看记忆卡
    会漏掉「卡漏记但台账已推进」的情况（第 6 章 FO-007 就是这样），变成假警报。
    """
    try:
        contract = chapters.read_outline_contract(book, no)
    except Exception:
        return []
    declared = [(str(o.get("fo_id") or ""), str(o.get("op") or ""))
                for o in (contract.get("foreshadow_ops") or [])]
    if not declared:
        return []
    try:
        card = chapters.read_memory(book, no)
    except Exception:
        return []
    actual = {(str(o.get("fo_id") or ""), str(o.get("op") or ""))
              for o in (card.get("foreshadow_ops") or [])}
    out: list[str] = []
    for fo_id, op in declared:
        if not fo_id or (fo_id, op) in actual:
            continue
        if _ledger_reflects(book, no, fo_id, op):
            continue
        got = sorted({o for f, o in actual if f == fo_id})
        out.append(
            f"{fo_id}：细纲声明 {op}，记忆卡记的是 {'/'.join(got) or '未出现'}"
            f"——台账按记忆卡推进，下一章可能重复安排同一操作"
        )
    return out


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

    result = {"snapshot_version": version, "writeback": None, "audit": None,
              "foreshadow_drift": []}
    try:
        result["writeback"] = writeback_memory(book, no, client=client)
    except Exception as e:
        # 记忆卡失败不回滚定稿，但标记待重试
        statemachine.mark_error(book, no, f"记忆卡回写失败: {e}")
        result["writeback"] = {"error": str(e)}
        return result

    result["audit"] = _maybe_audit(book, no, client=client)
    result["foreshadow_drift"] = foreshadow_drift(book, no)
    return result


# ---------------------------------------------------------------------------
# 7. 记忆卡回写（规格03 §2.7）
# ---------------------------------------------------------------------------

def memory_card_pending(book: str) -> list[dict[str, Any]]:
    """列出需要补做记忆卡的章（规格05 F-04）。

    三种待办，`reason` 如实区分——"导入待补"与"回写失败"是两回事，
    混在一起会让人以为系统坏了：
    - `missing`：定稿了但卡不存在（回写失败 / 导入的历史章）
    - `fingerprint_mismatch`：卡存在，但记的是旧版正文（正文改过）
    - `legacy_no_state`：卡是老格式（无 state_delta），长期记忆不完整
    """
    out: list[dict[str, Any]] = []
    for no in chapters.list_chapters(book):
        try:
            st = statemachine.current_status(book, no)
        except Exception:
            continue
        if st != "finalized":
            continue
        path = chapters.memory_path(book, no)
        if not path.exists():
            out.append({"no": no, "title": _chapter_title(book, no),
                        "reason": "missing", "has_card": False})
            continue
        try:
            prose = chapters.read_prose(book, no)
            cur_fp = mechcheck.fingerprint(prose["_body"])
            card_fp = chapters.memory_fingerprint(book, no)
            legacy = chapters.read_memory(book, no).get("legacy", False)
        except Exception:
            out.append({"no": no, "title": _chapter_title(book, no),
                        "reason": "unreadable", "has_card": True})
            continue
        if card_fp and card_fp != cur_fp:
            out.append({"no": no, "title": _chapter_title(book, no),
                        "reason": "fingerprint_mismatch", "has_card": True})
        elif legacy:
            out.append({"no": no, "title": _chapter_title(book, no),
                        "reason": "legacy_no_state", "has_card": True})
    return out


def _chapter_title(book: str, no: int) -> str:
    row = statemachine.get_row(book, no)
    return (row or {}).get("title", "") or f"第{no}章"


def rebuild_memory_card(book: str, no: int, client=None) -> dict[str, Any]:
    """单章补做记忆卡（幂等）。

    顺序很关键：先撤销本章已经产生的派生事实，再重跑 V4，否则伏笔会被推第二次
    （advance/pay 是单向机）。角色状态是覆盖写、天然幂等，不需要回滚。
    """
    st = statemachine.current_status(book, no)
    if st != "finalized":
        raise statemachine.StateError(
            f"第{no}章状态 {st} 不能补做记忆卡（需 finalized）")

    from . import state as state_mod

    rollback = {"state_rows": 0, "foreshadow": {}, "card_deleted": False}
    ops_from_card: list[dict[str, Any]] = []
    if chapters.memory_path(book, no).exists():
        try:
            ops_from_card = list(chapters.read_memory(book, no).get("foreshadow_ops") or [])
        except Exception:
            ops_from_card = []
    try:
        existing = state_mod.chapter_rows(book, no)
        state_mod.clear_chapter(book, no)
        rollback["state_rows"] = len(existing)
    except Exception as e:
        logger.warning("第%s章旧状态快照清理失败: %s", no, e)
    try:
        rollback["foreshadow"] = outline.rollback_foreshadow_ops(book, no, ops_from_card)
    except Exception as e:
        logger.warning("第%s章伏笔回滚失败: %s", no, e)
    if chapters.memory_path(book, no).exists():
        chapters.clear_memory(book, no)
        rollback["card_deleted"] = True

    wb = writeback_memory(book, no, client=client)
    db.log_generation(book_id=books.book_id(book), chapter_no=no,
                      stage="memory_rebuild", status="ok")
    return {"ok": True, "chapter_no": no, "rollback": rollback,
            "applied": {
                "char_updates": wb.get("applied_chars", []),
                "foreshadow_warnings": wb.get("warnings", []),
                "state_entries": len(state_mod.chapter_rows(book, no)),
                "event_summary": wb.get("event_summary", ""),
            }}


def rebuild_memory_batch(book: str, chapter_nos: list[int] | None = None,
                         client=None) -> dict[str, Any]:
    """批量补做：逐章串行，单章失败不阻断后续，最后汇总。"""
    targets = chapter_nos or [it["no"] for it in memory_card_pending(book)]
    done: list[int] = []
    failed: list[dict[str, Any]] = []
    for no in targets:
        try:
            rebuild_memory_card(book, no, client=client)
            done.append(no)
        except Exception as e:
            logger.warning("第%s章补做记忆卡失败: %s", no, e)
            failed.append({"no": no, "error": str(e)})
    return {"queued": len(targets), "done": done, "failed": failed}


def writeback_memory(book: str, no: int, client=None) -> dict[str, Any]:
    prose = chapters.read_prose(book, no)
    variables = {
        "正文": prose["_body"],
        "章号": str(no),
        "现有角色名清单": "\n".join(characters.list_characters(book)) or "（无）",
        # 只给"本章之前已埋设"的伏笔：补做历史章时若把未来的编号给模型，
        # 它会写出"第 8 章推进第 12 章的伏笔"这种时序错误
        "活跃伏笔编号清单": "\n".join(
            f"{r.get('编号')}[{r.get('状态')}]" for r in outline.active_foreshadows(book)
            if str(r.get("埋设章", "") or "").strip().isdigit()
            and int(str(r.get("埋设章")).strip()) <= no
        ) or "（无）",
    }
    result: dict[str, Any] | None = None
    feedback = ""
    legacy = False
    for _attempt in range(3):
        res, _p, _t = _call(book, no, "memory", "V4", variables, client=client,
                            extra_instruction=feedback)
        try:
            result = llm.extract_json(res.text)
            result["chapter_no"] = no
            legacy = chapters.validate_memory(result)
            break
        except (llm.LLMError, FM.FrontmatterError) as e:
            feedback = _feedback_instruction(str(e))
            # 只有"缺 state_delta"这一种情况可以降级：结构与类型都对，只是没新字段。
            # 其它错误继续回喂重试（不拿残缺卡污染长期记忆）。
            if isinstance(e, FM.FrontmatterError) and "state_delta" in str(e):
                result["chapter_no"] = no
                legacy = True
                logger.warning("第%s章 V4 未产出 state_delta，降级为 legacy 记忆卡", no)
                break
    if result is None:
        raise PipelineError("V4 记忆卡 JSON 3 次解析失败")

    body = f"{result.get('event_summary','')}（第{no}章记忆卡）\n"
    result["source_fingerprint"] = mechcheck.fingerprint(prose["_body"])
    chapters.write_memory(book, no, result, body)

    applied, conflict_list = characters.apply_status_updates(book, no, result.get("char_updates", []))
    if conflict_list:
        conflicts.enqueue(conflict_list)

    # F-05：状态快照落库 + 派生 STATE.md（无 state_delta 的 legacy 卡不清空历史行）
    state_entries = result.get("state_delta") or []
    if state_entries:
        try:
            from . import state as state_mod

            known = set(characters.list_characters(book))
            clean = [e for e in state_entries
                     if isinstance(e, dict) and (e.get("character") or e.get("name")) in known]
            unknown = [e.get("character") or e.get("name") for e in state_entries
                       if isinstance(e, dict) and (e.get("character") or e.get("name")) not in known]
            if unknown:
                conflicts.enqueue([
                    characters.Conflict("missing_char", book, no,
                                        f"state_delta 引用了不存在于 characters/ 的角色: {u}", None, None)
                    for u in unknown if u
                ])
            fp = mechcheck.fingerprint(prose["_body"])
            # 战力名称必须在圣经力量体系里：V4 会自创境界名（实测出现过 练气期 / 通脉境 /
            # 筑基初期，而正典是 感气境·初入/明目/洗髓）。一旦落库，M-13 的 realm_order
            # 查不到就静默跳过——「查不了」被当成「没问题」，正是最难发现的那类失效。
            # 这里不拒绝落库（事实优先，卡已经写了），但必须进裁决队列等人工归一。
            from . import bible

            ladder = bible.realm_order(book)
            bad_realm = [e for e in clean
                         if (e.get("power_level") or "").strip()
                         and str(e["power_level"]).strip() not in _NON_REALM_LABELS
                         and mechcheck.realm_index(str(e["power_level"]), ladder) is None]
            if ladder and bad_realm:
                conflicts.enqueue([
                    characters.Conflict(
                        "realm_name", book, no,
                        f"{e.get('character')} 的战力「{e['power_level']}」不在圣经体系内"
                        f"（正典：{'/'.join(ladder[:6])}…），M-13 无法比较",
                        None, None)
                    for e in bad_realm
                ])
                logger.warning("第%s章 有 %d 个战力名称不在力量体系内，已入裁决队列", no, len(bad_realm))
            state_mod.upsert_entries(book, no, clean, fingerprint=fp)
            state_mod.rebuild_state_md(book, upto_chapter=no)
        except Exception as e:  # 快照失败不能吞掉事实：记警告，章节仍可定稿
            logger.warning("第%s章状态快照落库失败: %s", no, e)
    else:
        try:
            from . import state as state_mod

            state_mod.rebuild_state_md(book, upto_chapter=no)
        except Exception as e:
            logger.warning("第%s章 STATE.md 重建失败: %s", no, e)

    # F-06：时间线候选只进裁决队列，**绝不自动落盘**（骨架真相必须人工确认）
    t_candidates = result.get("timeline_candidates") or []
    if t_candidates:
        try:
            from . import timeline

            conflicts.enqueue(timeline.candidates(book, t_candidates, no))
        except Exception as e:
            logger.warning("第%s章时间线候选入队失败: %s", no, e)

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
