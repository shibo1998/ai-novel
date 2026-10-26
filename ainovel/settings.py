"""全局设置：API keys（UI 掩码显示）、模型映射（含 base URL）、单价、lint 阈值。

存于 data/settings.json。文件为单一真相源；本模块只做读写与默认值合并。
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any

from . import atomic
from .paths import data_dir

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

DEFAULTS: dict[str, Any] = {
    # 只支持 OpenAI 兼容协议（官方 API / 第三方中转站通用）
    "providers": {
        "openai": {
            "api_key": "",
            "base_url": "https://api.deepseek.com/v1",
        },
    },
    # 一键指定所有档位的模型名；留空则用各书 config.json 的 model_stages
    "default_model": "",
    # 单价：每百万 token 的"分"（cost_cents 全程用整数分，避免浮点误差）
    "prices": {
        "deepseek-chat": {"input_per_mtok_cents": 27, "output_per_mtok_cents": 110},
        "deepseek-reasoner": {"input_per_mtok_cents": 55, "output_per_mtok_cents": 219},
        "gemini-2.5-pro": {"input_per_mtok_cents": 125, "output_per_mtok_cents": 1000},
        "gemini-2.5-flash": {"input_per_mtok_cents": 3, "output_per_mtok_cents": 15},
        # 中转站常见的模型别名（官方名 + 常见变体）。用不到的可在设置页删掉，
        # 自定义模型名请自行补一行——否则该模型成本按 0 计并记告警日志。
        "gemini-3.8-flash-high": {"input_per_mtok_cents": 3, "output_per_mtok_cents": 15},
        "gemini-2.5-flash-high": {"input_per_mtok_cents": 3, "output_per_mtok_cents": 15},
    },
    # 机械检查阈值（M0 设置页可调）
    "mechcheck": {
        "sentence_comma_max": 2,          # M-05
        "exclaim_per_1k_max": 2,          # M-06
        "ellipsis_per_1k_max": 3,         # M-06
        "flat_sentence_window": 4,        # M-07
        "flat_sentence_ratio": 0.2,       # M-07 长度差 <20%
        "paragraph_max_chars": 120,       # M-08
        "repeat_para_prefix_n": 3,        # M-09
        "ending_window_chars": 200,       # M-10
        "first_person_ratio_max": 0.08,   # M-11
    },
    # 装配预算（token 估算值；估算=中文字符×0.75）
    "assembly_budget_tokens": 32000,
}

# 敏感词命中禁止自动改写（规格03 §3 红线），不做配置项。


def _settings_path():
    return data_dir() / "settings.json"


def _validate_structure(s: dict[str, Any]) -> None:
    """结构校验（P1：写坏 settings.json 会让 masked()/llm.client() 持续 500，UI 无法自愈）。"""
    provs = s.get("providers")
    if provs is not None:
        if not isinstance(provs, dict):
            raise ValueError("providers 必须是对象")
        for name, p in provs.items():
            if not isinstance(p, dict):
                raise ValueError(f"providers.{name} 必须是对象")
    prices = s.get("prices")
    if prices is not None and not isinstance(prices, dict):
        raise ValueError("prices 必须是对象")
    mech = s.get("mechcheck")
    if mech is not None and not isinstance(mech, dict):
        raise ValueError("mechcheck 必须是对象")


def load() -> dict[str, Any]:
    path = _settings_path()
    merged = json.loads(json.dumps(DEFAULTS))  # deep copy
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            _validate_structure(stored)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            # 手改坏文件不致命：回退默认值并留痕，避免整个应用起不来
            logger.warning("settings.json 结构非法，已回退默认值: %s", e)
            stored = {}
        _deep_merge(merged, stored)
    else:
        # 首次运行：把默认配置落盘，保证 data/settings.json 始终存在、可手改
        try:
            data_dir().mkdir(parents=True, exist_ok=True)
            atomic.write_text(path, json.dumps(merged, ensure_ascii=False, indent=2))
        except OSError:
            pass  # 只读环境（如打包内资源目录）下允许不落盘，用内存默认值
    return merged


def _deep_merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


MASK_PLACEHOLDER = "已配置（留空保持不变…）"


def _guard_masked_keys(partial: dict[str, Any], current: dict[str, Any]) -> None:
    """拒绝把掩码占位串当成真 key 落盘（P0 回归：UI 二次保存会写坏真实 key）。

    背景：masked() 把 api_key 换成占位串给 UI；若前端把该串原样 PUT 回来，
    深合并会用它覆盖真实 key，导致 key 永久丢失且 has_key 仍为 true（UI 无感）。
    这里在落盘前兜底：来值等于占位串 → 删除该字段，保持原 key 不变。
    """
    provs = partial.get("providers")
    if not isinstance(provs, dict):
        return
    for name, incoming in provs.items():
        if not isinstance(incoming, dict):
            continue
        key = incoming.get("api_key")
        if isinstance(key, str) and (key == MASK_PLACEHOLDER or ("…" in key and "已配置" in key)):
            logger.warning("providers.%s.api_key 收到掩码占位串，已忽略以保留原 key", name)
            incoming.pop("api_key", None)


def save(settings: dict[str, Any]) -> None:
    with _LOCK:
        data_dir().mkdir(parents=True, exist_ok=True)
        atomic.write_text(_settings_path(), json.dumps(settings, ensure_ascii=False, indent=2))


def update(partial: dict[str, Any]) -> dict[str, Any]:
    """部分更新（PUT 语义：给什么覆盖什么），校验后落盘并返回完整设置。"""
    current = load()
    _guard_masked_keys(partial, current)  # 掩码占位串不得覆盖真 key
    _deep_merge(current, partial)
    _validate_structure(current)  # 非法结构直接拒绝，绝不落盘
    save(current)
    return current


def masked() -> dict[str, Any]:
    """给 UI 的掩码视图：api_key 不回显任何原文（前端留空即保持不变）。"""
    s = load()
    for prov in s.get("providers", {}).values():
        if not isinstance(prov, dict):
            continue
        key = prov.get("api_key", "")
        prov["api_key"] = MASK_PLACEHOLDER if key else ""
        prov["has_key"] = bool(key)
    return s


_WARNED_MODELS: set[str] = set()


def cost_cents(model: str, input_tokens: int, output_tokens: int) -> int:
    """按单价折算成本（规格03 §4）。

    未配置单价的模型不再静默返回 0 —— 那会让「单章 >1 元标黄」验收线永远不触发，
    成本汇总也永远是 0（审计发现的真实问题：中转站自定义模型名不在默认单价表里）。
    现在：首次遇到未知模型记一条告警日志，便于用户在设置页补单价；返回值仍是 0，
    但调用方与日志能区分「真的 0 成本」和「不知道怎么算」。
    """
    prices = load().get("prices", {})
    p = prices.get(model)
    if not p:
        if model not in _WARNED_MODELS:
            _WARNED_MODELS.add(model)
            logger.warning(
                "模型 %r 未配置单价，本次成本按 0 计（请在设置页「单价」补上，否则"
                "成本汇总与 >1 元标黄验收线不生效）", model)
        return 0
    return round(
        input_tokens / 1_000_000 * p.get("input_per_mtok_cents", 0)
        + output_tokens / 1_000_000 * p.get("output_per_mtok_cents", 0)
    )
