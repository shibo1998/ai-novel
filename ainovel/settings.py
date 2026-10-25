"""全局设置：API keys（UI 掩码显示）、模型映射（含 base URL）、单价、lint 阈值。

存于 data/settings.json。文件为单一真相源；本模块只做读写与默认值合并。
"""
from __future__ import annotations

import json
import threading
from typing import Any

from . import atomic
from .paths import data_dir

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


def load() -> dict[str, Any]:
    path = _settings_path()
    merged = json.loads(json.dumps(DEFAULTS))  # deep copy
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
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


def save(settings: dict[str, Any]) -> None:
    with _LOCK:
        data_dir().mkdir(parents=True, exist_ok=True)
        atomic.write_text(_settings_path(), json.dumps(settings, ensure_ascii=False, indent=2))


def update(partial: dict[str, Any]) -> dict[str, Any]:
    """部分更新（PUT 语义：给什么覆盖什么），落盘后返回完整设置。"""
    current = load()
    _deep_merge(current, partial)
    save(current)
    return current


def masked() -> dict[str, Any]:
    """给 UI 的掩码视图：api_key 只保留前 4 位 + 长度。"""
    s = load()
    for prov in s.get("providers", {}).values():
        key = prov.get("api_key", "")
        prov["api_key"] = (key[:4] + "…" + f"({len(key)}位)") if key else ""
        prov["has_key"] = bool(key)
    return s


def cost_cents(model: str, input_tokens: int, output_tokens: int) -> int:
    prices = load().get("prices", {})
    p = prices.get(model)
    if not p:
        return 0
    return round(
        input_tokens / 1_000_000 * p.get("input_per_mtok_cents", 0)
        + output_tokens / 1_000_000 * p.get("output_per_mtok_cents", 0)
    )
