"""规格05 能力补齐验收：T-01 ~ T-45（分特性分 class，与规格 §12.1 对应）。

原则：每条新增检查器都必须有一条**故意破坏**用例证明它会红（§12.3），
否则"全绿"只说明没查，不说明没问题。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ainovel import (books, chapters, db, mechcheck, pipeline, settings,
                     statemachine)
from ainovel.api import app
from ainovel.mockllm import MockLLM, make_prose

client = TestClient(app)


# ---------------------------------------------------------------------------
# 公共夹具
# ---------------------------------------------------------------------------

def _ready_chapter(book: str, no: int = 1, mock: MockLLM | None = None) -> MockLLM:
    """生成到 mech_check 状态（未跑机检）。"""
    mock = mock or MockLLM()
    pipeline.generate_outline(book, no, client=mock)
    pipeline.confirm_outline(book, no)
    pipeline.generate_prose(book, no, client=mock)
    assert statemachine.current_status(book, no) == "mech_check"
    return mock


def _lint(home: Path, name: str) -> Path:
    return home / "lint" / name


# ---------------------------------------------------------------------------
# T-01 ~ T-06  F-01 假绿防护
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_t01_missing_banned_words_is_not_effective(self, home, book_env):
        book, _ = book_env
        mock = _ready_chapter(book)
        _lint(home, "banned_words.txt").unlink()

        res = pipeline.run_mech_check(book, 1, client=mock)

        assert res.verdict == "not_effective"
        assert any(i.rule == "M-02" and i.reason == "missing_file" for i in res.ineffective)
        # 关键：不得因为"没查出命中"而推进到审校
        assert statemachine.current_status(book, 1) == "human_review"
        assert db.get_check_status(book_id=books.book_id(book),
                                   chapter_no=1)["checked_status"] == "not_effective"

    def test_t02_suspiciously_small_is_visible_but_not_blocking(self, home, book_env):
        book, _ = book_env
        mock = _ready_chapter(book)
        _lint(home, "banned_words.txt").write_text(
            "#L1\n微微\n缓缓\n陡然\n", encoding="utf-8")

        res = pipeline.run_mech_check(book, 1, client=mock)

        assert any(i.rule == "M-02" and i.reason == "suspiciously_small"
                   for i in res.ineffective)
        # 报警但不阻断：verdict 仍按命中判定，词表照样生效
        assert res.verdict != "not_effective"
        assert statemachine.current_status(book, 1) == "llm_review"

    def test_t03_bad_threshold_is_not_effective(self, home, book_env):
        book, _ = book_env
        cfg = books.load_config(book)
        cfg["chapter_word_range"] = [3000, 2000]
        books.save_config(book, cfg)
        mock = _ready_chapter(book)

        res = pipeline.run_mech_check(book, 1, client=mock)

        assert any(i.rule == "M-01" and i.reason == "bad_threshold" for i in res.ineffective)
        assert res.verdict == "not_effective"
        assert statemachine.current_status(book, 1) == "human_review"
        # 区间非法时不得拿它去判字数（否则必然误报 M-01 超区间）
        assert not any(h.rule == "M-01" for h in res.hits)

    def test_t04_normal_book_has_empty_ineffective(self, home, book_env):
        book, _ = book_env
        mock = _ready_chapter(book)
        res = pipeline.run_mech_check(book, 1, client=mock)
        assert res.ineffective == []
        assert res.verdict == "pass"
        assert res.fingerprint and len(res.fingerprint) == 16

    def test_t05_edit_one_char_makes_conclusion_stale(self, home, book_env):
        book, _ = book_env
        mock = _ready_chapter(book)
        pipeline.run_mech_check(book, 1, client=mock)

        prose = chapters.read_prose(book, 1)
        body = prose["_body"]
        fm = {k: v for k, v in prose.items() if not k.startswith("_")}
        chapters.write_prose(book, 1, fm, body.replace("林寒", "林寒 ", 1))

        st = client.get(f"/api/books/{book}/chapters/1/checkstatus").json()
        assert st["stale"] is True
        assert st["verdict"] == "stale"
        assert st["checked_fingerprint"] != st["current_fingerprint"]
        assert db.get_check_status(book_id=books.book_id(book),
                                   chapter_no=1)["checked_status"] == "stale"

    def test_t06_fingerprint_is_stable(self):
        body = "他在雨里站了很久。\r\n\n\n第三段。  "
        assert mechcheck.fingerprint(body) == mechcheck.fingerprint(body)
        # 规范化只抹平格式差异，不改字
        assert mechcheck.fingerprint(body) == mechcheck.fingerprint("他在雨里站了很久。\n\n第三段。")
        assert mechcheck.fingerprint(body) != mechcheck.fingerprint("他在雨里站了很久。\n\n第四段。")


# ---------------------------------------------------------------------------
# T-07 ~ T-12  F-02 AI 味确定性检测器
# ---------------------------------------------------------------------------

class TestDeslop:
    def test_t07_real_before_after_sample(self):
        demo = Path("D:/1-work/oh-story-claudecode/demo/去AI味对照")
        before_p, after_p = demo / "改前.md", demo / "改后.md"
        if not before_p.exists() or not after_p.exists():
            pytest.skip("对照样本不存在（外部仓库），跳过方向回归")
        from ainovel import deslop

        before = deslop.analyze(before_p.read_text(encoding="utf-8"))
        after = deslop.analyze(after_p.read_text(encoding="utf-8"))
        assert before.score < after.score
        assert after.score - before.score >= 10

    def test_t08_preserve_dialogue_not_reported(self):
        from ainovel import deslop

        body = ('他说："不是这样，而是那样。"\n' + "他把伞收起来，抖了抖水。\n" * 20)
        res = deslop.analyze(body)
        assert not [h for h in res.hits if h.rule == "D-01"]

    def test_t09_direction_matches_v1_ai_flavor(self):
        from ainovel import deslop

        from ainovel.mockllm import make_prose

        dash_text = ("他在门口站了很久。" * 20 + "——" * 30) * 3
        plain = make_prose((2200, 2800))
        assert deslop.analyze(dash_text).score < 45
        assert deslop.analyze(plain).score > 80

    def test_t10_deterministic(self):
        from ainovel import deslop

        body = ("他知道，从这天起，一切都不一样了——" + "他往前走。" * 30) * 3
        a = deslop.analyze(body).to_dict()
        b = deslop.analyze(body).to_dict()
        assert a == b

    def test_t11_empty_body_is_unparsed_not_zero(self):
        from ainovel import deslop

        res = deslop.analyze("")
        assert res.score == -1
        assert any(i["reason"] == "unparsed" for i in res.ineffective)

    def test_t12_performance(self):
        import time

        from ainovel import deslop
        from ainovel.mockllm import make_prose

        body = make_prose((2800, 3000))
        assert len(body) >= 2500
        start = time.perf_counter()
        deslop.analyze(body)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 80, f"检测耗时 {elapsed_ms:.1f}ms 超过 80ms"


# ---------------------------------------------------------------------------
# T-13 ~ T-15  F-03 词表与规则库
# ---------------------------------------------------------------------------

class TestRuleAssets:
    def test_t13_broken_yaml_raises_config_error(self, home):
        from ainovel import deslop

        (home / "lint" / "deslop.yaml").write_text("rules: [oops\n  bad: : :\n", encoding="utf-8")
        with pytest.raises(deslop.ConfigError):
            deslop.load_config()

    def test_t14_graded_parsing(self, home):
        from ainovel import mechcheck

        # 独立实现一份最小解析器，验证 loader 的段落归属与顺序
        def parse(name):
            levels = {"L1": [], "L2": []}
            cur = "L2"
            for line in (home / "lint" / name).read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#"):
                    token = s.lstrip("#").strip().upper()
                    if token in ("L1", "L2"):
                        cur = token
                    continue
                levels[cur].append(s)
            return levels

        expected = parse("banned_words.txt")
        got = mechcheck.load_banned_graded()
        assert got == expected
        assert len(got["L1"]) >= 40 and len(got["L2"]) >= 30
        # 首尾各 3 条抽样（顺序也要一致，否则提示词里的清单会错位）
        assert got["L1"][:3] == expected["L1"][:3]
        assert got["L1"][-3:] == expected["L1"][-3:]
        assert got["L2"][:3] == expected["L2"][:3]
        assert got["L2"][-3:] == expected["L2"][-3:]
        assert not set(got["L1"]) & set(got["L2"])

    def test_t15_rule_asset_size(self, home):
        from ainovel import mechcheck

        assert len(mechcheck.load_banned_graded()["L1"]) >= 40
        assert len(mechcheck.load_banned_graded()["L2"]) >= 30
        assert len(mechcheck.load_sensitive_words()) >= 30
        assert len(mechcheck.load_dialog_patterns()) >= 8
        # 分级语义：L1 阻断、L2 只报警
        body = "时光荏苒，岁月如梭。" + "正常句子推进。" * 300
        hits = mechcheck.check(body, word_range=[2200, 2800])
        assert any(h.rule == "M-02" and h.level == "block" for h in hits)
        body2 = "他微微点头。" + "正常句子推进。" * 300
        hits2 = mechcheck.check(body2, word_range=[2200, 2800])
        assert any(h.rule == "M-02" and h.level == "warn" for h in hits2)
        assert not any(h.rule == "M-02" and h.level == "block" for h in hits2)


# ---------------------------------------------------------------------------
# T-07 ~ T-12  F-02 AI 味确定性检测器
# ---------------------------------------------------------------------------

class TestDeslop:
    def test_t07_real_before_after_sample(self):
        demo = Path("D:/1-work/oh-story-claudecode/demo/去AI味对照")
        before_p, after_p = demo / "改前.md", demo / "改后.md"
        if not before_p.exists() or not after_p.exists():
            pytest.skip("对照样本不存在（外部仓库），跳过方向回归")
        from ainovel import deslop

        before = deslop.analyze(before_p.read_text(encoding="utf-8"))
        after = deslop.analyze(after_p.read_text(encoding="utf-8"))
        assert before.score < after.score
        assert after.score - before.score >= 10

    def test_t08_preserve_dialogue_not_reported(self):
        from ainovel import deslop

        body = ('他说："不是这样，而是那样。"\n' + "他把伞收起来，抖了抖水。\n" * 20)
        res = deslop.analyze(body)
        assert not [h for h in res.hits if h.rule == "D-01"]

    def test_t09_direction_matches_v1_ai_flavor(self):
        from ainovel import deslop

        from ainovel.mockllm import make_prose

        dash_text = ("他在门口站了很久。" * 20 + "——" * 30) * 3
        plain = make_prose((2200, 2800))
        assert deslop.analyze(dash_text).score < 45
        assert deslop.analyze(plain).score > 80

    def test_t10_deterministic(self):
        from ainovel import deslop

        body = ("他知道，从这天起，一切都不一样了——" + "他往前走。" * 30) * 3
        a = deslop.analyze(body).to_dict()
        b = deslop.analyze(body).to_dict()
        assert a == b

    def test_t11_empty_body_is_unparsed_not_zero(self):
        from ainovel import deslop

        res = deslop.analyze("")
        assert res.score == -1
        assert any(i["reason"] == "unparsed" for i in res.ineffective)

    def test_t12_performance(self):
        import time

        from ainovel import deslop
        from ainovel.mockllm import make_prose

        body = make_prose((2800, 3000))
        assert len(body) >= 2500
        start = time.perf_counter()
        deslop.analyze(body)
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert elapsed_ms < 80, f"检测耗时 {elapsed_ms:.1f}ms 超过 80ms"


# ---------------------------------------------------------------------------
# T-13 ~ T-15  F-03 词表与规则库
# ---------------------------------------------------------------------------

class TestRuleAssets:
    def test_t13_broken_yaml_raises_config_error(self, home):
        from ainovel import deslop

        (home / "lint" / "deslop.yaml").write_text("rules: [oops\n  bad: : :\n", encoding="utf-8")
        with pytest.raises(deslop.ConfigError):
            deslop.load_config()

    def test_t14_graded_parsing(self, home):
        from ainovel import mechcheck

        # 独立实现一份最小解析器，验证 loader 的段落归属与顺序
        def parse(name):
            levels = {"L1": [], "L2": []}
            cur = "L2"
            for line in (home / "lint" / name).read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#"):
                    token = s.lstrip("#").strip().upper()
                    if token in ("L1", "L2"):
                        cur = token
                    continue
                levels[cur].append(s)
            return levels

        expected = parse("banned_words.txt")
        got = mechcheck.load_banned_graded()
        assert got == expected
        assert len(got["L1"]) >= 40 and len(got["L2"]) >= 30
        # 首尾各 3 条抽样（顺序也要一致，否则提示词里的清单会错位）
        assert got["L1"][:3] == expected["L1"][:3]
        assert got["L1"][-3:] == expected["L1"][-3:]
        assert got["L2"][:3] == expected["L2"][:3]
        assert got["L2"][-3:] == expected["L2"][-3:]
        assert not set(got["L1"]) & set(got["L2"])

    def test_t15_rule_asset_size(self, home):
        from ainovel import mechcheck

        assert len(mechcheck.load_banned_graded()["L1"]) >= 40
        assert len(mechcheck.load_banned_graded()["L2"]) >= 30
        assert len(mechcheck.load_sensitive_words()) >= 30
        assert len(mechcheck.load_dialog_patterns()) >= 8
        # 分级语义：L1 阻断、L2 只报警
        body = "时光荏苒，岁月如梭。" + "正常句子推进。" * 300
        hits = mechcheck.check(body, word_range=[2200, 2800])
        assert any(h.rule == "M-02" and h.level == "block" for h in hits)
        body2 = "他微微点头。" + "正常句子推进。" * 300
        hits2 = mechcheck.check(body2, word_range=[2200, 2800])
        assert any(h.rule == "M-02" and h.level == "warn" for h in hits2)
        assert not any(h.rule == "M-02" and h.level == "block" for h in hits2)
