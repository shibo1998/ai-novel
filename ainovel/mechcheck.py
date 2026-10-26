"""机械检查（规格03 §2.4，规格05 §2）：零 token 规则表 M-01 ~ M-11。

阻断（block）：M-01/02/03/04；提醒（warn）：M-05..M-11。
M-04 敏感词 = 阻断 + 禁止自动改写（红线必须人审，规格03 §3）。

**失败关闭（规格05 §1）**：`check()` 只返回命中，无法表达"这次没查成"——
词表缺失时它返回空列表，调用方会当成"查了没问题"，整章直接判绿。
新代码一律用 `check_full()`，它返回 `CheckResult`，把「未生效」与「无命中」
区分开：`pass` / `blocked` / `not_effective` 三态，且结论绑定正文指纹。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Iterator

from .paths import lint_dir
from . import settings

ENDING_WORDS = ["他知道", "从这天起", "一切才刚刚开始", "未来"]
_DIALOG_RE = re.compile(r"[「『“][^」』”]*[」』”]")

# 未生效原因枚举（规格05 P-2，取值固定，前端按此显示文案）
REASON_MISSING_FILE = "missing_file"
REASON_EMPTY_FILE = "empty_file"
REASON_SUSPICIOUSLY_SMALL = "suspiciously_small"
REASON_BAD_THRESHOLD = "bad_threshold"
REASON_DISABLED = "disabled"
REASON_UNPARSED = "unparsed"
REASON_CRASH = "crash"

# 未生效即禁止推进的规则（M-02 的 suspiciously_small 例外，按 warn 处理）
BLOCK_LEVEL_RULES = {"M-01", "M-02", "M-04"}

# 词表健康下限：低于此条数视为"疑似被清空/截断"，报警但不阻断
BANNED_HEALTHY_MIN = 20
MIN_BODY_CHARS = 100


@dataclass
class Hit:
    rule: str
    level: str        # block | warn
    line: int         # 1-based；0=全文级
    word: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Ineffective:
    """一条"这次没查成"的记录。语义 ≠ 通过，也 ≠ 阻断。

    `blocking` 决定它是否"禁止推进"：词表缺失（P-2 的 block 级）为 True；
    "状态快照还没生成"这类当前阶段本就拿不到的数据为 False —— 仍然要显示，
    但不能每次都报成红色阻断，否则用户会习惯性忽略红条。
    """
    rule: str
    reason: str
    detail: str
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CheckResult:
    hits: list[Hit] = field(default_factory=list)
    ineffective: list[Ineffective] = field(default_factory=list)
    verdict: str = "pass"          # pass | blocked | not_effective
    fingerprint: str = ""
    word_count: int = 0
    checked_at: str = ""

    # 兼容旧调用方：`for h in run_mech_check(...)` / `[h.to_dict() for h in res]`
    def __iter__(self) -> Iterator[Hit]:
        return iter(self.hits)

    def __len__(self) -> int:
        return len(self.hits)

    def __getitem__(self, idx):
        return self.hits[idx]

    @property
    def blocks(self) -> list[Hit]:
        return [h for h in self.hits if h.level == "block"]

    @property
    def warns(self) -> list[Hit]:
        return [h for h in self.hits if h.level == "warn"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": [h.to_dict() for h in self.hits],
            "ineffective": [i.to_dict() for i in self.ineffective],
            "verdict": self.verdict,
            "fingerprint": self.fingerprint,
            "word_count": self.word_count,
            "checked_at": self.checked_at,
            "hits_count": {"block": len(self.blocks), "warn": len(self.warns)},
        }


@dataclass
class WordList:
    """词表加载结果：词条 + 健康状态（不健康也不会静默当空表用）。"""
    filename: str
    words: list[str]
    reason: str | None = None
    detail: str = ""


def _load_lines(filename: str) -> list[str]:
    """旧接口：只取词条。缺失返回 []（保留以兼容 check()；新代码用 _load_wordlist）。"""
    return _load_wordlist(filename).words


def _load_wordlist(filename: str, *, healthy_min: int = 0) -> WordList:
    """加载词表并如实报告健康状态。

    分级标记（规格05 §4.2）：`#L1` / `#L2` 只影响集合归属，不产生额外词条。
    这里是纯文本行解析，`#` 起头一律当注释/分节。
    """
    path = lint_dir() / filename
    if not path.exists():
        return WordList(filename, [], REASON_MISSING_FILE, f"lint/{filename} 不存在")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:  # 权限/编码问题：不静默当空表
        return WordList(filename, [], REASON_CRASH, f"lint/{filename} 读取失败: {e}")
    words = []
    for line in raw.splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            words.append(s)
    if not words:
        return WordList(filename, [], REASON_EMPTY_FILE, f"lint/{filename} 存在但 0 条有效规则")
    if healthy_min and len(words) < healthy_min:
        return WordList(filename, words, REASON_SUSPICIOUSLY_SMALL,
                        f"lint/{filename} 仅 {len(words)} 条（健康下限 {healthy_min}）")
    return WordList(filename, words)


def load_banned_words() -> list[str]:
    return _load_lines("banned_words.txt")


def load_sensitive_words() -> list[str]:
    return _load_lines("sensitive_words.txt")


def load_banned_graded() -> dict[str, list[str]]:
    return _load_graded("banned_words.txt")


def load_sensitive_graded() -> dict[str, list[str]]:
    return _load_graded("sensitive_words.txt")


def _load_graded(filename: str) -> dict[str, list[str]]:
    """按 `#L1` / `#L2` 分节解析词表（规格05 §4.2）。

    兼容规则：文件里**一个分级标记都没有**时，全部按 L1 处理。
    理由——历史上这些词都是 block 级，若按"默认 L2"变成只报警，
    等于用户换了新版程序后禁词突然就不拦了（静默降级，违反 P-4）。
    """
    path = lint_dir() / filename
    levels: dict[str, list[str]] = {"L1": [], "L2": []}
    if not path.exists():
        return levels
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return levels
    cur = "L2"
    marked = False
    legacy: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("#"):
            token = s.lstrip("#").strip().upper()
            if token in ("L1", "L2"):
                cur = token
                marked = True
            continue
        (levels[cur] if marked else legacy).append(s)
    if not marked:
        levels["L1"] = legacy
        levels["L2"] = []
    return levels


def load_dialog_patterns() -> list[re.Pattern]:
    return _compile_patterns(_load_lines("dialog_suffix.txt"))


def _compile_patterns(lines: list[str]) -> list[re.Pattern]:
    out = []
    for p in lines:
        try:
            out.append(re.compile(p))
        except re.error:
            # 单条正则写坏不该让整章机检崩掉；check_full 会记 ineffective
            continue
    return out


# ---------------------------------------------------------------------------
# 内容指纹（规格05 P-3）：结论绑定它判定的那一版正文
# ---------------------------------------------------------------------------

def normalize(body: str) -> str:
    """去 frontmatter → \\r\\n 归一 → 去行尾空白 → 折叠连续空行。"""
    text = body.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("---"):
        lines = text.split("\n")
        for i in range(1, len(lines)):
            if lines[i].strip() in ("---", "..."):
                text = "\n".join(lines[i + 1:])
                break
    out = [ln.rstrip() for ln in text.split("\n")]
    collapsed: list[str] = []
    for ln in out:
        if not ln and collapsed and not collapsed[-1]:
            continue
        collapsed.append(ln)
    return "\n".join(collapsed).strip()


def fingerprint(body: str) -> str:
    """sha256(规范化正文)[:16]。正文一变，旧结论即过期（禁用 mtime 判定）。"""
    return hashlib.sha256(normalize(body).encode("utf-8")).hexdigest()[:16]


def _bad_word_range(word_range: Any) -> str | None:
    """返回非法原因；合法返回 None。"""
    if not isinstance(word_range, (list, tuple)):
        return f"word_range 不是数组: {word_range!r}"
    if len(word_range) != 2:
        return f"word_range 长度 {len(word_range)} ≠ 2"
    try:
        lo, hi = int(word_range[0]), int(word_range[1])
    except (TypeError, ValueError):
        return f"word_range 含非数字: {word_range!r}"
    if lo < 0 or hi < 0:
        return f"word_range 含负数: {word_range!r}"
    if lo >= hi:
        return f"word_range 区间倒置或相等: [{lo}, {hi}]"
    return None


def count_words(body: str) -> int:
    return len(re.sub(r"\s+", "", body))


def _split_sentences(paragraph: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])", paragraph)
    return [p for p in (s.strip() for s in parts) if p]


def check(body: str, *, word_range: list[int] | None = None,
          thresholds: dict[str, Any] | None = None,
          whitelist: list[str] | None = None) -> list[Hit]:
    """body = 纯正文（无 frontmatter）。返回命中列表。

    whitelist：书级禁词豁免（config.json lint_whitelist）——目标文风作者
    的招牌词与通用禁词冲突时豁免（如"骤然"）。仅豁免 M-02 禁词，不豁免敏感词。

    .. deprecated::
       无法表达"这次没查成"（词表缺失时返回空列表 = 看起来通过）。
       新代码用 :func:`check_full`。
    """
    th = dict(settings.load().get("mechcheck", {}))
    if thresholds:
        th.update(thresholds)
    banned = load_banned_graded()
    sens = load_sensitive_graded()
    return _run_checks(
        body, word_range=word_range, th=th,
        banned_l1=[w for w in banned["L1"] if w not in (whitelist or [])],
        banned_l2=[w for w in banned["L2"] if w not in (whitelist or [])],
        dialog_pats=load_dialog_patterns(),
        sensitive_l1=sens["L1"], sensitive_l2=sens["L2"],
    )


def check_full(body: str, *, word_range: list[int] | None = None,
               thresholds: dict[str, Any] | None = None,
               whitelist: list[str] | None = None,
               context: dict[str, Any] | None = None) -> CheckResult:
    """一次带生效状态的检查结论（规格05 §2.3）。

    与 check() 的区别只在返回值：它必须能回答"哪条规则这次没查成"。
    `context` = 状态快照上下文（M-13/M-14/M-15 用）；不传则如实报"这几条没查"。
    """
    th = dict(settings.load().get("mechcheck", {}))
    if thresholds:
        th.update(thresholds)

    banned_info = _load_wordlist("banned_words.txt", healthy_min=BANNED_HEALTHY_MIN)
    dialog_info = _load_wordlist("dialog_suffix.txt")
    sensitive_info = _load_wordlist("sensitive_words.txt")

    ineffective: list[Ineffective] = []

    # M-01 字数区间
    effective_range = word_range
    if word_range is not None:
        bad = _bad_word_range(word_range)
        if bad:
            ineffective.append(Ineffective("M-01", REASON_BAD_THRESHOLD, bad))
            effective_range = None  # 区间非法 → 不拿它去判字数（否则必然误报）

    # M-02 禁词（缺失/空 = block 级；条数过少 = 只报警）
    if banned_info.reason:
        ineffective.append(Ineffective("M-02", banned_info.reason, banned_info.detail))
    # M-03 对话后缀（缺失/空 = warn 级）
    if dialog_info.reason:
        ineffective.append(Ineffective("M-03", dialog_info.reason, dialog_info.detail))
    # M-04 敏感词（缺失/空 = block 级：红线表为空等于没有红线）
    if sensitive_info.reason:
        ineffective.append(Ineffective("M-04", sensitive_info.reason, sensitive_info.detail))

    # M-05..M-11 依赖正文可解析
    wc = count_words(body or "")
    if wc < MIN_BODY_CHARS:
        ineffective.append(Ineffective(
            "M-05..M-11", REASON_UNPARSED,
            f"正文仅 {wc} 字（<{MIN_BODY_CHARS}），句式/密度类检查未执行"))

    dialog_pats = _compile_patterns(dialog_info.words)
    if dialog_info.words and not dialog_pats:
        ineffective.append(Ineffective("M-03", REASON_CRASH, "dialog_suffix.txt 全部正则编译失败"))

    hits = _run_checks(
        body, word_range=effective_range, th=th,
        banned_l1=[w for w in _load_graded("banned_words.txt")["L1"] if w not in (whitelist or [])],
        banned_l2=[w for w in _load_graded("banned_words.txt")["L2"] if w not in (whitelist or [])],
        dialog_pats=dialog_pats,
        sensitive_l1=_load_graded("sensitive_words.txt")["L1"],
        sensitive_l2=_load_graded("sensitive_words.txt")["L2"],
    )

    if not (body or "").strip() and not any(i.rule == "M-05..M-11" for i in ineffective):
        ineffective.append(Ineffective("M-05..M-11", REASON_UNPARSED, "正文为空"))

    # M-12 AI 味（规则检测器 = 客观证据；阈值刻意放宽，先报警不拦路）
    from . import deslop

    try:
        dres = deslop.analyze(body or "")
        for item in dres.ineffective:
            if item.get("rule") == "deslop":
                ineffective.append(Ineffective("M-12", item.get("reason", REASON_CRASH),
                                               item.get("detail", "")))
        if dres.score < 0:
            pass  # unparsed：已由 deslop 记入 ineffective
        else:
            block_th = float(th.get("ai_flavor_block", 45))
            warn_th = float(th.get("ai_flavor_warn", 60))
            top = "、".join(f"{h.rule}×1" for h in dres.blocking[:3]) or "密度超限"
            if dres.score < block_th and dres.blocking:
                hits.append(Hit("M-12", "block", 0, str(dres.score),
                                f"AI 味干净度 {dres.score}（<{block_th:.0f}），结构性硬伤：{top}"))
            elif dres.score < warn_th:
                hits.append(Hit("M-12", "warn", 0, str(dres.score),
                                f"AI 味干净度 {dres.score}（<{warn_th:.0f}），建议润色"))
    except deslop.ConfigError as e:  # 参数写坏：不崩，但必须留痕
        ineffective.append(Ineffective("M-12", REASON_CRASH, str(e)))
    except Exception as e:  # 规则崩溃不能让整章机检失败，但必须可见
        ineffective.append(Ineffective("M-12", REASON_CRASH, f"检测器异常: {e}"))

    # M-13 ~ M-15 状态一致性（需要有状态快照；没有就如实说没查）
    if context is None:
        ineffective.append(Ineffective("M-13..M-15", REASON_UNPARSED,
                                       "未提供状态快照上下文，状态类检查未执行"))
        ineffective.append(Ineffective("M-16..M-17", REASON_UNPARSED,
                                       "未提供时间线上下文，时间线检查未执行"))
    else:
        if not context.get("has_state"):
            ineffective.append(Ineffective(
                "M-13..M-15", REASON_UNPARSED,
                "本章无 state_delta 快照（V4 未产出或为老记忆卡），状态类检查未执行"))
        else:
            hits.extend(_state_hits(body or "", context))

        timeline_hits = context.get("timeline_hits")
        if timeline_hits is None:
            ineffective.append(Ineffective("M-16..M-17", REASON_UNPARSED,
                                           "时间线校验结果缺失，时间线检查未执行"))
        else:
            for d in timeline_hits:
                hits.append(Hit(d.get("rule", "M-16"), d.get("level", "block"),
                                int(d.get("line") or 0), str(d.get("word") or ""),
                                str(d.get("message") or "")))

    for item in ineffective:
        item.blocking = _is_block_level(item)
    blocked_ineffective = [i for i in ineffective if i.blocking]
    if blocked_ineffective:
        verdict = "not_effective"
    elif has_block(hits):
        verdict = "blocked"
    else:
        verdict = "pass"

    return CheckResult(
        hits=hits,
        ineffective=ineffective,
        verdict=verdict,
        fingerprint=fingerprint(body or ""),
        word_count=wc,
        checked_at=datetime.now().isoformat(timespec="seconds"),
    )


def _is_block_level(item: Ineffective) -> bool:
    if item.reason == REASON_SUSPICIOUSLY_SMALL:
        return False
    return item.rule in BLOCK_LEVEL_RULES


# ---------------------------------------------------------------------------
# M-13 ~ M-15 状态一致性（依赖 chapter_state 快照）
# ---------------------------------------------------------------------------

_MOVE_WORDS = re.compile(r"走|赶|飞|船|车|奔|渡|翻山|穿行|上路|启程|离城|进城|回城|"
                         r"个月|月后|数日|次日|翌日|三天|半月|一炷香|时辰")


def _state_hits(body: str, ctx: dict[str, Any]) -> list[Hit]:
    hits: list[Hit] = []
    order = ctx.get("realm_order") or []
    rows = ctx.get("rows") or []
    prev_map = ctx.get("prev") or {}
    char_status = ctx.get("char_status") or {}
    has_move = bool(_MOVE_WORDS.search(body or ""))

    for row in rows:
        name = row.get("character") or ""
        if not name:
            continue
        # M-13 战力倒退
        prev = prev_map.get(name) or {}
        cur_pw = (row.get("power_level") or "").strip()
        prev_pw = (prev.get("power_level") or "").strip()
        if cur_pw and prev_pw and order:
            ci, pi = _realm_index(cur_pw, order), _realm_index(prev_pw, order)
            if ci is not None and pi is not None and ci < pi:
                hits.append(Hit("M-13", "block", 0, f"{name}:{prev_pw}→{cur_pw}",
                                f"{name} 战力倒退：第{prev.get('chapter_no')}章 {prev_pw} → "
                                f"第{row.get('chapter_no')}章 {cur_pw}"))
        # M-14 死者复现（必须带时间比较：角色卡记的是**全书最终**状态，
        # 拿它去判"死亡之前"的章节会把正常出场判成死者复现——实测 ch1/2 的
        # 刀疤强（第 2 章才死）与 ch10/11 的执事（第 11 章才死）全被误判。
        # 只有在"卡状态更新章 < 本章"时才可能构成复现。）
        if char_status.get(name) == "死亡" and _death_known_before(ctx, name, row):
            acted = any((row.get(k) or "").strip() for k in
                        ("location", "physical", "mental"))
            if acted:
                hits.append(Hit("M-14", "block", 0, name,
                                f"{name} 角色卡记为死亡，本章仍有状态行动记录"))
        # M-15 位置跳跃
        cur_loc = (row.get("location") or "").strip()
        prev_loc = (prev.get("location") or "").strip()
        if cur_loc and prev_loc and cur_loc != prev_loc and not has_move:
            hits.append(Hit("M-15", "warn", 0, f"{name}:{prev_loc}→{cur_loc}",
                            f"{name} 位置从 {prev_loc} 变到 {cur_loc}，正文无位移描写"))
    return hits


def _death_known_before(ctx: dict[str, Any], name: str, row: dict[str, Any]) -> bool:
    """该角色"已知死亡"是否早于本章。拿不到更新章就保守不判（宁可漏报不误杀）。"""
    meta = (ctx.get("char_death") or {}).get(name)
    if meta is None:
        return False
    try:
        died_at = int(meta)
    except (TypeError, ValueError):
        return False
    try:
        cur = int(row.get("chapter_no") or 0)
    except (TypeError, ValueError):
        return False
    return died_at < cur


def _death_known_before(ctx: dict[str, Any], name: str, row: dict[str, Any]) -> bool:
    """该角色"已知死亡"是否早于本章。拿不到更新章就保守不判（宁可漏报不误杀）。"""
    meta = (ctx.get("char_death") or {}).get(name)
    if meta is None:
        return False
    try:
        died_at = int(meta)
    except (TypeError, ValueError):
        return False
    try:
        cur = int(row.get("chapter_no") or 0)
    except (TypeError, ValueError):
        return False
    return died_at < cur


def _realm_index(value: str, order: list[str]) -> int | None:
    v = value.replace("**", "").replace("*", "").strip()
    if not v:
        return None
    if v in order:
        return order.index(v)
    for i, item in enumerate(order):
        if item and (item in v or v in item):
            return i
    return None


def _run_checks(body: str, *, word_range: list[int] | None, th: dict[str, Any],
                banned_l1: list[str], banned_l2: list[str],
                dialog_pats: list[re.Pattern],
                sensitive_l1: list[str], sensitive_l2: list[str]) -> list[Hit]:
    hits: list[Hit] = []
    lines = body.splitlines()
    para_idx = [(i + 1, l.strip()) for i, l in enumerate(lines) if l.strip()]
    text = "\n".join(p for _, p in para_idx)

    # M-01 字数（阻断）
    if word_range:
        n = count_words(body)
        if not (word_range[0] <= n <= word_range[1]):
            hits.append(Hit("M-01", "block", 0, str(n),
                            f"字数 {n} 不在区间 [{word_range[0]}, {word_range[1]}]"))

    # M-02 禁词（L1 阻断 / L2 报警；书级白名单两档都豁免）
    for words, level in ((banned_l1, "block"), (banned_l2, "warn")):
        for word in words:
            for ln, line in para_idx:
                if word in line:
                    msg = f"禁词命中: {word}" if level == "block" else f"套式用词（建议替换）: {word}"
                    hits.append(Hit("M-02", level, ln, word, msg))

    # M-03 对话后缀禁式（阻断）
    for pat in dialog_pats:
        for ln, line in para_idx:
            m = pat.search(line)
            if m:
                hits.append(Hit("M-03", "block", ln, m.group(0), f"对话后缀禁式: {m.group(0)}"))

    # M-04 敏感词（L1 红线：阻断 + 禁止自动改写；L2 平台风险：仅报警）
    for word in sensitive_l1:
        for ln, line in para_idx:
            if word in line:
                hits.append(Hit("M-04", "block", ln, word,
                                f"敏感词红线: {word}（禁止自动改写，须人工处理）"))
    for word in sensitive_l2:
        for ln, line in para_idx:
            if word in line:
                hits.append(Hit("M-04", "warn", ln, word,
                                f"平台风险词: {word}（题材可写，请人工确认尺度）"))

    comma_max = int(th.get("sentence_comma_max", 2))
    # M-05 单句逗号 >2（提醒）
    for ln, line in para_idx:
        for sent in _split_sentences(line):
            c = sent.count("，") + sent.count(",")
            if c > comma_max:
                hits.append(Hit("M-05", "warn", ln, sent[:20], f"单句逗号 {c} 个（>{comma_max}）"))

    # M-06 每千字感叹号/省略号密度（提醒）
    total_chars = max(1, count_words(text))
    k = total_chars / 1000
    excl = sum(l.count("！") + l.count("!") for _, l in para_idx)
    ell = sum(len(re.findall(r"……|\.\.\.", l)) for _, l in para_idx)
    if excl / k > float(th.get("exclaim_per_1k_max", 2)):
        hits.append(Hit("M-06", "warn", 0, f"{excl}个感叹号",
                        f"每千字感叹号 {excl / k:.1f} 个（>{th.get('exclaim_per_1k_max', 2)}）"))
    if ell / k > float(th.get("ellipsis_per_1k_max", 3)):
        hits.append(Hit("M-06", "warn", 0, f"{ell}处省略号",
                        f"每千字省略号 {ell / k:.1f} 处（>{th.get('ellipsis_per_1k_max', 3)}）"))

    # M-07 连续 4+ 句长度差 <20%（爆发度塌陷，提醒）
    all_sents: list[tuple[int, str]] = []
    for ln, line in para_idx:
        for sent in _split_sentences(line):
            all_sents.append((ln, sent))
    win = int(th.get("flat_sentence_window", 4))
    ratio = float(th.get("flat_sentence_ratio", 0.2))
    i = 0
    while i < len(all_sents):
        j = i
        while j < len(all_sents) and j - i < 64:
            lens = [len(s) for _, s in all_sents[i:j + 1]]
            if max(lens) == 0 or (max(lens) - min(lens)) / max(lens) >= ratio:
                break
            j += 1
        run_len = j - i
        if run_len >= win:
            hits.append(Hit("M-07", "warn", all_sents[i][0], f"连续{run_len}句",
                            f"连续 {run_len} 句长度差 <{int(ratio * 100)}%（爆发度塌陷）"))
            i = j
        else:
            i += 1

    # M-08 段落 >120 字（提醒）
    pmax = int(th.get("paragraph_max_chars", 120))
    for ln, line in para_idx:
        if count_words(line) > pmax:
            hits.append(Hit("M-08", "warn", ln, line[:20], f"段落 {count_words(line)} 字（>{pmax}）"))

    # M-09 连续 3 段相同开头（提醒）
    rep = int(th.get("repeat_para_prefix_n", 3))
    i = 0
    while i < len(para_idx):
        j = i
        prefix = para_idx[i][1][:2]
        while j < len(para_idx) and para_idx[j][1][:2] == prefix:
            j += 1
        if j - i >= rep:
            hits.append(Hit("M-09", "warn", para_idx[i][0], prefix,
                            f"连续 {j - i} 段以 {prefix!r} 开头（模板检测）"))
        i = max(j, i + 1)

    # M-10 末 200 字总结式收尾（提醒）
    tail = re.sub(r"\s+", "", body)[-int(th.get("ending_window_chars", 200)):]
    for w in ENDING_WORDS:
        if w in tail:
            hits.append(Hit("M-10", "warn", len(lines), w, f"总结式收尾词: {w}"))

    # M-11 非对话段"我"占比 >8%（主视角漂移启发式，允许误报）
    non_dialog = _DIALOG_RE.sub("", text)
    if non_dialog:
        ratio_me = non_dialog.count("我") / max(1, len(non_dialog))
        if ratio_me > float(th.get("first_person_ratio_max", 0.08)):
            hits.append(Hit("M-11", "warn", 0, f"{ratio_me:.1%}",
                            f"非对话段'我'占比 {ratio_me:.1%}（>{th.get('first_person_ratio_max', 0.08):.0%}，主视角漂移启发式）"))
    return hits


def has_block(hits: list[Hit]) -> bool:
    return any(h.level == "block" for h in hits)


def has_sensitive(hits: list[Hit]) -> bool:
    """是否有**真红线**命中（L2 平台风险词不触发"禁止自动改写"）。"""
    return any(h.rule == "M-04" and h.level == "block" for h in hits)
