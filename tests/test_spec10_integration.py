"""（规格10 阶段B）真实候选发动机+文件任务集成，全部使用home/mockllm。"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from ainovel import atomic, books, chapters, conflicts, db, outline, pipeline, production, statemachine
from ainovel.mockllm import MockLLM, make_prose


def _start(book: str, client: MockLLM, count: int = 3) -> dict[str, Any]:
    if not outline.read_locations(book):
        outline.write_locations(book, [{"名称": "青云坊", "特征": "临时测试坊市", "方位/距离": "", "首现章": "1", "关联剧情": "验令"}])
    return production.create(book, {"mode": "prepare", "volume_no": 1, "count": count,
                                   "logline": "林寒在坊市寻找父母线索", "requirements": "", "client_request_id": str(uuid4())},
                             production.book_etag(book), client=client, background=False)


def _generate(book: str, job: dict[str, Any], client: MockLLM) -> dict[str, Any]:
    return production.confirm(book, job["id"], job["preview_digest"], str(uuid4()), job["etag"], client=client, background=False)


def _canonical(book: str) -> dict[str, bytes]:
    root = books.book_dir(book)
    files = [root / name for name in ("STORY_BIBLE.md", "PLOT_POINTS.md", "LOCATIONS.md", "STATE.md", "STATE.accepted.json", "TIMELINE.md")]
    files += list((root / "characters").glob("*.md"))
    return {str(p.relative_to(root)): p.read_bytes() for p in files if p.is_file()}


def test_three_chapters_run_to_pending_finalize_without_clicks(book_env: Any, monkeypatch: Any) -> None:
    book, _ = book_env
    client = MockLLM()
    calls = []
    monkeypatch.setattr(pipeline, "finalize", lambda *_args, **_kwargs: calls.append("forbidden"))
    job = _start(book, client)
    before = _canonical(book)
    assert job["status"] == "awaiting_outline_confirmation", job["reason"]
    assert not chapters.prose_path(book, 1).exists()
    job = _generate(book, job, client)
    assert job["status"] == "awaiting_final_review", job["reason"]
    assert job["completed"] == [1, 2, 3] and calls == []
    assert all(statemachine.current_status(book, no) == "pending_finalize" for no in job["numbers"])
    assert all(chapters.read_prose(book, no)["status"] == "draft" for no in job["numbers"])
    assert _canonical(book) == before
    assert not any(chapters.memory_path(book, no).exists() for no in job["numbers"])
    review = production.review(book, job["id"])
    assert len(review["chapters"]) == 3 and all(item["check"] and item["review"] for item in review["chapters"])
    assert all(len(item["fingerprint"]) == 64 for item in review["chapters"])
    assert (production.directory(book, job["id"]) / "draft_context.json").is_file()
    with db.tx() as conn:
        assert conn.execute("SELECT count(*) FROM generation_log WHERE book_id=?", (books.book_id(book),)).fetchone()[0] > 0


def test_redline_pauses_without_later_chapter_calls(book_env: Any) -> None:
    book, _ = book_env
    client = MockLLM()
    job = _start(book, client)
    before = _canonical(book)
    client.calls.clear()
    client.prose_factory = lambda no, rng: make_prose(rng) + "国家领导人"
    job = _generate(book, job, client)
    assert job["status"] == "paused"
    assert job["completed"] == []
    assert _canonical(book) == before
    assert not any("只修复问题清单" in call["user"] for call in client.calls)
    assert not chapters.prose_path(book, 2).exists()


def test_resume_skips_verified_completed_chapters(book_env: Any, monkeypatch: Any) -> None:
    book, _ = book_env
    client = MockLLM()
    job = _start(book, client)
    original_progress = production._progress
    once = [False]
    def pause(book: str, job_id: str, phase: str, no: int | None) -> None:
        if phase == "chapter" and no == 2 and not once[0]:
            once[0] = True
            current = production.get(book, job_id)
            production.control(book, job_id, "pause", current["etag"])
        original_progress(book, job_id, phase, no)
    monkeypatch.setattr(production, "_progress", pause)
    job = _generate(book, job, client)
    assert job["status"] == "paused" and job["completed"] == [1], job
    old_body = chapters.prose_path(book, 1).read_bytes()
    before_calls = len(client.calls)
    job = production.control(book, job["id"], "resume", job["etag"], rid=str(uuid4()), client=client, background=False)
    assert job["status"] == "awaiting_final_review", job["reason"]
    assert chapters.prose_path(book, 1).read_bytes() == old_body
    assert len(client.calls) > before_calls and job["completed"] == [1, 2, 3]


def test_source_edit_invalidates_dependent_suffix(book_env: Any) -> None:
    book, _ = book_env
    client = MockLLM()
    job = _generate(book, _start(book, client), client)
    assert job["status"] == "awaiting_final_review", job["reason"]
    old_third = chapters.prose_path(book, 3).read_bytes()
    prose = chapters.read_prose(book, 1)
    chapters.write_prose(book, 1, {k: v for k, v in prose.items() if not k.startswith("_")}, prose["_body"] + "\n人工调整。")
    assert production.get(book, job["id"])["status"] == "stale"
    assert chapters.prose_path(book, 3).read_bytes() == old_third
    with pytest.raises(production.ProductionError):
        production.finalize(book, job["id"], [1], {"1": "old"}, str(uuid4()), production.get(book, job["id"])["etag"], client=client)


def test_final_review_calls_finalize_only_after_human_request(book_env: Any) -> None:
    book, _ = book_env
    client = MockLLM()
    job = _generate(book, _start(book, client, 1), client)
    assert job["status"] == "awaiting_final_review", job["reason"]
    review = production.review(book, job["id"])
    fingerprints = {str(item["no"]): item["fingerprint"] for item in review["chapters"]}
    rid = str(uuid4())
    result = production.finalize(book, job["id"], [1], fingerprints, rid, job["etag"], client=client)
    assert result["results"][0]["applied"], result
    assert chapters.read_prose(book, 1)["status"] == "final"
    count = len(client.calls)
    assert production.finalize(book, job["id"], [1], fingerprints, rid, job["etag"], client=client) == result
    assert len(client.calls) == count


def test_unavailable_check_pauses(book_env: Any) -> None:
    book, home = book_env
    client = MockLLM()
    job = _start(book, client)
    # 确认后才在隔离运行时使红线词库缺失，不能被准备阶段调用掩盖。
    original = production._launch
    production._launch = lambda *_: None
    try:
        job = _generate(book, job, client)
    finally:
        production._launch = original
    (home / "lint/sensitive_words.txt").unlink()
    # 输入也改变，任务必须在调用前停止，不发送下一章请求。
    count = len(client.calls)
    production._run(book, job["id"], client)
    assert len(client.calls) == count
    assert production.get(book, job["id"])["status"] == "stale"


def test_prepare_resume_reuses_returned_checkpoint_without_rebilling(book_env: Any, monkeypatch: Any) -> None:
    book, _ = book_env
    client = MockLLM()
    original = production._progress
    once = [False]
    def stop_after_one(book: str, job_id: str, phase: str, no: int | None) -> None:
        if phase == "model" and len(client.calls) == 1 and not once[0]:
            once[0] = True
            current = production._load(book, job_id)
            current.update(status="paused", reason="隔离准备检查点暂停")
            production._save(book, current)
            raise production.Paused
        original(book, job_id, phase, no)
    monkeypatch.setattr(production, "_progress", stop_after_one)
    job = _start(book, client, 2)
    assert job["status"] == "paused" and len(client.calls) == 1
    first_user = client.calls[0]["user"]
    job = production.control(book, job["id"], "resume", job["etag"], rid=str(uuid4()), client=client, background=False)
    assert job["status"] == "awaiting_outline_confirmation", job["reason"]
    assert sum(call["user"] == first_user for call in client.calls) == 1
    with db.tx() as conn:
        rows = list(conn.execute("SELECT input_tokens,output_tokens,cost_millicents,request_meta_json FROM generation_log WHERE book_id=?", (books.book_id(book),)))
    import json
    cached = [row for row in rows if json.loads(row["request_meta_json"] or "{}").get("execution") == "prepared_checkpoint_cache"]
    assert cached and all(row["input_tokens"] == row["output_tokens"] == row["cost_millicents"] == 0 for row in cached)
