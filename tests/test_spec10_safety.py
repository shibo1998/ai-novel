"""（规格10 §5）一次有界复核发现的具体安全边界回归。"""
from __future__ import annotations

import copy
import json
import threading
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ainovel import api, atomic, books, chapters, pipeline, production
from ainovel.mockllm import MockLLM
from test_spec10_production import engine, start, validate
from test_spec10_integration import _start, _generate


@pytest.mark.parametrize("status", [[], {}, None, 1])
def test_corrupt_status_is_unavailable_not_unhandled(book_env: Any, engine: Any, status: Any) -> None:
    book, _ = book_env
    job = start(book)
    manifest = production._load(book, job["id"])
    manifest["status"] = status
    atomic.write_text(production.directory(book, job["id"]) / "manifest.json", json.dumps(manifest, ensure_ascii=False))
    with pytest.raises(production.ProductionError) as exc:
        production.get(book, job["id"])
    assert exc.value.code == "PRODUCTION_INVALID" and exc.value.status == 503
    assert production.volumes(book)["warnings"]


def test_publish_checks_each_not_yet_written_file(book_env: Any, engine: Any, monkeypatch: Any) -> None:
    book, _ = book_env
    def files_last(book: str, bundle: dict, numbers: list) -> dict:
        files = validate(book, bundle, numbers)
        return {name: files[name] for name in sorted(files, key=lambda name: not name.startswith("chapters/"))}
    monkeypatch.setattr(engine, "validate_bundle", files_last)
    job = start(book)
    original_write = atomic.write_text
    once = [False]
    def external_change(path: Any, text: str, **kwargs: Any) -> None:
        if str(path).endswith("001_细纲.md") and not once[0]:
            once[0] = True
            original_write(books.book_dir(book) / "OUTLINE.md", "第三方尚未发布档案")
        original_write(path, text, **kwargs)
    monkeypatch.setattr(atomic, "write_text", external_change)
    with pytest.raises(production.ProductionError):
        production.confirm(book, job["id"], job["preview_digest"], str(uuid4()), job["etag"], background=False)
    assert (books.book_dir(book) / "OUTLINE.md").read_text(encoding="utf-8") == "第三方尚未发布档案"
    assert production.get(book, job["id"])["status"] == "recovery_required"


def test_append_tracks_outside_previous_chapter(book_env: Any, engine: Any) -> None:
    book, _ = book_env
    path = chapters.prose_path(book, 12)
    atomic.write_text(path, "上一卷末正文")
    job = start(book, mode="append", count=1)
    atomic.write_text(path, "被修改的上一卷末正文")
    assert production.get(book, job["id"])["status"] == "stale"
    with pytest.raises(production.ProductionError):
        production.confirm(book, job["id"], job["preview_digest"], str(uuid4()), production.get(book, job["id"])["etag"], background=False)


def test_candidate_invalid_confirmed_has_stable_api_error(book_env: Any) -> None:
    book, _ = book_env
    job = _start(book, MockLLM(), 1)
    bundle = copy.deepcopy(production.review(book, job["id"])["candidate"])
    bundle["outlines"][0]["fm"]["confirmed"] = True
    response = TestClient(api.app, raise_server_exceptions=False).put(f"/api/books/{book}/production/{job['id']}/candidate",
                headers={"If-Match": job["etag"]}, json={"candidate": bundle})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "PRODUCTION_CANDIDATE_INVALID"


def test_final_receipt_recovery_reports_applied_without_recalling_model(book_env: Any, monkeypatch: Any) -> None:
    book, _ = book_env
    client = MockLLM()
    job = _generate(book, _start(book, client, 1), client)
    assert job["status"] == "awaiting_final_review", job
    fp = {"1": production.review(book, job["id"])["chapters"][0]["fingerprint"]}
    original_save = production._save
    once = [False]
    def fail_after_apply(book: str, value: dict) -> None:
        if value.get("applied") and not once[0]:
            once[0] = True
            raise OSError("定稿回执写失败")
        original_save(book, value)
    monkeypatch.setattr(production, "_save", fail_after_apply)
    rid = str(uuid4())
    with pytest.raises(OSError):
        production.finalize(book, job["id"], [1], fp, rid, job["etag"], client=client)
    assert chapters.read_prose(book, 1)["status"] == "final"
    calls = len(client.calls)
    result = production.finalize(book, job["id"], [1], fp, rid, job["etag"], client=client)
    assert result["results"] and result["results"][0]["applied"] is True
    assert result["stopped"] is True
    assert len(client.calls) == calls


def test_finalization_does_not_hold_global_manifest_lock(book_env: Any, monkeypatch: Any) -> None:
    book, _ = book_env
    client = MockLLM()
    job = _generate(book, _start(book, client, 1), client)
    fp = {"1": production.review(book, job["id"])["chapters"][0]["fingerprint"]}
    entered, release, queried = threading.Event(), threading.Event(), threading.Event()
    errors = []
    def slow_finalize(*args: Any, **kwargs: Any) -> dict:
        entered.set()
        release.wait(10)
        return {"writeback": {}}
    monkeypatch.setattr(pipeline, "finalize", slow_finalize)
    def write() -> None:
        try:
            production.finalize(book, job["id"], [1], fp, str(uuid4()), job["etag"], client=client)
        except Exception as exc:
            errors.append(exc)
    def query() -> None:
        production.get(book, job["id"])
        queried.set()
    worker = threading.Thread(target=write)
    worker.start()
    assert entered.wait(3)
    reader = threading.Thread(target=query)
    reader.start()
    try:
        assert queried.wait(1), "模型等待期间任务读取被全局锁阻塞"
    finally:
        release.set()
        worker.join(5)
        reader.join(5)
    assert not errors
