"""FTS5 全文索引：trigram 优先，不可用降级 bigram 预切分（规格01 §3、M0 检查单 §4.3）。"""
from __future__ import annotations

import re
import sqlite3

from . import db

_TRIGRAM_OK: bool | None = None


def _try_trigram(conn: sqlite3.Connection) -> bool:
    global _TRIGRAM_OK
    if _TRIGRAM_OK is not None:
        return _TRIGRAM_OK
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x, tokenize='trigram')")
        conn.execute("DROP TABLE _fts_probe")
        conn.commit()
        _TRIGRAM_OK = True
    except sqlite3.OperationalError:
        _TRIGRAM_OK = False
    return _TRIGRAM_OK


def ensure_table(conn: sqlite3.Connection) -> None:
    if _try_trigram(conn):
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chapters_fts USING fts5"
            "(book_id UNINDEXED, chapter_no UNINDEXED, body, tokenize='trigram')"
        )
    else:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chapters_fts USING fts5"
            "(book_id UNINDEXED, chapter_no UNINDEXED, body)"
        )
    conn.commit()


def tokenize_mode() -> str:
    return "trigram" if _try_trigram(db.get_conn()) else "bigram"


def _bigram(text: str) -> str:
    """入库/查询前 bigram 切分（空格连接，等价于 2-gram 检索）。"""
    text = re.sub(r"\s+", "", text)
    if len(text) < 2:
        return text
    return " ".join(text[i:i + 2] for i in range(len(text) - 1))


def _prepare(text: str) -> str:
    return text if tokenize_mode() == "trigram" else _bigram(text)


def index_chapter(book_id: int, chapter_no: int, body: str) -> None:
    with db.tx() as conn:
        ensure_table(conn)
        conn.execute("DELETE FROM chapters_fts WHERE book_id=? AND chapter_no=?", (book_id, chapter_no))
        conn.execute(
            "INSERT INTO chapters_fts (book_id, chapter_no, body) VALUES (?,?,?)",
            (book_id, chapter_no, _prepare(body)),
        )


def search(book_id: int, query: str, top_k: int = 8) -> list[tuple[int, str]]:
    """按 query 关键词检索，返回 (chapter_no, 命中片段)。每片段 ≤150 字（规格03 §2.2）。

    trigram 模式下 <3 字的关键词（如两字角色名）MATCH 不到，走 Python 子串兜底。
    """
    keywords = [k for k in re.split(r"[\s,，、;；]+", query) if len(k) >= 2]
    if not keywords:
        return []
    mode = tokenize_mode()
    long_kws = [k for k in keywords if mode == "bigram" or len(k) >= 3]
    short_kws = [k for k in keywords if mode == "trigram" and len(k) < 3]

    rows: list[tuple[int, str]] = []
    seen: set[int] = set()
    try:
        with db.tx() as conn:
            ensure_table(conn)
            if long_kws:
                if mode == "trigram":
                    match_expr = " OR ".join(f'"{k}"' for k in long_kws)
                else:
                    match_expr = " OR ".join(
                        "(" + " OR ".join(f'"{t}"' for t in _bigram(k).split()) + ")" for k in long_kws
                    )
                for r in conn.execute(
                    "SELECT chapter_no, body FROM chapters_fts WHERE chapters_fts MATCH ? AND book_id=?",
                    (match_expr, book_id),
                ).fetchall():
                    if r["chapter_no"] not in seen:
                        seen.add(r["chapter_no"])
                        rows.append((r["chapter_no"], r["body"] if mode == "trigram" else r["body"].replace(" ", "")))
            if short_kws:
                for r in conn.execute(
                    "SELECT chapter_no, body FROM chapters_fts WHERE book_id=?", (book_id,)
                ).fetchall():
                    if r["chapter_no"] in seen:
                        continue
                    body = r["body"]
                    if any(k in body for k in short_kws):
                        seen.add(r["chapter_no"])
                        rows.append((r["chapter_no"], body))
    except sqlite3.OperationalError:
        return []

    results: list[tuple[int, str]] = []
    for no, body in rows:
        snippet = _extract_snippet(body, keywords)
        results.append((no, snippet[:150]))
    return results[:top_k]


def _extract_snippet(body: str, keywords: list[str], width: int = 150) -> str:
    low = body
    pos = -1
    for kw in keywords:
        pos = low.find(kw)
        if pos >= 0:
            break
    if pos < 0:
        return body[:width]
    start = max(0, pos - width // 3)
    return body[start:start + width]


def rebuild_index() -> None:
    """全量重建：从各书 chapters/ 正文文件重灌。"""
    from .paths import book_dir, books_dir

    with db.tx() as conn:
        ensure_table(conn)
        conn.execute("DELETE FROM chapters_fts")
        book_rows = conn.execute("SELECT id, name FROM books").fetchall()
        name2id = {r["name"]: r["id"] for r in book_rows}
    if not books_dir().exists():
        return
    for bdir in sorted(books_dir().glob("*")):
        if not bdir.is_dir() or bdir.name not in name2id:
            continue
        book_id = name2id[bdir.name]
        for f in sorted((bdir / "chapters").glob("*_正文.md")) if (bdir / "chapters").exists() else []:
            try:
                no = int(f.name.split("_", 1)[0])
            except ValueError:
                continue
            try:
                text = f.read_text(encoding="utf-8")
                # 去掉 frontmatter，只索引正文
                if text.startswith("---"):
                    _, text = _split_fm(text)
                index_chapter(book_id, no, text)
            except Exception:
                continue


def _split_fm(text: str) -> tuple[str, str]:
    import re

    m = re.match(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", text, re.DOTALL)
    if m:
        return m.group(1), text[m.end():]
    return "", text
