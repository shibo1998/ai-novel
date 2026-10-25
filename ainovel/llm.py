"""LLM 客户端：DeepSeek（OpenAI 兼容）/ Gemini（原生 SSE），流式 + 指数退避重试 + Mock。

异常处理（规格03 §3）：API 超时/限流 → 指数退避 ×3；全程记 generation_log。
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from . import settings

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 300.0
RETRY_BASE_DELAY = 1.0
MAX_RETRIES = 3


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    finish_reason: str = "stop"
    model: str = ""


class LLMError(Exception):
    pass


def est_tokens(text: str) -> int:
    return round(len(re.sub(r"\s+", "", text)) * 0.75)


# ---------------------------------------------------------------------------
# OpenAI 兼容（DeepSeek 等）
# ---------------------------------------------------------------------------

class OpenAICompatClient:
    name = "openai-compat"

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def chat(self, *, model: str, system: str, user: str, temperature: float,
             stream_cb: Callable[[str], None] | None = None) -> LLMResult:
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        url = f"{self.base_url}/chat/completions"

        def once() -> LLMResult:
            chunks: list[str] = []
            finish = "stop"
            usage: dict[str, Any] = {}
            with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
                with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code >= 400:
                        body = resp.read().decode("utf-8", "replace")[:500]
                        raise LLMError(f"HTTP {resp.status_code}: {body}")
                    for line in resp.iter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("usage"):
                            usage = obj["usage"]
                        choices = obj.get("choices") or []
                        if choices:
                            delta = choices[0].get("delta") or {}
                            piece = delta.get("content") or ""
                            if piece:
                                chunks.append(piece)
                                if stream_cb:
                                    stream_cb(piece)
                            fr = choices[0].get("finish_reason")
                            if fr:
                                finish = fr
            text = "".join(chunks)
            if not text:
                raise LLMError("模型返回空内容")
            return LLMResult(
                text=text,
                input_tokens=int(usage.get("prompt_tokens") or est_tokens(system + user)),
                output_tokens=int(usage.get("completion_tokens") or est_tokens(text)),
                finish_reason=finish,
                model=model,
            )

        return _with_retry(once)


# ---------------------------------------------------------------------------
# 重试 / 工厂
# ---------------------------------------------------------------------------

_RETRYABLE = (httpx.TimeoutException, httpx.TransportError, ConnectionError)


def _with_retry(fn) -> LLMResult:
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return fn()
        except LLMError as e:
            # 429/5xx/限流可重试；其余 4xx 参数类错误不重试
            m = re.search(r"HTTP (\d{3})", str(e))
            if m and m.group(1) != "429" and 400 <= int(m.group(1)) < 500:
                raise
            last_err = e
        except _RETRYABLE as e:
            last_err = e
        if attempt < MAX_RETRIES - 1:
            delay = RETRY_BASE_DELAY * (2**attempt)
            logger.warning("LLM 调用失败（第 %d/%d 次），%.0fs 后重试: %s",
                           attempt + 1, MAX_RETRIES, delay, last_err)
            time.sleep(delay)
    raise LLMError(f"重试 {MAX_RETRIES} 次后仍失败: {last_err}")


def client() -> OpenAICompatClient:
    """唯一连接：OpenAI 兼容协议（官方 / 第三方中转站通用）。"""
    s = settings.load()
    prov = s.get("providers", {}).get("openai")
    if not isinstance(prov, dict) or not prov.get("api_key"):
        raise LLMError("未配置 API key（设置页或 data/settings.json 填写）")
    return OpenAICompatClient(prov["base_url"], prov["api_key"])


# ---------------------------------------------------------------------------
# JSON 抽取（V1/V3/V4 等结构化输出用；解析失败由管线回喂重试）
# ---------------------------------------------------------------------------

def extract_json(text: str) -> Any:
    """剥掉 markdown 代码栅栏，取第一个平衡的 JSON 对象/数组。"""
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
    if fence:
        t = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start = t.find(opener)
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(t)):
            c = t[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == opener:
                depth += 1
            elif c == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i + 1])
                    except json.JSONDecodeError as e:
                        raise LLMError(f"JSON 解析失败: {e}")
    raise LLMError("输出中未找到 JSON 对象")
