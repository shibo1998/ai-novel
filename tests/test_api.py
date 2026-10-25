"""API 冒烟：关键路由可用性（TestClient，不触真实 LLM）。"""
from fastapi.testclient import TestClient

from ainovel import books, characters, outline, pipeline, statemachine
from ainovel.api import app
from ainovel.mockllm import MockLLM

client = TestClient(app)


def test_health_and_fts_mode(home):
    r = client.get("/api/health").json()
    assert r["ok"] and r["fts_mode"] in ("trigram", "bigram")
    assert client.get("/api/fts/mode").json()["mode"]


def test_book_crud(home):
    r = client.post("/api/books", json={"name": "接口书", "genre_template": "urban"})
    assert r.status_code == 200
    assert any(b["name"] == "接口书" for b in client.get("/api/books").json())
    ov = client.get("/api/books/接口书/overview").json()
    assert ov["meta"]["title"] == "接口书"
    assert client.delete("/api/books/接口书").status_code == 200
    assert all(b["name"] != "接口书" for b in client.get("/api/books").json())


def test_settings_masked(home):
    s = client.get("/api/settings").json()
    assert "providers" in s and "openai" in s["providers"]
    client.put("/api/settings", json={"settings": {"assembly_budget_tokens": 100}})
    assert client.get("/api/settings").json()["assembly_budget_tokens"] == 100


def test_lint_crud(home):
    client.put("/api/lint/banned_words", json={"which": "banned_words", "content": "# t\n测试禁词XYZ"})
    r = client.get("/api/lint/banned_words").json()
    assert "测试禁词XYZ" in r["content"]


def test_chapter_flow_and_export(home, book_env):
    book, _ = book_env
    mock = MockLLM()
    pipeline.generate_outline(book, 1, client=mock)
    pipeline.confirm_outline(book, 1)
    pipeline.generate_prose(book, 1, client=mock)
    pipeline.run_mech_check(book, 1, client=mock)
    pipeline.run_llm_review(book, 1, client=mock)
    pipeline.finalize(book, 1, client=mock)

    wb = client.get(f"/api/books/{book}/chapters/1").json()
    assert wb["status"] == "finalized"
    assert wb["review"]["verdict"] == "pass"
    assert wb["mech"]["issues"] is not None

    pv = client.get(f"/api/books/{book}/chapters/1/preview").json()
    assert len(pv["slots"]) == 13

    txt = client.post(f"/api/books/{book}/export/txt").text
    assert "第1章" in txt and "林寒" in txt

    zip_resp = client.post(f"/api/books/{book}/export/zip")
    assert zip_resp.status_code == 200 and zip_resp.headers["content-type"] == "application/zip"


def test_conflict_endpoints(home, book_env):
    book, _ = book_env
    from ainovel import conflicts

    characters.apply_status_updates(book, 2, [{"name": "老周", "changes": {"alive": False}}])
    _, cs = characters.apply_status_updates(book, 3, [{"name": "老周", "changes": {"alive": True}}])
    conflicts.enqueue(cs)
    items = client.get("/api/conflicts").json()
    assert len(items) == 1
    r = client.post(f"/api/conflicts/{items[0]['id']}/resolve",
                    json={"action": "keep_archive"}).json()
    assert r["ok"]
    assert client.get("/api/conflicts").json() == []
