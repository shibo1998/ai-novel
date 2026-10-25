"""FastAPI：全部路由 + SSE 流式正文 + 静态 UI。"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (assembler, bible, books, chapters, characters, conflicts, db,
               fts, llm, mechcheck, outline, pipeline, prompts, settings,
               statemachine)
from .paths import book_dir, project_root

app = FastAPI(title="AI 网文工作台")


@app.on_event("startup")
def _ensure_defaults() -> None:
    """启动即生成 data/settings.json（否则用户找不到可手改的配置文件）。"""
    settings.load()


def _err(e: Exception, code: int = 400) -> HTTPException:
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
            "SELECT chapter_no, SUM(cost_cents) c FROM generation_log "
            "WHERE book_id=? AND status='ok' GROUP BY chapter_no", (bid,)
        ).fetchall()
    cost_by_ch = {r["chapter_no"]: r["c"] for r in vol_rows}
    for c in chs:
        c["cost_cents"] = cost_by_ch.get(c["no"], 0)
        c["cost_over_yuan"] = c["cost_cents"] > 100  # 单章 >1 元标黄（验收线）
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
    rows = payload.pop("chapter_rows", [])
    rhythm = payload.pop("_rhythm", "")
    try:
        outline.write_outline(book, payload, rows, rhythm)
    except Exception as e:
        raise _err(e)
    return {"ok": True}


@app.get("/api/books/{book}/plot_points")
def get_pp(book: str):
    return outline.read_plot_points(book)


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
    """
    from .mdtable import parse_table

    text = payload.content
    # 章节总表（先解析，卷兜底要用行号范围）
    ccols, crows = parse_table(text, "本章目标")
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
    vcols, vrows = parse_table(text, "卷号")
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

    # 伏笔总表 → FO 编号
    fcols, frows = parse_table(text, "编号")
    fo_rows = []
    id_map: dict[str, str] = {}
    for i, r in enumerate(frows, 1):
        old_id = r.get("编号", "")
        new_id = f"FO-{i:03d}"
        id_map[old_id] = new_id
        fo_rows.append({"编号": new_id, "内容": r.get("内容", ""), "埋设章": r.get("埋设章", ""),
                        "推进章": r.get("推进章", ""), "预计回收章": r.get("预计回收章", ""),
                        "状态": "planted", "备注": r.get("备注", "")})
    m = re.search(r"(?ms)^##\s*爽点节奏总谱\s*\n(.*?)(?=^##|\Z)", text)
    rhythm = m.group(1).strip() if m else ""
    logline = books.read_meta(book).get("_body", "（待填）").strip()
    outline.write_outline(book, {"logline": logline, "ending_direction": "", "volumes": volumes},
                          rows=norm_rows, rhythm=rhythm)
    if fo_rows:
        outline.write_plot_points(book, fo_rows)
    return {"ok": True, "volumes": len(volumes), "chapter_rows": len(norm_rows),
            "foreshadows": len(fo_rows), "id_map": id_map}


@app.post("/api/books/{book}/adopt/characters")
def adopt_characters(book: str, payload: AdoptPayload):
    """解析 G3/G4 输出（===角色:<名>=== 分卡）→ 建角色档案。"""
    cards = re.split(r"(?m)^===\s*角色[:：]\s*(.+?)\s*===\s*$", payload.content)
    created: list[str] = []
    # cards: [前置, 名1, 卡1, 名2, 卡2, ...]
    it = iter(cards[1:])
    for name, card in zip(it, it):
        name = name.strip()
        role = "龙套"
        m = re.search(r"主角|女主|反派|重要配角", card[:60])
        if m:
            role = m.group(0)
        if name in characters.list_characters(book):
            continue
        body = card.strip()
        # 关系网络矩阵（G4）合并到各角色"关系网络"节
        characters.create(book, name, role=role)
        d = characters.read(book, name)
        fm = {k: v for k, v in d.items() if not k.startswith("_")}
        characters.write(book, fm, body + "\n")
        created.append(name)
    return {"ok": True, "created": created}


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
        hits = pipeline.run_mech_check(book, no)
    except Exception as e:
        raise _err(e)
    return {"hits": [h.to_dict() for h in hits],
            "status": statemachine.current_status(book, no)}


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
