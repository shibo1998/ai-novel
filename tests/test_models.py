"""规格12 §6：模型解析优先级、生效来源与设置迁移。

核心回归：改前 `default_model` 会压过书级 `model_stages`，导致"建书时选的规划模型
一次都没生效"——用户看到的现象是"模型定死"。
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from ainovel import books, db, models, settings


def _create(name: str, stages: dict[str, str] | None = None) -> str:
    books.create_book(name, model_stages=stages)
    return name


def test_book_stage_wins_over_fallback(home: Any) -> None:
    """（12-01）书级档位优先于兜底模型——本 bug 的正反两面都锁住。"""
    book = _create("模型书", {"plan": "deepseek-chat"})
    settings.update({"fallback_model": "gemini-3.8-flash-high"})
    ref = models.resolve(book, "plan")
    assert (ref.model, ref.source) == ("deepseek-chat", "book")


def test_resolve_chain_four_layers(home: Any) -> None:
    """（12-02）书级空 → 全局档 → 兜底 → 代码默认，逐层 source 正确。"""
    book = _create("链路书", {"plan": ""})
    settings.update({"fallback_model": "gemini-3.8-flash-high"})
    ref = models.resolve(book, "plan")
    assert (ref.model, ref.source) == ("gemini-3.8-flash-high", "fallback")

    settings.update({"model_defaults": {"plan": "gemini-2.5-flash"}})
    ref = models.resolve(book, "plan")
    assert (ref.model, ref.source) == ("gemini-2.5-flash", "global_stage")

    settings.update({"model_defaults": {"plan": ""}, "fallback_model": ""})
    ref = models.resolve(book, "plan")
    assert (ref.model, ref.source) == (books.DEFAULT_MODEL_STAGES["plan"], "default")


def test_invalid_model_name_is_skipped(home: Any) -> None:
    """（12-08）非法模型名（空格/中文）跳过该层继续下探，不抛错。"""
    book = _create("脏配置书", {"plan": "deep seek 中文"})
    settings.update({"fallback_model": "gemini-3.8-flash-high"})
    ref = models.resolve(book, "plan")
    assert (ref.model, ref.source) == ("gemini-3.8-flash-high", "fallback")


def test_missing_config_falls_back_without_raising(home: Any) -> None:
    """配置读不出来也不能让生成直接失败：退到全局层并留痕。"""
    settings.update({"fallback_model": "gemini-3.8-flash-high"})
    ref = models.resolve("不存在的书", "prose")
    assert ref.model == "gemini-3.8-flash-high"


def test_validate_stages_rejects_unknown_and_dirty() -> None:
    """建书/改配置入口的校验：未知档位与非法字符一律拒绝，空串合法。"""
    assert models.validate_stages({"plan": " deepseek-chat "}) == {"plan": "deepseek-chat"}
    assert models.validate_stages({"plan": ""}) == {"plan": ""}
    assert models.validate_stages(None) == {}
    with pytest.raises(ValueError, match="未知模型档位"):
        models.validate_stages({"outline": "x"})
    with pytest.raises(ValueError, match="非法字符"):
        models.validate_stages({"plan": "deep seek"})


def test_assembler_and_pipeline_agree(book_env: Any) -> None:
    """（12-03）装配摘要里的模型名必须等于实际调用用的模型名。

    这条守的是本 bug 的根因：两处各写一遍表达式，迟早分叉。
    """
    from ainovel import assembler, pipeline, style
    from ainovel.mockllm import MockLLM

    book, _ = book_env
    settings.update({"fallback_model": "gemini-3.8-flash-high"})
    variables = {"语料样本": style.read_corpus(book)}
    request = assembler.render_request(book, "F1", variables)
    client = MockLLM()
    pipeline._call(book, None, "style", "F1", variables, client=client)
    assert request["meta"]["model"] == client.calls[-1]["model"] == "deepseek-chat"


def test_create_book_with_model_stages(home: Any) -> None:
    """（12-04）建书即选型；不传等于代码默认档位表。"""
    book = _create("选型书", {"plan": "deepseek-chat", "prose": "gemini-2.5-pro"})
    stages = books.load_config(book)["model_stages"]
    assert stages["plan"] == "deepseek-chat" and stages["prose"] == "gemini-2.5-pro"

    other = _create("默认书")
    assert books.load_config(other)["model_stages"] == books.DEFAULT_MODEL_STAGES


def test_default_model_migrates_once(home: Any) -> None:
    """（12-06）旧键读成兜底；update 一次后旧键消失，重复执行结果不变。"""
    path = home / "data" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"default_model": "gemini-3.8-flash-high"}), encoding="utf-8")

    assert settings.load()["fallback_model"] == "gemini-3.8-flash-high"
    settings.update({})
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert "default_model" not in stored
    assert stored["fallback_model"] == "gemini-3.8-flash-high"
    settings.update({})
    assert json.loads(path.read_text(encoding="utf-8")) == stored


def test_generation_log_records_source(book_env: Any) -> None:
    """（12-07）日志记下 provider/model_source，且 model 与实际请求一致。"""
    from ainovel import pipeline, style
    from ainovel.mockllm import MockLLM

    book, _ = book_env
    client = MockLLM()
    pipeline._call(book, None, "style", "F1", {"语料样本": style.read_corpus(book)}, client=client)
    row = db.get_conn().execute(
        "SELECT * FROM generation_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["model"] == client.calls[-1]["model"]
    assert row["provider"] == "openai"
    assert row["model_source"] == "book"


def test_config_effective_is_stripped_on_write(book_env: Any) -> None:
    """（12-05）GET 返回派生字段，整体 PUT 回去后 config.json 不含该键。"""
    from fastapi.testclient import TestClient

    from ainovel.api import app

    book, _ = book_env
    client = TestClient(app)
    got = client.get(f"/api/books/{book}/config")
    cfg = got.json()
    assert cfg["model_effective"]["plan"]["model"]
    assert cfg["model_effective"]["plan"]["source"] in ("book", "global_stage", "fallback", "default")

    cfg["model_stages"]["plan"] = "gemini-2.5-flash"
    put = client.put(f"/api/books/{book}/config", json=cfg,
                     headers={"If-Match": got.headers["etag"]})
    assert put.status_code == 200
    from ainovel.paths import book_dir

    stored = json.loads(book_dir(book).joinpath("config.json").read_text(encoding="utf-8"))
    assert "model_effective" not in stored
    assert stored["model_stages"]["plan"] == "gemini-2.5-flash"
    assert put.json()["model_effective"]["plan"] == {
        "model": "gemini-2.5-flash", "source": "book", "provider": "openai"}


def test_error_brief_keeps_diagnostics_without_leaking_body() -> None:
    """（规格11 §3）失败摘要要能定位"模型不存在"，但不能把响应体原文带出去。"""
    from ainovel import llm

    brief = llm.error_brief(llm.LLMError(
        'HTTP 503: {"error":{"code":"model_not_found","message":"secret-body 泄露"},"key":"sk-xxx"}'))
    assert "503" in brief and "model_not_found" in brief
    assert "secret-body" not in brief and "sk-xxx" not in brief

    assert llm.error_brief(RuntimeError("secret-provider-auth")) == "RuntimeError"


def test_story_ideas_failure_names_the_model(book_env: Any) -> None:
    """模型不存在时，报错必须点名是哪个模型，而不是一句"请求未完成"。"""
    from types import SimpleNamespace

    from ainovel import llm, storyideas

    book, _ = book_env
    message = storyideas._model_failure_message(
        book, SimpleNamespace(fm={"model_stage": "plan"}),
        llm.LLMError('HTTP 503: {"error":{"code":"model_not_found"}}'))
    assert "deepseek-chat" in message and "model_not_found" in message
    assert "换" in message  # 给出可执行的下一步


def test_error_brief_keeps_diagnostics_without_leaking_body() -> None:
    """（规格11 §3）失败摘要要能定位"模型不存在"，但不能把响应体原文带出去。"""
    from ainovel import llm

    brief = llm.error_brief(llm.LLMError(
        'HTTP 503: {"error":{"code":"model_not_found","message":"secret-body 泄露"},"key":"sk-xxx"}'))
    assert "503" in brief and "model_not_found" in brief
    assert "secret-body" not in brief and "sk-xxx" not in brief

    assert llm.error_brief(RuntimeError("secret-provider-auth")) == "RuntimeError"


def test_story_ideas_failure_names_the_model(book_env: Any) -> None:
    """模型不存在时，报错必须点名是哪个模型，而不是一句"请求未完成"。"""
    from types import SimpleNamespace

    from ainovel import llm, storyideas

    book, _ = book_env
    message = storyideas._model_failure_message(
        book, SimpleNamespace(fm={"model_stage": "plan"}),
        llm.LLMError('HTTP 503: {"error":{"code":"model_not_found"}}'))
    assert "deepseek-chat" in message and "model_not_found" in message
    assert "换" in message  # 给出可执行的下一步


# ---------------------------------------------------------------------------
# 规格13：多接口（端点前缀）
# ---------------------------------------------------------------------------

def _two_providers() -> None:
    settings.update({"providers": {
        "openai": {"base_url": "https://a/v1", "api_key": "k1"},
        "backup": {"base_url": "https://b/v1", "api_key": "k2", "label": "备用站"},
    }})


def test_resolve_provider_prefix(home: Any) -> None:
    """（13-01）`端点:模型` 解析出端点，模型名只取冒号后半段。"""
    _two_providers()
    book = _create("多接口书", {"plan": "backup@gemini-3.8-flash-high"})
    ref = models.resolve(book, "plan")
    assert (ref.provider, ref.model, ref.source) == ("backup", "gemini-3.8-flash-high", "book")
    assert ref.qualified == "backup@gemini-3.8-flash-high"


def test_resolve_default_provider(home: Any) -> None:
    """（13-02）无前缀值落到默认接口，行为与规格12 完全一致。"""
    _two_providers()
    book = _create("默认接口书", {"plan": "deepseek-chat"})
    ref = models.resolve(book, "plan")
    assert (ref.provider, ref.model) == ("openai", "deepseek-chat")


def test_colon_model_name_is_not_a_provider(home: Any) -> None:
    """（13-03）模型名自带冒号（如 gemma4:31b）不能被误判成端点前缀。"""
    _two_providers()
    book = _create("冒号模型书", {"plan": "gemma4:31b"})
    ref = models.resolve(book, "plan")
    assert (ref.provider, ref.model, ref.source) == ("openai", "gemma4:31b", "book")


def test_validate_stages_rejects_unknown_provider(home: Any) -> None:
    """建书/改配置时，未配置的端点必须当场拒绝，而不是等到调用才炸。"""
    _two_providers()
    assert models.validate_stages({"plan": "backup@deepseek-chat"}) == {"plan": "backup@deepseek-chat"}
    # 未配置的端点前缀不当作端点，而是当成模型名的一部分（真实模型名可能含冒号）
    assert models.validate_stages({"plan": "nope:deepseek-chat"}) == {"plan": "nope:deepseek-chat"}
    with pytest.raises(ValueError, match="缺少 @ 后的模型名"):
        models.validate_stages({"plan": "backup@"})


def test_price_prefers_provider_key(home: Any) -> None:
    """（13-05）价格先按 `端点:模型` 匹配，缺失时回落裸模型名。"""
    _two_providers()
    settings.update({"prices": {
        "backup@gemini-3.8-flash-high": {"input_per_mtok_cents": 100, "output_per_mtok_cents": 200},
        "gemini-3.8-flash-high": {"input_per_mtok_cents": 1, "output_per_mtok_cents": 2},
    }})
    assert settings.cost_millicents("gemini-3.8-flash-high", 1_000_000, 0, provider="backup") == 100_000
    assert settings.cost_millicents("gemini-3.8-flash-high", 1_000_000, 0, provider="openai") == 1_000


def test_generation_log_records_provider_name(book_env: Any) -> None:
    """（13-09）日志记真实端点名，而不是恒为 openai。"""
    from ainovel import pipeline, style
    from ainovel.mockllm import MockLLM

    book, _ = book_env
    _two_providers()
    cfg = books.load_config(book)
    cfg["model_stages"] = {**cfg["model_stages"], "plan": "backup@deepseek-chat"}
    books.save_config(book, cfg)
    client = MockLLM()
    pipeline._call(book, None, "style", "F1", {"语料样本": style.read_corpus(book)}, client=client)
    row = db.get_conn().execute(
        "SELECT * FROM generation_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["provider"] == "backup"
    assert client.calls[-1]["provider"] == "backup"
    assert client.calls[-1]["model"] == "deepseek-chat"


def test_models_endpoint_per_provider(book_env: Any, monkeypatch: Any) -> None:
    """（13-06）接口不存在 → 404；存在则按该接口的 base_url 去拉清单。"""
    from fastapi.testclient import TestClient

    from ainovel import llm
    from ainovel.api import app

    _two_providers()
    client = TestClient(app)
    assert client.get("/api/models?provider=nope").status_code == 404

    seen: list[str] = []

    class FakeResp:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"data": [{"id": "m1"}]}

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, headers=None):
            seen.append(url)
            return FakeResp()

    monkeypatch.setattr(llm.httpx, "Client", lambda *a, **k: FakeSession())
    body = client.get("/api/models?provider=backup").json()
    assert body["ok"] is True and body["provider"] == "backup"
    assert seen and seen[0].startswith("https://b/v1")
