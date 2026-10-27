"""（规格06 §10、§12）文风草案与候选 API 集成，全部使用临时 home 和 MockLLM。"""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from ainovel import api, atomic, books, chapters, db, llm, mechcheck, pipeline, polish, prompts, statemachine, style
from ainovel.mockllm import MockLLM


client = TestClient(api.app)


class CandidateMock(MockLLM):
    """（规格06 §7）只改变一处已存在的措辞，提供真实前后文本。"""

    def _route(self, system: str, user: str) -> str:
        if "服务端原稿分块JSON" in user:
            import re

            match = re.search(r"【服务端原稿分块JSON[^\n]*】\n(.*?)\n【选区", user, re.S)
            assert match
            items = json.loads(match.group(1))
            first = items[0]
            text = first["text"]
            replacement = text.replace("雨点砸在铁皮棚上", "雨点敲在铁皮棚上", 1)
            assert replacement != text
            return json.dumps({"edits": [{"start_block": first["id"], "end_block": first["id"],
                                          "original": text, "replacement": replacement,
                                          "category": "语气", "reason": "保留事件并调整声响表达"}]}, ensure_ascii=False)
        return super()._route(system, user)


def ready(book: str) -> MockLLM:
    mock = MockLLM()
    pipeline.generate_outline(book, 1, client=mock)
    pipeline.confirm_outline(book, 1)
    pipeline.generate_prose(book, 1, client=mock)
    pipeline.run_mech_check(book, 1, client=mock)
    pipeline.run_llm_review(book, 1, client=mock)
    assert statemachine.current_status(book, 1) == "pending_finalize"
    return mock


def test_style_analysis_is_unsaved_draft(home, book_env, monkeypatch):
    book, _ = book_env
    style.write_corpus(book, "他把门关上，等了片刻才走。\n\n" * 90)
    style.write_card(book, "## 句法特征\n人工确认卡。\n")
    before = style.card_path(book).read_bytes()
    monkeypatch.setattr(llm, "client", lambda: MockLLM())
    result = client.post(f"/api/books/{book}/style/analyze")
    assert result.status_code == 200, result.text
    assert result.json()["applied"] is False
    assert style.card_path(book).read_bytes() == before
    assert result.json()["card_fingerprint"] == style.describe(book)["card_fingerprint"]


def test_style_fingerprints_conflict(home, book_env):
    book, _ = book_env
    url = f"/api/books/{book}/style/card"
    baseline = client.get(f"/api/books/{book}/style").json()
    first = client.put(url, json={"text": "人工卡一", "expected_fingerprint": baseline["card_fingerprint"]})
    assert first.status_code == 200
    assert first.json()["concurrency_protected"] is True
    second = client.put(url, json={"text": "旧页面卡", "expected_fingerprint": baseline["card_fingerprint"]})
    assert second.status_code == 409
    assert style.read_card(book) == "人工卡一"


def test_examples_validate_ranges_and_corpus(home, book_env):
    book, _ = book_env
    style.write_corpus(book, "第一段完整句。\n\n第二段完整句。")
    info = client.get(f"/api/books/{book}/style").json()
    url = f"/api/books/{book}/style/examples"
    valid = client.put(url, json={"corpus_fingerprint": info["corpus_fingerprint"],
                                 "examples": [{"start_paragraph": 1, "end_paragraph": 2,
                                                "scene_types": ["dialogue"]}]})
    assert valid.status_code == 200, valid.text
    bad = client.put(url, json={"corpus_fingerprint": info["corpus_fingerprint"],
                               "examples": [{"start_paragraph": 1, "end_paragraph": 99,
                                              "scene_types": ["dialogue"]}]})
    assert bad.status_code == 422
    style.write_corpus(book, "新语料。")
    stale = client.put(url, json={"corpus_fingerprint": info["corpus_fingerprint"], "examples": []})
    assert stale.status_code == 409
    assert client.get(f"/api/books/{book}/style").json()["examples_stale"] is True


def test_candidate_preview_no_write_and_explicit_approve(home, book_env, monkeypatch):
    book, _ = book_env
    ready(book)
    monkeypatch.setattr(llm, "client", lambda: CandidateMock())
    url = f"/api/books/{book}/chapters/1"
    before = chapters.prose_path(book, 1).read_bytes()
    row = statemachine.get_row(book, 1)
    payload = {"expected_revision": row["state_revision"], "client_request_id": str(uuid4()), "mode": "auto"}
    response = client.post(url + "/polish", json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["ready"] is True, result
    assert result["applied"] is False
    assert chapters.prose_path(book, 1).read_bytes() == before
    assert statemachine.get_row(book, 1)["state_revision"] == row["state_revision"]
    repeated = client.post(url + "/polish", json=payload).json()
    assert repeated["candidate_id"] == result["candidate_id"]
    assert repeated["cost"] == result["cost"]
    detail = client.get(url + f"/polish/candidates/{result['candidate_id']}")
    assert detail.status_code == 200
    approve_url = url + f"/polish/candidates/{result['candidate_id']}/approve"
    approval = {"expected_revision": row["state_revision"],
                "source_fingerprint": result["source_fingerprint"],
                "candidate_fingerprint": result["candidate_fingerprint"]}
    applied = client.post(approve_url, json=approval)
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] is True
    assert statemachine.current_status(book, 1) == "mech_check"
    assert chapters.read_prose(book, 1)["_body"] == result["candidate"]
    revision = statemachine.get_row(book, 1)["state_revision"]
    assert client.post(approve_url, json=approval).json()["applied"] is True
    assert statemachine.get_row(book, 1)["state_revision"] == revision


def test_no_change_and_discard_preserve_current_checks(home, book_env, monkeypatch):
    book, _ = book_env
    ready(book)
    monkeypatch.setattr(llm, "client", lambda: MockLLM())
    before = chapters.prose_path(book, 1).read_bytes()
    row = statemachine.get_row(book, 1)
    response = client.post(f"/api/books/{book}/chapters/1/polish", json={
        "client_request_id": str(uuid4()), "expected_revision": row["state_revision"], "mode": "auto"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "no_change"
    assert response.json()["applied"] is False
    assert chapters.prose_path(book, 1).read_bytes() == before
    assert statemachine.get_row(book, 1) == row


def test_legacy_polish_cover_requires_upgrade_before_call(home, book_env, monkeypatch):
    book, _ = book_env
    ready(book)
    target = books.book_dir(book) / "prompts" / "review" / "V2.md"
    original = prompts.get("V2", book).full_text()
    atomic.write_text(target, original.replace("interface_version: 2", "interface_version: 1"))
    mock = MockLLM()
    monkeypatch.setattr(llm, "client", lambda: mock)
    response = client.post(f"/api/books/{book}/chapters/1/polish", json={
        "client_request_id": str(uuid4()), "mode": "auto"})
    assert response.status_code == 409, response.text
    assert "PROMPT_UPGRADE_REQUIRED" in response.text
    assert not mock.calls


def test_actual_request_evidence_and_old_digest_semantics(home, book_env):
    book, _ = book_env
    mock = MockLLM()
    pipeline.generate_outline(book, 1, client=mock)
    with db.tx() as conn:
        log = conn.execute("SELECT * FROM generation_log ORDER BY id DESC LIMIT 1").fetchone()
    assert log["prompt_digest"] == prompts.get("G6", book).digest
    meta = json.loads(log["request_meta_json"])
    snapshot = home / "data" / "assembly_previews" / book / "requests" / f"{log['request_id']}.json"
    actual = json.loads(snapshot.read_text(encoding="utf-8"))
    assert actual["system"] == mock.calls[-1]["system"]
    assert actual["user"] == mock.calls[-1]["user"]
    assert meta["request_digest"]
    assert "model_stage:" not in actual["user"]
    preview = pipeline.build_preview(book, 1)
    assert preview["actual_requests"][-1]["request_id"] == log["request_id"]


def test_log_failure_prevents_model_call(home, book_env, monkeypatch):
    book, _ = book_env
    mock = MockLLM()
    monkeypatch.setattr(db, "log_generation", lambda **_: (_ for _ in ()).throw(OSError("log unavailable")))
    with pytest.raises(OSError, match="log unavailable"):
        pipeline._call(book, None, "style", "F1", {"语料样本": "测试语料。" * 100}, client=mock)
    assert not mock.calls


@pytest.mark.parametrize("endpoint", ["prose", "polish/blocks"])
def test_prose_block_snapshot_rejects_concurrent_write(home, book_env, monkeypatch, endpoint):
    """（规格06 §10）块与修订必须同锁读取，并发写不能混入快照。"""
    import threading
    from ainovel.operations import BusyError

    book, _ = book_env
    ready(book)
    original = chapters.read_prose
    before = chapters.prose_path(book, 1).read_bytes()
    row = statemachine.get_row(book, 1)
    outcomes = []

    def reading(book, no):
        prose = original(book, no)
        def concurrent_write():
            try:
                fm = {key: value for key, value in prose.items() if not key.startswith("_")}
                chapters.write_prose(book, no, fm, "插入新段。\n\n" + prose["_body"])
                outcomes.append("unexpected_write")
            except BusyError:
                outcomes.append("blocked")
        thread = threading.Thread(target=concurrent_write)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        return prose

    monkeypatch.setattr(chapters, "read_prose", reading)
    response = client.get(f"/api/books/{book}/chapters/1/{endpoint}")
    assert response.status_code == 200, response.text
    assert outcomes == ["blocked"]
    value = response.json()
    revision = value["state_revision"] if endpoint == "prose" else value["source_revision"]
    assert revision == row["state_revision"]
    assert chapters.prose_path(book, 1).read_bytes() == before
