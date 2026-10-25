"""机械检查（规格03 §2.4）：零 token 规则表 M-01 ~ M-11。

阻断（block）：M-01/02/03/04；提醒（warn）：M-05..M-11。
M-04 敏感词 = 阻断 + 禁止自动改写（红线必须人审，规格03 §3）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from typing import Any

from .paths import lint_dir
from . import settings

ENDING_WORDS = ["他知道", "从这天起", "一切才刚刚开始", "未来"]
_DIALOG_RE = re.compile(r"[「『“][^」』”]*[」』”]")


@dataclass
class Hit:
    rule: str
    level: str        # block | warn
    line: int         # 1-based；0=全文级
    word: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_lines(filename: str) -> list[str]:
    path = lint_dir() / filename
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def load_banned_words() -> list[str]:
    return _load_lines("banned_words.txt")


def load_sensitive_words() -> list[str]:
    return _load_lines("sensitive_words.txt")


def load_dialog_patterns() -> list[re.Pattern]:
    return [re.compile(p) for p in _load_lines("dialog_suffix.txt")]


def count_words(body: str) -> int:
    return len(re.sub(r"\s+", "", body))


def _split_sentences(paragraph: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])", paragraph)
    return [p for p in (s.strip() for s in parts) if p]


def check(body: str, *, word_range: list[int] | None = None,
          thresholds: dict[str, Any] | None = None) -> list[Hit]:
    """body = 纯正文（无 frontmatter）。返回命中列表。"""
    th = dict(settings.load().get("mechcheck", {}))
    if thresholds:
        th.update(thresholds)
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

    # M-02 禁词（阻断）
    for word in load_banned_words():
        for ln, line in para_idx:
            if word in line:
                hits.append(Hit("M-02", "block", ln, word, f"禁词命中: {word}"))

    # M-03 对话后缀禁式（阻断）
    for pat in load_dialog_patterns():
        for ln, line in para_idx:
            m = pat.search(line)
            if m:
                hits.append(Hit("M-03", "block", ln, m.group(0), f"对话后缀禁式: {m.group(0)}"))

    # M-04 敏感词（阻断 + 禁止自动改写）
    for word in load_sensitive_words():
        for ln, line in para_idx:
            if word in line:
                hits.append(Hit("M-04", "block", ln, word, f"敏感词红线: {word}（禁止自动改写，须人工处理）"))

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
    return any(h.rule == "M-04" for h in hits)
