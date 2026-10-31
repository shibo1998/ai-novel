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
               fts, frontmatter as FM, llm, mechcheck, models, outline, prompts,
               settings, statemachine, summaries)
from .memory_commit import memory_commit
from .operations import chapter_serialized as serialized

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
    extra_instruction: str = "", retry: bool = True,
) -> tuple[Any, Any, str]:
    """（规格06 §5、规格11 §3）请求先落证据；故事候选可显式禁止客户端重试。"""
    from uuid import uuid4

    from . import atomic
    from .paths import data_dir

    from . import draftcontext

    draftcontext.before_call(book, no, stage)
    cfg = books.load_config(book)
    p = prompts.get(prompt_id, book)
    request = assembler.render_request(book, prompt_id, variables, extra_instruction)
    # （规格12 §2.3）唯一解析入口：书级档位优先，全局只做兜底。
    # 记录值直接取自 ModelRef，不再二次计算——否则 log 里的 model 会和真实请求分叉。
    ref = models.resolve(book, p.fm.get("model_stage"), cfg)
    model = ref.model
    temperature = float(p.fm.get("temperature", 0.7))
    request_id = str(uuid4())
    meta = dict(request["meta"], request_id=request_id, model=model, model_source=ref.source,
                temperature=temperature, book=book, chapter_no=no, stage=stage)
    record = {"request_id": request_id, "system": request["system"], "user": request["user"], "meta": meta}
    atomic.write_text(data_dir() / "assembly_previews" / book / "requests" / f"{request_id}.json",
                      json.dumps(record, ensure_ascii=False, indent=2))
    # 先记未完成记录：日志失败时不发送；中断后也不会伪装成未产生请求。
    log_id = db.log_generation(
        book_id=books.book_id(book), chapter_no=no, stage=stage, prompt_id=prompt_id,
        prompt_version=str(p.fm.get("version")), prompt_digest=p.digest, model=model,
        provider=ref.provider, model_source=ref.source,
        request_id=request_id, request_meta=meta, status="error", error="请求已准备，结果尚未确认")
    # （规格13 §2.3）按解析出的端点选连接；显式注入的 client（测试/批产包装）优先。
    cli = client or llm.client(ref.provider)
    try:
        res = cli.chat(model=model, provider=ref.provider, system=request["system"],
                       user=request["user"], temperature=temperature, stream_cb=stream_cb,
                       **({"retry": False, "require_complete": True} if not retry else {}))
        cost_mc = settings.cost_millicents(model, res.input_tokens, res.output_tokens,
                                           provider=ref.provider)
        if getattr(res, "cached_response", None):
            meta["execution"] = "prepared_checkpoint_cache"
            meta["cache_key"] = res.cached_response
            record["meta"] = meta
            atomic.write_text(data_dir() / "assembly_previews" / book / "requests" / f"{request_id}.json", json.dumps(record, ensure_ascii=False, indent=2))
        with db.tx() as conn:
            conn.execute("UPDATE generation_log SET input_tokens=?, output_tokens=?, cost_cents=?, "
                         "cost_millicents=?, status='ok', error=NULL WHERE id=?",
                         (res.input_tokens, res.output_tokens, round(cost_mc / 1000), cost_mc, log_id))
            if getattr(res, "cached_response", None):
                conn.execute("UPDATE generation_log SET request_meta_json=? WHERE id=?", (json.dumps(meta, ensure_ascii=False), log_id))
        res.request_id = request_id
        res.request_meta = meta
        return res, p, request["user"]
    except Exception as error:
        # 不把供应商回显的连接配置、认证信息或正文放进错误日志；
        # 但状态码与错误码必须留下，否则"模型不存在"和"超时"在日志里长得一样。
        with db.tx() as conn:
            conn.execute("UPDATE generation_log SET status='error', error=? WHERE id=?",
                         (f"请求未完成：{llm.error_brief(error)}", log_id))
        raise


def _feedback_instruction(error: str) -> str:
    return (f"【上次输出被程序驳回】\n{error}\n"
            f"请严格按任务要求修正后，重新输出完整结果。")


# ---------------------------------------------------------------------------
# 1. 细纲生成与确认关（规格03 §2.1）
# ---------------------------------------------------------------------------

@serialized
def generate_outline(book: str, no: int, client=None) -> dict[str, Any]:
    conflicts.guard(book)  # 台账冲突队列非空 → 禁止开下一章细纲
    # 规格08 §3.2：rolling 书独立重查规划链就绪（不依赖前端/预检缓存）
    from . import navigation as _nav
    gate = _nav.planning_gate(book, no)
    if not gate["allowed"]:
        raise PipelineError(gate["message"] or "规划门禁未通过")
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
        "批次计划说明": "单章实时输入：档案为正式档案；上章结尾仅使用真实正文，缺失时不伪造原文。",
    }

    recent_rhythm: list[dict[str, Any]] = []
    for previous_no in range(max(1, no - 3), no):
        if not chapters.outline_path(book, previous_no).exists():
            continue
        previous_contract = chapters.read_outline_contract(book, previous_no)
        recent_rhythm.append({"chapter_no": previous_no,
                              "core_event": previous_contract.get("core_event"),
                              "scenes": previous_contract.get("scenes"),
                              "hook": previous_contract.get("hook"),
                              "emotion_task": previous_contract.get("emotion_task")})
    variables["最近三章节奏"] = json.dumps(recent_rhythm, ensure_ascii=False)

    # 规格08 §2.4/§3.4：G6 v5 新增「所属剧情单元」「本章规划边界」。
    # rolling 书只装配当前单元闭包（不整读所有单元）；legacy 书补明确说明，不猜造单元。
    variables["所属剧情单元"], variables["本章规划边界"] = _planning_closure(book, no, od)
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


def prepare_outline(book: str, no: int, *, projection: dict[str, Any],
                    previous_contracts: list[dict[str, Any]], client=None) -> dict[str, Any]:
    """（规格10 §2.3/§4）内存候选 G6，不写档、不迁移状态、不伪装人工采纳。"""
    from . import draftcontext, volumeproduction

    conflicts.guard(book)
    od = projection["outline"]
    row = next((r for r in od.get("chapter_rows") or [] if outline._row_no(r.get("章")) == no), None)
    if row is None:
        raise PipelineError(f"候选章纲缺第{no}章")
    previous = []
    for item in previous_contracts:
        contract = item.get("fm", item)
        if type(contract.get("chapter_no")) is not int or contract["chapter_no"] >= no:
            raise PipelineError("计划承接含当前/未来合同")
        previous.append(contract)
    previous.sort(key=lambda contract: contract["chapter_no"])
    prior = next((contract for contract in previous if contract["chapter_no"] == no - 1), {})
    if not prior and no > 1 and chapters.outline_path(book, no - 1).exists():
        prior = chapters.read_outline_contract(book, no - 1)
    plots = projection["plot_rows"]
    for contract in previous:
        plots = draftcontext.apply_fo_ops(plots, contract.get("foreshadow_ops") or [], contract["chapter_no"])
    actual_ending = ""
    if no > 1 and not any(contract["chapter_no"] == no - 1 for contract in previous):
        if chapters.prose_path(book, no - 1).exists():
            actual_ending = chapters.read_prose(book, no - 1)["_body"].strip()[-600:]
    vol = next((v for v in od.get("volumes") or [] if v.get("chapters")
                and v["chapters"][0] <= no <= v["chapters"][1]), {})
    unit = next((u for u in od.get("planning", {}).get("units") or []
                 if u.get("chapters") and u["chapters"][0] <= no <= u["chapters"][1]), {})
    wr = books.load_config(book)["chapter_word_range"]
    variables = {
        "全局大纲该章行": json.dumps(row, ensure_ascii=False),
        "所属卷目标": json.dumps(vol, ensure_ascii=False),
        "所属剧情单元": json.dumps(unit, ensure_ascii=False),
        "本章规划边界": json.dumps({k: unit.get(k) for k in ("can_reveal", "must_hold")}, ensure_ascii=False),
        "上一章钩子": str((prior.get("hook") or {}).get("content") or "（无前章钩子）"),
        "上一章钩子类型": str((prior.get("hook") or {}).get("type") or "无"),
        "上一章结尾原文": actual_ending or "（尚无已写原文；仅依据批次计划说明设计预期承接）",
        "上一章场景结构": json.dumps(prior.get("scenes") or [], ensure_ascii=False),
        "出场角色状态": json.dumps({name: {key: card.get(key) for key in ("role", "status")}
                                    for name, card in projection["characters"].items()}, ensure_ascii=False),
        "活跃伏笔线程": json.dumps([r for r in plots if r.get("状态") != "paid"], ensure_ascii=False),
        "最近三章节奏": json.dumps(previous[-3:], ensure_ascii=False),
        "章号": str(no), "总章数": str(max([outline._row_no(r.get("章")) or 0 for r in od["chapter_rows"]] + [no])),
        "字数区间": f"{wr[0]}-{wr[1]}",
        "批次计划说明": "卷准备阶段：全部上游为内存候选，未采纳、未确认。以下是前章计划合同而非已写原文："
            + json.dumps(prior, ensure_ascii=False),
    }
    if "〖批次计划说明〗" not in prompts.get("G6", book).body:
        raise PipelineError("PROMPT_UPGRADE_REQUIRED: G6 书级覆盖缺少批次计划说明")
    feedback = ""
    for _attempt in range(3):
        res, _p, _t = _call(book, no, "outline", "G6", variables, client=client, extra_instruction=feedback)
        try:
            if res.finish_reason in ("length", "max_tokens", "max_output_tokens"):
                raise PipelineError("G6 输出截断")
            block = _extract_contract_block(res.text)
            if block is None:
                raise PipelineError("G6 缺少合同")
            raw, _body = FM.parse("---\n" + block[0] + "\n---\n" + block[1])
            if raw.get("chapter_no") != no or raw.get("confirmed") is not False:
                raise PipelineError("G6 章号不一致或 confirmed 不是 false")
            if not isinstance(raw.get("foreshadow_ops"), list):
                raise PipelineError("G6 foreshadow_ops 必须为操作对象列表，不能静默丢弃")
            fm, body = _parse_outline_output(res.text, no)
            fm["word_range"] = list(wr)
            volumeproduction.validate_contract(book, fm, body, no, projection, plots,
                                               str((prior.get("hook") or {}).get("type") or ""))
            return {"no": no, "fm": fm, "body": body}
        except (FM.FrontmatterError, PipelineError, draftcontext.DraftContextError, ValueError) as error:
            feedback = _feedback_instruction(str(error))
    raise PipelineError(f"第{no}章候选细纲三次未通过校验：{feedback}")


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


@serialized
def confirm_outline(book: str, no: int) -> None:
    chapters.confirm_outline(book, no)
    statemachine.transition(book, no, "confirm")


# ---------------------------------------------------------------------------
# 2/3. 装配 + 正文生成（规格03 §2.2/§2.3）
# ---------------------------------------------------------------------------

def build_preview(book: str, no: int) -> dict[str, Any]:
    a = assembler.assemble(book, no)
    preview = a.preview()
    from .paths import data_dir
    from uuid import UUID

    actual: list[dict[str, Any]] = []
    with db.tx() as conn:
        rows = conn.execute("SELECT request_id FROM generation_log WHERE book_id=? AND chapter_no=? "
                            "AND request_id IS NOT NULL ORDER BY id DESC LIMIT 12",
                            (books.book_id(book), no)).fetchall()
    for row in reversed(rows):
        try:
            request_id = str(UUID(row["request_id"]))
            path = data_dir() / "assembly_previews" / book / "requests" / f"{request_id}.json"
            actual.append(json.loads(path.read_text(encoding="utf-8")))
        except (ValueError, OSError) as error:
            preview.setdefault("warnings", []).append(f"实际请求证据不可读：{type(error).__name__}")
    preview["actual_requests"] = actual
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


def _style_excerpt(book: str, chars: int = 1200) -> str:
    """（规格06 §4）兼容旧调用，使用确定性完整范例而非随机切片。"""
    from . import style as style_mod

    return "\n\n".join(item["text"] for item in style_mod.context(book)["examples"])


def _style_points(book: str) -> str:
    """（规格06 §4）所有阶段遵守同一个文风开关，不从关闭的卡片评分。"""
    from . import style as style_mod

    ctx = style_mod.context(book)
    return ctx["card"] if ctx["enabled"] else "（未启用书级文风）"


def _deslop_brief(body: str, limit: int = 8) -> str:
    """给 V1/V2 的检测器摘要：分数 + 前 N 条命中（带行号，便于定点改）。"""
    from . import deslop

    try:
        res = deslop.analyze(body)
    except Exception as e:  # 参数写坏等情况：如实告知模型"这次没有客观依据"
        return f"（检测器未执行：{e}）"
    if res.score < 0:
        return "（正文过短，检测器未评分）"
    head = f"文风规则参考分 {res.score}/100（非AI生成概率；仅建议，不能单独阻断或自动改稿）"
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


@serialized
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
    if st == "error":
        statemachine.transition(book, no, "retry")
        st = statemachine.current_status(book, no)
    if st == "outline_locked":
        statemachine.transition(book, no, "start_generate")
    elif st != "generating":
        raise statemachine.StateError(f"第{no}章状态 {st} 不能生成正文")

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


@serialized
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


@serialized
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
    if fr in ("length", "max_tokens", "max_output_tokens") or not body.strip():
        raise PipelineError("正文仍被截断或为空，保留草稿，未写正式正文")
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
        if res.finish_reason in ("length", "max_tokens", "max_output_tokens") or not res.text.strip():
            raise PipelineError(f"场景{i}被截断或为空，未拼接为正式正文")
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
                              "按人物目标与语境处理潜台词，允许直接表达，不强制回避或打断。",
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
        if res.finish_reason in ("length", "max_tokens", "max_output_tokens") or not res.text.strip():
            log.append({"reason": "output_truncated_or_empty", "from": n})
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

def _pass_mechanical_gate(book: str, no: int) -> None:
    """（规格05 F-08）历史导入章无细纲，不伪造合同，不强迫走 G6/V1。"""
    prose = chapters.read_prose(book, no)
    event = "pass_imported" if prose.get("imported") and not chapters.outline_path(book, no).exists() else "pass"
    statemachine.transition(book, no, event)


@serialized
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
        _pass_mechanical_gate(book, no)
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
                _pass_mechanical_gate(book, no)
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
        _pass_mechanical_gate(book, no)
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
        from . import draftcontext

        ctx = draftcontext.mech_context(book, no, ctx)
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
    """（规格03 §2.6）状态机和正文文件双重保护已定稿稿件。"""
    st = statemachine.current_status(book, no)
    prose = chapters.read_prose(book, no)
    if st == "finalized" or prose.get("status") == "final":
        raise statemachine.StateError(f"第{no}章已定稿，不能{action}；请先解锁")


@serialized
def run_targeted_fix(book: str, no: int, issues: list[Any], client=None) -> str:
    _assert_not_finalized(book, no, "定向修复")
    prose = chapters.read_prose(book, no)
    old_body = prose["_body"]
    cfg = books.load_config(book)
    wr = cfg["chapter_word_range"]
    before = _check(book, no, wr)
    if before.verdict == "not_effective":
        raise PipelineError("机检未完整执行，先修复规则配置再调用 AI 修复")
    if mechcheck.has_sensitive(before.hits):
        raise PipelineError("命中敏感词红线，禁止自动改写，请人工处理")
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
    2. 全章级问题无法精确定位时仍保留 10% 上限；字数调整使用独立 W6 通道，
       不能借全章级问题放宽未涉及句段的越权改动阈值。
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
                # 只按实际匹配的引用确定问题句段，所有重复命中都纳入范围。
                # 无法定位时仍执行 10% 上限，不再把阈值放宽为 35%。
                whole_chapter = True
                q = str(it.get("quote") or "")
                for frag in _split_quote(q):
                    for i, ln in enumerate(old_lines):
                        if frag in ln:
                            protect(i)
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
    limit = 0.10
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
                from . import draftcontext

                batch = draftcontext.current(book, no)
                if batch is not None:
                    st = batch["statuses"].get(name, st)
                have = f"境界={st.get('realm','')}｜位置={st.get('location','')}｜伤势={st.get('injuries',[])}"
                lines.append(f"[状态diff] {name}：细纲要求进入状态「{want}」，档案当前：{have}——请核对正文是否完成该状态迁移")
            except Exception:
                lines.append(f"[缺档案] 角色 {name} 在细纲出场但 characters/ 无档案")

    # 伏笔编号核对
    from . import draftcontext

    batch = draftcontext.current(book, no)
    rows = {r.get("编号"): r for r in (batch["plot_rows"] if batch is not None else outline.read_plot_points(book))}
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

def _validate_review(result: Any) -> dict[str, Any]:
    """（规格02 P-V1）五维分数及举证必须完整，格式错误不得放行。"""
    import math

    if not isinstance(result, dict) or result.get("verdict") not in ("pass", "fix", "fail"):
        raise llm.LLMError("审校 verdict 必须为 pass/fix/fail")
    scores = result.get("scores")
    if not isinstance(scores, dict):
        raise llm.LLMError("审校缺少五维 scores")
    for key in RUBRIC_DIMS:
        value = scores.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
            raise llm.LLMError(f"审校 {key} 必须是 0–100 的有限数字")
    issues = result.get("issues")
    if not isinstance(issues, list):
        raise llm.LLMError("审校 issues 必须为数组")
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("severity") not in ("blocking", "warning"):
            raise llm.LLMError("审校问题 severity 非法")
        if any(not isinstance(issue.get(k), str) or not issue[k].strip()
               for k in ("category", "location", "quote", "description", "suggestion")):
            raise llm.LLMError("审校问题缺少原文举证或建议")
    return result


def _assert_current_check(book: str, no: int, body: str, *, allow_exempt: bool = False) -> None:
    """（规格05 P-3）审校/定稿只能使用当前正文的有效机检或人工豁免。"""
    checked = db.get_check_status(book_id=books.book_id(book), chapter_no=no) or {}
    allowed = ("pass", "exempt") if allow_exempt else ("pass",)
    if checked.get("checked_status") not in allowed or checked.get("checked_fingerprint") != mechcheck.fingerprint(body):
        raise statemachine.StateError(f"第{no}章机检未通过或已过期，请重新运行机检")


def _enforce_rubric(result: dict[str, Any]) -> str:
    """程序强制 rubric 守门（规格02 P-V1 改造点5、规格03 §6）。

    规格要求「存在 blocking 或任一维 <70 → fix」，但原实现只信模型自己给的
    verdict：模型若判 pass 却给出低分（放水/自相矛盾），程序直接放行到定稿。
    这里按分数与 blocking 问题重算 verdict，模型只能把结果判得更严、不能更松。
    """
    try:
        _validate_review(dict(result, issues=[]))
    except llm.LLMError as error:
        result["validation_error"] = str(error)
        return "fail"
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


@serialized
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
    _assert_current_check(book, no, prose["_body"])
    contract = chapters.read_outline_contract(book, no)
    from . import draftcontext

    batch = draftcontext.current(book, no)
    variables = {
        "系统段": assembler.render_system(book),
        "正文": prose["_body"],
        "章合同": assembler._render_contract(contract),
        "疑似冲突清单": build_suspect_list(book, no),
        "台账摘要": "\n".join(
            f"{r.get('编号')}[{r.get('状态')}] {r.get('内容','')}" for r in (batch["plot_rows"] if batch is not None else outline.read_plot_points(book))
        ) or "（台账为空）",
        "字数区间": f"{wr[0]}-{wr[1]}",
        "程序字数": str(mechcheck.count_words(prose["_body"])),
        "文风要点": _style_points(book),
        "检测器结果": _deslop_brief(prose["_body"]),
        "时间线约束": _timeline_constraints(book, no),
        "批次临时上下文": draftcontext.evidence(batch) or "（单章调用，无批次临时事实）",
        "上一章结尾原文": (batch["previous_body"].strip()[-600:] if batch is not None else "") or "（本次未提供）",
    }

    result: dict[str, Any] | None = None
    feedback = ""
    for _attempt in range(3):  # JSON 解析失败 → 错误回喂重试 ×2
        res, _p, _t = _call(book, no, "review", "V1", variables, client=client,
                            extra_instruction=feedback)
        try:
            if res.finish_reason in ("length", "max_tokens", "max_output_tokens"):
                raise llm.LLMError("V1 输出截断，不能当作完整审校")
            candidate = _validate_review(llm.extract_json(res.text))
            result = candidate
            break
        except llm.LLMError as e:
            result = None
            feedback = _feedback_instruction(str(e))
    if result is None:
        statemachine.mark_error(book, no, "V1 JSON 解析失败")
        raise PipelineError("V1 审校 JSON 3 次解析失败")


    verdict = _enforce_rubric(result)
    result["verdict"] = verdict
    db.add_review(book_id=books.book_id(book), chapter_no=no, version=_version(book, no),
                  kind="llm", scores=result.get("scores"), issues=result.get("issues"),
                  verdict=verdict, fingerprint=mechcheck.fingerprint(prose["_body"]))
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

def polish(book: str, no: int, client=None, *, expected_revision: int | None = None,
           client_request_id: str | None = None, mode: str = "auto",
           selected_blocks: list[str] | None = None) -> dict[str, Any]:
    """（规格06 §7—§9）只生成候选；写回必须另经人工采纳。"""
    from .polish import create_candidate

    return create_candidate(book, no, expected_revision=expected_revision,
                            client_request_id=client_request_id, mode=mode,
                            selected_blocks=selected_blocks, client=client)


def _fidelity_error(old: str, new: str) -> str | None:
    """（规格05 C-01）限制长度、句末结构与四元字串骨架丢失，不替代语义人审。"""
    original = re.sub(r"\s+", "", old)
    candidate = re.sub(r"\s+", "", new)
    if not original or not 0.55 <= len(candidate) / len(original) <= 1.9:
        return "正文长度比例异常"
    before = len(re.findall(r"[。！？!?]", original))
    after = len(re.findall(r"[。！？!?]", candidate))
    if before and after < before * 0.6:
        return "句末标点数量锐减"
    grams = {original[i:i + 4] for i in range(max(0, len(original) - 3))}
    if grams and sum(gram in candidate for gram in grams) / len(grams) < 0.30:
        return "四元字串骨架覆盖率低于 30%"
    return None




# ---------------------------------------------------------------------------
# 6. 人工关口：豁免 / 定稿 / 重写（规格03 §2.5-§2.6、规格01 §4）
# ---------------------------------------------------------------------------

@serialized
def exempt(book: str, no: int, reason: str) -> None:
    """豁免必须填理由，写入 generation_log（合规证据链）。"""
    if not reason or not reason.strip():
        raise PipelineError("豁免必须填写理由")
    if statemachine.current_status(book, no) != "human_review":
        raise statemachine.StateError("仅人工复核状态可豁免")
    res = _check(book, no, books.load_config(book)["chapter_word_range"])
    if res.verdict == "not_effective":
        raise statemachine.StateError("机检未完整执行，请先修复词库或配置，不能豁免未执行检查")
    statemachine.transition(book, no, "approve")
    db.set_check_status(book_id=books.book_id(book), chapter_no=no,
                        fingerprint=res.fingerprint, status="exempt")
    db.log_generation(book_id=books.book_id(book), chapter_no=no, stage="exempt",
                      status="ok", error=f"正文指纹 {res.fingerprint}；豁免理由：{reason.strip()}")


@serialized
def edit_outline(book: str, no: int) -> None:
    statemachine.transition(book, no, "edit_outline")  # → outline_draft（改细纲）


@serialized
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
        except Exception as e:
            raise PipelineError(f"当前正文读取失败，未开新版本、未改动文件：{e}") from e

    # 2) 落盘新版本
    new_version = _next_version(book, no)
    fm = {"chapter_no": no, "version": new_version, "status": "draft",
          "word_count": mechcheck.count_words(body), "finalized_at": None}
    chapters.write_prose(book, no, fm, body, recheck=False)
    statemachine.set_current_version(book, no, new_version)
    statemachine.set_fix_rounds(book, no, 0)
    statemachine.transition(book, no, "rewrite")  # → generating
    return new_version


PROSE_CORRECT_MAX_RATIO = 0.05  # 订正上限：超过 5% 就是重写


@serialized
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


@serialized
def finalize(book: str, no: int, client=None) -> dict[str, Any]:
    """定稿 = 二次确认（API 层）→ 快照 + status:final → 记忆卡回写。"""
    from .polish import guard_recovery

    guard_recovery(book, no)
    st = statemachine.current_status(book, no)
    if st != "pending_finalize":
        raise statemachine.StateError(f"第{no}章状态 {st} 不能定稿")

    prose = chapters.read_prose(book, no)
    if prose.get("imported") and not (db.get_check_status(book_id=books.book_id(book), chapter_no=no) or {}).get("checked_fingerprint"):
        checked = _check(book, no, books.load_config(book)["chapter_word_range"])
        if checked.verdict != "pass":
            raise statemachine.StateError("导入章机检未通过，请先处理并重跑机检")
        db.set_check_status(book_id=books.book_id(book), chapter_no=no,
                            fingerprint=checked.fingerprint, status="pass")
    _assert_current_check(book, no, prose["_body"], allow_exempt=True)
    current_check = _check(book, no, books.load_config(book)["chapter_word_range"])
    saved = db.get_check_status(book_id=books.book_id(book), chapter_no=no) or {}
    if current_check.verdict == "not_effective" or (current_check.verdict != "pass" and saved.get("checked_status") != "exempt"):
        raise statemachine.StateError("当前机检规则未通过或未完整执行，请重跑机检或人工处理")
    fm = {k: v for k, v in prose.items() if not k.startswith("_")}
    previous_snapshot = get_snapshot(book, no, fm["version"])
    if previous_snapshot is not None and previous_snapshot != prose["_body"]:
        fm["version"] = _next_version(book, no)
        statemachine.set_current_version(book, no, fm["version"])
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
        logger.warning("第%s章已定稿但记忆卡回写失败，可从补做入口重试: %s", no, e)
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


@serialized
def rebuild_memory_card(book: str, no: int, client=None) -> dict[str, Any]:
    """（规格05 F-04）先抽取候选，再在可回滚边界内替换旧派生结果。"""
    st = statemachine.current_status(book, no)
    if st != "finalized":
        raise statemachine.StateError(
            f"第{no}章状态 {st} 不能补做记忆卡（需 finalized）")

    from . import state as state_mod

    prose = chapters.read_prose(book, no)
    candidate = _extract_memory(book, no, prose, client=client)
    with memory_commit(book):
        old_card = chapters.read_memory(book, no) if chapters.memory_path(book, no).exists() else {}
        rollback = {"state_rows": len(state_mod.chapter_rows(book, no)),
                    "foreshadow": outline.rollback_foreshadow_ops(book, no, old_card.get("foreshadow_ops", [])),
                    "card_deleted": False}
        for name, baseline in old_card.get("status_before", {}).items():
            data = characters.read(book, name)
            if data.get("status", {}).get("updated_chapter") == no:
                fm = {k: v for k, v in data.items() if not k.startswith("_")}
                fm["status"] = baseline
                characters.write(book, fm, data["_body"])
        state_mod.clear_chapter(book, no)
        wb = _apply_memory(book, no, prose, candidate)
    db.log_generation(book_id=books.book_id(book), chapter_no=no,
                      stage="memory_rebuild", status="ok")
    return {"ok": True, "chapter_no": no, "rollback": rollback,
            "applied": {"char_updates": wb.get("applied_chars", []),
                        "foreshadow_warnings": wb.get("warnings", []),
                        "state_entries": len(state_mod.chapter_rows(book, no)),
                        "event_summary": wb.get("event_summary", "")}}


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


@serialized
def writeback_memory(book: str, no: int, client=None) -> dict[str, Any]:
    prose = chapters.read_prose(book, no)
    candidate = _extract_memory(book, no, prose, client=client)
    with memory_commit(book):
        return _apply_memory(book, no, prose, candidate)


def _extract_memory(book: str, no: int, prose: dict[str, Any], client=None) -> dict[str, Any]:
    """（规格03 §2.7）只抽取并校验，不改档案；重试调用照常记账。"""
    from . import draftcontext

    batch = draftcontext.current(book, no)
    variables = {
        "正文": prose["_body"],
        "章号": str(no),
        "现有角色名清单": "\n".join(characters.list_characters(book)) or "（无）",
        # 只给"本章之前已埋设"的伏笔：补做历史章时若把未来的编号给模型，
        # 它会写出"第 8 章推进第 12 章的伏笔"这种时序错误
        "活跃伏笔编号清单": "\n".join(
            f"{r.get('编号')}[{r.get('状态')}]" for r in (batch["plot_rows"] if batch is not None else outline.active_foreshadows(book))
            if str(r.get("埋设章", "") or "").strip().isdigit()
            and int(str(r.get("埋设章")).strip()) <= no
        ) or "（无）",
    }
    feedback = ""
    for _attempt in range(3):
        res, _p, _t = _call(book, no, "memory", "V4", variables, client=client,
                            extra_instruction=feedback)
        try:
            if res.finish_reason in ("length", "max_tokens", "max_output_tokens"):
                raise llm.LLMError("V4 输出截断，不能作为完整记忆")
            candidate = llm.extract_json(res.text)
            if not isinstance(candidate, dict):
                raise llm.LLMError("V4 必须输出 JSON 对象")
            candidate["chapter_no"] = no
            chapters.validate_memory(candidate)
            _validate_memory_items(candidate)
            return candidate
        except (llm.LLMError, FM.FrontmatterError) as e:
            feedback = _feedback_instruction(str(e))
    raise PipelineError("V4 记忆卡 3 次解析/校验失败，未改动原档案")


def _apply_memory(book: str, no: int, prose: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """（规格03 §2.7）在可回滚保护内应用已校验的候选。"""

    body = f"{result.get('event_summary','')}（第{no}章记忆卡）\n"
    result["source_fingerprint"] = mechcheck.fingerprint(prose["_body"])

    result["status_before"] = {name: characters.read(book, name).get("status", {})
                               for name in characters.list_characters(book)}
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
        except Exception:
            raise

    # F-06：时间线候选只进裁决队列，**绝不自动落盘**（骨架真相必须人工确认）
    t_candidates = result.get("timeline_candidates") or []
    if t_candidates:
        from . import timeline

        conflicts.enqueue(timeline.candidates(book, t_candidates, no))

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
    chapters.write_memory(book, no, result, body)
    from . import state as state_mod

    state_mod.rebuild_state_md(book)

    return {
        "applied_chars": applied,
        "conflicts": [c.to_dict() for c in conflict_list] + [{"detail": e} for e in fo_errors],
        "warnings": fo_warns,
        "event_summary": result.get("event_summary", ""),
    }


def _validate_memory_items(result: dict[str, Any]) -> None:
    """（规格05 F-04/F-05）拒绝数组中的坏类型，不允许半途写入后才报错。"""
    for key in ("char_updates", "new_chars", "foreshadow_ops", "state_delta", "timeline_candidates"):
        for item in result.get(key) or []:
            if not isinstance(item, dict):
                raise llm.LLMError(f"V4 {key} 条目必须为对象")
    for entry in result.get("state_delta") or []:
        name = entry.get("character") or entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise llm.LLMError("state_delta 缺角色名")
        for key in ("location", "physical", "mental", "power_level"):
            if entry.get(key) is not None and not isinstance(entry[key], str):
                raise llm.LLMError(f"state_delta.{key} 必须为文本")


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


def _planning_closure(book: str, no: int, od: dict[str, Any]) -> tuple[str, str]:
    """（规格08 §3.4）G6 的最小规划闭包：当前单元详情 + 本章边界。

    只装配**当前章所属单元**，不整读所有卷和单元；legacy 书返回明确的无单元说明，
    不猜造。`od` 为已读的 OUTLINE frontmatter（含 planning 时）。
    """
    from . import planning as _planning

    try:
        info = _planning.read_planning(book)
    except _planning.PlanningInvalid:
        info = {"mode": "legacy", "planning": None}
    if info["mode"] != "rolling":
        return ("旧模式无单元：按既有章纲/卷目标执行，不猜造剧情单元。",
                "旧模式：无单元信息边界，按既有章纲与已写事实控制信息释放。")

    units = info["planning"].get("units") or []
    unit = next((u for u in units
                 if (u.get("chapters") or [0, 0])[0] <= no <= (u.get("chapters") or [0, 0])[-1]),
                None)
    if unit is None:
        return ("本章未映射到剧情单元（规划页尚未采纳本批次章纲）；按既有章纲执行。",
                "无单元边界信息；只依据已写事实与既有章纲。")

    lines = [
        f"单元「{unit.get('name') or unit.get('id')}」（第 {unit.get('chapters')} 章，规划而非已写事实）",
        f"- 单元目标：{unit.get('goal') or '（未填）'}",
        f"- 主要阻力：{unit.get('obstacle') or '（未填）'}",
        f"- 关键选择：{'；'.join(unit.get('choices') or []) or '（未填）'}",
        f"- 收束结果（计划）：{unit.get('outcome') or '（未填）'}",
        f"- 可释放信息：{'；'.join(unit.get('can_reveal') or []) or '（无）'}",
        f"- 必须保留（不得提前释放）：{'；'.join(unit.get('must_hold') or []) or '（无）'}",
        "- 相邻单元信息不在本次上下文，不得引用其未释放内容",
    ]
    unit_text = "\n".join(lines)
    rhythm = _planning.rhythm_batch(str(od.get("_rhythm") or ""),
                                    str(unit.get("id")),
                                    (unit.get("chapters") or [no, no])[0],
                                    (unit.get("chapters") or [no, no])[-1])
    boundary = (
        f"本章属单元 {unit.get('id')} 第 {no} 章。"
        f"单元节奏说明（已采纳批次小节）：{rhythm or '（无）'}。"
        "「必须保留」信息一律不得在本章泄露；未来设定/角色建议不得冒充已确认世界。"
    )
    return unit_text, boundary
