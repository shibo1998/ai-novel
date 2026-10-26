"""SQLite：表结构、连接、章节行 CRUD、rebuild（规格01 §3/§5.4）。

SQLite = 可随时重建的索引与历史；文件才是单一真相源。
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from . import frontmatter as FM
from .paths import book_dir, books_dir, data_dir

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
    created_at TEXT NOT NULL
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
    created_at TEXT NOT NULL
);
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
        _ensure_column(conn, "generation_log", "cost_millicents", "INTEGER")
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
) -> int:
    import json

    with tx() as conn:
        cur = conn.execute(
            """INSERT INTO review_records
               (book_id, chapter_no, version, kind, scores_json, issues_json, verdict, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (book_id, chapter_no, version, kind,
             json.dumps(scores, ensure_ascii=False) if scores is not None else None,
             json.dumps(issues, ensure_ascii=False) if issues is not None else None,
             verdict, now()),
        )
        return cur.lastrowid


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
    """扫描 books/ 目录重建 books 与 chapters 索引行 + FTS。

    - generation_log / review_records / snapshots 保留原值（运行历史，
      DB 文件本身丢失时这部分历史无法从文件恢复——已在 README 声明）。
    - 章节状态从文件证据推导（推导规则见 _derive_status），中段瞬态
      统一停靠到可恢复状态。
    """
    from . import fts

    report = {"books": 0, "chapters": 0, "fts_reindexed": 0, "dropped": []}
    with tx() as conn:
        # 清掉文件里已不存在的书
        for row in conn.execute("SELECT id, name FROM books").fetchall():
            if not book_dir(row["name"]).exists():
                conn.execute("DELETE FROM books WHERE id=?", (row["id"],))
                report["dropped"].append(row["name"])
        seen_books: set[str] = set()
        for bdir in sorted(books_dir().glob("*")) if books_dir().exists() else []:
            if not bdir.is_dir() or not (bdir / "meta.md").exists():
                continue
            seen_books.add(bdir.name)
            bid = conn.execute("SELECT id FROM books WHERE name=?", (bdir.name,)).fetchone()
            if bid is None:
                cur = conn.execute(
                    "INSERT INTO books (name, path, created_at) VALUES (?,?,?)",
                    (bdir.name, str(bdir), now()),
                )
                book_id: int = cur.lastrowid
            else:
                book_id = bid["id"]
            report["books"] += 1
            for chapter_no, title, status, version in _scan_chapters(bdir):
                conn.execute(
                    """INSERT INTO chapters (book_id, no, title, status, current_version, updated_at)
                       VALUES (?,?,?,?,?,?)
                       ON CONFLICT(book_id, no) DO UPDATE SET
                         title=excluded.title, current_version=excluded.current_version,
                         status=excluded.status, updated_at=excluded.updated_at""",
                    (book_id, chapter_no, title, status, version, now()),
                )
                report["chapters"] += 1
    # FTS 重建
    fts.rebuild_index()
    report["fts_reindexed"] = 1
    return report


def _scan_chapters(bdir: Path) -> Iterator[tuple[int, str, str, int]]:
    chapters_dir = bdir / "chapters"
    if not chapters_dir.exists():
        return
    nos: set[int] = set()
    for f in chapters_dir.glob("*.md"):
        try:
            nos.add(int(f.name.split("_", 1)[0]))
        except ValueError:
            continue
    for chapter_no in sorted(nos):
        title = ""
        version = 1
        outline_p = bdir / "chapters" / f"{chapter_no:03d}_细纲.md"
        if outline_p.exists():
            try:
                fm, _ = FM.parse(outline_p.read_text(encoding="utf-8"))
                title = str(fm.get("title") or "")
            except Exception:
                pass
        status = _derive_status(bdir, chapter_no)
        body = chapters_dir / f"{chapter_no:03d}_正文.md"
        if body.exists():
            try:
                fm, _ = FM.parse(body.read_text(encoding="utf-8"))
                version = int(fm.get("version") or 1)
            except Exception:
                pass
        yield chapter_no, title, status, version


def _derive_status(bdir: Path, chapter_no: int) -> str:
    """备用：单章状态推导（供 _scan_chapters 之外的测试使用）。"""
    outline = bdir / "chapters" / f"{chapter_no:03d}_细纲.md"
    body = bdir / "chapters" / f"{chapter_no:03d}_正文.md"
    if not outline.exists():
        return "awaiting_outline"
    try:
        fm, _ = FM.parse(outline.read_text(encoding="utf-8"))
        if not fm.get("confirmed"):
            return "outline_draft"
    except Exception:
        return "outline_draft"
    if not body.exists():
        return "outline_locked"
    try:
        fm, _ = FM.parse(body.read_text(encoding="utf-8"))
        return "finalized" if fm.get("status") == "final" else "mech_check"
    except Exception:
        return "human_review"
