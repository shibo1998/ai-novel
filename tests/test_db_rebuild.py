"""SQLite rebuild：从文件全量重建（规格01 §5.4）+ FTS。"""
from ainovel import chapters, db, fts, pipeline, statemachine
from ainovel.mockllm import MockLLM


def test_rebuild_restores_chapters(book_env):
    book, h = book_env
    mock = MockLLM()
    pipeline.generate_outline(book, 1, client=mock)
    pipeline.confirm_outline(book, 1)
    pipeline.generate_prose(book, 1, client=mock)
    pipeline.run_mech_check(book, 1, client=mock)
    pipeline.run_llm_review(book, 1, client=mock)
    pipeline.finalize(book, 1, client=mock)

    with db.tx() as conn:
        gen_before = conn.execute("SELECT COUNT(*) n FROM generation_log").fetchone()["n"]
    snap_before = len(pipeline.list_versions(book, 1))

    # 删库重建（保留 generation_log/snapshots：历史不在文件里，rebuild 不应清掉）
    report = db.rebuild()
    assert report["books"] >= 1
    assert statemachine.current_status(book, 1) == "finalized"

    with db.tx() as conn:
        gen_after = conn.execute("SELECT COUNT(*) n FROM generation_log").fetchone()["n"]
    assert gen_after == gen_before
    assert len(pipeline.list_versions(book, 1)) == snap_before

    # FTS 重建后可检索
    hits = fts.search(1, "玄铁令")
    assert hits and hits[0][0] == 1


def test_fts_short_and_long_keyword(book_env):
    book, _ = book_env
    mock = MockLLM()
    pipeline.generate_outline(book, 1, client=mock)
    pipeline.confirm_outline(book, 1)
    pipeline.generate_prose(book, 1, client=mock)
    fts.index_chapter(1, 1, chapters.read_prose(book, 1)["_body"])
    mode = fts.tokenize_mode()
    if mode == "trigram":
        assert fts.search(1, "林寒")  # 2 字兜底
    assert fts.search(1, "玄铁令") or fts.search(1, "令牌")


def test_rebuild_drops_deleted_book(book_env):
    book, h = book_env
    from ainovel import books

    statemachine.ensure_chapter_row(book, 1)
    db.rebuild()  # 入库
    with db.tx() as conn:
        n = conn.execute("SELECT COUNT(*) n FROM chapters").fetchone()["n"]
    assert n >= 1

    # 场景1：API 删除（DB 行随之清除）
    books.delete_book(book)
    db.rebuild()
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) n FROM chapters").fetchone()["n"] == 0

    # 场景2：手动删目录（DB 行残留）→ rebuild 报告 dropped
    books.create_book("孤儿书")
    statemachine.ensure_chapter_row("孤儿书", 1)
    import shutil as _sh

    _sh.rmtree(h / "books" / "孤儿书")
    report = db.rebuild()
    assert "孤儿书" in report["dropped"]
