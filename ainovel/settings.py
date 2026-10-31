"""全局设置：API keys（UI 掩码显示）、模型映射（含 base URL）、单价、lint 阈值。

存于 data/settings.json。文件为单一真相源；本模块只做读写与默认值合并。
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from . import atomic
from .paths import data_dir

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()

DEFAULTS: dict[str, Any] = {
    # 只支持 OpenAI 兼容协议（官方 API / 第三方中转站通用）。
    # 可以有多个接口（规格13）：键是端点名，档位值写 "端点名:模型名" 即可走不同 URL/密钥。
    # 默认端点固定叫 openai：旧配置、旧的无前缀模型值都落到它身上，不需要迁移。
    "providers": {
        "openai": {
            "label": "默认接口",
            "api_key": "",
            "base_url": "https://api.deepseek.com/v1",
        },
    },
    # 全局兜底模型：只在书级档位与全局档位都没配时才用（规格12 §2.2）。
    # 原 default_model 语义是"覆盖一切"，与书级 model_stages 冲突，已降级改名。
    "fallback_model": "",
    # 全局五档默认；留空则该档继续向下探到 fallback_model
    "model_defaults": {"plan": "", "prose": "", "polish": "", "review": "", "longctx": ""},
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
        "ai_flavor_block": 45,            # M-12 历史强提示阈值，仅保留来源标签，实际不阻断（规格06 §3）
        "ai_flavor_warn": 60,             # M-12 文风建议阈值，不代表AI生成概率
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
        if not provs:
            raise ValueError("providers 至少要有一个接口")
        for name, p in provs.items():
            if not PROVIDER_NAME_RE.match(str(name)):
                raise ValueError(f"接口名 {name!r} 非法：只用小写字母开头，后接小写字母/数字/_/-，最长 24 字符")
            if not isinstance(p, dict):
                raise ValueError(f"providers.{name} 必须是对象")
            base = p.get("base_url")
            if not isinstance(base, str) or not base.strip():
                raise ValueError(f"providers.{name}.base_url 必须是非空字符串")
            if p.get("label") is not None and not isinstance(p.get("label"), str):
                raise ValueError(f"providers.{name}.label 必须是字符串")
    prices = s.get("prices")
    if prices is not None and not isinstance(prices, dict):
        raise ValueError("prices 必须是对象")
    mech = s.get("mechcheck")
    if mech is not None and not isinstance(mech, dict):
        raise ValueError("mechcheck 必须是对象")
    defaults = s.get("model_defaults")
    if defaults is not None:
        if not isinstance(defaults, dict):
            raise ValueError("model_defaults 必须是对象")
        for stage, value in defaults.items():
            if value is not None and not isinstance(value, str):
                raise ValueError(f"model_defaults.{stage} 必须是字符串")
    fallback = s.get("fallback_model")
    if fallback is not None and not isinstance(fallback, str):
        raise ValueError("fallback_model 必须是字符串")


def _absorb_legacy(merged: dict[str, Any]) -> None:
    """把旧键 default_model 降级搬进 fallback_model（规格12 §2.2 / §4）。

    旧语义是"覆盖一切"，新语义是"最后兜底"，所以只在新键未填时搬值；
    随后丢弃旧键，避免 masked() 把它回显给 UI、让人以为还有两个兜底模型。
    幂等：对同一份数据重复执行结果一致。
    """
    legacy = merged.pop("default_model", None)
    if isinstance(legacy, str) and legacy.strip() and not str(merged.get("fallback_model") or "").strip():
        merged["fallback_model"] = legacy.strip()
        logger.info("settings.json: default_model 已迁移为 fallback_model（语义降为兜底）")


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
    _absorb_legacy(merged)  # 规格12 §2.2：旧 default_model → fallback_model
    return merged


def _deep_merge(base: dict, override: dict) -> None:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


# 端点名规则（规格13 §2.1）：小写字母开头，后接小写字母/数字/_/-，最长 24 字符。
PROVIDER_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
DEFAULT_PROVIDER = "openai"


def providers() -> dict[str, dict[str, Any]]:
    """全部接口配置（含密钥，仅供后端使用；给 UI 一律走 masked()）。"""
    raw = load().get("providers")
    return raw if isinstance(raw, dict) else {}


def provider(name: str = "") -> dict[str, Any]:
    """取指定接口；name 为空取默认接口。不存在直接报错，不静默回落。"""
    key = (name or DEFAULT_PROVIDER).strip().lower()
    found = providers().get(key)
    if not isinstance(found, dict):
        raise KeyError(f"接口 {key} 不存在，请先在设置页添加")
    return found


def provider_names() -> list[str]:
    return sorted(providers())


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
    """部分更新（PUT 语义：给什么覆盖什么），校验后落盘并返回完整设置。

    （规格12 §2.2）旧客户端仍可能提交 default_model：映射为 fallback_model 后丢弃旧键，
    保证落盘文件里只有一套兜底语义。
    """
    current = load()
    partial = dict(partial)
    if "default_model" in partial and "fallback_model" not in partial:
        partial["fallback_model"] = partial["default_model"]
    partial.pop("default_model", None)
    # 深合并只能加/改，删不掉接口；删除必须显式列出来（规格13 §3）。
    removals = partial.pop("remove_providers", None)
    _guard_masked_keys(partial, current)  # 掩码占位串不得覆盖真 key
    _deep_merge(current, partial)
    if removals is not None:
        if not isinstance(removals, list):
            raise ValueError("remove_providers 必须是数组")
        provs = current.get("providers")
        if isinstance(provs, dict):
            for name in removals:
                if str(name) == DEFAULT_PROVIDER:
                    raise ValueError(f"默认接口 {DEFAULT_PROVIDER} 不能删除")
                provs.pop(str(name), None)
    current.pop("default_model", None)
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


def cost_millicents(model: str, input_tokens: int, output_tokens: int,
                    provider: str = "") -> int:
    """按单价折算成本，单位是**千分之一分**（毫分）。

    原来按整数分记账，且是**每次调用各自四舍五入**：gemini-flash 单价 3/15 分
    每百万 token，一次调用约 7.7k 输入 + 4.4k 输出只值 0.09 分，round() 每次都
    抹成 0。第 12 章 19 次调用累计 1.68 分，记下来的是 0 —— 成本汇总恒为 0、
    「单章 >1 元标黄」永不触发，表面上看还像「真的没花钱」，是典型的静默失效。
    毫分口径下同一章读到 1683，展示时再降到分/元。

    未配置单价的模型不静默返回 0：首次遇到记一条告警，便于在设置页补单价。
    """
    prices = load().get("prices", {})
    # 先按 "端点@模型" 精确匹配（不同中转站同名模型单价可能不同），再回落裸模型名。
    p = prices.get(f"{provider}@{model}") if provider else None
    if not p:
        p = prices.get(model)
    if not p:
        key = f"{provider}@{model}" if provider else model
        if key not in _WARNED_MODELS:
            _WARNED_MODELS.add(key)
            logger.warning(
                "模型 %r 未配置单价，本次成本按 0 计（请在设置页「单价」补一行 "
                "\"端点@模型\" 或 \"模型\"，否则成本汇总与 >1 元标黄验收线不生效）", key)
        return 0
    return round(
        (input_tokens * p.get("input_per_mtok_cents", 0)
         + output_tokens * p.get("output_per_mtok_cents", 0)) / 1000
    )


def cost_cents(model: str, input_tokens: int, output_tokens: int) -> int:
    """整数分（兼容旧调用与旧字段）。精度损失由 cost_millicents 承担。"""
    return round(cost_millicents(model, input_tokens, output_tokens) / 1000)
