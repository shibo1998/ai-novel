"""模型解析：书级档位优先的五层下探（规格12 §2.3）。

改前根因：`pipeline._call` 与 `assembler.render_request` 各写了一遍
`settings.default_model or cfg.model_stages[stage]`——全局默认模型**优先于**书级档位，
于是 `books/<书名>/config.json` 里的 `model_stages` 从未生效（实测 default_model 非空时，
plan 档配的 deepseek-chat 一次都没跑过）。

本模块是该规则**唯一**实现：两处调用点都必须走 `resolve()`，否则装配预览里记的模型名
会和真实请求对不上，request_digest 与审计链一起失真。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from . import books, settings

logger = logging.getLogger(__name__)

# 默认端点名（规格13 §2.1）：无前缀的模型值都落到它身上，旧数据因此零迁移。
DEFAULT_PROVIDER = settings.DEFAULT_PROVIDER

# 模型名的合法字符集：中转站的模型名可能带 `-` `.` `_` `/`，也可能带 `:` 前缀。
# 中文/全角/空白一律视为配置错误——发出去只会得到一句难读的供应商 400。
MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9._:\-/]+$")

# 最后一层硬兜底：保证 resolve() 永不返回空串（空 model 会被当成"没填"发出去）。
LAST_RESORT = "deepseek-chat"

STAGES: tuple[str, ...] = tuple(books.DEFAULT_MODEL_STAGES)


@dataclass(frozen=True)
class ModelRef:
    """一次解析的结果：走哪个接口、发给它什么模型名、这个值来自哪一层。

    raw 保留配置里的原始写法（可能带 `端点:` 前缀），用于价格查找与诊断。
    """

    model: str
    source: str                    # book | global_stage | fallback | default
    provider: str = DEFAULT_PROVIDER
    raw: str = ""

    @property
    def qualified(self) -> str:
        """规范化的 `端点@模型`，供日志与 UI 显示。"""
        return f"{self.provider}@{self.model}"


def _parse(value: Any, *, layer: str, stage: str,
           known: dict[str, Any]) -> tuple[str, str] | None:
    """解析 `[端点:]模型`。非空且合法才返回 (端点, 模型)，否则记 warning 返回 None。

    不抛错是有意的：一个手抖的空格不该让整章生成卡死；但也不能静默——
    跳过必须留下可查的日志，否则又变成"改了不起作用"。
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    # 分隔符用 `@` 而不是 `:`——中转站真有 `gemma4:31b`、`nemotron-3-nano:30b` 这类
    # 自带冒号的模型名，用冒号会把它们误判成端点前缀。
    head, sep, tail = raw.partition("@")
    if sep and head.strip().lower() in known:
        provider, model = head.strip().lower(), tail.strip()
        if not model:
            logger.warning("模型值 %r 缺少 @ 后的模型名（%s 层 / %s 档），已跳过该层",
                           value, layer, stage)
            return None
    else:
        provider, model = DEFAULT_PROVIDER, raw
    if not MODEL_NAME_RE.match(model):
        logger.warning("模型名非法（%s 层 / %s 档）：%r 已跳过该层", layer, stage, value)
        return None
    return provider, model


def resolve(book: str | None, stage: str, cfg: dict[str, Any] | None = None) -> ModelRef:
    """按规格12 §2.3 的五层顺序取第一个非空且合法的模型名。

    cfg 可传入已读好的书级配置，避免同一次调用里重复读盘（assembler/pipeline 都已有 cfg）。
    """
    s = settings.load()
    if cfg is None and book:
        try:
            cfg = books.load_config(book)
        except (FileNotFoundError, ValueError, OSError) as e:
            # 配置读不出来不该让生成直接失败：退到全局层，并留痕。
            logger.warning("读取 %s 的 config.json 失败，模型解析跳过书级层：%s", book, e)
            cfg = {}

    stages = (cfg or {}).get("model_stages")
    if not isinstance(stages, dict):
        stages = {}
    global_stages = s.get("model_defaults")
    if not isinstance(global_stages, dict):
        global_stages = {}

    known = s.get("providers")
    if not isinstance(known, dict):
        known = {}

    for layer, source, value in (
        ("book", "book", stages.get(stage)),
        ("global_stage", "global_stage", global_stages.get(stage)),
        ("fallback", "fallback", s.get("fallback_model")),
        ("default", "default", books.DEFAULT_MODEL_STAGES.get(stage)),
    ):
        parsed = _parse(value, layer=layer, stage=stage, known=known)
        if parsed:
            provider, model = parsed
            return ModelRef(model=model, source=source, provider=provider,
                            raw=str(value).strip())
    return ModelRef(model=LAST_RESORT, source="default")


def effective(book: str, cfg: dict[str, Any] | None = None) -> dict[str, dict[str, str]]:
    """五档的实际生效模型与来源（规格12 §3：GET /config 的只读派生字段）。

    派生字段不落盘——PUT 回传时必须剥离，否则会污染 config.json。
    """
    result: dict[str, dict[str, str]] = {}
    for stage in STAGES:
        ref = resolve(book, stage, cfg)
        result[stage] = {"model": ref.model, "source": ref.source, "provider": ref.provider}
    return result


def validate_stages(payload: Any) -> dict[str, str]:
    """校验建书 / 改配置传入的 model_stages；返回清洗后的副本。

    允许空串（= 清除该书级该档配置，规格12 §2.1），但拒绝未知档位与非法模型名。
    """
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise ValueError("model_stages 必须是对象")
    known = settings.providers()
    cleaned: dict[str, str] = {}
    for stage, value in payload.items():
        if stage not in STAGES:
            raise ValueError(f"未知模型档位 {stage!r}，只支持 {'/'.join(STAGES)}")
        if value is None:
            cleaned[stage] = ""
            continue
        if not isinstance(value, str):
            raise ValueError(f"model_stages.{stage} 必须是字符串")
        name = value.strip()
        if not name:
            cleaned[stage] = ""
            continue
        head, sep, tail = name.partition("@")
        if sep and head.strip().lower() in known:
            if not tail.strip():
                raise ValueError(f"model_stages.{stage} 缺少 @ 后的模型名")
            model = tail.strip()
        else:
            model = name
        if not MODEL_NAME_RE.match(model):
            raise ValueError(f"model_stages.{stage} 含非法字符：{value!r}")
        cleaned[stage] = name
    return cleaned
