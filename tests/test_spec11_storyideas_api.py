"""（规格11 §3/6）严格候选API、版本前置与旧T1兼容，全home+Mock。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
import json
from ainovel import api, atomic, books, db, llm
import httpx

from ainovel.mockllm import MockLLM


@pytest.fixture
def case(home: Path, monkeypatch: Any) -> tuple[TestClient, str, MockLLM]:
    books.create_book("接口灵感书", genre_template="suspense", target_words=30000)
    client = MockLLM(); monkeypatch.setattr(llm, "client", lambda: client)
    return TestClient(api.app), "/api/books/接口灵感书/story-ideas", client


def test_context_is_read_only_then_generate_binds_server_genre(case: Any) -> None:
    http, base, mock = case
    response = http.get(base + "/context")
    ctx = response.json()
    assert response.status_code == 200 and response.headers["etag"] == ctx["etag"]
    assert ctx["genre_template"] == "suspense" and mock.calls == []
    response = http.post(base + "/generate", headers={"If-Match": ctx["etag"]}, json={})
    result = response.json()
    assert response.status_code == 200 and len(result["ideas"]) == 3
    assert result["context_etag"] == ctx["etag"] and result["genre_template"] == "suspense"
    assert len(mock.calls) == 1


@pytest.mark.parametrize("extra", [{"genre_template": "urban"}, {"prompt_id": "G1"}, {"count": 5}, {"preferences": 1}, {"previous_loglines": [1]}, {"previous_loglines": ["a"] * 4}, {"preferences": "a" * 2001}, {"previous_loglines": [""]}])
def test_extra_or_bad_input_does_not_call_model(case: Any, extra: Any) -> None:
    http, base, mock = case
    ctx = http.get(base + "/context").json()
    response = http.post(base + "/generate", headers={"If-Match": ctx["etag"]}, json=extra)
    assert response.status_code == 422 and mock.calls == []


def test_preconditions_and_missing_book(case: Any) -> None:
    http, base, mock = case
    assert http.get("/api/books/不存在/story-ideas/context").status_code == 404
    response = http.post(base + "/generate", json={})
    assert response.status_code == 428 and isinstance(response.json()["detail"]["code"], str)
    response = http.post(base + "/generate", headers={"If-Match": '"old"'}, json={})
    assert response.status_code == 412 and mock.calls == []


def test_model_error_is_sanitized_and_not_retried(case: Any) -> None:
    http, base, mock = case
    ctx = http.get(base + "/context").json()
    mock.fail_next = RuntimeError("secret-provider-auth")
    response = http.post(base + "/generate", headers={"If-Match": ctx["etag"]}, json={})
    assert response.status_code == 502 and "secret-provider-auth" not in response.text
    assert mock.calls == []


def test_bad_config_stable_unavailable(case: Any) -> None:
    http, base, _ = case
    atomic.write_text(books.book_dir("接口灵感书") / "config.json", "{}")
    response = http.get(base + "/context")
    assert response.status_code == 503 and isinstance(response.json()["detail"], dict)


def test_old_ai_generate_t1_still_renders_default_report(case: Any) -> None:
    http, _, mock = case
    response = http.post("/api/books/接口灵感书/ai/generate", json={"prompt_id": "T1", "variables": {"题材方向": "悬疑", "市场观察": "无数据", "对标书": "无"}})
    assert response.status_code == 200 and "报告" in response.json()["text"] and len(mock.calls) == 1


@pytest.mark.parametrize("mode", ["timeout", "missing_done", "complete"])
def test_story_endpoint_uses_one_real_http_attempt_and_requires_stream_done(case: Any, monkeypatch: Any, mode: str) -> None:
    http, base, _ = case
    ctx = http.get(base + "/context").json()
    idea = {"title": "线索守夜", "logline": "守夜人为了查清失踪案必须追查遗留物，但证人拒绝作证。",
            "synopsis": "守夜人调查遗留物并逐步发现证据之间的联系。", "protagonist": "一名守夜人",
            "core_conflict": "追查失踪案，但证人拒绝作证。", "selling_point": "封闭空间中的层层证据",
            "volume_goal": "找到失踪案的关键证人", "volume_hook": "关键证人留下新的录音。"}
    result = json.dumps({"schema_version": 1, "genre_template": "suspense", "ideas": [idea | {"title": f"方案{i}", "logline": f"守夜人{i}为了查清失踪案必须追查遗留物，但证人{i}拒绝作证。"} for i in range(3)]}, ensure_ascii=False)
    attempts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if mode == "timeout":
            raise httpx.ReadTimeout("隔离传输超时", request=request)
        body = ("data: " + json.dumps({"choices": [{"delta": {"content": result}, "finish_reason": None}]})
                + "\n\ndata: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n")
        if mode == "complete":
            body += "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(llm, "client", lambda: llm.OpenAICompatClient("https://mock.invalid/v1", "test-key"))
    monkeypatch.setattr(llm.httpx, "Client", lambda *args, **kwargs: real_client(*args, transport=transport, **kwargs))
    response = http.post(base + "/generate", headers={"If-Match": ctx["etag"]}, json={})
    expected = 200 if mode == "complete" else 502
    assert response.status_code == expected and len(attempts) == 1
    with db.tx() as conn:
        rows = conn.execute("SELECT status FROM generation_log WHERE book_id=? AND prompt_id='T1'",
                            (books.book_id("接口灵感书"),)).fetchall()
    assert len(rows) == 1 and rows[0]["status"] == ("ok" if mode == "complete" else "error")
    if mode == "complete":
        assert len(response.json()["ideas"]) == 3
