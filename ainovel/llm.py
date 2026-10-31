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

TIMEOUT_SECONDS = 300.0        # 单次读写超时（两次收到数据之间）
# 整次调用上限。httpx 的 timeout 只管"两次收到数据之间"：服务端只要持续发心跳或
# 思考分片，一次调用就能无限期挂着——批量生产里表现为"任务一直卡着、界面无进展"。
MAX_CALL_SECONDS = 600.0
RETRY_BASE_DELAY = 1.0
MAX_RETRIES = 3


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    finish_reason: str = "stop"
    model: str = ""
    provider: str = ""
    stream_complete: bool = True


class LLMError(Exception):
    pass


# 供应商错误码白名单：只放行这些可公开的诊断码，其余响应体一律不透传
# （响应体可能回显连接配置、认证信息或提示词内容）。
_SAFE_ERROR_CODES = frozenset({
    "model_not_found", "invalid_api_key", "insufficient_quota", "invalid_request_error",
    "context_length_exceeded", "rate_limit_exceeded", "content_filter", "authentication_error",
})
_HTTP_STATUS_RE = re.compile(r"HTTP (\d{3})")
_ERROR_CODE_RE = re.compile(r'"code"\s*:\s*"([A-Za-z0-9_.\-]{1,40})"')


def error_brief(error: BaseException) -> str:
    """把异常压成"可诊断但不泄密"的短摘要，供错误消息与 generation_log 使用。

    （规格11 §3、规格12）改前上层只记 `type(error).__name__`，用户看到的是
    "选题请求未完成"这种通用话，日志里也只有 `LLMError`——模型名不存在（中转站 503
    `model_not_found`）和网络超时长得一模一样，无从判断。这里只保留 HTTP 状态码与
    白名单错误码，够定位、又不把响应体原文写进日志。
    """
    text = str(error)
    parts: list[str] = []
    status = _HTTP_STATUS_RE.search(text)
    if status:
        parts.append(f"HTTP {status.group(1)}")
    code = _ERROR_CODE_RE.search(text)
    if code and code.group(1).lower() in _SAFE_ERROR_CODES:
        parts.append(code.group(1))
    return " / ".join(parts) if parts else type(error).__name__


def est_tokens(text: str) -> int:
    return round(len(re.sub(r"\s+", "", text)) * 0.75)


class OpenAICompatClient:
    name = "openai-compat"

    def __init__(self, base_url: str, api_key: str, provider: str = ""):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.provider = provider or settings.DEFAULT_PROVIDER

    def chat(self, *, model: str, system: str, user: str, temperature: float,
             stream_cb: Callable[[str], None] | None = None, retry: bool = True,
             require_complete: bool = False, provider: str = "") -> LLMResult:
        """（规格11 §3）故事候选显式单次调用并校验完整流；其它调用保留默认重试。

        provider 只对"包装型客户端"有意义（如批产的任务客户端要按端点选真实连接）；
        本类已绑定单一端点，收到也只用于回显与计费归属（规格13 §2.3）。
        """
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
            started = time.monotonic()
            chunks: list[str] = []
            finish = ""
            usage: dict[str, Any] = {}
            done = False
            with httpx.Client(timeout=TIMEOUT_SECONDS) as session:
                with session.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code >= 400:
                        body = resp.read().decode("utf-8", "replace")[:500]
                        raise LLMError(f"HTTP {resp.status_code}: {body}")
                    for line in resp.iter_lines():
                        if time.monotonic() - started > MAX_CALL_SECONDS:
                            raise LLMError(f"调用超时（超过 {int(MAX_CALL_SECONDS)} 秒未完成）")
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            done = True
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
                            reason = choices[0].get("finish_reason")
                            if reason:
                                finish = reason
            text = "".join(chunks)
            if require_complete and (not done or not finish):
                raise LLMError("模型流未收到完整结束标记")
            if not text:
                raise LLMError("模型返回空内容")
            return LLMResult(
                text=text,
                input_tokens=int(usage.get("prompt_tokens") or est_tokens(system + user)),
                output_tokens=int(usage.get("completion_tokens") or est_tokens(text)),
                finish_reason=finish or "stop",
                model=model,
                provider=provider or self.provider,
                stream_complete=done,
            )

        return _with_retry(once) if retry else once()


_RETRYABLE = (httpx.TimeoutException, httpx.TransportError, ConnectionError)


def _with_retry(fn) -> LLMResult:
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            return fn()
        except LLMError as e:
            # 总时长超时重试只会再等一轮（还可能是同一份长提示词），直接失败交人工决定。
            if str(e).startswith("调用超时"):
                raise
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


def client(provider: str = "") -> OpenAICompatClient:
    """按端点取连接（规格13 §2.3）：省略 = 默认接口 openai，兼容旧调用。

    端点不存在或没有密钥都直接报错，不静默回落到默认接口——那会悄悄换到
    另一个中转站和另一份账单。
    """
    key = (provider or settings.DEFAULT_PROVIDER).strip().lower()
    try:
        prov = settings.provider(key)
    except KeyError as error:
        raise LLMError(str(error).strip("'\"")) from None
    if not prov.get("api_key"):
        raise LLMError(f"接口 {key} 未配置密钥（设置页填写）")
    return OpenAICompatClient(prov["base_url"], prov["api_key"], provider=key)


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
