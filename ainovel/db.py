"""SQLite：表结构、连接、章节行 CRUD、rebuild（规格01 §3/§5.4）。

SQLite = 可随时重建的索引与历史；文件才是单一真相源。
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from . import frontmatter as FM
from .paths import attached_books, book_dir, books_dir, data_dir, validate_book_name

_LOCK = threading.RLock()
_CONN: sqlite3.Connection | None = None


def db_path() -> Path:
    return data_dir() / "ainovel.db"


def get_conn() -> sqlite3.Connection:
    global _CONN
    with _LOCK:
        if _CONN is None:
            data_dir().mkdir(parents=True, exist_ok=True)
            _CONN = sqlite3.connect(str(db_path()), check_same_thread=False)
            _CONN.row_factory = sqlite3.Row
            _CONN.execute("PRAGMA journal_mode=WAL")
            _CONN.execute("PRAGMA foreign_keys=ON")
            init_schema(_CONN)
        return _CONN


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = get_conn()
    with _LOCK:
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    detached INTEGER NOT NULL DEFAULT 0  -- 摘除保留主键及不可恢复的运行历史
);
CREATE TABLE IF NOT EXISTS chapters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    no INTEGER NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'awaiting_outline',
    prev_status TEXT,                -- error 恢复用（规格01 §4：error──重试──▶原状态）
    fix_rounds INTEGER NOT NULL DEFAULT 0,
    current_version INTEGER NOT NULL DEFAULT 1,
    state_revision INTEGER NOT NULL DEFAULT 0,
    checked_fingerprint TEXT,        -- 机检结论对应的正文指纹（规格05 P-3）
    checked_status TEXT,             -- pass | blocked | not_effective | stale
    imported INTEGER NOT NULL DEFAULT 0,  -- 由 txt 导入（无细纲、无记忆卡）
    updated_at TEXT NOT NULL,
    UNIQUE(book_id, no)
);
CREATE TABLE IF NOT EXISTS generation_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_no INTEGER,
    stage TEXT NOT NULL,
    prompt_id TEXT,
    prompt_version TEXT,
    prompt_digest TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_cents INTEGER,
    cost_millicents INTEGER,         -- 千分之一分（毫分）；分记账会把单章成本抹成 0
    status TEXT NOT NULL CHECK(status IN ('ok','error')),
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_no INTEGER NOT NULL,
    version INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('mech','llm','polish','audit')),
    scores_json TEXT,
    issues_json TEXT,
    verdict TEXT,
    fingerprint TEXT,                -- 本条结论判定的是哪一版正文
    ineffective_json TEXT,           -- 未生效规则清单（规格05 P-2）
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chapter_state (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_no INTEGER NOT NULL,
    character TEXT NOT NULL,
    location TEXT,
    physical TEXT,                   -- 伤势/疲劳/衣着
    mental TEXT,                     -- 情绪/动机
    power_level TEXT,                -- 战力口径，须命中 Bible 境界序列
    arc_stage TEXT,                  -- 弧光阶段
    knowledge_json TEXT,             -- 该角色本章结束"已知"的事实
    goals_json TEXT,                 -- 当前目标
    delta_json TEXT,                 -- 原始增量（回滚基线）
    fingerprint TEXT,                -- 对应正文指纹
    created_at TEXT NOT NULL,
    UNIQUE(book_id, chapter_no, character)
);
CREATE INDEX IF NOT EXISTS idx_chapter_state_lookup
    ON chapter_state(book_id, character, chapter_no);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    chapter_no INTEGER NOT NULL,
    version INTEGER NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(book_id, chapter_no, version)
);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    with _LOCK:
        conn.executescript(SCHEMA)
        _ensure_column(conn, "books", "detached", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "generation_log", "cost_millicents", "INTEGER")
        # 规格05 §11.1：老库轻量迁移（CREATE TABLE IF NOT EXISTS 不会改已有表）
        _ensure_column(conn, "chapters", "checked_fingerprint", "TEXT")
        _ensure_column(conn, "chapters", "checked_status", "TEXT")
        _ensure_column(conn, "chapters", "imported", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "chapters", "state_revision", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "review_records", "fingerprint", "TEXT")
        _ensure_column(conn, "review_records", "ineffective_json", "TEXT")
        conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    """轻量迁移：老库缺列就补上（CREATE TABLE IF NOT EXISTS 不会改已有表）。"""
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if col not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


# ---------------------------------------------------------------------------
# generation_log / review_records / snapshots 便捷写入
# ---------------------------------------------------------------------------

def log_generation(
    *,
    book_id: int,
    chapter_no: int | None,
    stage: str,
    prompt_id: str | None = None,
    prompt_version: str | None = None,
    prompt_digest: str | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_millicents: int = 0,
    status: str = "ok",
    error: str | None = None,
) -> int:
    with tx() as conn:
        cur = conn.execute(
            """INSERT INTO generation_log
               (book_id, chapter_no, stage, prompt_id, prompt_version, prompt_digest,
                model, input_tokens, output_tokens, cost_cents, cost_millicents,
                status, error, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (book_id, chapter_no, stage, prompt_id, prompt_version, prompt_digest,
             model, input_tokens, output_tokens,
             round(cost_millicents / 1000), cost_millicents,
             status, error, now()),
        )
        return cur.lastrowid


def add_review(
    *,
    book_id: int,
    chapter_no: int,
    version: int,
    kind: str,
    scores: dict | None = None,
    issues: list | None = None,
    verdict: str | None = None,
    fingerprint: str | None = None,
    ineffective: list | None = None,
) -> int:
    import json

    with tx() as conn:
        cur = conn.execute(
            """INSERT INTO review_records
               (book_id, chapter_no, version, kind, scores_json, issues_json, verdict,
                fingerprint, ineffective_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (book_id, chapter_no, version, kind,
             json.dumps(scores, ensure_ascii=False) if scores is not None else None,
             json.dumps(issues, ensure_ascii=False) if issues is not None else None,
             verdict, fingerprint,
             json.dumps(ineffective, ensure_ascii=False) if ineffective is not None else None,
             now()),
        )
        return cur.lastrowid


def set_check_status(*, book_id: int, chapter_no: int, fingerprint: str | None,
                     status: str) -> None:
    """记录机检结论与其判定的正文指纹（规格05 §2.6）。"""
    with tx() as conn:
        conn.execute(
            "UPDATE chapters SET checked_fingerprint=?, checked_status=?, updated_at=? "
            "WHERE book_id=? AND no=?",
            (fingerprint, status, now(), book_id, chapter_no))


def mark_check_stale(*, book_id: int, chapter_no: int, current_fingerprint: str) -> bool:
    """正文改动后调用：指纹与机检结论不一致 → 结论置 stale。

    返回是否真的置为 stale（无历史结论时不标记——没查过就不存在"过期"）。
    """
    with tx() as conn:
        row = conn.execute(
            "SELECT checked_fingerprint, checked_status FROM chapters "
            "WHERE book_id=? AND no=?", (book_id, chapter_no)).fetchone()
        if row is None or not row["checked_fingerprint"]:
            return False
        if row["checked_fingerprint"] == current_fingerprint:
            return False
        if row["checked_status"] == "stale":
            return True
        conn.execute(
            "UPDATE chapters SET checked_status='stale', updated_at=? WHERE book_id=? AND no=?",
            (now(), book_id, chapter_no))
        return True


def get_check_status(*, book_id: int, chapter_no: int) -> dict | None:
    with tx() as conn:
        row = conn.execute(
            "SELECT checked_fingerprint, checked_status, imported FROM chapters "
            "WHERE book_id=? AND no=?", (book_id, chapter_no)).fetchone()
        if row is None:
            return None
        last = conn.execute(
            "SELECT verdict, issues_json, ineffective_json, fingerprint, created_at "
            "FROM review_records WHERE book_id=? AND chapter_no=? AND kind='mech' "
            "ORDER BY id DESC LIMIT 1", (book_id, chapter_no)).fetchone()
    return {"checked_fingerprint": row["checked_fingerprint"],
            "checked_status": row["checked_status"],
            "imported": row["imported"],
            "last_mech": dict(last) if last else None}


def add_snapshot(*, book_id: int, chapter_no: int, version: int, label: str, content: str) -> int:
    with tx() as conn:
        # 快照是定稿历史（只增不改）：同版本重放时保留先到的快照，绝不覆盖
        conn.execute(
            """INSERT INTO snapshots
               (book_id, chapter_no, version, label, content, created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(book_id, chapter_no, version) DO NOTHING""",
            (book_id, chapter_no, version, label, content, now()),
        )
        cur = conn.execute(
            "SELECT id FROM snapshots WHERE book_id=? AND chapter_no=? AND version=?",
            (book_id, chapter_no, version),
        )
        return cur.fetchone()["id"]


def backfill_cost_millicents() -> int:
    """老行按 model 单价重算毫分——历史 cost_cents 已被逐次进位抹平，反推只能靠 token 数。

    只在单价表里有该模型时回填；查不到单价的保持 NULL，交由 COALESCE 兜底，
    避免"回填成 0"把「不知道怎么算」永久固化成「确认没花钱」。
    """
    from . import settings as _settings

    prices = _settings.load().get("prices", {})
    n = 0
    with tx() as conn:
        rows = conn.execute(
            "SELECT id, model, input_tokens, output_tokens FROM generation_log "
            "WHERE cost_millicents IS NULL AND model IS NOT NULL"
        ).fetchall()
        for r in rows:
            p = prices.get(r["model"])
            if not p:
                continue
            mc = round(
                ((r["input_tokens"] or 0) * p.get("input_per_mtok_cents", 0)
                 + (r["output_tokens"] or 0) * p.get("output_per_mtok_cents", 0)) / 1000
            )
            conn.execute("UPDATE generation_log SET cost_millicents=? WHERE id=?", (mc, r["id"]))
            n += 1
    return n


def cost_summary(book_id: int, chapter_no: int | None = None) -> dict[str, Any]:
    """成本汇总（规格03 §4）：本章 / 全书。调用方自行聚卷。

    口径统一用 cost_millicents（毫分）—— 按分汇总时单章 0.56 分会进位成 0，
    几十章加起来仍是 0，用户看到的是「这本书没花钱」。老行没有该列就用
    cost_cents*1000 回填，保证迁移前后口径连续。
    """
    expr = "COALESCE(SUM(COALESCE(cost_millicents, cost_cents * 1000)), 0)"
    with tx() as conn:
        if chapter_no is None:
            row = conn.execute(
                f"SELECT {expr} c, COUNT(*) n FROM generation_log WHERE book_id=? AND status='ok'",
                (book_id,),
            ).fetchone()
        else:
            row = conn.execute(
                f"SELECT {expr} c, COUNT(*) n FROM generation_log "
                "WHERE book_id=? AND chapter_no=? AND status='ok'",
                (book_id, chapter_no),
            ).fetchone()
    return {
        "cost_millicents": row["c"],
        "cost_cents": round(row["c"] / 1000),
        "calls": row["n"],
    }


# ---------------------------------------------------------------------------
# rebuild：从文件全量重建 books / chapters（规格01 §5.4）
# ---------------------------------------------------------------------------

def rebuild() -> dict[str, Any]:
    """从活动内置/外挂文件重建索引（规格01 §5.4、规格05 F-07/F-08/C-02）。

    摘除或目录丢失只停用主记录，不级联删除 generation_log/review_records/snapshots。
    路径登记表是活动外挂的真相源；chapter_state 从记忆卡重建，导入标志从正文恢复。
    """
    from . import fts

    report: dict[str, Any] = {"books": 0, "chapters": 0, "fts_reindexed": 0, "dropped": []}
    external = attached_books()
    directories: dict[str, Path] = {}
    if books_dir().exists():
        for p in books_dir().iterdir():
            if p.is_dir() and (p / "meta.md").is_file():
                try:
                    validate_book_name(p.name)
                except ValueError:
                    continue
                directories[p.name] = p
    # 未补骨架的外挂书也可以只有正文或 txt，不能以 meta.md 作为入库前提。
    directories.update({name: Path(path) for name, path in external.items()
                        if Path(path).is_dir()})
    with tx() as conn:
        for row in conn.execute("SELECT id, name, path, detached FROM books").fetchall():
            name = row["name"]
            candidate = directories.get(name)
            if candidate is not None and Path(row["path"]).resolve() != candidate.resolve():
                raise ValueError(f"书名对应的路径与历史不一致，拒绝复用运行历史: {name}")
            # 兼容旧版 detach：外部路径已不在活动登记表时，补记停用而非级联删除。
            if candidate is None:
                conn.execute("UPDATE books SET detached=1 WHERE id=?", (row["id"],))
                conn.execute("DELETE FROM chapters WHERE book_id=?", (row["id"],))
                conn.execute("DELETE FROM chapter_state WHERE book_id=?", (row["id"],))
                if not row["detached"]:
                    report["dropped"].append(name)
        for name, bdir in sorted(directories.items()):
            report["chapters"] += _rebuild_book(conn, name, bdir)
            report["books"] += 1
    fts.rebuild_index()
    report["fts_reindexed"] = 1
    return report


def rebuild_book(name: str) -> None:
    """挂载后立即同步该书的章节、状态与 FTS（规格05 F-07），不改其他书。"""
    from . import fts

    bdir = book_dir(name)
    with tx() as conn:
        _rebuild_book(conn, name, bdir)
        row = conn.execute("SELECT id FROM books WHERE name=?", (name,)).fetchone()
    fts.rebuild_index(book_id=row["id"])


def _rebuild_book(conn: sqlite3.Connection, name: str, bdir: Path) -> int:
    row = conn.execute("SELECT id, path FROM books WHERE name=?", (name,)).fetchone()
    if row and Path(row["path"]).resolve() != bdir.resolve():
        raise ValueError(f"书名对应的路径与历史不一致，拒绝复用运行历史: {name}")
    conn.execute(
        "INSERT INTO books (name, path, created_at) VALUES (?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET detached=0",
        (name, str(bdir), now()),
    )
    book_id = conn.execute("SELECT id FROM books WHERE name=?", (name,)).fetchone()["id"]
    files = _chapter_files(bdir)
    conn.execute("DELETE FROM chapter_state WHERE book_id=?", (book_id,))
    for chapter_no, title, status, version in _scan_chapters(bdir, files):
        paths = files[chapter_no]
        body_fm = _read_fm(paths.get("正文"))
        imported = int(body_fm.get("imported") is True)
        conn.execute(
            """INSERT INTO chapters (book_id, no, title, status, current_version, imported, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(book_id, no) DO UPDATE SET
                 title=excluded.title, current_version=excluded.current_version,
                 status=excluded.status, imported=excluded.imported, prev_status=NULL,
                 state_revision=chapters.state_revision+1, updated_at=excluded.updated_at""",
            (book_id, chapter_no, title, status, version, imported, now()),
        )
        _restore_chapter_state(conn, book_id, chapter_no, paths.get("记忆卡"))
    # 不使用超长 IN 参数列表，千章以上的书也能重建；运行历史没有章节级联关系。
    for row in conn.execute("SELECT no FROM chapters WHERE book_id=?", (book_id,)).fetchall():
        if row["no"] not in files:
            conn.execute("DELETE FROM chapters WHERE book_id=? AND no=?", (book_id, row["no"]))
    return len(files)


def _chapter_files(bdir: Path) -> dict[int, dict[str, Path]]:
    """只扫描数字_细纲/正文/记忆卡.md（规格01 §1），排除半成品及备份。"""
    found: dict[int, dict[str, Path]] = {}
    for p in sorted((bdir / "chapters").glob("*.md")):
        m = re.fullmatch(r"([0-9]+)_(细纲|正文|记忆卡)\.md", p.name)
        if m and p.is_file():
            found.setdefault(int(m[1]), {})[m[2]] = p
    return found


def _read_fm(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        return FM.read_file(path)[0]
    except (OSError, ValueError, FM.FrontmatterError):
        return {}


def _scan_chapters(bdir: Path, files: dict[int, dict[str, Path]] | None = None
                   ) -> Iterator[tuple[int, str, str, int]]:
    files = _chapter_files(bdir) if files is None else files
    for chapter_no, paths in sorted(files.items()):
        outline_fm = _read_fm(paths.get("细纲"))
        body_fm = _read_fm(paths.get("正文"))
        title = str(outline_fm.get("title") or body_fm.get("title") or "")
        try:
            version = int(body_fm.get("version") or 1)
        except (TypeError, ValueError):
            version = 1
        yield chapter_no, title, _derive_status(bdir, chapter_no, paths), version


def _derive_status(bdir: Path, chapter_no: int, paths: dict[str, Path] | None = None) -> str:
    """从文件证据恢复状态（规格01 §4、规格05 F-08）；导入章不要求伪造细纲。"""
    paths = _chapter_files(bdir).get(chapter_no, {}) if paths is None else paths
    body_fm = _read_fm(paths.get("正文"))
    if body_fm.get("status") == "final":
        return "finalized"
    if body_fm.get("imported") is True:
        return "pending_finalize"
    if "细纲" not in paths:
        return "human_review" if "正文" in paths else "awaiting_outline"
    if not _read_fm(paths["细纲"]).get("confirmed"):
        return "outline_draft"
    if "正文" not in paths:
        return "outline_locked"
    return "mech_check" if body_fm else "human_review"


def _restore_chapter_state(conn: sqlite3.Connection, book_id: int, chapter_no: int,
                           memory_path: Path | None) -> None:
    """从有效记忆卡恢复结构化状态（规格01 §2.9/§5.4、规格05 F-05），不重写档案。"""
    from . import chapters

    if memory_path is None:
        return
    try:
        fm, _ = FM.read_file(memory_path)
        chapters.validate_memory(fm)
        if fm["chapter_no"] != chapter_no:
            raise ValueError("记忆卡章号与文件名不一致")
    except (OSError, ValueError, FM.FrontmatterError) as e:
        logging.getLogger(__name__).warning("跳过无效记忆卡 %s: %s", memory_path, e)
        return

    def text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, (list, tuple)):
            return "、".join(str(v) for v in value if str(v).strip())
        return str(value).strip()

    entries = fm.get("state_delta")
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        character = entry.get("character") or entry.get("name")
        if not isinstance(character, str) or not character.strip():
            continue
        conn.execute(
            """INSERT OR REPLACE INTO chapter_state
               (book_id, chapter_no, character, location, physical, mental, power_level,
                arc_stage, knowledge_json, goals_json, delta_json, fingerprint, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (book_id, chapter_no, character.strip(),
             *(text(entry.get(k)) for k in ("location", "physical", "mental", "power_level", "arc_stage")),
             json.dumps(entry["knowledge"], ensure_ascii=False) if entry.get("knowledge") is not None else None,
             json.dumps(entry["goals"], ensure_ascii=False) if entry.get("goals") is not None else None,
             json.dumps(entry, ensure_ascii=False), str(fm.get("source_fingerprint") or ""), now()),
        )


def set_imported(*, book_id: int, chapter_no: int) -> None:
    """标记该章由 txt 导入（无细纲、可能无记忆卡，UI 需区分"导入待补"）。"""
    with tx() as conn:
        conn.execute("UPDATE chapters SET imported=1, updated_at=? WHERE book_id=? AND no=?",
                     (now(), book_id, chapter_no))
