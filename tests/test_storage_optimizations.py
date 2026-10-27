"""存储反向回归（规格01 §3/§5.4、规格05 F-07/F-08/C-02）。"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ainovel import atomic, attach, bible, books, db, frontmatter as FM, fts, outline
from ainovel.paths import attached_books, book_dir, data_dir, save_attached


def _external(home: Path, dirname: str = "外部书稿") -> Path:
    path = home / "外部" / dirname
    (path / "chapters").mkdir(parents=True)
    return path


def _prose(path: Path, no: int = 1, *, status: str = "final", imported: bool = False,
           filename: str | None = None) -> str:
    text = f"林寒捡起玄铁令，第{no}章的雨落在青云坊。\n"
    fm = {"chapter_no": no, "title": f"夜雨{no}", "version": 3, "status": status,
          "word_count": len(text), "imported": imported}
    atomic.write_text(path / "chapters" / (filename or f"{no:03d}_正文.md"), FM.dump(fm, text))
    return text


def _memory(path: Path, no: int, *, legacy: bool = False) -> None:
    fm: dict[str, Any] = {
        "chapter_no": no, "char_updates": [], "new_chars": [], "new_locations": [],
        "foreshadow_ops": [], "event_summary": "林寒抵达青云坊。", "time_elapsed": "半日",
        "source_fingerprint": "memory-source",
    }
    if not legacy:
        fm["state_delta"] = [{
            "character": "林寒", "location": "青云坊", "physical": ["轻伤", "疲惫"],
            "mental": "警惕", "power_level": "炼气三层", "arc_stage": "触发",
            "knowledge": ["玄铁令是钥匙"], "goals": ["寻找老周"],
        }]
    atomic.write_text(path / "chapters" / f"{no:03d}_记忆卡.md", FM.dump(fm, "记忆摘要\n"))


def _files(path: Path) -> dict[str, tuple[bytes, int]]:
    return {str(p.relative_to(path)): (p.read_bytes(), p.stat().st_mtime_ns)
            for p in path.rglob("*") if p.is_file()}


def _seed_history(bid: int, no: int = 1) -> dict[str, list[dict[str, Any]]]:
    db.log_generation(book_id=bid, chapter_no=no, stage="test", prompt_digest="digest",
                      cost_millicents=500)
    db.add_review(book_id=bid, chapter_no=no, version=3, kind="llm", verdict="pass")
    db.add_snapshot(book_id=bid, chapter_no=no, version=3, label="历史", content="旧正文")
    return _history(bid)


def _history(bid: int) -> dict[str, list[dict[str, Any]]]:
    with db.tx() as conn:
        return {table: [dict(r) for r in conn.execute(
            f"SELECT * FROM {table} WHERE book_id=? ORDER BY id", (bid,)).fetchall()]
            for table in ("generation_log", "review_records", "snapshots")}


def _drop_database() -> None:
    path = db.db_path()
    if db._CONN is not None:
        db._CONN.close()
    db._CONN = None
    fts._TRIGRAM_OK = None
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)


@pytest.mark.parametrize("name", ["", "  ", "../逃逸", "..", "带/分隔符", "非法:名", "坏\x00名"])
def test_invalid_name_rejected_before_any_io(home: Path, monkeypatch: pytest.MonkeyPatch,
                                            name: str) -> None:
    path = _external(home)
    _prose(path)
    before = _files(home)

    def unexpected_target_check(_: str | Path) -> Path:
        pytest.fail("非法书名不得进入目标检查或任何写盘步骤")

    monkeypatch.setattr(attach, "validate_target", unexpected_target_check)
    with pytest.raises(ValueError, match="非法书名"):
        attach.attach(name, path)
    assert _files(home) == before
    assert not data_dir().exists()


def test_scaffold_reports_original_layout_and_valid_schemas(home: Path) -> None:
    path = _external(home)
    atomic.write_text(path / "全本.txt", "第一章 夜雨\n正文\n")
    before = _files(path)
    result = attach.attach("补齐书", path)
    assert result["detected"]["layout"] == "flat-txt"
    assert result["detected"]["has_config"] is False
    assert result["detected"]["has_outline"] is False
    assert result["detected"]["has_bible"] is False
    assert result["detected"]["has_meta"] is False
    assert outline.read_outline("补齐书")["logline"] == ""
    assert set(bible.read("补齐书")) == set(books.BIBLE_SECTIONS)
    assert bible.validate_sections(bible.read("补齐书")) == []
    assert books.read_meta("补齐书")["title"] == "补齐书"
    config = books.load_config("补齐书")
    assert config["model_stages"] == books.DEFAULT_MODEL_STAGES
    assert all(config["model_stages"].values())
    assert config["original_layout"] == "flat-txt"
    assert config["attached_at"]
    assert config["external"] is True
    assert {k: _files(path)[k] for k in before} == before
    manifest = json.loads(Path(result["backup"]).read_text(encoding="utf-8"))
    assert {i["rel"] for i in manifest["files"]} == {"全本.txt"}


@pytest.mark.parametrize("scaffold", [True, False])
def test_attach_never_modifies_existing_files_including_config(home: Path, scaffold: bool) -> None:
    path = _external(home)
    _prose(path)
    for name in attach.SCAFFOLD_FILES:
        if name == "config.json":
            text = '{ "chapter_word_range": [1234, 4321], "custom": "原配置" }\r\n'
        else:
            text = f"# 原始{name}，不要求改造成新格式\r\n"
        atomic.write_text(path / name, text)
    before = _files(path)
    attach.attach("不覆盖", path, scaffold=scaffold)
    assert {k: _files(path)[k] for k in before} == before
    if not scaffold:
        assert _files(path) == before


def test_attach_without_meta_immediately_restores_chapters_and_fts(home: Path) -> None:
    path = _external(home)
    _prose(path)
    _prose(path, no=7, status="draft", imported=True, filename="7_正文.md")
    _prose(path, no=99, filename="099_正文.draft.md")
    before = _files(path)
    attach.attach("无壳外挂", path, scaffold=False)
    bid = books.book_id("无壳外挂")
    with db.tx() as conn:
        rows = conn.execute("SELECT no, title, status, current_version, imported FROM chapters "
                            "WHERE book_id=? ORDER BY no", (bid,)).fetchall()
    assert [tuple(r) for r in rows] == [(1, "夜雨1", "finalized", 3, 0),
                                      (7, "夜雨7", "pending_finalize", 3, 1)]
    assert {no for no, _ in fts.search(bid, "玄铁令")} == {1, 7}
    assert {no for no, _ in fts.search(bid, "林寒")} == {1, 7}
    assert _files(path) == before
    assert any(b["name"] == "无壳外挂" for b in books.list_books())


def test_detach_rebuild_and_reattach_keep_identity_and_all_history(home: Path) -> None:
    path = _external(home)
    _prose(path)
    _memory(path, 1)
    attach.attach("可摘书", path)
    bid = books.book_id("可摘书")
    history = _seed_history(bid)
    before = _files(path)
    attach.detach("可摘书")
    assert "可摘书" not in attached_books()
    assert not fts.search(bid, "林寒")
    with pytest.raises(FileNotFoundError, match="摘除"):
        books.book_id("可摘书")
    for _ in range(2):
        db.rebuild()
        fts.rebuild_index()
        assert _history(bid) == history
        assert not fts.search(bid, "玄铁令")
        assert all(b["name"] != "可摘书" for b in books.list_books())
        with db.tx() as conn:
            row = conn.execute("SELECT id, detached FROM books WHERE name='可摘书'").fetchone()
        assert tuple(row) == (bid, 1)
    attach.attach("可摘书", path / ".", scaffold=False)
    assert books.book_id("可摘书") == bid
    assert _history(bid) == history
    assert fts.search(bid, "林寒")
    assert _files(path) == before
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chapter_state WHERE book_id=?", (bid,)).fetchone()[0] == 1


def test_same_name_different_path_cannot_reuse_detached_history(home: Path) -> None:
    first = _external(home, "原目录")
    second = _external(home, "另一本")
    _prose(first)
    _prose(second)
    attach.attach("同名", first, scaffold=False)
    bid = books.book_id("同名")
    history = _seed_history(bid)
    attach.detach("同名")
    before = _files(second)
    with pytest.raises(attach.AttachError, match="另一目录"):
        attach.attach("同名", second)
    with pytest.raises(ValueError, match="历史"):
        books.create_book("同名")
    assert _files(second) == before
    assert not attached_books()
    assert _history(bid) == history
    assert not (home / "books" / "同名").exists()


def test_rebuild_refuses_registry_path_hijack(home: Path) -> None:
    first = _external(home, "原目录")
    second = _external(home, "另一本")
    _prose(first)
    _prose(second)
    attach.attach("路径保护", first, scaffold=False)
    bid = books.book_id("路径保护")
    history = _seed_history(bid)
    save_attached({"路径保护": str(second)})
    with pytest.raises(ValueError, match="历史"):
        db.rebuild()
    assert _history(bid) == history
    with db.tx() as conn:
        assert conn.execute("SELECT path FROM books WHERE id=?", (bid,)).fetchone()[0] == str(first)


def test_legacy_detach_without_flag_does_not_cascade_history(home: Path) -> None:
    path = _external(home)
    _prose(path)
    attach.attach("旧版摘除", path, scaffold=False)
    bid = books.book_id("旧版摘除")
    history = _seed_history(bid)
    save_attached({})  # 旧版只有删除登记表条目，没有 detached 列。
    db.rebuild()
    assert _history(bid) == history
    assert not fts.search(bid, "林寒")
    with db.tx() as conn:
        assert conn.execute("SELECT detached FROM books WHERE id=?", (bid,)).fetchone()[0] == 1
    attach.attach("旧版摘除", path, scaffold=False)
    assert books.book_id("旧版摘除") == bid


def test_missing_attached_directory_is_not_historical_deletion(home: Path) -> None:
    path = _external(home)
    _prose(path)
    attach.attach("失联外挂", path, scaffold=False)
    bid = books.book_id("失联外挂")
    history = _seed_history(bid)
    shutil.rmtree(path)
    report = db.rebuild()
    assert "失联外挂" in report["dropped"]
    assert _history(bid) == history
    assert not fts.search(bid, "林寒")
    assert "失联外挂" in attached_books()
    # 目录暂时失联不是摘除；登记仍在，恢复目录后沿用原 ID 与历史。
    (path / "chapters").mkdir(parents=True)
    _prose(path)
    db.rebuild()
    assert books.book_id("失联外挂") == bid
    assert _history(bid) == history
    assert fts.search(bid, "林寒")


def test_database_deletion_rebuilds_external_internal_imports_and_memory_state(home: Path) -> None:
    books.create_book("内置书")
    internal = book_dir("内置书")
    external = _external(home)
    for path in (internal, external):
        _prose(path, 1, imported=True)
        _prose(path, 2, status="draft", imported=True)
        _memory(path, 1)
        _memory(path, 2, legacy=True)
    attach.attach("外挂书", external, scaffold=False)
    registry = (data_dir() / "attached_books.json").read_bytes()
    _drop_database()
    report = db.rebuild()
    assert report["books"] == 2
    assert report["chapters"] == 4
    assert (data_dir() / "attached_books.json").read_bytes() == registry
    for name in ("内置书", "外挂书"):
        bid = books.book_id(name)
        with db.tx() as conn:
            rows = conn.execute("SELECT no, status, imported, current_version, title FROM chapters "
                                "WHERE book_id=? ORDER BY no", (bid,)).fetchall()
            states = conn.execute("SELECT * FROM chapter_state WHERE book_id=?", (bid,)).fetchall()
        assert [tuple(r) for r in rows] == [(1, "finalized", 1, 3, "夜雨1"),
                                          (2, "pending_finalize", 1, 3, "夜雨2")]
        assert len(states) == 1
        state = states[0]
        assert state["character"] == "林寒" and state["location"] == "青云坊"
        assert state["physical"] == "轻伤、疲惫"
        assert state["power_level"] == "炼气三层"
        assert state["fingerprint"] == "memory-source"
        assert json.loads(state["knowledge_json"]) == ["玄铁令是钥匙"]
        assert json.loads(state["goals_json"]) == ["寻找老周"]
        assert {no for no, _ in fts.search(bid, "林寒")} == {1, 2}
        assert not list(book_dir(name).glob("chapters/*_细纲.md"))


def test_rebuild_removes_orphan_chapters_and_state_but_keeps_history(home: Path) -> None:
    path = _external(home)
    for no in (1, 2):
        _prose(path, no)
        _memory(path, no)
    attach.attach("清孤儿", path, scaffold=False)
    bid = books.book_id("清孤儿")
    history = _seed_history(bid)
    with db.tx() as conn:
        conn.execute("UPDATE chapters SET state_revision=12 WHERE book_id=? AND no=2", (bid,))
    for p in path.glob("chapters/001_*.md"):
        p.unlink()
    (path / "chapters" / "002_记忆卡.md").unlink()
    db.rebuild()
    with db.tx() as conn:
        rows = conn.execute("SELECT no, state_revision FROM chapters WHERE book_id=?", (bid,)).fetchall()
        assert [tuple(r) for r in rows] == [(2, 13)]
        assert conn.execute("SELECT COUNT(*) FROM chapter_state WHERE book_id=?", (bid,)).fetchone()[0] == 0
    assert _history(bid) == history
    assert [no for no, _ in fts.search(bid, "林寒")] == [2]


def test_scanning_excludes_drafts_notes_and_extra_suffixes(home: Path) -> None:
    path = _external(home)
    _prose(path, 7, filename="7_正文.md")
    for name in ("099_正文.draft.md", "100_笔记.md", "101_正文.v1.md", "102_细纲.bak.md",
                 "103_附录_正文.md", "104_草稿_正文.md", "note_正文.md", "105_正文.md.bak"):
        atomic.write_text(path / "chapters" / name, "不应索引的草稿词\n")
    attach.attach("过滤扫描", path, scaffold=False)
    assert list(db._chapter_files(path)) == [7]
    assert list(db._scan_chapters(path))[0][0] == 7
    db.rebuild()
    bid = books.book_id("过滤扫描")
    with db.tx() as conn:
        assert [r[0] for r in conn.execute("SELECT no FROM chapters WHERE book_id=?", (bid,))] == [7]
    assert not fts.search(bid, "草稿词")
    assert [no for no, _ in fts.search(bid, "林寒")] == [7]


def test_bad_memory_card_does_not_abort_rebuild_or_restore_garbage(home: Path) -> None:
    path = _external(home)
    _prose(path)
    atomic.write_text(path / "chapters" / "001_记忆卡.md", "---\nchapter_no: [broken\n---\n")
    attach.attach("坏卡", path, scaffold=False)
    db.rebuild()
    bid = books.book_id("坏卡")
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chapter_state WHERE book_id=?", (bid,)).fetchone()[0] == 0
    assert fts.search(bid, "玄铁令")


def test_detach_delete_files_rejected_without_side_effects(home: Path) -> None:
    path = _external(home)
    _prose(path)
    attach.attach("不可删", path, scaffold=False)
    before = _files(path)
    with pytest.raises(attach.AttachError, match="不支持删除"):
        attach.detach("不可删", delete_files=True)
    assert attached_books()["不可删"] == str(path)
    assert _files(path) == before
    assert fts.search(books.book_id("不可删"), "林寒")


def test_old_schema_migrates_detached_and_state_revision_without_losing_rows(home: Path) -> None:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript("""
            CREATE TABLE books (id INTEGER PRIMARY KEY, name TEXT UNIQUE, path TEXT, created_at TEXT);
            INSERT INTO books VALUES (7, '旧书', 'old-path', 'old-time');
            CREATE TABLE chapters (id INTEGER PRIMARY KEY, book_id INTEGER, no INTEGER,
                                   title TEXT, status TEXT, current_version INTEGER, updated_at TEXT);
            INSERT INTO chapters VALUES (9, 7, 1, '旧章', 'finalized', 2, 'old-time');
        """)
        db.init_schema(conn)
        assert conn.execute("SELECT id, name, detached FROM books").fetchone() == (7, "旧书", 0)
        assert conn.execute("SELECT id, state_revision, imported FROM chapters").fetchone() == (9, 0, 0)
    finally:
        conn.close()


@pytest.mark.parametrize("mode", ["trigram", "bigram"])
def test_chinese_two_character_fallback_survives_attach_and_rebuild(
        home: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    if mode == "trigram" and not fts._try_trigram(db.get_conn()):
        pytest.skip("SQLite 不支持 trigram，另一个参数覆盖 bigram 降级")
    monkeypatch.setattr(fts, "_TRIGRAM_OK", mode == "trigram")
    path = _external(home)
    _prose(path)
    attach.attach("分词回归", path, scaffold=False)
    db.rebuild()
    bid = books.book_id("分词回归")
    assert fts.tokenize_mode() == mode
    assert fts.search(bid, "林寒")
    assert fts.search(bid, "玄铁令")


def test_fts_table_setup_cannot_commit_an_enclosing_storage_transaction(home: Path) -> None:
    books.create_book("事务书")
    bid = books.book_id("事务书")
    with pytest.raises(RuntimeError, match="回滚"):
        with db.tx() as conn:
            conn.execute("INSERT INTO generation_log (book_id, stage, status, created_at) "
                         "VALUES (?, 'rollback-test', 'ok', ?)", (bid, db.now()))
            fts.ensure_table(conn)
            raise RuntimeError("必须回滚")
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM generation_log WHERE book_id=?", (bid,)).fetchone()[0] == 0


def test_attaching_one_book_does_not_reset_another_books_live_state(home: Path) -> None:
    books.create_book("正在审查")
    bid = books.book_id("正在审查")
    with db.tx() as conn:
        conn.execute("INSERT INTO chapters (book_id, no, status, state_revision, updated_at) "
                     "VALUES (?, 1, 'llm_review', 8, ?)", (bid, db.now()))
    path = _external(home)
    _prose(path)
    attach.attach("新挂载", path, scaffold=False)
    with db.tx() as conn:
        row = conn.execute("SELECT status, state_revision FROM chapters WHERE book_id=?", (bid,)).fetchone()
    assert tuple(row) == ("llm_review", 8)


def test_deleted_database_does_not_rediscover_a_detached_external_directory(home: Path) -> None:
    path = _external(home)
    _prose(path)
    attach.attach("已摘除", path, scaffold=False)
    attach.detach("已摘除")
    _drop_database()
    report = db.rebuild()
    assert report["books"] == 0 and report["chapters"] == 0
    assert books.list_books() == []
    assert path.exists() and not attached_books()


def test_shadow_builtin_directory_cannot_delete_detached_external_history(home: Path) -> None:
    path = _external(home)
    _prose(path)
    attach.attach("影子书名", path, scaffold=False)
    bid = books.book_id("影子书名")
    history = _seed_history(bid)
    attach.detach("影子书名")
    shadow = book_dir("影子书名")
    shadow.mkdir(parents=True)
    atomic.write_text(shadow / "meta.md", "影子目录，不属于原外挂书\n")
    with pytest.raises(ValueError, match="历史"):
        books.delete_book("影子书名")
    assert shadow.is_dir() and path.is_dir()
    assert _history(bid) == history


def test_rebuild_cleans_database_only_chapter_placeholders(home: Path) -> None:
    books.create_book("没有章节文件")
    bid = books.book_id("没有章节文件")
    with db.tx() as conn:
        conn.execute("INSERT INTO chapters (book_id, no, status, updated_at) "
                     "VALUES (?, 1, 'awaiting_outline', ?)", (bid, db.now()))
    db.rebuild()
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chapters WHERE book_id=?", (bid,)).fetchone()[0] == 0
