"""章节状态机（规格01 §4）。

补充图上未画但异常表/正文语义要求的迁移：
- mech_check 自动 lint_fix 一轮后仍阻断 → human_review（规格03 §2.4"仍有阻断 → human_review"）
- human_review --edit_outline--> outline_draft（规格03 §2.1"改细纲 = 回到 outline_draft"）
- error 记录 prev_status，retry 恢复原状态
"""
from __future__ import annotations

from typing import Any

from . import db

STATES = [
    "awaiting_outline", "outline_draft", "outline_locked", "generating",
    "mech_check", "lint_fix", "llm_review", "targeted_fix",
    "human_review", "pending_finalize", "finalized", "error",
]

TRANSITIONS: dict[tuple[str, str], str] = {
    ("awaiting_outline", "generate_outline"): "outline_draft",
    ("outline_draft", "confirm"): "outline_locked",
    ("outline_locked", "start_generate"): "generating",
    ("generating", "complete"): "mech_check",
    ("mech_check", "blocked"): "lint_fix",
    ("lint_fix", "recheck"): "mech_check",
    ("mech_check", "blocked_after_fix"): "human_review",
    ("mech_check", "pass"): "llm_review",
    ("llm_review", "fix"): "targeted_fix",
    ("targeted_fix", "recheck"): "mech_check",
    ("llm_review", "pass"): "pending_finalize",
    ("llm_review", "fail"): "human_review",
    ("llm_review", "rounds_exhausted"): "human_review",
    ("outline_draft", "generate_outline"): "outline_draft",  # 未确认前允许重生成细纲
    ("human_review", "edit_outline"): "outline_draft",
    ("human_review", "rewrite"): "generating",
    ("human_review", "approve"): "pending_finalize",
    ("pending_finalize", "finalize"): "finalized",
    ("finalized", "rewrite"): "generating",
}


class StateError(Exception):
    pass


def ensure_chapter_row(book: str, no: int, title: str = "") -> None:
    from . import books

    book_id = books.book_id(book)
    with db.tx() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO chapters (book_id, no, title, status, current_version, updated_at)
               VALUES (?,?,?, 'awaiting_outline', 1, ?)""",
            (book_id, no, title, db.now()),
        )


def get_row(book: str, no: int) -> dict[str, Any] | None:
    from . import books

    book_id = books.book_id(book)
    with db.tx() as conn:
        row = conn.execute(
            "SELECT * FROM chapters WHERE book_id=? AND no=?", (book_id, no)
        ).fetchone()
    return dict(row) if row else None


def current_status(book: str, no: int) -> str:
    row = get_row(book, no)
    return row["status"] if row else "awaiting_outline"


def transition(book: str, no: int, event: str) -> str:
    """按事件迁移状态；非法迁移抛 StateError。"""
    from . import books

    ensure_chapter_row(book, no)
    book_id = books.book_id(book)
    with db.tx() as conn:
        row = conn.execute(
            "SELECT * FROM chapters WHERE book_id=? AND no=?", (book_id, no)
        ).fetchone()
        cur = row["status"]
        if cur == "error" and event == "retry":
            target = row["prev_status"] or "awaiting_outline"
        else:
            target = TRANSITIONS.get((cur, event))
            if target is None:
                raise StateError(f"第{no}章状态 {cur} 不允许事件 {event}")
        conn.execute(
            "UPDATE chapters SET status=?, updated_at=? WHERE book_id=? AND no=?",
            (target, db.now(), book_id, no),
        )
    return target


def mark_error(book: str, no: int, message: str) -> None:
    from . import books

    ensure_chapter_row(book, no)
    book_id = books.book_id(book)
    with db.tx() as conn:
        row = conn.execute(
            "SELECT status FROM chapters WHERE book_id=? AND no=?", (book_id, no)
        ).fetchone()
        if row["status"] != "error":
            conn.execute(
                "UPDATE chapters SET status='error', prev_status=?, updated_at=? WHERE book_id=? AND no=?",
                (row["status"], db.now(), book_id, no),
            )


def set_fix_rounds(book: str, no: int, rounds: int) -> None:
    from . import books

    book_id = books.book_id(book)
    with db.tx() as conn:
        conn.execute(
            "UPDATE chapters SET fix_rounds=?, updated_at=? WHERE book_id=? AND no=?",
            (rounds, db.now(), book_id, no),
        )


def set_current_version(book: str, no: int, version: int) -> None:
    from . import books

    book_id = books.book_id(book)
    with db.tx() as conn:
        conn.execute(
            "UPDATE chapters SET current_version=?, updated_at=? WHERE book_id=? AND no=?",
            (version, db.now(), book_id, no),
        )


def list_chapters(book: str) -> list[dict[str, Any]]:
    from . import books

    book_id = books.book_id(book)
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT no, title, status, current_version, fix_rounds, updated_at "
            "FROM chapters WHERE book_id=? ORDER BY no", (book_id,)
        ).fetchall()
    return [dict(r) for r in rows]
