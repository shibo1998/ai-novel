"""（规格05 F-04）记忆卡派生事实应用失败的回滚边界。"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from . import atomic, books, conflicts, db, fts, summaries


@contextmanager
def memory_commit(book: str) -> Iterator[None]:
    """（规格01 §5.1）只回滚派生文件/索引，模型调用历史与计费保留。"""
    root = books.book_dir(book)
    fixed = [root / name for name in ("PLOT_POINTS.md", "LOCATIONS.md", "STATE.md",
                                      "STATE.proposed.md", "STATE.proposed.json", "STATE.accepted.json")]
    directories = [root / "characters", root / "chapters"]
    paths = fixed + [p for directory in directories for p in directory.glob("*.md")
                     if directory.name == "characters" or p.name.endswith("_记忆卡.md")]
    paths.append(summaries._path(book))
    bid = books.book_id(book)
    with conflicts._LOCK:
        paths.append(conflicts._path())
        backup = {}
        for path in paths:
            if path.exists():
                with path.open(encoding="utf-8", newline="") as file:
                    backup[path] = file.read()
            else:
                backup[path] = None
        with db.tx() as conn:
            fts.ensure_table(conn)
            rows = [dict(r) for r in conn.execute("SELECT * FROM chapter_state WHERE book_id=?", (bid,))]
            indexed = [dict(r) for r in conn.execute("SELECT * FROM chapters_fts WHERE book_id=?", (bid,))]
        try:
            yield
        except Exception:
            for directory in directories:
                for path in directory.glob("*.md"):
                    if (directory.name == "characters" or path.name.endswith("_记忆卡.md")) and path not in backup:
                        path.unlink()
            for path, text in backup.items():
                if text is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic.write_text(path, text)
            with db.tx() as conn:
                conn.execute("DELETE FROM chapter_state WHERE book_id=?", (bid,))
                for row in rows:
                    columns = list(row)
                    conn.execute(f"INSERT INTO chapter_state ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                                 tuple(row.values()))
                conn.execute("DELETE FROM chapters_fts WHERE book_id=?", (bid,))
                for row in indexed:
                    conn.execute("INSERT INTO chapters_fts (book_id,chapter_no,body) VALUES(?,?,?)",
                                 (row["book_id"], row["chapter_no"], row["body"]))
            raise
