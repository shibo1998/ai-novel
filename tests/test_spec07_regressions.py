"""规格07 前端体验整改的后端回归（§6.2 自动回归清单）。

全部使用临时 AINOVEL_HOME / MockLLM，不读写真实 books/、data/，不联网。
"""
from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

from ainovel import atomic, bible, books, chapters, characters, conflicts, db, progress, statemachine, timeline
from ainovel import frontmatter as FM
from ainovel.api import app

client = TestClient(app)


def _prose(book: str, no: int, body: str, status: str = "final") -> None:
    fm = {"chapter_no": no, "version": 1, "status": status,
          "word_count": len(body.replace("\n", "").replace(" ", "")), "finalized_at": None}
    atomic.write_text(chapters.prose_path(book, no), FM.dump(fm, body))


def _file_bytes(path) -> bytes:
    return path.read_bytes()


# ---------------------------------------------------------------------------
# 一：章节规划、文件存在性与 ETag
# ---------------------------------------------------------------------------

def test_workbench_unplanned_chapter(home):
    books.create_book("空书")
    r = client.get("/api/books/空书/chapters/5")
    assert r.status_code == 200
    body = r.json()
    assert body["planned"] is False
    assert body["exists"] is False
    assert body["outline_generation"]["allowed"] is False
    assert body["outline_generation"]["code"] == "OUTLINE_ROW_MISSING"
    assert statemachine.get_row("空书", 5) is None


def test_workbench_planned_by_outline_only(book_env):
    book, _ = book_env
    r = client.get(f"/api/books/{book}/chapters/3")
    assert r.status_code == 200
    body = r.json()
    assert body["planned"] is True
    assert body["exists"] is False
    assert body["row"] is None
    assert body["outline_generation"]["allowed"] is True
    assert body["max_chapter"] == 12


def test_workbench_row_after_failed_outline_not_planned(book_env):
    book, _ = book_env
    statemachine.ensure_chapter_row(book, 20)
    bid = books.book_id(book)
    with db.tx() as conn:
        conn.execute("UPDATE chapters SET status='error' WHERE book_id=? AND no=?", (bid, 20))
        before = conn.execute("SELECT COUNT(*) FROM chapters WHERE book_id=?", (bid,)).fetchone()[0]
    r = client.get(f"/api/books/{book}/chapters/20")
    assert r.status_code == 200
    body = r.json()
    assert body["planned"] is False
    assert body["exists"] is False
    assert body["status"] == "error"
    assert body["outline_generation"]["code"] == "OUTLINE_ROW_MISSING"
    with db.tx() as conn:
        after = conn.execute("SELECT COUNT(*) FROM chapters WHERE book_id=?", (bid,)).fetchone()[0]
    assert after == before


def test_workbench_imported_prose_without_outline(book_env):
    book, _ = book_env
    _prose(book, 13, "导入的正文仍然可读。\n")
    r = client.get(f"/api/books/{book}/chapters/13")
    assert r.status_code == 200
    body = r.json()
    assert body["planned"] is False
    assert body["exists"] is True
    assert body["prose"]["_body"].strip() == "导入的正文仍然可读。"
    assert body["outline_generation"]["allowed"] is False
    assert body["outline_generation"]["code"] == "OUTLINE_ROW_MISSING"


def test_workbench_sparse_and_invalid_chapters(home):
    books.create_book("稀疏书")
    book = "稀疏书"
    chapters.chapters_dir(book).mkdir(parents=True, exist_ok=True)
    (chapters.chapters_dir(book) / "010_细纲.md").write_text("x", encoding="utf-8")
    (chapters.chapters_dir(book) / "10_正文.md").write_text("x", encoding="utf-8")
    body = client.get(f"/api/books/{book}/chapters/10").json()
    assert body["chapter_numbers"] == [10]
    assert body["max_chapter"] == 10

    r = client.get(f"/api/books/{book}/chapters/0")
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "CHAPTER_NO_INVALID"

    r = client.get("/api/books/不存在的书/chapters/1")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "BOOK_NOT_FOUND"


def test_workbench_invalid_outline_not_empty(book_env):
    book, _ = book_env
    _prose(book, 1, "第一章正文。\n")
    atomic.write_text(books.book_dir(book) / "OUTLINE.md", "## 章节总表\n\n没有 frontmatter")
    body = client.get(f"/api/books/{book}/chapters/1").json()
    assert body["planned"] is None
    assert body["planning_error"]["code"] == "OUTLINE_INVALID"
    assert body["prose"]["_body"].strip() == "第一章正文。"
    assert body["outline_generation"]["code"] == "OUTLINE_INVALID"


def test_archive_if_match_rejects_stale_save(book_env):
    book, _ = book_env
    path = books.book_dir(book) / "STORY_BIBLE.md"
    r = client.get(f"/api/books/{book}/bible")
    stale = r.headers["etag"]
    assert stale.startswith('"sha256-')
    sections = bible.read(book)
    sections["基础设定"] = "另一个会话已写入"
    bible.write(book, sections)
    before = _file_bytes(path)
    r = client.put(f"/api/books/{book}/bible", json={"基础设定": "旧会话覆盖"},
                   headers={"If-Match": stale})
    assert r.status_code == 412
    assert r.json()["detail"]["code"] == "RESOURCE_CHANGED"
    assert _file_bytes(path) == before


def test_lint_resource_identity_and_missing_file(home):
    path = home / "lint" / "banned_words.txt"
    path.unlink()
    r = client.get("/api/lint/banned_words")
    assert r.status_code == 200
    assert r.json()["exists"] is False
    missing_etag = r.headers["etag"]

    bad = client.put("/api/lint/banned_words",
                     json={"which": "sensitive_words", "content": "x"},
                     headers={"If-Match": missing_etag})
    assert bad.status_code == 400
    assert not path.exists()

    created = client.put("/api/lint/banned_words",
                         json={"which": "banned_words", "content": "甲"},
                         headers={"If-Match": missing_etag})
    assert created.status_code == 200
    stale = created.headers["etag"]
    path.write_text("乙", encoding="utf-8", newline="")
    r = client.put("/api/lint/banned_words",
                   json={"which": "banned_words", "content": "丙"},
                   headers={"If-Match": stale})
    assert r.status_code == 412
    assert path.read_text(encoding="utf-8") == "乙"


def test_archive_adopt_if_match(book_env):
    book, _ = book_env
    bible_path = books.book_dir(book) / "STORY_BIBLE.md"
    stale_bible = client.get(f"/api/books/{book}/bible").headers["etag"]
    sections = bible.read(book)
    sections["基础设定"] = "人工新版本"
    bible.write(book, sections)
    before = _file_bytes(bible_path)
    r = client.post(f"/api/books/{book}/adopt/bible",
                    json={"content": "## 基础设定\n\n旧候选覆盖\n"},
                    headers={"If-Match": stale_bible})
    assert r.status_code == 412
    assert _file_bytes(bible_path) == before

    stale_chars = client.get(f"/api/books/{book}/characters").headers["etag"]
    characters.create(book, "新角色")
    r = client.post(f"/api/books/{book}/adopt/characters",
                    json={"content": "===角色:林寒===\n\n## 关系网络\n| 角色 | 关系 |\n|---|---|\n| 新角色 | 同门 |\n"},
                    headers={"If-Match": stale_chars})
    assert r.status_code == 412
    assert "新角色" not in (characters.read(book, "林寒").get("_body") or "")


# ---------------------------------------------------------------------------
# 二：进度派生与书架统计
# ---------------------------------------------------------------------------

def test_books_list_progress_fields(book_env):
    book, _ = book_env
    _prose(book, 1, "第一章正文。\n")
    _prose(book, 2, "第二章草稿。\n", status="draft")
    items = client.get("/api/books").json()
    item = next(it for it in items if it["name"] == book)
    assert item["chapters_done"] == 1
    assert item["words_done"] == len("第一章正文。")
    assert item["chapters_total"] == 12
    assert item["last_edited_at"] is not None
    assert item["progress_warnings"] == []


def test_progress_rebuild_preserves_edit_time(book_env):
    book, _ = book_env
    _prose(book, 1, "第一章正文。\n")
    before = progress.derive(book)
    db.rebuild()
    after = progress.derive(book)
    assert {k: after[k] for k in ("chapters_done", "chapters_total", "words_done")} == \
           {k: before[k] for k in ("chapters_done", "chapters_total", "words_done")}
    assert after["last_edited_at"] == before["last_edited_at"]
    items = {it["name"]: it for it in books.list_books()}
    assert items[book]["last_edited_at"] == before["last_edited_at"]

    later = time.time() + 60
    os.utime(books.book_dir(book) / "meta.md", (later, later))
    assert progress.derive(book)["last_edited_at"] != before["last_edited_at"]


def test_progress_invalid_files_are_unknown(home):
    books.create_book("干净书")
    books.create_book("坏书")
    _prose("干净书", 1, "正常正文。\n")
    atomic.write_text(books.book_dir("坏书") / "OUTLINE.md", "## 章节总表\n\n损坏")
    data = progress.derive("坏书")
    assert data["chapters_total"] is None
    assert any(w["code"] == "OUTLINE_INVALID" for w in data["progress_warnings"])

    atomic.write_text(books.book_dir("坏书") / "chapters" / "1_正文.md", "损坏正文")
    data = progress.derive("坏书")
    assert data["chapters_done"] is None
    assert data["words_done"] is None
    assert any(w["code"] == "PROSE_INVALID" for w in data["progress_warnings"])

    items = {it["name"]: it for it in client.get("/api/books").json()}
    assert items["干净书"]["chapters_done"] == 1
    assert items["干净书"]["progress_warnings"] == []
    assert items["坏书"]["chapters_done"] is None

    missing = progress.derive("没有这本书")
    assert missing["chapters_done"] == 0
    assert missing["chapters_total"] == 0


# ---------------------------------------------------------------------------
# 三：冲突裁决真实写回
# ---------------------------------------------------------------------------

def _open_item(book: str, conflict: characters.Conflict) -> dict:
    conflicts.enqueue([conflict])
    return next(it for it in conflicts.list_open(book) if it["kind"] == conflict.kind)


def test_conflict_manual_values_reach_files(book_env):
    book, _ = book_env
    item = _open_item(book, characters.Conflict(
        "new_setting", book, 2, "待补录设定：青囊术：以灵气温养经脉", None, None))
    assert "manual" in item["allowed_actions"]
    result = conflicts.resolve(item["id"], "manual",
                               {"term": "青囊术", "description": "以灵气温养经脉"})
    assert result["effect"] == "applied"
    assert "| 青囊术 | 以灵气温养经脉 |" in bible.read(book)["术语表"]

    tl = _open_item(book, characters.Conflict(
        "timeline_candidate", book, 5, "林寒取得玄铁令", None,
        {"characters": ["林寒"], "reader_known": True}))
    result = conflicts.resolve(tl["id"], "adopt_ai")
    assert result["effect"] == "applied"
    row = next(r for r in timeline.read(book)["truth"] if r["事件"] == "林寒取得玄铁令")
    assert row["章"] == "5"
    assert row["涉及角色"] == "林寒"
    assert row["读者已知"] == "是"
    assert row["知情角色"] == "林寒"

    mc = _open_item(book, characters.Conflict(
        "missing_char", book, 7, "角色 阿七 不存在", None,
        {"realm": "炼气一层", "alive": True, "known_info": ["玄铁令"]}, char="阿七"))
    result = conflicts.resolve(mc["id"], "manual",
                               {"name": "阿七", "role": "重要配角",
                                "status": {"realm": "炼气一层", "alive": True}})
    assert result["effect"] == "applied"
    card = characters.read(book, "阿七")
    assert card["role"] == "重要配角"
    assert card["status"]["realm"] == "炼气一层"
    assert card["status"]["alive"] is True
    assert card["status"]["updated_chapter"] == 7


def test_conflict_setting_creates_missing_term_table(home):
    books.create_book("新书")
    item = _open_item("新书", characters.Conflict(
        "new_setting", "新书", 1, "待补录设定：青囊术：以灵气温养经脉", None, None))
    result = conflicts.resolve(item["id"], "manual",
                               {"term": "青囊术", "description": "以灵气温养经脉"})
    assert result["effect"] == "applied"
    terms = bible.read("新书")["术语表"]
    assert "| 术语 | 含义 |" in terms
    assert "| 青囊术 | 以灵气温养经脉 |" in terms


def test_conflict_failed_write_stays_open(book_env, monkeypatch):
    book, _ = book_env
    item = _open_item(book, characters.Conflict(
        "new_setting", book, 2, "待补录设定：青囊术：以灵气温养经脉", None, None))

    def boom(*args, **kwargs):
        raise OSError("磁盘不可写")

    monkeypatch.setattr(bible, "write", boom)
    with pytest.raises(conflicts.ConflictWriteError) as error:
        conflicts.resolve(item["id"], "adopt_ai")
    assert error.value.code == "CONFLICT_WRITE_FAILED"
    assert any(it["id"] == item["id"] for it in conflicts.list_open(book))
    assert not any(it.get("resolved") for it in conflicts._load() if it["id"] == item["id"])


def test_conflict_unsupported_action_and_old_shape(book_env):
    book, _ = book_env
    info = _open_item(book, characters.Conflict(
        "unknown_field", book, 3, "未知字段 mood", None, "紧张", char="林寒", field="mood"))
    assert info["allowed_actions"] == ["keep_archive"]
    assert info["manual_schema"] is None
    with pytest.raises(conflicts.ConflictActionUnavailable):
        conflicts.resolve(info["id"], "adopt_ai")
    assert conflicts.resolve(info["id"], "keep_archive")["effect"] == "acknowledged"

    old = _open_item(book, characters.Conflict(
        "missing_char", book, 4, "旧条目缺少状态", None, None, char="阿七"))
    assert "adopt_ai" not in old["allowed_actions"]
    with pytest.raises(conflicts.ConflictActionUnavailable):
        conflicts.resolve(old["id"], "adopt_ai")
    with pytest.raises(conflicts.ConflictValueInvalid):
        conflicts.resolve(old["id"], "manual", {"name": "阿七", "role": "龙套"})
    assert conflicts.resolve(old["id"], "keep_archive")["effect"] == "kept"

    with pytest.raises(conflicts.ConflictActionUnavailable):
        conflicts.resolve(old["id"], "auto_magic")


def test_conflict_receipt_failure_retry_no_duplicate(book_env, monkeypatch):
    book, _ = book_env
    item = _open_item(book, characters.Conflict(
        "new_setting", book, 2, "待补录设定：青囊术：以灵气温养经脉", None, None))
    real_save = conflicts._save
    calls = {"n": 0}

    def flaky(items):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("回执写入失败")
        real_save(items)

    monkeypatch.setattr(conflicts, "_save", flaky)
    with pytest.raises(conflicts.ConflictReceiptError):
        conflicts.resolve(item["id"], "adopt_ai")
    assert "青囊术" in bible.read(book)["术语表"]

    result = conflicts.resolve(item["id"], "adopt_ai")
    assert result["effect"] == "applied"
    terms = bible.read(book)["术语表"]
    assert terms.count("| 青囊术 |") == 1
    assert not any(it["id"] == item["id"] for it in conflicts.list_open(book))
