"""AI 味确定性检测器（规格05 F-02 / §3）：纯规则、零 token、可解释、只报不改。

为什么要有它：
- V1 审校的 `ai_flavor` 是 LLM 主观分——不可解释（说不出哪句像 AI）、不可回归
  （换模型就漂）、可被讨好（模型倾向给高分）。规则检测器的分数是可复现的客观证据。
- 它只输出「命中清单 + 干净度分」，不改正文：改写仍由 V2/V5 做，且改写后要重新过机检。

方向约定（踩过坑）：`score` 是**干净度**，越高越干净，与 V1 的 `ai_flavor` 同向。
"""

from __future__ import annotations

import bisect
import math
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import yaml

from .paths import lint_dir

CONFIG_NAME = "deslop.yaml"

# 层级权重默认值（可被 lint/deslop.yaml 覆盖）
DEFAULT_WEIGHTS = {"L1": 12, "L2": 5, "L3": 2, "L4": 8}
DEFAULT_SATURATION_K = 36.0
MIN_BODY_CHARS = 100


class ConfigError(Exception):
    """规则参数写坏（YAML 语法错/结构错）。抛出来，不静默兜底（规格05 P-4）。"""


@dataclass
class DeslopHit:
    rule: str
    level: str        # blocking | advisory
    line: int
    quote: str
    weight: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "level": self.level, "line": self.line,
                "quote": self.quote, "weight": self.weight, "message": self.message}


@dataclass
class DeslopResult:
    score: int = 100
    hits: list[DeslopHit] = field(default_factory=list)
    ineffective: list[dict[str, str]] = field(default_factory=list)
    word_count: int = 0
    density_penalty: float = 0.0
    raw_penalty: float = 0.0

    @property
    def blocking(self) -> list[DeslopHit]:
        return [h for h in self.hits if h.level == "blocking"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "hits": [h.to_dict() for h in self.hits],
            "ineffective": self.ineffective,
            "word_count": self.word_count,
            "hits_count": {"blocking": len(self.blocking),
                           "advisory": len(self.hits) - len(self.blocking)},
            "density_penalty": round(self.density_penalty, 2),
        }

    def top_hits(self, n: int = 8) -> str:
        """给 V1/V2 提示词用的紧凑摘要：`D-05 第12行 「——」`。"""
        lines = []
        for h in self.hits[:n]:
            quote = (h.quote or "").replace("\n", " ")[:20]
            lines.append(f"{h.rule} 第{h.line}行 「{quote}」")
        return "\n".join(lines) or "（无命中）"


# ---------------------------------------------------------------------------
# 规则定义（元数据 + 检测函数）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RuleSpec:
    id: str
    name: str
    level_tag: str      # L1 / L2 / L3 / L4
    default_level: str  # blocking | advisory


RULES: tuple[RuleSpec, ...] = (
    RuleSpec("D-01", "否定对举", "L4", "blocking"),
    RuleSpec("D-02", "反向否定", "L4", "advisory"),
    RuleSpec("D-03", "声音反差", "L4", "blocking"),
    RuleSpec("D-04", "否定排比", "L4", "blocking"),
    RuleSpec("D-05", "破折号", "L1", "blocking"),
    RuleSpec("D-06", "升华式收尾", "L4", "blocking"),
    RuleSpec("D-07", "总结式收尾", "L2", "advisory"),
    RuleSpec("D-08", "三段排比", "L4", "advisory"),
    RuleSpec("D-09", "微动作套式", "L1", "advisory"),
    RuleSpec("D-10", "套式反应", "L2", "advisory"),
    RuleSpec("D-11", "动作清单", "L4", "advisory"),
    RuleSpec("D-12", "抽象总结", "L2", "advisory"),
    RuleSpec("D-13", "陈词密度", "L3", "advisory"),
    RuleSpec("D-14", "比喻密度", "L3", "advisory"),
    RuleSpec("D-15", "因果链套式", "L2", "advisory"),
    RuleSpec("D-16", "系统提示腔", "L1", "blocking"),
    RuleSpec("D-17", "电报体", "L3", "advisory"),
    RuleSpec("D-18", "连接词稀薄", "L3", "advisory"),
    RuleSpec("D-19", "句号断续", "L3", "blocking"),
    RuleSpec("D-20", "超长段", "L1", "advisory"),
    RuleSpec("D-21", "引号强调腔", "L2", "advisory"),
)

RULES_BY_ID = {r.id: r for r in RULES}

_DEFAULT_PRESERVE = [
    r"[「『“][^」』”]*[」』”]",
    r'"[^"\n]{1,200}"',          # 直引号对话（英文键盘输入时常见，同样冻结）
    r"\d+(?:\.\d+)?\s*(?:米|里|丈|尺|年|月|日|天|人|个|次|成|层|级|阶|品|枚|柄|把)",
    r"【[^】]{1,20}】",
    r"《[^》]{1,40}》",
    r"(?:像|如同|仿佛)[^。！？]{6,40}[。！？]",
]

_DEFAULT_RULES: dict[str, dict[str, Any]] = {
    "D-01": {"enabled": True, "level": "blocking"},
    "D-02": {"enabled": True, "level": "advisory"},
    "D-03": {"enabled": True, "level": "blocking"},
    "D-04": {"enabled": True, "level": "blocking", "run": 3},
    "D-05": {"enabled": True, "level": "blocking"},
    "D-06": {"enabled": True, "level": "blocking", "tail_chars": 200},
    "D-07": {"enabled": True, "level": "advisory"},
    "D-08": {"enabled": True, "level": "advisory", "run": 3, "length_gap": 0.15},
    "D-09": {"enabled": True, "level": "advisory", "density": {"count": 5, "per_1000": 6}},
    "D-10": {"enabled": True, "level": "advisory", "density": {"count": 4, "per_1500": 4}},
    "D-11": {"enabled": True, "level": "advisory"},
    "D-12": {"enabled": True, "level": "advisory", "density": {"count": 3, "per_4000": 3}},
    "D-13": {"enabled": True, "level": "advisory", "max_per_chapter": 2},
    "D-14": {"enabled": True, "level": "advisory", "density": {"count": 7, "per_3000": 7}},
    "D-15": {"enabled": True, "level": "advisory"},
    "D-16": {"enabled": True, "level": "blocking"},
    "D-17": {"enabled": True, "level": "advisory", "run": 6, "max_len": 8},
    "D-18": {"enabled": True, "level": "advisory", "min_per_100": 1.5},
    "D-19": {"enabled": True, "level": "blocking", "run": 5},
    "D-20": {"enabled": True, "level": "advisory", "max_chars": 120},
    "D-21": {"enabled": True, "level": "advisory"},
}


def load_config() -> tuple[dict[str, Any], list[dict[str, str]]]:
    """读 lint/deslop.yaml。

    返回 (config, ineffective)。**YAML 写坏直接抛 ConfigError**——参数错着跑出
    一个"看起来正常"的分数，比报错更危险。
    """
    cfg: dict[str, Any] = {
        "rules": {k: dict(v) for k, v in _DEFAULT_RULES.items()},
        "preserve": list(_DEFAULT_PRESERVE),
        "score": {"weights": dict(DEFAULT_WEIGHTS),
                  "saturation_k": DEFAULT_SATURATION_K,
                  "min_body_chars": MIN_BODY_CHARS},
    }
    path = lint_dir() / CONFIG_NAME
    if not path.exists():
        return cfg, [{"rule": "deslop", "reason": "missing_file",
                      "detail": f"lint/{CONFIG_NAME} 不存在，已用内置默认参数"}]
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"lint/{CONFIG_NAME} 解析失败: {e}") from e
    if raw is None:
        raise ConfigError(f"lint/{CONFIG_NAME} 为空文件")
    if not isinstance(raw, dict):
        raise ConfigError(f"lint/{CONFIG_NAME} 顶层必须是映射")

    rules = raw.get("rules")
    if rules is not None:
        if not isinstance(rules, dict):
            raise ConfigError("rules 必须是映射")
        for rid, spec in rules.items():
            if rid not in RULES_BY_ID:
                raise ConfigError(f"未知规则 ID: {rid}")
            if not isinstance(spec, dict):
                raise ConfigError(f"{rid} 配置必须是映射")
            merged = dict(cfg["rules"].get(rid, {}))
            merged.update(spec)
            cfg["rules"][rid] = merged

    preserve = raw.get("preserve")
    if preserve is not None:
        if not isinstance(preserve, list) or not all(isinstance(p, str) for p in preserve):
            raise ConfigError("preserve 必须是字符串数组")
        for p in preserve:
            try:
                re.compile(p)
            except re.error as e:
                raise ConfigError(f"preserve 正则非法 {p!r}: {e}") from e
        cfg["preserve"] = preserve

    score = raw.get("score")
    if score is not None:
        if not isinstance(score, dict):
            raise ConfigError("score 必须是映射")
        w = score.get("weights")
        if w is not None:
            if not isinstance(w, dict):
                raise ConfigError("score.weights 必须是映射")
            for k, v in w.items():
                if k not in ("L1", "L2", "L3", "L4"):
                    raise ConfigError(f"score.weights 未知层级: {k}")
                if not isinstance(v, (int, float)) or v < 0:
                    raise ConfigError(f"score.weights.{k} 必须是非负数")
            cfg["score"]["weights"].update(w)
        k = score.get("saturation_k")
        if k is not None:
            if not isinstance(k, (int, float)) or k <= 0:
                raise ConfigError("score.saturation_k 必须是正数")
            cfg["score"]["saturation_k"] = float(k)
        m = score.get("min_body_chars")
        if m is not None:
            cfg["score"]["min_body_chars"] = int(m)
    return cfg, []


# ---------------------------------------------------------------------------
# 保护区
# ---------------------------------------------------------------------------

def _preserve_ranges(text: str, patterns: Iterable[str]) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for p in patterns:
        for m in re.finditer(p, text):
            if m.end() > m.start():
                spans.append((m.start(), m.end()))
    spans.sort()
    merged: list[tuple[int, int]] = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


class _Scanner:
    """负责坐标换算 + 保护区过滤，避免每条规则重复实现。"""

    def __init__(self, text: str, preserve: list[tuple[int, int]]):
        self.text = text
        self.preserve = preserve
        self._starts = [i for i, ch in enumerate(text) if ch == "\n"]

    def line_of(self, pos: int) -> int:
        return bisect.bisect_right(self._starts, pos) + 1

    def preserved(self, pos: int) -> bool:
        if not self.preserve:
            return False
        i = bisect.bisect_right(self.preserve, (pos, 10**9)) - 1
        if i < 0:
            return False
        s, e = self.preserve[i]
        return s <= pos < e

    def find(self, pattern: re.Pattern) -> list[re.Match]:
        return [m for m in pattern.finditer(self.text) if not self.preserved(m.start())]


_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;\n])")
_CLAUSE_SPLIT = re.compile(r"[，,；;]")


def _sentences(text: str) -> list[tuple[int, str]]:
    """返回 (起始偏移, 句子)。句子保留终止标点。"""
    out: list[tuple[int, str]] = []
    pos = 0
    for m in _SENT_SPLIT.finditer(text):
        seg = text[pos:m.end()]
        if seg.strip():
            out.append((pos, seg.strip()))
        pos = m.end()
    if pos < len(text) and text[pos:].strip():
        out.append((pos, text[pos:].strip()))
    return out


def _paragraphs(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    pos = 0
    for line in text.split("\n"):
        if line.strip():
            out.append((pos, line.strip()))
        pos += len(line) + 1
    return out


# ---------------------------------------------------------------------------
# 主检测
# ---------------------------------------------------------------------------

_NEGATE_PARALLEL = re.compile(r"^(?:没有|不再|不曾)")
_MICRO_ACTION = re.compile(r"嘴角勾起|眼中闪过|眼底划过|心中一凛|眉头微皱|嘴角微扬|瞳孔微缩")
_STOCK_REACTION = re.compile(r"深吸一口气|喉咙一紧|心里一沉|拳头攥紧")
_CONNECTIVES = re.compile(r"[而但却于是因此]")
_SYSTEM_TONE = re.compile(r"叮！|恭喜宿主|检测到|结算如下|系统提示")
_CLICHE = re.compile(r"仿佛|似乎|宛如|犹如|恍若")
_SIMILE = re.compile(r"(?:像|如同|好似)[^。！？\n]{2,20}")


def analyze(body: str, *, config: dict[str, Any] | None = None) -> DeslopResult:
    """跑全部启用的规则。确定性：同一文本两次结果完全一致（无随机/无时间依赖）。"""
    ineffective: list[dict[str, str]] = []
    if config is None:
        config, ineff = load_config()
        ineffective.extend(ineff)

    text = body or ""
    words = len(re.sub(r"\s+", "", text))
    min_chars = int(config["score"].get("min_body_chars", MIN_BODY_CHARS))
    if words < min_chars:
        return DeslopResult(score=-1, hits=[], word_count=words,
                            ineffective=ineffective + [{
                                "rule": "deslop", "reason": "unparsed",
                                "detail": f"正文仅 {words} 字（<{min_chars}），不评分"}])

    rules = config["rules"]
    weights = config["score"]["weights"]
    k_sat = float(config["score"]["saturation_k"])
    base = max(words, 300) / 1000
    preserve = _preserve_ranges(text, config["preserve"])
    sc = _Scanner(text, preserve)
    hits: list[DeslopHit] = []
    density_penalty = 0.0

    def enabled(rid: str) -> bool:
        return bool(rules.get(rid, {}).get("enabled", True))

    def level_of(rid: str) -> str:
        return rules.get(rid, {}).get("level") or RULES_BY_ID[rid].default_level

    def add(rid: str, line: int, quote: str, message: str, *, weight: bool = True) -> None:
        spec = RULES_BY_ID[rid]
        w = int(weights.get(spec.level_tag, 0)) if weight else 0
        hits.append(DeslopHit(rid, level_of(rid), line, quote.strip()[:40], w, message))

    # D-01 否定对举
    if enabled("D-01"):
        for m in sc.find(re.compile(r"不是[^。！？\n]{0,30}?而是|并非[^。！？\n]{0,30}?而是")):
            add("D-01", sc.line_of(m.start()), m.group(0), "否定对举句式（不是…而是）")

    # D-02 反向否定
    if enabled("D-02"):
        for m in sc.find(re.compile(r"(?:不但|不仅|不光)[^。！？\n]{0,30}?(?:反而|反倒)")):
            add("D-02", sc.line_of(m.start()), m.group(0), "反向否定句式（不但…反而）")

    # D-03 声音反差
    if enabled("D-03"):
        for m in sc.find(re.compile(r"(?:声音不大|语气(?:平淡|平静)|声音很轻)[^。！？\n]{0,12}?却")):
            add("D-03", sc.line_of(m.start()), m.group(0), "声音/语气反差套式")

    # D-04 否定排比
    if enabled("D-04"):
        run_need = int(rules["D-04"].get("run", 3))
        sents = _sentences(text)
        i = 0
        while i < len(sents):
            if not _NEGATE_PARALLEL.match(sents[i][1]):
                i += 1
                continue
            j = i
            while j < len(sents) and _NEGATE_PARALLEL.match(sents[j][1]):
                j += 1
            if j - i >= run_need:
                add("D-04", sc.line_of(sents[i][0]), sents[i][1][:20],
                    f"连续 {j - i} 句否定起句（否定排比）")
            i = max(j, i + 1)

    # D-05 破折号
    if enabled("D-05"):
        for m in sc.find(re.compile(r"——|--")):
            add("D-05", sc.line_of(m.start()), m.group(0), "破折号（网文排印应避免）")

    # D-06 升华式收尾 / D-07 总结式收尾
    tail_chars = int(rules.get("D-06", {}).get("tail_chars", 200))
    tail = re.sub(r"\s+", "", text)[-tail_chars:]
    if enabled("D-06"):
        for w in ("他知道", "从这天起", "一切才刚刚开始", "一切才刚开始"):
            if w in tail:
                add("D-06", sc.line_of(max(0, len(text) - 1)), w, f"升华式收尾: {w}")
    if enabled("D-07"):
        for w in ("总之", "由此可见", "这一刻，他明白", "他明白"):
            if w in tail:
                add("D-07", sc.line_of(max(0, len(text) - 1)), w, f"总结式收尾: {w}")

    # D-08 三段排比（同结构 + 长度差 <15%）
    if enabled("D-08"):
        run_need = int(rules["D-08"].get("run", 3))
        gap = float(rules["D-08"].get("length_gap", 0.15))
        # 先按句切、再按逗号切分句：否则逗号会把相邻两句粘成一条"分句"，
        # 造成"同构排比"假阳性（真实文本里最常见）。
        clauses: list[tuple[int, str]] = []
        for soff, sent in _sentences(text):
            for part in _CLAUSE_SPLIT.split(sent):
                p_ = part.strip()
                if p_:
                    clauses.append((soff, p_))
        i = 0
        while i < len(clauses):
            head = clauses[i][1][:1]
            j = i
            while (j + 1 < len(clauses) and clauses[j + 1][1][:1] == head and head
                   and _len_close(clauses[i][1], clauses[j + 1][1], gap)):
                j += 1
            if j - i + 1 >= run_need:
                add("D-08", sc.line_of(clauses[i][0]), clauses[i][1][:20],
                    f"连续 {j - i + 1} 组同构分句（三段排比）")
            i = max(j + 1, i + 1)

    # D-09 / D-10 套式密度
    if enabled("D-09"):
        count = len(sc.find(_MICRO_ACTION))
        need = int((rules["D-09"].get("density") or {}).get("count", 5))
        per_k = float((rules["D-09"].get("density") or {}).get("per_1000", 6))
        threshold = max(need, per_k * base)
        if count >= need and count > threshold:
            add("D-09", 0, "", f"微动作套式 {count} 次（阈值 {threshold:.0f}）", weight=False)
            density_penalty += (count - threshold) * 2
    if enabled("D-10"):
        count = len(sc.find(_STOCK_REACTION))
        need = int((rules["D-10"].get("density") or {}).get("count", 4))
        per_k = float((rules["D-10"].get("density") or {}).get("per_1500", 4))
        threshold = max(need, per_k * base * 1.5)
        if count >= need and count > threshold:
            add("D-10", 0, "", f"套式反应 {count} 次（阈值 {threshold:.0f}）", weight=False)
            density_penalty += (count - threshold) * 2

    # D-11 动作清单
    if enabled("D-11"):
        for poff, para in _paragraphs(text):
            if re.search(r"先[^。]{0,20}?然后[^。]{0,20}?接着[^。]{0,20}?最后", para):
                add("D-11", sc.line_of(poff), para[:20], "单段动作清单（先…然后…接着…最后）")
                continue
            if len(re.findall(r"又[^，。！？]{1,6}", para)) >= 4:
                add("D-11", sc.line_of(poff), para[:20], "单段 4+ 个『又…』并列")

    # D-12 抽象总结
    if enabled("D-12"):
        count = len(sc.find(re.compile(r"这一切都|意味着|预示着|说明了")))
        need = int((rules["D-12"].get("density") or {}).get("count", 3))
        per_k = float((rules["D-12"].get("density") or {}).get("per_4000", 3))
        threshold = max(need, per_k * base * 4)
        if count >= need and count > threshold:
            add("D-12", 0, "", f"抽象总结句 {count} 处", weight=False)
            density_penalty += (count - threshold) * 2

    # D-13 陈词密度（按章上限）
    if enabled("D-13"):
        cap = int(rules["D-13"].get("max_per_chapter", 2))
        count = len(sc.find(_CLICHE))
        if count > cap:
            add("D-13", 0, "", f"陈词（仿佛/似乎/宛如类）{count} 次（上限 {cap}）", weight=False)
            density_penalty += (count - cap) * 2

    # D-14 比喻密度
    if enabled("D-14"):
        count = len(sc.find(_SIMILE))
        need = int((rules["D-14"].get("density") or {}).get("count", 7))
        per_k = float((rules["D-14"].get("density") or {}).get("per_3000", 7))
        threshold = max(need, per_k * base * 3)
        if count >= need and count > threshold:
            add("D-14", 0, "", f"比喻 {count} 处（阈值 {threshold:.0f}）", weight=False)
            density_penalty += (count - threshold) * 2

    # D-15 因果链套式
    if enabled("D-15"):
        for poff, para in _paragraphs(text):
            if re.search(r"因为[^。]{0,30}?所以[^。]{0,30}?于是", para):
                add("D-15", sc.line_of(poff), para[:20], "因果链套式（因为…所以…于是）")
            elif re.search(r"由于[^。]{0,30}?因此", para):
                add("D-15", sc.line_of(poff), para[:20], "因果链套式（由于…因此）")

    # D-16 系统提示腔（对话内不算）
    if enabled("D-16"):
        for m in sc.find(_SYSTEM_TONE):
            add("D-16", sc.line_of(m.start()), m.group(0), "系统提示腔")

    # D-17 电报体（连续短句）
    if enabled("D-17"):
        run_need = int(rules["D-17"].get("run", 6))
        max_len = int(rules["D-17"].get("max_len", 8))
        sents = _sentences(text)
        i = 0
        while i < len(sents):
            if len(sents[i][1]) >= max_len:
                i += 1
                continue
            j = i
            while j < len(sents) and len(sents[j][1]) < max_len:
                j += 1
            if j - i >= run_need:
                add("D-17", sc.line_of(sents[i][0]), sents[i][1],
                    f"连续 {j - i} 句均 <{max_len} 字（电报体）", weight=False)
                density_penalty += 2
            i = max(j, i + 1)

    # D-18 连接词稀薄
    if enabled("D-18"):
        need = float(rules["D-18"].get("min_per_100", 1.5))
        if words >= 800:
            density = len(_CONNECTIVES.findall(text)) / (words / 100) if words else 0
            if density < need:
                add("D-18", 0, "", f"连接词密度 {density:.2f}/百字（<{need}）", weight=False)
                density_penalty += 2

    # D-19 句号断续
    if enabled("D-19"):
        run_need = int(rules["D-19"].get("run", 5))
        sents = _sentences(text)
        i = 0
        while i < len(sents):
            if not _is_staccato(sents[i][1]):
                i += 1
                continue
            j = i
            while j < len(sents) and _is_staccato(sents[j][1]):
                j += 1
            if j - i >= run_need:
                add("D-19", sc.line_of(sents[i][0]), sents[i][1][:20],
                    f"连续 {j - i} 句句号断开、无逗号（断续体）")
            i = max(j, i + 1)

    # D-20 超长段
    if enabled("D-20"):
        limit = int(rules["D-20"].get("max_chars", 120))
        for poff, para in _paragraphs(text):
            n = len(re.sub(r"\s+", "", para))
            if n > limit:
                add("D-20", sc.line_of(poff), para[:20], f"段落 {n} 字（>{limit}）")

    # D-21 引号强调腔
    if enabled("D-21"):
        for m in sc.find(re.compile(r"所谓[「『“][^」』”]{1,20}[」』”]|这就是[「『“][^」』”]{1,20}[」』”]")):
            add("D-21", sc.line_of(m.start()), m.group(0), "引号强调腔")

    # 计分（L3 命中 weight=0，只通过 density_penalty 生效）
    raw = sum(h.weight for h in hits)
    per_k = (raw + density_penalty) / max(base, 0.001)
    penalty = 100 * (1 - math.exp(-per_k / k_sat))
    score = max(0, min(100, int(round(100 - penalty))))

    hits.sort(key=lambda h: (h.line or 10**6, h.rule))
    return DeslopResult(score=score, hits=hits, ineffective=ineffective,
                        word_count=words, density_penalty=density_penalty,
                        raw_penalty=raw)


def _len_close(a: str, b: str, gap: float) -> bool:
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return False
    return abs(la - lb) / max(la, lb) <= gap


def _is_staccato(sent: str) -> bool:
    """以句号/叹号/问号结尾、且整句没有任何逗号。"""
    core = sent.rstrip()
    if not core.endswith(("。", "！", "？", "!", "?")):
        return False
    return ("，" not in core) and ("," not in core)
