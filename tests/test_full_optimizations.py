from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ainovel import (attach, books, chapters, db, importer, pipeline, state,
                     statemachine)
from ainovel.api import app
from ainovel.mockllm import MockLLM

client = TestClient(app)


def _ready(book: str, mock: MockLLM | None = None) -> MockLLM:
    mock = mock or MockLLM()
    pipeline.generate_outline(book, 1, client=mock)
    pipeline.confirm_outline(book, 1)
    pipeline.generate_prose(book, 1, client=mock)
    pipeline.run_mech_check(book, 1, client=mock)
    pipeline.run_llm_review(book, 1, client=mock)
    return mock

def test_rubric_missing_dimensions_cannot_pass(home):
    result = {"verdict": "pass", "scores": {}, "issues": []}
    assert pipeline._enforce_rubric(result) != "pass"


def test_prose_edit_from_llm_review_restarts_mechanical_gate(home, book_env):
    book, _ = book_env
    mock = MockLLM()
    pipeline.generate_outline(book, 1, client=mock)
    pipeline.confirm_outline(book, 1)
    pipeline.generate_prose(book, 1, client=mock)
    pipeline.run_mech_check(book, 1, client=mock)
    assert statemachine.current_status(book, 1) == "llm_review"

    r = client.put(f"/api/books/{book}/chapters/1/prose", json={"content": "新正文" * 500})
    assert r.status_code == 200
    assert r.json()["status"] == "mech_check"
    assert client.post(f"/api/books/{book}/chapters/1/finalize").status_code == 400


def test_finalize_rejects_stale_mechanical_fingerprint(home, book_env):
    book, _ = book_env
    mock = _ready(book)
    prose = chapters.read_prose(book, 1)
    fm = {k: v for k, v in prose.items() if not k.startswith("_")}
    chapters.write_prose(book, 1, fm, prose["_body"] + "\n人工改动。")

    with pytest.raises(statemachine.StateError):
        pipeline.finalize(book, 1, client=mock)


def test_import_preserves_parsed_chapter_numbers(home, book_env):
    book, _ = book_env
    source = home / "sparse.txt"
    source.write_text("第1章 甲\n甲正文\n第3章 丙\n丙正文\n", encoding="utf-8")
    preview = importer.preview(source)
    assert [item["no"] for item in preview["chapters"]] == [1, 3]


def test_upload_book_path_cannot_escape_imports(home, book_env):
    import urllib.parse

    book, _ = book_env
    encoded = base64.b64encode("第1章\n正文。".encode("utf-8")).decode("ascii")
    unsafe_book = urllib.parse.quote("..\\..", safe="")
    response = client.post(f"/api/books/{unsafe_book}/import/upload", json={
        "filename": "novel.txt", "content_b64": encoded,
    })
    assert response.status_code in (400, 404)
    assert not (home / "uploads-novel.txt").exists()


def test_attach_does_not_rewrite_existing_config(home, book_env):
    root = home / "external"
    root.mkdir()
    (root / "chapter.txt").write_text("原稿", encoding="utf-8")
    cfg = root / "config.json"
    original = '{"chapter_word_range": [100, 200], "custom": "keep"}\n'
    cfg.write_text(original, encoding="utf-8")

    attach.attach("外部书", root, scaffold=True)
    assert cfg.read_text(encoding="utf-8") == original


def test_detach_preserves_database_history(home, book_env):
    root = home / "external"
    root.mkdir()
    (root / "chapter.txt").write_text("原稿", encoding="utf-8")
    attach.attach("外部书", root, scaffold=True)
    bid = books.book_id("外部书")
    with db.tx() as conn:
        conn.execute("INSERT INTO snapshots(book_id, chapter_no, version, label, content, created_at) VALUES(?,?,?,?,?,?)",
                     (bid, 1, 1, "v1", "historical prose", db.now()))

    attach.detach("外部书")
    with db.tx() as conn:
        assert conn.execute("SELECT id FROM books WHERE name=?", ("外部书",)).fetchone()
        assert conn.execute("SELECT content FROM snapshots WHERE book_id=?", (bid,)).fetchone()["content"] == "historical prose"


def test_human_rewrite_read_failure_does_not_write_empty_prose(home, book_env, monkeypatch):
    book, _ = book_env
    mock = _ready(book)
    pipeline.finalize(book, 1, client=mock)
    original = chapters.prose_path(book, 1).read_text(encoding="utf-8")
    monkeypatch.setattr(chapters, "read_prose", lambda *_: (_ for _ in ()).throw(OSError("read failed")))

    with pytest.raises(pipeline.PipelineError, match="正文"):
        pipeline.human_rewrite(book, 1)
    assert chapters.prose_path(book, 1).read_text(encoding="utf-8") == original


def test_memory_rebuild_failure_keeps_existing_memory_and_state(home, book_env):
    book, _ = book_env
    mock = _ready(book)
    pipeline.finalize(book, 1, client=mock)
    card_before = chapters.memory_path(book, 1).read_text(encoding="utf-8")
    rows_before = state.chapter_rows(book, 1)
    boom = type("Boom", (), {"chat": lambda self, **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))})()

    with pytest.raises(RuntimeError):
        pipeline.rebuild_memory_card(book, 1, client=boom)
    assert chapters.memory_path(book, 1).read_text(encoding="utf-8") == card_before
    assert state.chapter_rows(book, 1) == rows_before


def test_state_proposal_requires_human_approval(home, book_env):
    book, _ = book_env
    mock = _ready(book)
    pipeline.finalize(book, 1, client=mock)
    assert state.proposed_path(book).exists()
    assert not state.state_path(book).exists()
    proposed = state.proposed_path(book).read_text(encoding="utf-8")
    state.approve_proposed(book)
    assert state.state_path(book).read_text(encoding="utf-8") == proposed


def test_state_overwrite_needs_explicit_replace(home, book_env):
    book, _ = book_env
    mock = _ready(book)
    pipeline.finalize(book, 1, client=mock)
    state.approve_proposed(book)
    prior = state.state_path(book).read_text(encoding="utf-8")
    state.upsert_entries(book, 1, [{"character": "林寒", "location": "新地点"}])
    state.rebuild_state_md(book)
    assert state.state_path(book).read_text(encoding="utf-8") == prior
    with pytest.raises(ValueError, match="并发"):
        state.approve_proposed(book)
    state.approve_proposed(book, expected_fingerprint=state.proposal_info(book)["accepted_fingerprint"])
    assert state.state_path(book).read_text(encoding="utf-8") != prior


def test_rebuild_removes_orphan_chapter_rows_and_indexes_attached_books(home, book_env):
    ext = home / "external"
    ext.mkdir()
    books.create_book("临时书")
    import shutil
    shutil.copytree(books.book_dir("临时书"), ext, dirs_exist_ok=True)
    attach.attach("外挂重建", ext, scaffold=False)
    body = "外部角色记录" * 80
    chapters.write_prose("外挂重建", 1, {"chapter_no": 1, "version": 1,
                                       "status": "draft", "word_count": len(body)}, body)
    statemachine.ensure_chapter_row("外挂重建", 1)
    db.rebuild()
    bid = books.book_id("外挂重建")
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chapters_fts WHERE book_id=?", (bid,)).fetchone()[0] == 1
        statemachine.ensure_chapter_row("外挂重建", 12)
    (ext / "chapters" / "012_正文.md").unlink(missing_ok=True)
    db.rebuild()
    with db.tx() as conn:
        assert conn.execute("SELECT COUNT(*) FROM chapters WHERE book_id=? AND no=12", (bid,)).fetchone()[0] == 0


def test_imported_chapter_conflicts_are_rejected_without_overwrite(home, book_env):
    book, _ = book_env
    source = home / "existing.txt"
    source.write_text("第1章 甲\n正文\n", encoding="utf-8")
    prose = chapters.prose_path(book, 1)
    prose.parent.mkdir(parents=True, exist_ok=True)
    prose.write_text("untouched", encoding="utf-8")
    with pytest.raises(importer.ImportError_, match="已存在"):
        importer.commit(book, source, mode="draft", confirm_warnings=True)
    assert prose.read_text(encoding="utf-8") == "untouched"


def test_upload_rejects_invalid_base64(home, book_env):
    book, _ = book_env
    response = client.post(f"/api/books/{book}/import/upload", json={
        "filename": "novel.txt", "content_b64": "not-base64!",
    })
    assert response.status_code == 400


def test_state_revision_detects_stale_edit(book_env):
    book, _ = book_env
    statemachine.ensure_chapter_row(book, 1)
    row = statemachine.get_row(book, 1)
    first = statemachine.bump_revision(book, 1, expected=row["state_revision"])
    with pytest.raises(statemachine.StateError, match="并发"):
        statemachine.bump_revision(book, 1, expected=row["state_revision"])
    assert statemachine.get_row(book, 1)["state_revision"] == first




def test_review_missing_scores_retries_then_errors(home, book_env):
    book, _ = book_env
    mock = MockLLM()
    pipeline.generate_outline(book, 1, client=mock)
    pipeline.confirm_outline(book, 1)
    pipeline.generate_prose(book, 1, client=mock)
    pipeline.run_mech_check(book, 1, client=mock)
    mock.review_verdicts = [{"verdict": "pass", "scores": {}, "issues": []}] * 3
    with pytest.raises(pipeline.PipelineError):
        pipeline.run_llm_review(book, 1, client=mock)
    assert statemachine.current_status(book, 1) == "error"


def test_memory_apply_failure_restores_files_and_indices(home, book_env, monkeypatch):
    from ainovel import fts

    book, _ = book_env
    mock = _ready(book)
    pipeline.finalize(book, 1, client=mock)
    before = chapters.memory_path(book, 1).read_bytes()
    rows = state.chapter_rows(book, 1)
    monkeypatch.setattr(fts, "index_chapter", lambda *_: (_ for _ in ()).throw(OSError("index failed")))
    with pytest.raises(OSError):
        pipeline.rebuild_memory_card(book, 1, client=mock)
    assert chapters.memory_path(book, 1).read_bytes() == before
    assert state.chapter_rows(book, 1) == rows


def test_polish_missing_sensitive_rules_keeps_original(home, book_env):
    book, _ = book_env
    mock = _ready(book)
    before = chapters.prose_path(book, 1).read_bytes()
    (home / "lint" / "sensitive_words.txt").unlink()
    calls = len(mock.calls)
    result = pipeline.polish(book, 1, client=mock)
    assert result["status"] == "rejected" and not result["ready"] and not result["applied"]
    assert result["reason_code"] == "NOT_EFFECTIVE"
    assert len(mock.calls) == calls
    assert chapters.prose_path(book, 1).read_bytes() == before


def test_fidelity_rejects_same_length_unrelated_story(home):
    old = "他推开门，雨灌进领口。" * 100
    new = "她登上船，风吹过江岸。" * 100
    assert pipeline._fidelity_error(old, new)
    assert pipeline._fidelity_error(old, old.replace("领口", "衣襟", 1)) is None


def test_old_editor_revision_is_rejected_before_write(home, book_env):
    book, _ = book_env
    _ready(book)
    revision = statemachine.get_row(book, 1)["state_revision"]
    url = f"/api/books/{book}/chapters/1/prose"
    assert client.put(url, json={"content": "第一版", "expected_revision": revision}).status_code == 200
    before = chapters.prose_path(book, 1).read_bytes()
    response = client.put(url, json={"content": "旧页面", "expected_revision": revision})
    assert response.status_code == 409
    assert chapters.prose_path(book, 1).read_bytes() == before


def test_two_generation_requests_do_not_overlap(home, book_env, monkeypatch):
    import threading
    from ainovel import operations

    book, _ = book_env
    mock = MockLLM()
    pipeline.generate_outline(book, 1, client=mock)
    pipeline.confirm_outline(book, 1)
    started, release = threading.Event(), threading.Event()
    errors = []
    original = pipeline._generate_prose_inner

    def delayed(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline, "_generate_prose_inner", delayed)
    def first():
        try:
            pipeline.generate_prose(book, 1, client=mock)
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=first)
    thread.start()
    try:
        assert started.wait(10)
        with pytest.raises(operations.BusyError):
            pipeline.generate_prose(book, 1, client=mock)
    finally:
        release.set()
        thread.join(15)
    assert not thread.is_alive() and errors == []
    assert statemachine.current_status(book, 1) == "mech_check"


def test_timeline_invalid_save_keeps_original(home, book_env):
    from ainovel import timeline

    book, _ = book_env
    row = {"编号": "T-001", "章": "1", "事件": "令牌发热", "涉及角色": "林寒",
           "读者已知": "是", "知情角色": "林寒"}
    timeline.write(book, [row], [{"章": "1", "本章向读者释放的真相": "T-001"}])
    before = timeline.timeline_path(book).read_bytes()
    response = client.put(f"/api/books/{book}/timeline", json={"truth": [row, row]})
    assert response.status_code == 400
    assert timeline.timeline_path(book).read_bytes() == before


def test_stale_state_proposal_cannot_be_approved(home, book_env):
    book, _ = book_env
    mock = _ready(book)
    pipeline.finalize(book, 1, client=mock)
    info = state.proposal_info(book)
    path = chapters.prose_path(book, 1)
    path.write_text(path.read_text(encoding="utf-8") + "变更", encoding="utf-8")
    with pytest.raises(ValueError, match="来源"):
        state.approve_proposed(book, info["proposed_fingerprint"])
    assert not state.state_path(book).exists()


def test_unconfirmed_state_is_not_used_by_assembly(home, book_env):
    book, _ = book_env
    mock = _ready(book)
    pipeline.finalize(book, 1, client=mock)
    assert state.confirmed_map(book, 1) == {}
    state.approve_proposed(book)
    assert state.confirmed_map(book, 1)["林寒"]["location"] == "青云坊"


def test_manual_ai_fix_cannot_rewrite_sensitive_redline(home, book_env):
    book, _ = book_env
    mock = _ready(book)
    prose = chapters.read_prose(book, 1)
    fm = {k: v for k, v in prose.items() if not k.startswith("_")}
    chapters.write_prose(book, 1, fm, prose["_body"] + "国家领导人")
    count = len(mock.calls)
    with pytest.raises(pipeline.PipelineError, match="禁止自动改写"):
        pipeline.run_targeted_fix(book, 1, [], client=mock)
    assert len(mock.calls) == count


def test_finalize_rechecks_rule_availability(home, book_env):
    book, _ = book_env
    mock = _ready(book)
    (home / "lint" / "sensitive_words.txt").unlink()
    with pytest.raises(statemachine.StateError, match="未完整执行"):
        pipeline.finalize(book, 1, client=mock)
    assert chapters.read_prose(book, 1)["status"] == "draft"


def test_rebuild_maintenance_cannot_interrupt_generation(home, book_env):
    from ainovel import operations

    book, _ = book_env
    with operations.writing(book):
        response = client.post("/api/db/rebuild")
        assert response.status_code == 409
    assert client.post("/api/db/rebuild").status_code == 200


def test_refinalize_changed_prose_keeps_old_snapshot(home, book_env):
    book, _ = book_env
    mock = _ready(book)
    pipeline.finalize(book, 1, client=mock)
    original = pipeline.get_snapshot(book, 1, 1)
    assert client.post(f"/api/books/{book}/chapters/1/unlock").status_code == 200
    changed = original.replace("雨点", "雨珠", 1)
    assert client.put(f"/api/books/{book}/chapters/1/prose", json={"content": changed}).status_code == 200
    pipeline.run_mech_check(book, 1, client=mock)
    pipeline.run_llm_review(book, 1, client=mock)
    result = pipeline.finalize(book, 1, client=mock)
    assert result["snapshot_version"] == 2
    assert pipeline.get_snapshot(book, 1, 1) == original
    assert pipeline.get_snapshot(book, 1, 2) == changed


def test_character_create_is_rejected_during_memory_write(home, book_env):
    from ainovel import operations, characters

    book, _ = book_env
    with operations.writing(book):
        response = client.post(f"/api/books/{book}/characters", json={"name": "人工新角色"})
        assert response.status_code == 409
    assert "人工新角色" not in characters.list_characters(book)


def test_human_review_snapshot_rewrite_does_not_recheck_midway(home, book_env):
    book, _ = book_env
    mock = _ready(book)
    db.add_snapshot(book_id=books.book_id(book), chapter_no=1, version=1,
                    label="旧稿", content="旧版本正文")
    with db.tx() as conn:
        conn.execute("UPDATE chapters SET status='human_review' WHERE book_id=? AND no=1", (books.book_id(book),))
    version = pipeline.human_rewrite(book, 1, base_version=1)
    assert version == 2
    assert statemachine.current_status(book, 1) == "generating"
    assert chapters.read_prose(book, 1)["_body"] == "旧版本正文"


def test_imported_draft_edit_can_refinalize_without_fake_outline(home, book_env):
    from ainovel.mockllm import make_prose

    book, _ = book_env
    source = home / "ready.txt"
    source.write_text("第1章 雨夜\n" + make_prose((2200, 2600)), encoding="utf-8")
    importer.commit(book, source, mode="draft", confirm_warnings=True)
    body = chapters.read_prose(book, 1)["_body"].replace("雨点", "雨珠", 1)
    assert client.put(f"/api/books/{book}/chapters/1/prose", json={"content": body}).status_code == 200
    pipeline.run_mech_check(book, 1, client=MockLLM())
    assert statemachine.current_status(book, 1) == "pending_finalize"
    pipeline.finalize(book, 1, client=MockLLM())
    assert statemachine.current_status(book, 1) == "finalized"
    assert not chapters.outline_path(book, 1).exists()


@pytest.mark.parametrize("event", ["甲\n乙", "甲|乙"])
def test_timeline_rejects_table_breaking_cells(home, book_env, event):
    from ainovel import timeline

    book, _ = book_env
    row = {"编号": "T-001", "章": "1", "事件": event, "涉及角色": "林寒",
           "读者已知": "是", "知情角色": "林寒"}
    with pytest.raises(ValueError, match="单行"):
        timeline.write(book, [row])
    assert not timeline.timeline_path(book).exists()


def test_external_unpadded_chapter_is_readable_and_exported(home, book_env):
    from ainovel import frontmatter as FM

    root = home / "external-unpadded"
    (root / "chapters").mkdir(parents=True)
    body = "非补零的原稿内容。"
    path = root / "chapters" / "7_正文.md"
    path.write_text(FM.dump({"chapter_no": 7, "version": 1, "status": "final", "word_count": len(body)}, body), encoding="utf-8")
    attach.attach("非补零书", root, scaffold=True)
    assert chapters.prose_path("非补零书", 7) == path
    assert chapters.read_prose("非补零书", 7)["_body"] == body
    assert body in client.post("/api/books/非补零书/export/txt").text
    assert not (root / "chapters" / "007_正文.md").exists()


def test_attach_cannot_interrupt_another_book_write(home, book_env):
    from ainovel import operations

    book, _ = book_env
    root = home / "pending-attach"
    root.mkdir()
    (root / "chapter.txt").write_text("原稿", encoding="utf-8")
    with operations.writing(book):
        response = client.post("/api/books/attach", json={"name": "维护挂载", "path": str(root)})
        assert response.status_code == 409
    assert not (root / "meta.md").exists()
