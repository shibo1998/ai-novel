"""按章状态快照（规格05 F-05）：把「每章发生了什么」变成可机检、可回滚的结构化事实。

三层：
1. `chapter_state` 表 —— 每章每人一行状态增量（V4 的 `state_delta` 落库）
2. `STATE.md` —— 人读派生文件，每次回写后整篇重建，禁止手改
3. 装配消费 —— `char_cards` / `recaps` 槽优先读 STATE.md（结构化、短）

为什么需要它：记忆卡是叙述性摘要，无法回答"林寒第 12 章断了两根肋骨，第 15 章
能不能背人跑三里地"这种问题——只有 LLM 看得见，规则层完全瞎。有了结构化快照，
M-13 战力倒退 / M-14 死者复现 / M-15 位置跳跃 才能真拦。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterable

from . import atomic, books, chapters, db, outline, summaries
from .paths import book_dir

logger = logging.getLogger(__name__)

STATE_FILE = "STATE.md"
STATE_MAX_BYTES = 12 * 1024

CORE_ROLES = ("主角", "女主", "反派", "重要配角")


# ---------------------------------------------------------------------------
# DB 读写
# ---------------------------------------------------------------------------

_STATE_FIELDS = ("location", "physical", "mental", "power_level", "arc_stage")


def upsert_entries(book: str, chapter_no: int, entries: Iterable[dict[str, Any]],
                   fingerprint: str = "") -> int:
    """写入本章状态增量（UPSERT：同一章重跑 V4 不产生重复行）。

    只写结构里认识的字段；character 为空或非字符串的条目直接丢弃（脏数据不入库）。
    """
    book_id = books.book_id(book)
    n = 0
    with db.tx() as conn:
        for e in entries:
            if not isinstance(e, dict):
                continue
            name = (e.get("character") or e.get("name") or "").strip()
            if not name:
                continue
            knowledge = e.get("knowledge")
            goals = e.get("goals")
            conn.execute(
                """INSERT INTO chapter_state
                   (book_id, chapter_no, character, location, physical, mental,
                    power_level, arc_stage, knowledge_json, goals_json, delta_json,
                    fingerprint, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(book_id, chapter_no, character) DO UPDATE SET
                     location=excluded.location, physical=excluded.physical,
                     mental=excluded.mental, power_level=excluded.power_level,
                     arc_stage=excluded.arc_stage,
                     knowledge_json=excluded.knowledge_json,
                     goals_json=excluded.goals_json, delta_json=excluded.delta_json,
                     fingerprint=excluded.fingerprint, created_at=excluded.created_at""",
                (book_id, chapter_no, name,
                 _s(e.get("location")), _s(e.get("physical")), _s(e.get("mental")),
                 _s(e.get("power_level")), _s(e.get("arc_stage")),
                 json.dumps(knowledge, ensure_ascii=False) if knowledge is not None else None,
                 json.dumps(goals, ensure_ascii=False) if goals is not None else None,
                 json.dumps(e, ensure_ascii=False), fingerprint, db.now()),
            )
            n += 1
    return n


def _s(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return "、".join(str(x) for x in v if str(x).strip())
    return str(v).strip()


def clear_chapter(book: str, chapter_no: int) -> None:
    with db.tx() as conn:
        conn.execute("DELETE FROM chapter_state WHERE book_id=? AND chapter_no=?",
                     (books.book_id(book), chapter_no))


def chapter_rows(book: str, chapter_no: int) -> list[dict[str, Any]]:
    book_id = books.book_id(book)
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT * FROM chapter_state WHERE book_id=? AND chapter_no=? ORDER BY character",
            (book_id, chapter_no)).fetchall()
    return [_row_to_dict(r) for r in rows]


def latest_before(book: str, character: str, chapter_no: int) -> dict[str, Any] | None:
    """该角色在 < chapter_no 的最近一条状态（M-13 的比较基准）。"""
    book_id = books.book_id(book)
    with db.tx() as conn:
        row = conn.execute(
            "SELECT * FROM chapter_state WHERE book_id=? AND character=? AND chapter_no<? "
            "ORDER BY chapter_no DESC LIMIT 1",
            (book_id, character, chapter_no)).fetchone()
    return _row_to_dict(row) if row else None


def latest_map(book: str, chapter_no: int | None = None) -> dict[str, dict[str, Any]]:
    """每个角色截至 chapter_no（含）的最近状态。"""
    book_id = books.book_id(book)
    sql = ("SELECT * FROM chapter_state WHERE book_id=?"
           + (" AND chapter_no<=?" if chapter_no is not None else "")
           + " ORDER BY chapter_no DESC")
    args: tuple = (book_id,) if chapter_no is None else (book_id, chapter_no)
    with db.tx() as conn:
        rows = conn.execute(sql, args).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r["character"] not in out:
            out[r["character"]] = _row_to_dict(r)
    return out


def _row_to_dict(row) -> dict[str, Any]:
    d = dict(row)
    for k in ("knowledge_json", "goals_json", "delta_json"):
        raw = d.pop(k, None)
        key = k.replace("_json", "")
        try:
            d[key] = json.loads(raw) if raw else None
        except (TypeError, json.JSONDecodeError):
            d[key] = None
    return d


def count_rows(book: str) -> int:
    with db.tx() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM chapter_state WHERE book_id=?",
                           (books.book_id(book),)).fetchone()
    return int(row["c"]) if row else 0


# ---------------------------------------------------------------------------
# STATE.md 派生
# ---------------------------------------------------------------------------

def state_path(book: str):
    return book_dir(book) / STATE_FILE


def read_state_md(book: str) -> str:
    p = state_path(book)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def rebuild_state_md(book: str, upto_chapter: int | None = None) -> str:
    """整篇重建 STATE.md（派生文件，不手改）。

    章节多时只取最近 200 章 + 全部未回收伏笔（规格05 §14 风险对策）。
    """
    nos = chapters.list_chapters(book)
    if upto_chapter is not None:
        nos = [n for n in nos if n <= upto_chapter]
    if not nos:
        return ""
    last_no = max(nos)
    recent = nos[-200:]
    latest = latest_map(book, last_no)

    lines: list[str] = ["<!-- generated by V4; do not edit -->",
                        f"# 当前状态（截至第 {last_no} 章）", ""]

    # 1. 当前位置与时间
    locs = [f"{name}@{v.get('location')}" for name, v in latest.items() if v.get("location")]
    time_hint = _last_time_elapsed(book, recent)
    lines += ["## 1. 当前位置与时间",
              ("；".join(locs) if locs else "（无结构化位置记录）")
              + (f"；时间流逝：{time_hint}" if time_hint else ""), ""]

    # 2. 核心角色状态
    lines += ["## 2. 核心角色状态", "| 角色 | 位置 | 身体 | 心理 | 战力 | 弧光 |",
              "|---|---|---|---|---|---|"]
    core = [n for n in _core_characters(book) if n in latest]
    for name in core:
        v = latest[name]
        lines.append("| {n} | {loc} | {ph} | {me} | {pw} | {arc} |".format(
            n=name, loc=v.get("location") or "—", ph=v.get("physical") or "—",
            me=v.get("mental") or "—", pw=v.get("power_level") or "—",
            arc=v.get("arc_stage") or "—"))
    if not core:
        lines.append("| （无） | — | — | — | — | — |")
    lines.append("")

    # 3. 关系张力（未建结构化关系变更源，如实说明而不是编造）
    lines += ["## 3. 关系张力", "（无结构化关系变更记录；关系网由 G4 维护）", ""]

    # 4. 未回收伏笔
    try:
        rows = [r for r in outline.read_plot_points(book) if r.get("状态") != "paid"]
    except Exception:
        rows = []
    lines += ["## 4. 未回收伏笔（≤15 条）"]
    lines += [f"- {r.get('编号')}[{r.get('状态')}] {r.get('内容','')}" for r in rows[:15]] or ["（无）"]
    lines.append("")

    # 5. 近三章速记
    lines += ["## 5. 近三章速记"]
    recents = _recent_summaries(book, recent, 3)
    lines += [f"- 第{n}章：{s}" for n, s in recents] or ["（无）"]
    lines.append("")

    # 6. 下一章承诺
    lines += ["## 6. 下一章承诺"]
    try:
        contract = chapters.read_outline_contract(book, last_no + 1)
        hook = contract.get("hook") or {}
        lines.append(str(hook.get("content") or "（细纲未写钩子）"))
    except Exception:
        lines.append("（下一章细纲未建）")
    lines.append("")

    # 7. 连贯性风险
    lines += ["## 7. 连贯性风险"]
    risks = _coherence_risks(book, recent)
    lines += risks or ["（近三章无未解阻断）"]
    lines.append("")

    text = _enforce_size("\n".join(lines), book)
    atomic.write_text(state_path(book), text)
    return text


def _core_characters(book: str) -> list[str]:
    from . import characters

    out = []
    for name in characters.list_characters(book):
        try:
            data = characters.read(book, name)
        except Exception:
            continue
        if data.get("role") in CORE_ROLES:
            out.append(name)
    return out


def _last_time_elapsed(book: str, nos: list[int]) -> str:
    for no in reversed(nos):
        try:
            fm = chapters.read_memory(book, no)
        except Exception:
            continue
        t = fm.get("time_elapsed")
        if t:
            return str(t)
    return ""


def _recent_summaries(book: str, nos: list[int], limit: int) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for no in reversed(nos[-8:]):
        text = ""
        try:
            text = str(chapters.read_memory(book, no).get("event_summary") or "")
        except Exception:
            s = summaries.load(book)
            for r in s.get("recent", []):
                if r.get("no") == no:
                    text = r.get("summary", "")
        if text:
            out.append((no, text[:60]))
        if len(out) >= limit:
            break
    return list(reversed(out))


def _coherence_risks(book: str, nos: list[int]) -> list[str]:
    out: list[str] = []
    book_id = books.book_id(book)
    with db.tx() as conn:
        for no in nos[-3:]:
            row = conn.execute(
                "SELECT issues_json, verdict FROM review_records WHERE book_id=? "
                "AND chapter_no=? AND kind='mech' ORDER BY id DESC LIMIT 1",
                (book_id, no)).fetchone()
            if not row:
                continue
            try:
                issues = json.loads(row["issues_json"] or "[]")
            except json.JSONDecodeError:
                issues = []
            blocks = [i for i in issues if i.get("level") == "block"]
            if blocks:
                kinds = "、".join(sorted({str(i.get("rule")) for i in blocks}))
                out.append(f"- 第{no}章：未解阻断 {len(blocks)} 条（{kinds}）")
    return out


def _enforce_size(text: str, book: str) -> str:
    """超 12KB 按节截断：速记先丢，其次伏笔、关系（规格05 T-24）。"""
    if len(text.encode("utf-8")) <= STATE_MAX_BYTES:
        return text
    text = re.sub(r"(?ms)(## 5\. 近三章速记\n).*?(?=## 6\.)", r"\1（已省略：STATE.md 超长）\n\n", text)
    if len(text.encode("utf-8")) <= STATE_MAX_BYTES:
        return text
    text = re.sub(r"(?ms)(## 4\. 未回收伏笔（≤15 条）\n).*?(?=## 5\.)", r"\1（已省略：STATE.md 超长）\n\n", text)
    if len(text.encode("utf-8")) <= STATE_MAX_BYTES:
        return text
    text = text.encode("utf-8")[:STATE_MAX_BYTES].decode("utf-8", "ignore")
    logger.warning("第%s章 STATE.md 超长，已硬截断", book)
    return text


# ---------------------------------------------------------------------------
# 装配消费
# ---------------------------------------------------------------------------

def state_rows_text(book: str, chapter_no: int, names: list[str]) -> str:
    """本章出场角色的结构化状态行（char_cards 槽优先取它，省 token 且更准）。"""
    latest = latest_map(book, chapter_no)
    rows = []
    for name in names:
        v = latest.get(name)
        if not v:
            continue
        parts = [f"{k}={v.get(k)}" for k in ("location", "physical", "mental",
                                             "power_level", "arc_stage")
                 if v.get(k)]
        if parts:
            rows.append(f"- {name}：{'；'.join(parts)}")
    return "\n".join(rows)


def recaps_text(book: str, chapter_no: int) -> str:
    """STATE.md 第 5 节（近三章速记）供 recaps 槽。"""
    text = read_state_md(book)
    if not text:
        return ""
    m = re.search(r"(?ms)^## 5\. 近三章速记\s*\n(.*?)(?=^## |\Z)", text)
    if not m:
        return ""
    body = m.group(1).strip()
    if not body or body.startswith("（"):
        return ""
    return body


# ---------------------------------------------------------------------------
# 机检上下文（M-13 ~ M-15）
# ---------------------------------------------------------------------------

def mech_context(book: str, chapter_no: int) -> dict[str, Any]:
    """给 mechcheck 的只读上下文。没有快照时 `has_state=False`（不装作查过）。"""
    rows = chapter_rows(book, chapter_no)
    prev: dict[str, dict[str, Any]] = {}
    for r in rows:
        p = latest_before(book, r["character"], chapter_no)
        if p:
            prev[r["character"]] = p
    from . import bible, characters

    char_status: dict[str, str] = {}
    char_death: dict[str, int] = {}
    for name in characters.list_characters(book):
        try:
            data = characters.read(book, name)
        except Exception:
            continue
        status = data.get("status") or {}
        char_status[name] = _alive_label(status)
        if char_status[name] == "死亡":
            # 死亡是在哪一章被记下的：只有"死亡章 < 本章"才谈得上死者复现
            try:
                char_death[name] = int(status.get("updated_chapter") or 0)
            except (TypeError, ValueError):
                continue
    try:
        realm = bible.realm_order(book)
    except Exception:
        realm = []
    return {"has_state": bool(rows), "rows": rows, "prev": prev,
            "char_status": char_status, "char_death": char_death,
            "realm_order": realm}


def _alive_label(status: dict[str, Any]) -> str:
    alive = status.get("alive")
    if alive is False:
        return "死亡"
    if isinstance(alive, str) and alive in ("死亡", "已死", "dead"):
        return "死亡"
    if status.get("状态") in ("死亡", "已死"):
        return "死亡"
    return "存活"
