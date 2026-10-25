"""LLM 工具：JSON 抽取 / 重试判定 / Mock。"""
import pytest

from ainovel import llm
from ainovel.mockllm import MockLLM, make_prose


def test_extract_json_plain():
    assert llm.extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced_with_prose():
    text = '好的，以下是结果：\n```json\n{"verdict": "pass", "scores": {"plot": 90}}\n```\n以上。'
    assert llm.extract_json(text)["verdict"] == "pass"


def test_extract_json_nested_and_trailing():
    text = '前缀 {"issues": [{"q": "他\\"说\\""}], "n": [1,2]} 后缀'
    obj = llm.extract_json(text)
    assert obj["issues"][0]["q"] == '他"说"'
    assert obj["n"] == [1, 2]


def test_extract_json_failure():
    with pytest.raises(llm.LLMError):
        llm.extract_json("完全没有 JSON")


def test_retry_on_transient(monkeypatch):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise llm.httpx.TimeoutException("timeout")
        return llm.LLMResult(text="ok", input_tokens=1, output_tokens=1)

    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    res = llm._with_retry(fn)
    assert res.text == "ok"
    assert calls["n"] == 3  # 退避重试满 3 次后成功


def test_no_retry_on_4xx(monkeypatch):
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise llm.LLMError("HTTP 400: bad request")

    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    with pytest.raises(llm.LLMError):
        llm._with_retry(fn)
    assert calls["n"] == 1  # 4xx 不重试


def test_retry_exhaustion(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    with pytest.raises(llm.LLMError, match="3 次"):
        llm._with_retry(lambda: (_ for _ in ()).throw(llm.httpx.ConnectError("refused")))


def test_mock_make_prose_in_range():
    body = make_prose((2200, 2800))
    from ainovel import mechcheck

    n = mechcheck.count_words(body)
    assert 2200 <= n <= 2800, n
    assert not mechcheck.has_block(mechcheck.check(body, word_range=[2200, 2800]))


def test_single_openai_client(home):
    """唯一 OpenAI 兼容端点：base_url/key 从 providers.openai 读取。"""
    from ainovel import settings

    with pytest.raises(llm.LLMError, match="API key"):
        llm.client()

    settings.update({
        "providers": {"openai": {"api_key": "sk-xxx", "base_url": "https://relay.example.com/v1"}},
    })
    cli = llm.client()
    assert isinstance(cli, llm.OpenAICompatClient)
    assert cli.base_url == "https://relay.example.com/v1"
    assert cli.api_key == "sk-xxx"
