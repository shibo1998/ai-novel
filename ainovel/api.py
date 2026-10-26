"""FastAPI：全部路由 + SSE 流式正文 + 静态 UI。"""
from __future__ import annotations

import io
import json
import logging
import re
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (assembler, bible, books, chapters, characters, conflicts, db,
               fts, llm, mechcheck, outline, pipeline, prompts, settings,
               statemachine)
from .paths import book_dir, project_root

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """启动即生成 data/settings.json（否则用户找不到可手改的配置文件）。

    用 lifespan 取代已弃用的 @app.on_event（FastAPI 后续版本会移除 on_event）。
    """
    settings.load()
    try:
        n = db.backfill_cost_millicents()
        if n:
            logger.info("历史成本回填：%d 条按 token 数重算为毫分口径", n)
    except Exception:  # 回填失败不该拦住启动
        logger.exception("历史成本回填失败，成本展示可能偏低")
    yield


app = FastAPI(title="AI 网文工作台", lifespan=_lifespan)


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """未处理异常统一兜底：服务端留全量堆栈日志，前端只拿笼统提示（不泄内部细节）。"""
    logger.exception("未处理异常 %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "内部错误，详见服务端日志"})


def _err(e: Exception, code: int = 400) -> HTTPException:
    logger.warning("请求处理失败: %s", e)
    return HTTPException(status_code=code, detail=str(e))


# ---------------------------------------------------------------------------
# 系统
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"ok": True, "version": "0.1.0", "fts_mode": fts.tokenize_mode()}


@app.post("/api/db/rebuild")
def rebuild():
    return db.rebuild()


@app.get("/api/fts/mode")
def fts_mode():
    return {"mode": fts.tokenize_mode()}


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------

class SettingsPayload(BaseModel):
    settings: dict[str, Any]


@app.get("/api/settings")
def get_settings():
    return settings.masked()


@app.put("/api/settings")
def put_settings(payload: SettingsPayload):
    merged = settings.update(payload.settings)
    return settings.masked()


@app.post("/api/settings/test")
def test_connection(payload: dict):
    """测试唯一 OpenAI 兼容端点。payload.model 可选，默认 deepseek-chat。"""
    s = settings.load()
    prov = s.get("providers", {}).get("openai")
    if not prov or not prov.get("api_key"):
        return {"ok": False, "error": "未配置 API key"}
    model = payload.get("model") or s.get("default_model") or "deepseek-chat"
    try:
        cli = llm.OpenAICompatClient(prov["base_url"], prov["api_key"])
        res = cli.chat(model=model, system="你是连通性测试。", user="回复：OK",
                       temperature=0.0)
        return {"ok": True, "reply": res.text[:50], "model": model}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


class LintPayload(BaseModel):
    which: str   # banned_words | sensitive_words | dialog_suffix
    content: str


@app.get("/api/lint/{which}")
def get_lint(which: str):
    if which not in ("banned_words", "sensitive_words", "dialog_suffix"):
        raise _err(ValueError("非法词库"))
    from .paths import lint_dir

    p = lint_dir() / f"{which}.txt"
    return {"which": which, "content": p.read_text(encoding="utf-8") if p.exists() else ""}


@app.put("/api/lint/{which}")
def put_lint(which: str, payload: LintPayload):
    if which not in ("banned_words", "sensitive_words", "dialog_suffix"):
        raise _err(ValueError("非法词库"))
    from . import atomic
    from .paths import lint_dir

    atomic.write_text(lint_dir() / f"{which}.txt", payload.content)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 书
# ---------------------------------------------------------------------------

class BookCreate(BaseModel):
    name: str
    genre_template: str = "urban"
    target_words: int = 1_000_000
    author: str = ""
    platform: str = "番茄"
    chapter_word_range: list[int] | None = None


@app.get("/api/books")
def list_books():
    return books.list_books()


@app.post("/api/books")
def create_book(payload: BookCreate):
    try:
        return books.create_book(payload.name, genre_template=payload.genre_template,
                                 target_words=payload.target_words, author=payload.author,
                                 platform=payload.platform,
                                 chapter_word_range=payload.chapter_word_range)
    except Exception as e:
        raise _err(e)


@app.delete("/api/books/{book}")
def delete_book(book: str):
    try:
        books.delete_book(book)
        return {"ok": True}
    except Exception as e:
        raise _err(e, 404)


@app.get("/api/books/{book}/overview")
def overview(book: str):
    try:
        meta = books.read_meta(book)
    except Exception as e:
        raise _err(e, 404)
    chs = statemachine.list_chapters(book)
    bid = books.book_id(book)
    with db.tx() as conn:
        vol_rows = conn.execute(
            "SELECT chapter_no, SUM(COALESCE(cost_millicents, cost_cents * 1000)) c "
            "FROM generation_log WHERE book_id=? AND status='ok' GROUP BY chapter_no", (bid,)
        ).fetchall()
    cost_by_ch = {r["chapter_no"]: r["c"] for r in vol_rows}
    for c in chs:
        # 毫分口径：按分聚合时单章 0.56 分会进位成 0，整本书看起来"没花钱"
        c["cost_millicents"] = cost_by_ch.get(c["no"], 0)
        c["cost_cents"] = round(c["cost_millicents"] / 1000)
        c["cost_over_yuan"] = c["cost_millicents"] > 100_000  # 单章 >1 元标黄（验收线）
    return {
        "meta": {k: v for k, v in meta.items() if not k.startswith("_")},
        "chapters": chs,
        "cost_total": db.cost_summary(bid),
        "open_conflicts": len(conflicts.list_open(book)),
        "characters": characters.list_characters(book),
        "outline_rows": outline.read_outline(book)["chapter_rows"],
        "prompts_source": "book" if (book_dir(book) / "prompts").exists() else "global",
    }


@app.get("/api/books/{book}/config")
def get_config(book: str):
    return books.load_config(book)


@app.put("/api/books/{book}/config")
def put_config(book: str, payload: dict):
    cfg = books.load_config(book)
    cfg.update(payload)
    books.save_config(book, cfg)
    return cfg


@app.get("/api/books/{book}/meta")
def get_meta(book: str):
    return books.read_meta(book)


@app.put("/api/books/{book}/meta")
def put_meta(book: str, payload: dict):
    body = payload.pop("_body", None)
    try:
        books.write_meta(book, payload, body)
    except Exception as e:
        raise _err(e)
    return books.read_meta(book)


# ---------------------------------------------------------------------------
# 档案：Bible / 角色 / 大纲 / 台账 / 地点
# ---------------------------------------------------------------------------

class LocationPayload(BaseModel):
    """地点档案整表（规格01 §2.6：5 列）。"""
    rows: list[dict[str, str]] = []


@app.get("/api/books/{book}/bible")
def get_bible(book: str):
    return bible.read(book)


@app.put("/api/books/{book}/bible")
def put_bible(book: str, payload: dict):
    try:
        bible.write(book, payload)
    except Exception as e:
        raise _err(e)
    return {"ok": True}


@app.get("/api/books/{book}/characters")
def get_characters(book: str):
    return [characters.read(book, n) for n in characters.list_characters(book)]


@app.get("/api/books/{book}/characters/{name}")
def get_character(book: str, name: str):
    try:
        return characters.read(book, name)
    except Exception as e:
        raise _err(e, 404)


@app.put("/api/books/{book}/characters/{name}")
def put_character(book: str, name: str, payload: dict):
    body = payload.pop("_body", None)
    try:
        payload["name"] = name
        characters.write(book, payload, body if body is not None else payload.get("_body", ""))
    except Exception as e:
        raise _err(e)
    return {"ok": True}


@app.post("/api/books/{book}/characters")
def post_character(book: str, payload: dict):
    try:
        characters.create(book, payload["name"], role=payload.get("role", "龙套"))
    except Exception as e:
        raise _err(e)
    return {"ok": True}


@app.get("/api/books/{book}/outline")
def get_outline(book: str):
    return outline.read_outline(book)


@app.put("/api/books/{book}/outline")
def put_outline(book: str, payload: dict):
    """保存大纲（规格01 §2.4）。

    前端会把 GET 回来的整个对象原样回传，其中含读接口专用的 `_body`。
    必须剥离，否则会被当成 frontmatter 键写进 OUTLINE.md（污染 YAML）。
    `_rhythm` 前端不提供时沿用文件里已有的「爽点节奏总谱」，避免每次保存
    都把该节重置为「（待规划）」（P0 回归）。
    """
    payload.pop("_body", None)
    rows = payload.pop("chapter_rows", [])
    rhythm = payload.pop("_rhythm", None)
    try:
        if rhythm is None:
            rhythm = outline.read_rhythm(book)
        outline.write_outline(book, payload, rows, rhythm)
    except Exception as e:
        raise _err(e)
    return {"ok": True}


@app.get("/api/books/{book}/plot_points")
def get_pp(book: str):
    return outline.read_plot_points(book)


@app.get("/api/books/{book}/plot_points/overdue")
def get_pp_overdue(book: str, chapter: int | None = None):
    """伏笔超期预警只读回看（规格01 PLOT_POINTS 三条程序校验之一）。

    `planted` 超过 30 章仍未进入 `building` → 提示作者推进或回收。原先只有 G6
    落盘那一刻把 warns 回给调用方，用户保存完就再也看不到；这里按当前进度重算一次。
    章号缺省取已有章节的最大号（没有章节时为 0），口径与 `outline.overdue_warnings` 完全一致。
    只读接口：不改台账、不迁状态。
    """
    rows = outline.read_plot_points(book)
    cur = chapter if chapter is not None else max(chapters.list_chapters(book) or [0])
    return {"chapter": cur, "grace": 30, "overdue": outline.overdue_warnings(rows, cur)}


@app.put("/api/books/{book}/plot_points")
def put_pp(book: str, payload: dict):
    rows = payload.get("rows", [])
    old = outline.read_plot_points(book)
    errs = outline.validate_transitions(old, rows)
    if errs:
        raise _err(ValueError("状态迁移非法: " + "; ".join(errs)))
    outline.write_plot_points(book, rows)
    return {"ok": True}


@app.get("/api/books/{book}/locations")
def get_locations(book: str):
    return outline.read_locations(book)


@app.put("/api/books/{book}/locations")
def put_locations(book: str, payload: LocationPayload):
    """整表保存地点档案（规格01 §2.6 / 规格04 §1"地点档案可导航可编辑"）。

    前端传 5 列表格行；多余键忽略、缺列补空串（由 outline.write_locations 归一化）。
    行内空白「名称」直接丢弃——空名行在档案里没有意义，且会破坏 FTS 关键词抽取。
    """
    rows = [r for r in payload.rows if str(r.get("名称", "")).strip()]
    try:
        outline.write_locations(book, rows)
    except Exception as e:
        raise _err(e)
    return {"ok": True}


# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------

@app.get("/api/prompts")
def list_prompts(book: str | None = None):
    try:
        return prompts.list_meta(book)
    except prompts.PromptError as e:
        raise _err(e)


@app.get("/api/prompts/{prompt_id}")
def get_prompt(prompt_id: str, book: str | None = None):
    try:
        p = prompts.get(prompt_id, book)
        return {"id": p.id, "meta": p.fm, "body": p.body, "digest": p.digest, "source": p.source}
    except prompts.PromptError as e:
        raise _err(e, 404)


# ---------------------------------------------------------------------------
# G 链：AI 生成档案 → diff 预览 → 采纳落盘（AI 永不静默覆盖人工内容）
# ---------------------------------------------------------------------------

class AIGenPayload(BaseModel):
    prompt_id: str
    variables: dict[str, Any] = {}


@app.post("/api/books/{book}/ai/generate")
def ai_generate(book: str, payload: AIGenPayload):
    try:
        res, p, text = pipeline._call(book, None, "archive", payload.prompt_id,
                                      payload.variables)
        return {"text": res.text, "prompt_id": p.id, "digest": p.digest,
                "input_tokens": res.input_tokens, "output_tokens": res.output_tokens}
    except prompts.PromptError as e:
        raise _err(e, 404)
    except Exception as e:
        raise _err(e, 502)


class AdoptPayload(BaseModel):
    content: str


@app.post("/api/books/{book}/adopt/bible")
def adopt_bible(book: str, payload: AdoptPayload):
    """合并式采纳：G1 整本或 G2 单节都只更新出现的节，其余保留。"""
    incoming = bible._parse_sections(payload.content)
    # 剥离一切自检节（G1 的"自检"、G2 的"平衡五问自检"等）
    incoming = {k: v for k, v in incoming.items() if "自检" not in k}
    if not incoming:
        # G2 输出没有 H2 节标题、直接给表格：整体按力量体系收
        if "大境界" in payload.content:
            incoming = {"力量体系": payload.content.strip()}
        else:
            raise _err(ValueError("生成结果未解析到任何 H2 节"))
    sections = bible.read(book)
    sections.update(incoming)
    bible.write(book, sections)
    return {"ok": True, "sections": list(sections), "updated": list(incoming)}


@app.post("/api/books/{book}/adopt/outline")
def adopt_outline(book: str, payload: AdoptPayload):
    """解析 G5 输出 → OUTLINE.md + PLOT_POINTS.md（伏笔编号自动转 FO-xxx）。

    真实模型的章号/卷号常写成"第1章/第一卷"——采纳时统一归一化为纯数字。

    合并语义（规格02 §4 末句"AI 生成的档案永远不直接静默覆盖人工内容"）：
    - 章节总表/卷规划：G5 是全书大纲的完整产出，按本次输出整体替换（本就是全书级重规划）
    - 伏笔台账：**合并**。已有内容相同的行保留原编号与原状态/埋设章/推进章，只补空字段；
      新内容才分配新编号（`max(现有FO数字)+1` 递增）；G5 未提及的旧行一律保留。
    - logline：OUTLINE.md 已有非空 logline 时保留，只有为空才用 meta 正文兜底。
    """
    from .mdtable import parse_table

    text = payload.content
    # 章节总表（先解析，卷兜底要用行号范围）
    _ccols, crows = parse_table(text, "本章目标")
    norm_rows = []
    for r in crows:
        m = re.search(r"\d+", str(r.get("章", "")))
        if not m:
            continue
        vm = re.search(r"\d+", str(r.get("卷", "")))
        r = dict(r)
        r["章"] = m.group()
        if vm:
            r["卷"] = vm.group()
        norm_rows.append(r)
    if not norm_rows:
        raise _err(ValueError("章节总表未解析到任何有效行"))
    lo, hi = int(norm_rows[0]["章"]), max(int(x["章"]) for x in norm_rows)

    # 卷规划（归一化：卷号抽数字；起止章抽两个数字，缺失则用总表范围）
    volumes = []
    _vcols, vrows = parse_table(text, "卷号")
    for i, r in enumerate(vrows, 1):
        nums = [int(x) for x in re.findall(r"\d+", str(r.get("起止章", "")))]
        m = re.search(r"\d+", str(r.get("卷号", "")))
        ch = (nums + [lo, hi])[:2] if len(nums) >= 2 else [lo, hi]
        volumes.append({"no": m.group() if m else str(i), "name": r.get("卷名", ""),
                        "chapters": ch, "core_conflict": r.get("核心冲突", ""),
                        "goal": r.get("本卷目标", ""), "climax": r.get("卷末高潮", ""),
                        "hook_out": r.get("卷末钩子(出卷悬念)", "")})
    if not volumes:
        volumes = [{"no": "1", "name": "", "chapters": [lo, hi], "core_conflict": "",
                    "goal": "", "climax": "", "hook_out": ""}]

    # 伏笔总表 → 合并式落盘（FO 编号只增不重编）
    _fcols, frows = parse_table(text, "编号")
    fo_rows, id_map, added, kept = _merge_foreshadows(book, frows)

    m = re.search(r"(?ms)^##\s*爽点节奏总谱\s*\n(.*?)(?=^##|\Z)", text)
    rhythm = m.group(1).strip() if m else ""
    try:
        old = outline.read_outline(book)
    except Exception:
        # OUTLINE.md 不存在/frontmatter 损坏时按"没有旧值"处理：
        # 采纳的首要任务是能写进一份合法大纲，不能因旧文件坏了就整条链断掉。
        old = {}
    logline = str(old.get("logline") or "").strip()
    if not logline:
        # OUTLINE.md 没有 logline 时才用 meta 正文兜底（避免覆盖用户已填的内容）
        logline = str(books.read_meta(book).get("_body", "")).strip() or "（待填）"
    writing = {"logline": logline,
               "ending_direction": old.get("ending_direction", ""),
               "volumes": volumes}
    outline.write_outline(book, writing, rows=norm_rows, rhythm=rhythm)
    if fo_rows:
        outline.write_plot_points(book, fo_rows)
    return {"ok": True, "volumes": len(volumes), "chapter_rows": len(norm_rows),
            "foreshadows": len(frows), "id_map": id_map,
            "foreshadows_added": added, "foreshadows_kept": kept}


def _merge_foreshadows(
    book: str, frows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], dict[str, str], int, int]:
    """把 G5 伏笔行合并进现有台账（规格02 §4 编号自动分配 + 永不静默覆盖）。

    匹配键 = 「内容」文本（去空白）。命中 → 沿用原编号与已有状态/埋设章/推进章/预计回收章，
    只补空字段；未命中 → 分配 `max(现有FO数字)+1` 递增的新编号。

    返回 (合并后台账行, {G5编号: FO编号}, 新增数, 命中保留数)。
    """
    try:
        existing = outline.read_plot_points(book)
    except FileNotFoundError:
        # 台账文件缺失（手工删过/老目录升级）：按空台账处理，G5 至少能建起一份
        existing = []
    merged = [dict(r) for r in existing]
    # 内容 → 合并副本（首条优先；空内容不参与匹配，避免互相误吞）
    by_content: dict[str, dict[str, str]] = {}
    next_no = 1
    for row in merged:
        fid = str(row.get("编号", ""))
        m = re.search(r"(\d+)", fid)
        if m:
            next_no = max(next_no, int(m.group(1)) + 1)
        content = str(row.get("内容", "")).strip()
        if content and content not in by_content:
            by_content[content] = row

    id_map: dict[str, str] = {}
    added = kept = 0
    for r in frows:
        g5_id = str(r.get("编号", "")).strip()
        content = str(r.get("内容", "")).strip()
        hit = by_content.get(content) if content else None
        if hit is not None:
            # 已有：保留原编号/状态/章次，只补空字段
            for col in ("埋设章", "推进章", "预计回收章", "备注"):
                if not str(hit.get(col, "")).strip() and str(r.get(col, "")).strip():
                    hit[col] = r.get(col, "")
            id_map[g5_id] = str(hit.get("编号", ""))
            kept += 1
            continue
        new_id = f"FO-{next_no:03d}"
        next_no += 1
        row = {"编号": new_id, "内容": content, "埋设章": r.get("埋设章", ""),
               "推进章": r.get("推进章", ""), "预计回收章": r.get("预计回收章", ""),
               "状态": "planted", "备注": r.get("备注", "")}
        merged.append(row)
        if content:
            by_content[content] = row
        id_map[g5_id] = new_id
        added += 1
    return merged, id_map, added, kept


@app.post("/api/books/{book}/adopt/characters")
def adopt_characters(book: str, payload: AdoptPayload):
    """解析 G3/G4 输出（===角色:<名>=== 分卡）→ 建角色档案 + 关系网合并写回。

    规格02 §4：G4「结果合并写回各角色的『关系网络』节」。原实现对已存在角色直接
    `continue`，关系表整段丢弃（注释与代码不符）；新角色则把卡整体当正文写，
    没有 `## 关系网络` 节标题，`characters.relationships()` 读不到。
    现在：已存在角色 → 只把关系行合并进其关系网络节；新角色 → 建卡 + 写节。
    """
    cards = re.split(r"(?m)^===\s*角色[:：]\s*(.+?)\s*===\s*$", payload.content)
    created: list[str] = []
    updated: list[str] = []
    rel_added: dict[str, int] = {}
    # cards: [前置, 名1, 卡1, 名2, 卡2, ...]
    it = iter(cards[1:])
    for name, card in zip(it, it):
        name = name.strip()
        role = "龙套"
        m = re.search(r"主角|女主|反派|重要配角", card[:60])
        if m:
            role = m.group(0)
        rel_rows = _parse_relationship_rows(card, name)
        if name not in characters.list_characters(book):
            characters.create(book, name, role=role)
            d = characters.read(book, name)
            fm = {k: v for k, v in d.items() if not k.startswith("_")}
            characters.write(book, fm, card.strip() + "\n")
            created.append(name)
        n = characters.merge_relationships(book, name, rel_rows)
        if n:
            rel_added[name] = n
            if name not in created:
                updated.append(name)
    return {"ok": True, "created": created, "updated": updated,
            "relationship_rows_added": rel_added}


def _parse_relationship_rows(card: str, self_name: str) -> list[dict[str, str]]:
    """从 G4 分卡正文解析关系行，返回可写进「关系网络」节的 {对象,关系,不稳定因素} 行。

    兼容三种形态（G4 提示词自己会产出前两种）：
    1. 逐角色节的三列表：`| 对象 | 关系 | 不稳定因素 |`
    2. 关系矩阵总览六列表：`| 对象A | 对象B | 关系 | 渊源… | 张力点 | 真实态度 |`
       → 取涉及本角色的行，对方作「对象」、张力点作「不稳定因素」
    3. 列表式：`- 甲 → 乙：关系`
    解析不到返回空列表。
    """
    from .mdtable import parse_table

    rows: list[dict[str, str]] = []
    # 1) 三列关系表：按精确表头找，避免误吞下面的六列矩阵（其表头也含"对象"）
    _, rel_rows = parse_table(_find_rel_table(card), "对象")
    for r in rel_rows:
        obj = str(r.get("对象", "")).strip()
        if obj and obj != self_name:
            rows.append({c: str(r.get(c, "")) for c in characters.REL_COLS})
    # 2) 六列关系矩阵总览
    _, matrix = parse_table(card, "对象A")
    for r in matrix:
        a = str(r.get("对象A", "")).strip()
        b = str(r.get("对象B", "")).strip()
        if self_name not in (a, b):
            continue
        other = b if a == self_name else a
        tension = str(r.get("张力点（稳定/暗流/将转变）", "") or r.get("张力点", "")).strip()
        rows.append({"对象": other, "关系": str(r.get("关系", "")).strip(),
                     "不稳定因素": tension})
    # 3) 列表式：- 甲 → 乙：关系
    for m in re.finditer(r"(?m)^\s*[-*]\s*(\S+?)\s*(?:→|->|—>)\s*(\S+?)\s*[：:]\s*(.+?)\s*$", card):
        a, b, rel = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
        if self_name not in (a, b):
            continue
        rows.append({"对象": b if a == self_name else a, "关系": rel, "不稳定因素": ""})
    # 去重（同对象保留首次出现）
    seen: set[str] = set()
    uniq: list[dict[str, str]] = []
    for r in rows:
        obj = r["对象"]
        if not obj or obj in seen:
            continue
        seen.add(obj)
        uniq.append(r)
    return uniq


def _find_rel_table(card: str) -> str:
    """从分卡里切出「对象|关系|不稳定因素」那张表（找不到返回空串）。

    六列矩阵表头 `对象A | 对象B | …` 也含"对象"，直接拿 "对象" 当 marker 会先命中它，
    导致真正的三列表被漏掉——这里按表头单元格精确匹配。
    """
    lines = card.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if not s.startswith("|"):
            continue
        cols = [c.strip() for c in s.strip("|").split("|")]
        if cols[:2] == ["对象", "关系"]:
            return "\n".join(lines[i:])
    return ""


# ---------------------------------------------------------------------------
# 章节工作台
# ---------------------------------------------------------------------------

@app.get("/api/books/{book}/chapters/{no}")
def chapter_workbench(book: str, no: int):
    result: dict[str, Any] = {"no": no}
    row = statemachine.get_row(book, no)
    result["row"] = row
    result["status"] = row["status"] if row else "awaiting_outline"
    try:
        result["outline"] = chapters.read_outline_contract(book, no)
    except Exception:
        result["outline"] = None
    try:
        result["prose"] = chapters.read_prose(book, no)
    except Exception:
        result["prose"] = None
    bid = books.book_id(book)
    with db.tx() as conn:
        mech = conn.execute(
            "SELECT * FROM review_records WHERE book_id=? AND chapter_no=? AND kind='mech' "
            "ORDER BY id DESC LIMIT 1", (bid, no)).fetchone()
        rev = conn.execute(
            "SELECT * FROM review_records WHERE book_id=? AND chapter_no=? AND kind='llm' "
            "ORDER BY id DESC LIMIT 1", (bid, no)).fetchone()
    result["mech"] = ({"verdict": mech["verdict"],
                       "issues": json.loads(mech["issues_json"] or "[]")} if mech else None)
    result["review"] = ({"verdict": rev["verdict"],
                         "scores": json.loads(rev["scores_json"] or "{}"),
                         "issues": json.loads(rev["issues_json"] or "[]")} if rev else None)
    result["cost"] = db.cost_summary(bid, no)
    result["golden3"] = no <= 3
    return result


@app.get("/api/books/{book}/chapters/{no}/outline/raw")
def get_outline_raw(book: str, no: int):
    p = chapters.outline_path(book, no)
    if not p.exists():
        raise _err(FileNotFoundError("细纲未生成"), 404)
    return {"content": p.read_text(encoding="utf-8")}


class OutlineEditPayload(BaseModel):
    fm: dict[str, Any] | None = None
    fm_raw: str | None = None
    body: str


@app.put("/api/books/{book}/chapters/{no}/outline")
def edit_outline_file(book: str, no: int, payload: OutlineEditPayload):
    st = statemachine.current_status(book, no)
    if st not in ("awaiting_outline", "outline_draft", "error"):
        raise _err(statemachine.StateError(
            f"第{no}章状态 {st}：细纲已锁定，改细纲请先走「改细纲重写」回到 outline_draft"))
    if payload.fm_raw is not None:
        import yaml

        try:
            fm = yaml.safe_load(payload.fm_raw) or {}
        except yaml.YAMLError as e:
            raise _err(ValueError(f"frontmatter YAML 解析失败: {e}"))
    else:
        fm = payload.fm
    try:
        chapters.write_outline_contract(book, no, fm, payload.body)
    except Exception as e:
        raise _err(e)
    return {"ok": True}


@app.post("/api/books/{book}/chapters/{no}/outline/generate")
def gen_outline(book: str, no: int):
    try:
        fm = pipeline.generate_outline(book, no)
        return {"ok": True, "outline": fm}
    except Exception as e:
        raise _err(e)


@app.post("/api/books/{book}/chapters/{no}/outline/confirm")
def confirm_outline(book: str, no: int):
    try:
        pipeline.confirm_outline(book, no)
    except Exception as e:
        raise _err(e)
    return {"ok": True, "status": statemachine.current_status(book, no)}


@app.get("/api/books/{book}/chapters/{no}/preview")
def assembly_preview(book: str, no: int):
    try:
        return pipeline.build_preview(book, no)
    except Exception as e:
        raise _err(e)


@app.post("/api/books/{book}/chapters/{no}/generate")
def generate_stream(book: str, no: int, payload: dict | None = None):
    import queue as q_mod
    import threading

    prompt_id = (payload or {}).get("prompt_id")
    if prompt_id not in (None, "W1", "W2", "W3", "W4", "W5"):
        raise _err(ValueError(f"非法场景专项: {prompt_id}"))
    q: "q_mod.Queue[tuple]" = q_mod.Queue()

    def cb(piece: str):
        q.put(("chunk", piece))

    def worker():
        try:
            r = pipeline.generate_prose(book, no, stream_cb=cb, prompt_id=prompt_id)
            q.put(("done", r))
        except Exception as e:
            q.put(("error", str(e)))

    threading.Thread(target=worker, daemon=True).start()

    def event_stream():
        while True:
            kind, payload = q.get()
            if kind == "done":
                yield f"event: done\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                break
            if kind == "error":
                yield f"event: error\ndata: {json.dumps(str(payload), ensure_ascii=False)}\n\n"
                break
            yield "data: " + json.dumps({"text": payload}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/books/{book}/chapters/{no}/mechcheck")
def mech_check(book: str, no: int):
    try:
        # 人工复核后重新验：human_review 因机检阻断而落点，用户定点修复/手改正文
        # 之后点「重跑机检」必须能回到机检，否则修干净的章也只能靠豁免放行。
        if statemachine.current_status(book, no) == "human_review":
            statemachine.transition(book, no, "recheck")
        res = pipeline.run_mech_check(book, no)
    except Exception as e:
        raise _err(e)
    body = res.to_dict()
    body["status"] = statemachine.current_status(book, no)
    return body


@app.get("/api/books/{book}/chapters/{no}/checkstatus")
def check_status(book: str, no: int):
    """机检结论 + 过期判定（规格05 §2.6）。"""
    try:
        st = db.get_check_status(book_id=books.book_id(book), chapter_no=no)
        prose = chapters.read_prose(book, no)
    except Exception as e:
        raise _err(e)
    current = mechcheck.fingerprint(prose["_body"])
    if st is None:
        return {"verdict": None, "checked_at": None, "checked_fingerprint": None,
                "current_fingerprint": current, "stale": False,
                "ineffective": [], "hits_count": {"block": 0, "warn": 0}}
    last = st["last_mech"] or {}
    ineffective = json.loads(last.get("ineffective_json") or "[]")
    issues = json.loads(last.get("issues_json") or "[]")
    checked_fp = st["checked_fingerprint"]
    stale = bool(checked_fp) and checked_fp != current
    verdict = "stale" if stale else st["checked_status"]
    return {
        "verdict": verdict,
        "checked_status": st["checked_status"],
        "checked_at": last.get("created_at"),
        "checked_fingerprint": checked_fp,
        "current_fingerprint": current,
        "stale": stale,
        "ineffective": ineffective,
        "hits_count": {
            "block": sum(1 for i in issues if i.get("level") == "block"),
            "warn": sum(1 for i in issues if i.get("level") == "warn"),
        },
        "imported": bool(st.get("imported")),
    }


@app.get("/api/books/{book}/chapters/{no}/deslop")
def deslop_check(book: str, no: int):
    """单独跑 AI 味检测（只报不改，调试/手动用）。"""
    from . import deslop

    try:
        body = chapters.read_prose(book, no)["_body"]
        res = deslop.analyze(body)
    except deslop.ConfigError as e:
        raise _err(e, code=500)
    except Exception as e:
        raise _err(e)
    return res.to_dict()


@app.post("/api/books/{book}/chapters/{no}/review")
def review(book: str, no: int):
    try:
        result = pipeline.run_llm_review(book, no)
    except Exception as e:
        raise _err(e)
    result["status"] = statemachine.current_status(book, no)
    return result


class FixPayload(BaseModel):
    issues: list[dict] | None = None  # 缺省用最近一次审校/机检的问题


@app.post("/api/books/{book}/chapters/{no}/fix")
def manual_fix(book: str, no: int, payload: FixPayload | None = None):
    issues = (payload.issues if payload and payload.issues else None)
    if not issues:
        bid = books.book_id(book)
        with db.tx() as conn:
            row = conn.execute(
                "SELECT issues_json FROM review_records WHERE book_id=? AND chapter_no=? "
                "AND kind IN ('llm','mech') ORDER BY id DESC LIMIT 1", (bid, no)).fetchone()
        issues = json.loads(row["issues_json"] or "[]") if row else []
    try:
        new_body = pipeline.run_targeted_fix(book, no, issues)
    except pipeline.FixOverreach as e:
        raise _err(e)
    except Exception as e:
        raise _err(e)
    return {"ok": True, "word_count": mechcheck.count_words(new_body)}


class ExemptPayload(BaseModel):
    reason: str


@app.post("/api/books/{book}/chapters/{no}/exempt")
def exempt(book: str, no: int, payload: ExemptPayload):
    try:
        pipeline.exempt(book, no, payload.reason)
    except Exception as e:
        raise _err(e)
    return {"ok": True, "status": statemachine.current_status(book, no)}


@app.post("/api/books/{book}/chapters/{no}/edit-outline")
def back_to_outline(book: str, no: int):
    try:
        pipeline.edit_outline(book, no)
    except Exception as e:
        raise _err(e)
    return {"ok": True, "status": statemachine.current_status(book, no)}


@app.post("/api/books/{book}/chapters/{no}/finalize")
def finalize(book: str, no: int):
    try:
        return pipeline.finalize(book, no)
    except Exception as e:
        raise _err(e)


class ProseEditPayload(BaseModel):
    """人工手改正文（规格03 §2.5 人工关三选项之一）。"""
    content: str


# 允许手改正文的状态：正文已生成且仍是 draft。finalized 必须拒绝（规格03 §2.5）。
PROSE_EDITABLE_STATES = ("mech_check", "llm_review", "human_review",
                         "pending_finalize", "error")


@app.put("/api/books/{book}/chapters/{no}/prose")
def edit_prose(book: str, no: int, payload: ProseEditPayload):
    """人工手改正文（规格03 §2.5 人工关三选项之一）。

    只改正文 body：重算 word_count、status 保持 draft、version 不变（同版本内人工修订），
    走 chapters.write_prose 原子落盘。**不自动推进状态**——改完必须由人重新跑机检/审校，
    人工关不许自动化。已定稿章一律拒绝（否则正文/快照/导出三方不一致，AGENTS.md 红线 3）：
    既看状态机（finalized），也看正文文件自身 status:final——记忆卡回写失败时
    定稿章会被 mark_error 置成 error 态，只查状态机会漏掉这种已定稿正文。
    """
    st = statemachine.current_status(book, no)
    try:
        prose = chapters.read_prose(book, no)
    except Exception as e:
        raise _err(e, 404)
    if st == "finalized" or prose.get("status") == "final":
        raise _err(statemachine.StateError(f"第{no}章已定稿，请先解锁"))
    if st not in PROSE_EDITABLE_STATES:
        raise _err(statemachine.StateError(
            f"第{no}章状态 {st}：正文尚不存在或不支持手改（需已生成正文且未定稿）"))
    fm = {k: v for k, v in prose.items() if not k.startswith("_")}
    body = payload.content
    fm["word_count"] = mechcheck.count_words(body)
    fm["status"] = "draft"
    try:
        chapters.write_prose(book, no, fm, body)
    except Exception as e:
        raise _err(e)
    # 手改正文后必须重新过机械检查（P1 审计：原实现不推进状态，
    # 于是改完就能直接定稿，M-01~M-11 被绕过）。仅 pending_finalize 需要回退——
    # 其余可手改状态本来就要重新跑机检，不额外搬动状态机。
    if st == "pending_finalize":
        statemachine.transition(book, no, "recheck")  # pending_finalize → mech_check
    return {"ok": True, "word_count": fm["word_count"], "status": statemachine.current_status(book, no)}


class ProseCorrectPayload(BaseModel):
    """定稿后字词级订正：逐条 old→new + 强制理由。"""
    edits: list[dict[str, str]]
    reason: str = ""


@app.post("/api/books/{book}/chapters/{no}/prose/correct")
def correct_prose_endpoint(book: str, no: int, payload: ProseCorrectPayload):
    """定稿章的字词级订正（错别字 / 术语旧称 / 事实矛盾）。

    与 `PUT /prose` 的区别：那是整篇覆盖、只对未定稿章开启；这里是受控替换，
    专给已定稿章用——逐条 old→new、old 必须唯一命中、总改动 ≤5%、强制留痕。
    没有它，定稿章里一处旧称就只能靠「解锁→重跑机检→重跑审校→重新定稿」，
    烧一轮 token 只为改三个字。
    """
    try:
        return pipeline.correct_prose(book, no, payload.edits, payload.reason)
    except Exception as e:
        raise _err(e)


@app.post("/api/books/{book}/chapters/{no}/unlock")
def unlock_chapter(book: str, no: int):
    """定稿返工：finalized → pending_finalize（规格01 §4）。

    定稿正文受快照保护，`/fix`、`/prose` 一律拒绝改写 finalized 章节。
    要返工必须先显式解锁，回到 pending_finalize 再走手改/修复/重写。
    没有这个入口，用户会被"请先解锁"的提示指向一个不存在的地方。

    同步把正文文件里的 status 从 final 改回 draft——否则文件自带的 final
    标记仍会让下游（/prose、导出 finalized_only）认定它已定稿。
    """
    try:
        st = statemachine.transition(book, no, "unlock")
    except Exception as e:
        raise _err(e)
    try:
        prose = chapters.read_prose(book, no)
        if prose.get("status") == "final":
            fm = {k: v for k, v in prose.items() if not k.startswith("_")}
            fm["status"] = "draft"
            chapters.write_prose(book, no, fm, prose["_body"])
    except Exception as e:  # 文件侧同步失败不阻断状态机，但要留痕
        logger.warning("第%s章解锁后同步正文 status 失败: %s", no, e)
    return {"ok": True, "status": statemachine.current_status(book, no)}


@app.get("/api/books/{book}/chapters/{no}/snapshots/{version}")
def get_snapshot(book: str, no: int, version: int):
    """快照正文回看（规格04 §2「版本列表可回看任意快照」）。

    /versions 只给元信息，正文快照存在 snapshots 表里；label/created_at 从
    pipeline.list_versions() 取同版本元信息，缺失时给空串（不回退猜测）。
    """
    content = pipeline.get_snapshot(book, no, version)
    if content is None:
        raise _err(FileNotFoundError(f"第{no}章 v{version} 快照不存在"), 404)
    meta = next((v for v in pipeline.list_versions(book, no)
                 if int(v.get("version") or 0) == version), {})
    return {"version": version, "label": meta.get("label", ""),
            "created_at": meta.get("created_at", ""), "content": content}


@app.get("/api/books/{book}/chapters/{no}/versions")
def versions(book: str, no: int):
    return pipeline.list_versions(book, no)


class RewritePayload(BaseModel):
    base_version: int | None = None


@app.post("/api/books/{book}/chapters/{no}/rewrite")
def rewrite(book: str, no: int, payload: RewritePayload | None = None):
    try:
        v = pipeline.human_rewrite(book, no, payload.base_version if payload else None)
    except Exception as e:
        raise _err(e)
    return {"ok": True, "new_version": v}


@app.post("/api/books/{book}/chapters/{no}/retry")
def retry(book: str, no: int):
    try:
        st = statemachine.transition(book, no, "retry")
    except Exception as e:
        raise _err(e)
    return {"ok": True, "status": st}


@app.post("/api/books/{book}/chapters/{no}/polish")
def polish(book: str, no: int):
    """V2 整章润色去AI味 + V3 复核（M3）。"""
    try:
        return pipeline.polish(book, no)
    except Exception as e:
        raise _err(e)


@app.post("/api/books/{book}/audit/{last_chapter}")
def manual_audit(book: str, last_chapter: int):
    """手动触发卷末审计 V6（定稿后自动触发之外的人工入口）。"""
    try:
        return pipeline.audit_volume(book, last_chapter)
    except Exception as e:
        raise _err(e)


@app.get("/api/books/{book}/audits")
def list_audits(book: str):
    """列出本书的卷末审计报告文件（books/<书名>/audits/*.md）。

    审计结论同时写进 review_records(kind='audit')，但报告正文是 Markdown 文件，
    这里按文件列出供 UI 展示与回看（规格03 §2.8）。
    """
    d = book_dir(book) / "audits"
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        try:
            content = p.read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"伏笔回收率（程序计算）：(\S+)", content)
        out.append({"file": p.name, "path": str(p),
                    "foreshadow_pay_rate": m.group(1) if m else "",
                    "content": content})
    return out


# ---------------------------------------------------------------------------
# 文风系统（M5：语料 → F1 特征卡 → F2 注入）
# ---------------------------------------------------------------------------

@app.get("/api/books/{book}/style")
def get_style(book: str):
    from . import style as st

    return {"corpus": st.read_corpus(book), "card": st.read_card(book),
            "enabled": st.enabled(book)}


class TextPayload(BaseModel):
    text: str


@app.put("/api/books/{book}/style/corpus")
def put_corpus(book: str, payload: TextPayload):
    from . import style as st

    st.write_corpus(book, payload.text)
    return {"ok": True, "chars": len(payload.text)}


@app.put("/api/books/{book}/style/card")
def put_card(book: str, payload: TextPayload):
    from . import style as st

    st.write_card(book, payload.text)
    return {"ok": True}


@app.post("/api/books/{book}/style/analyze")
def analyze_style(book: str):
    """F1：语料 → 特征卡（写盘但不自动启用，人改定后手动开启）。"""
    from . import style as st

    corpus = st.read_corpus(book)
    if len(corpus.strip()) < 500:
        raise _err(ValueError(f"语料太短（{len(corpus.strip())} 字），至少 500 字"))
    try:
        res, p, _t = pipeline._call(book, None, "style", "F1", {"语料样本": corpus[:60000]})
    except Exception as e:
        raise _err(e, 502)
    st.write_card(book, res.text)
    return {"ok": True, "card": res.text, "digest": p.digest}


class StyleToggle(BaseModel):
    enabled: bool


@app.put("/api/books/{book}/style/enabled")
def put_style_enabled(book: str, payload: StyleToggle):
    from . import style as st

    if payload.enabled and not st.read_card(book):
        raise _err(ValueError("特征卡不存在，先「分析语料」"))
    st.set_enabled(book, payload.enabled)
    return {"ok": True, "enabled": payload.enabled}


# ---------------------------------------------------------------------------
# 裁决队列
# ---------------------------------------------------------------------------

@app.get("/api/conflicts")
def list_conflicts(book: str | None = None):
    return conflicts.list_open(book)


class ResolvePayload(BaseModel):
    action: str
    manual_value: Any = None


@app.post("/api/conflicts/{conflict_id}/resolve")
def resolve_conflict(conflict_id: int, payload: ResolvePayload):
    try:
        item = conflicts.resolve(conflict_id, payload.action, payload.manual_value)
    except Exception as e:
        raise _err(e, 404)
    return {"ok": True, "item": item}


# ---------------------------------------------------------------------------
# 导出（规格04 §1：txt/md 合并、zip 打包）
# ---------------------------------------------------------------------------

@app.post("/api/books/{book}/export/txt")
def export_txt(book: str, finalized_only: bool = True):
    from urllib.parse import quote

    parts = [f"{books.read_meta(book).get('title', book)}\n"]
    for no in chapters.list_chapters(book):
        try:
            prose = chapters.read_prose(book, no)
        except Exception:
            continue
        if finalized_only and prose.get("status") != "final":
            continue
        parts.append(f"\n第{no}章\n\n{prose['_body'].strip()}\n")
    content = "\n".join(parts)
    return StreamingResponse(iter([content]), media_type="text/plain",
                             headers={"Content-Disposition":
                                      f"attachment; filename*=UTF-8''{quote(book)}.txt"})


@app.post("/api/books/{book}/export/md")
def export_md(book: str, finalized_only: bool = True):
    """导出 Markdown 合并版（规格04 §1「章节合并导出 txt/md」）。

    与 export_txt 同结构；章节标题用二级标题 `## 第N章`，正文原样（不转义、不改缩进）。
    """
    from urllib.parse import quote

    title = books.read_meta(book).get("title", book)
    parts = [f"# {title}\n"]
    for no in chapters.list_chapters(book):
        try:
            prose = chapters.read_prose(book, no)
        except Exception:
            continue
        if finalized_only and prose.get("status") != "final":
            continue
        parts.append(f"\n## 第{no}章\n\n{prose['_body'].strip()}\n")
    content = "\n".join(parts)
    return StreamingResponse(iter([content]), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition":
                                      f"attachment; filename*=UTF-8''{quote(book)}.md"})


@app.post("/api/books/{book}/export/zip")
def export_zip(book: str):
    from urllib.parse import quote

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in book_dir(book).rglob("*"):
            if p.is_file():
                zf.write(p, p.relative_to(book_dir(book).parent))
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/zip",
                             headers={"Content-Disposition":
                                      f"attachment; filename*=UTF-8''{quote(book)}.zip"})


# ---------------------------------------------------------------------------
# 静态 UI（规格04 §0：Vue3 构建产物 web/dist/，源码在 web/，禁止手改 dist）
# ---------------------------------------------------------------------------

_web_dist = project_root() / "web" / "dist"
if _web_dist.exists():
    app.mount("/", StaticFiles(directory=str(_web_dist), html=True), name="web")
else:

    @app.get("/")
    def _ui_not_built():
        """前端未构建时的兜底提示（构建：cd web && npm install && npm run build）。"""
        return {
            "hint": "前端未构建：在 web/ 下执行 npm install && npm run build，"
            "或开发模式 npm run dev（/api 已代理到本端口）",
        }
