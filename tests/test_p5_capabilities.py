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
        # 状态类检查要等 V4 产出快照，此刻只能如实报"未执行"，且不得影响判定
        assert [i.rule for i in res.ineffective] == ["M-13..M-15"]
        assert all(not i.blocking for i in res.ineffective)
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


# ---------------------------------------------------------------------------
# T-16 ~ T-19  F-04 记忆卡补做
# ---------------------------------------------------------------------------

def _memory_with_state(no: int, ops: list[dict] | None = None) -> dict:
    return {
        "chapter_no": no,
        "char_updates": [],
        "new_chars": [],
        "new_locations": [],
        "foreshadow_ops": ops or [],
        "new_settings_pending": [],
        "state_delta": [
            {"character": "林寒", "location": "青云坊", "physical": "右肩刀伤",
             "mental": "警觉", "power_level": "炼气", "arc_stage": 1,
             "knowledge": ["玄铁令发热"], "goals": ["查令牌"]},
            {"character": "老周", "location": "坊市西口", "physical": "无恙",
             "mental": "犹豫", "power_level": "炼气", "arc_stage": 1,
             "knowledge": [], "goals": ["盯住林寒"]},
        ],
        "timeline_candidates": [],
        "event_summary": "林寒验出令牌异常，老周在暗处跟踪。",
        "time_elapsed": "半日",
    }


def _finalize_chapter(book: str, no: int = 1, mem: dict | None = None) -> MockLLM:
    mock = MockLLM()
    if mem is not None:
        mock.memory_factory = lambda n: dict(mem, chapter_no=n)
    pipeline.generate_outline(book, no, client=mock)
    pipeline.confirm_outline(book, no)
    pipeline.generate_prose(book, no, client=mock)
    pipeline.run_mech_check(book, no, client=mock)
    pipeline.run_llm_review(book, no, client=mock)
    pipeline.finalize(book, no, client=mock)
    assert statemachine.current_status(book, no) == "finalized"
    return mock


class _BoomLLM:
    """永远抛错：用于验收"补做失败不能算已处理"。"""

    def chat(self, **_kw):
        from ainovel import llm

        raise llm.LLMError("模拟模型不可用")


class TestMemoryRebuild:
    def test_t16_missing_card_can_be_rebuilt(self, home, book_env):
        book, _ = book_env
        mock = _finalize_chapter(book, 1, _memory_with_state(1))
        chapters.memory_path(book, 1).unlink()

        pending = pipeline.memory_card_pending(book)
        assert [p["no"] for p in pending] == [1]
        assert pending[0]["reason"] == "missing"

        pipeline.rebuild_memory_card(book, 1, client=mock)
        assert pipeline.memory_card_pending(book) == []
        assert chapters.memory_path(book, 1).exists()

    def test_t17_non_finalized_chapter_rejected(self, home, book_env):
        book, _ = book_env
        mock = _ready_chapter(book)
        with pytest.raises(statemachine.StateError):
            pipeline.rebuild_memory_card(book, 1, client=mock)
        r = client.post(f"/api/books/{book}/chapters/1/memory-card/rebuild")
        assert r.status_code == 409

    def test_t18_rebuild_is_idempotent_for_foreshadow(self, home, book_env):
        book, _ = book_env
        ops = [{"op": "advance", "fo_id": "FO-001", "note": "令牌再热"}]
        mock = _finalize_chapter(book, 1, _memory_with_state(1, ops))

        from ainovel import outline

        def fo():
            return [r for r in outline.read_plot_points(book) if r["编号"] == "FO-001"][0]

        assert fo()["状态"] == "building"
        assert fo()["推进章"] == "1"

        pipeline.rebuild_memory_card(book, 1, client=mock)
        pipeline.rebuild_memory_card(book, 1, client=mock)

        assert fo()["状态"] == "building"       # 不能被推进两次或跳级
        assert fo()["推进章"] == "1"
        from ainovel import state as state_mod

        assert len(state_mod.chapter_rows(book, 1)) == 2  # 快照不重复

    def test_t19_failure_keeps_chapter_pending(self, home, book_env):
        book, _ = book_env
        _finalize_chapter(book, 1, _memory_with_state(1))
        chapters.memory_path(book, 1).unlink()

        with pytest.raises(Exception):
            pipeline.rebuild_memory_card(book, 1, client=_BoomLLM())

        assert [p["no"] for p in pipeline.memory_card_pending(book)] == [1]
        assert not chapters.memory_path(book, 1).exists()


# ---------------------------------------------------------------------------
# T-20 ~ T-25  F-05 按章状态快照
# ---------------------------------------------------------------------------

class TestStateSnapshot:
    def test_t20_state_delta_lands_in_table_and_state_md(self, home, book_env):
        from ainovel import state as state_mod

        book, _ = book_env
        _finalize_chapter(book, 1, _memory_with_state(1))

        rows = state_mod.chapter_rows(book, 1)
        assert len(rows) == 2
        assert {r["character"] for r in rows} == {"林寒", "老周"}
        lin = [r for r in rows if r["character"] == "林寒"][0]
        assert lin["power_level"] == "炼气"
        assert lin["knowledge"] == ["玄铁令发热"]

        md = state_mod.read_state_md(book)
        assert "截至第 1 章" in md
        assert "| 林寒 | 青云坊 | 右肩刀伤 |" in md
        assert "generated by V4; do not edit" in md

    def test_t21_legacy_card_does_not_raise(self, home, book_env):
        book, _ = book_env
        legacy = {
            "chapter_no": 1, "char_updates": [], "new_chars": [],
            "new_locations": [], "foreshadow_ops": [],
            "event_summary": "老格式记忆卡", "time_elapsed": "一日",
        }
        chapters.write_memory(book, 1, legacy, "老格式\n")
        card = chapters.read_memory(book, 1)
        assert card["legacy"] is True
        assert card["event_summary"] == "老格式记忆卡"

    def test_t22_power_regression_blocks(self, home, book_env):
        from ainovel import state as state_mod

        book, _ = book_env
        state_mod.upsert_entries(book, 5, [{"character": "林寒", "power_level": "筑基"}])
        state_mod.upsert_entries(book, 6, [{"character": "林寒", "power_level": "炼气"}])
        ctx = state_mod.mech_context(book, 6)
        res = mechcheck.check_full(make_prose((2200, 2800)),
                                   word_range=[2200, 2800], context=ctx)
        assert any(h.rule == "M-13" and h.level == "block" for h in res.hits)
        assert res.verdict == "blocked"

    def test_t23_dead_character_reappearing_blocks(self, home, book_env):
        from ainovel import characters, state as state_mod

        book, _ = book_env
        characters.apply_status_updates(book, 3, [{"name": "老周", "changes": {"alive": False}}])
        state_mod.upsert_entries(book, 4, [{"character": "老周",
                                            "location": "坊市西口",
                                            "physical": "提着灯"}])
        ctx = state_mod.mech_context(book, 4)
        res = mechcheck.check_full(make_prose((2200, 2800)),
                                   word_range=[2200, 2800], context=ctx)
        assert any(h.rule == "M-14" and h.level == "block" for h in res.hits)

    def test_t24_state_md_truncated_when_too_large(self, home, book_env, monkeypatch):
        from ainovel import state as state_mod

        book, _ = book_env
        _finalize_chapter(book, 1, _memory_with_state(1))
        monkeypatch.setattr(state_mod, "STATE_MAX_BYTES", 600)
        text = state_mod.rebuild_state_md(book, upto_chapter=1)
        assert len(text.encode("utf-8")) <= 600

    def test_t25_upsert_not_duplicate(self, home, book_env):
        from ainovel import state as state_mod

        book, _ = book_env
        entries = [{"character": "林寒", "power_level": "炼气", "location": "坊市"}]
        state_mod.upsert_entries(book, 1, entries)
        state_mod.upsert_entries(book, 1, [{"character": "林寒", "power_level": "炼气",
                                            "location": "乱葬岗"}])
        rows = state_mod.chapter_rows(book, 1)
        assert len(rows) == 1
        assert rows[0]["location"] == "乱葬岗"


# ---------------------------------------------------------------------------
# T-26 ~ T-29  F-06 双时间线
# ---------------------------------------------------------------------------

_TIMELINE_OK = """# 时间线

## 一、作者真相（故事世界实际发生）

| 编号 | 章 | 事件 | 涉及角色 | 读者已知 | 知情角色 |
|---|---|---|---|---|---|
| T-001 | 1 | 林寒父母死于货柜爆炸 | 林寒 | 是 | 林寒,老周 |
| T-002 | 3 | 老周其实是幕后主使 | 老周 | 否 | 老周 |

## 二、读者已知（按章释放）

| 章 | 本章向读者释放的真相 |
|---|---|
| 1 | T-001 |
"""


class TestTimeline:
    def test_t26_duplicate_id_blocks(self, home, book_env):
        from ainovel import timeline

        book, _ = book_env
        timeline.timeline_path(book).write_text(
            _TIMELINE_OK.replace("| T-002 | 3 |", "| T-001 | 3 |"), encoding="utf-8")
        issues = timeline.validate(book)
        dup = [i for i in issues if "编号重复" in i["message"]]
        assert dup and dup[0]["level"] == "block"
        assert dup[0]["line"] > 0

    def test_t27_unknown_informed_character_blocks(self, home, book_env):
        from ainovel import timeline

        book, _ = book_env
        timeline.timeline_path(book).write_text(
            _TIMELINE_OK.replace("| 老周 | 否 | 老周 |", "| 老周 | 否 | 张不存在 |"),
            encoding="utf-8")
        issues = timeline.validate(book)
        assert any("张不存在" in i["message"] and i["level"] == "block" for i in issues)

    def test_t28_candidates_wait_for_human(self, home, book_env):
        from ainovel import conflicts, timeline

        book, _ = book_env
        mem = _memory_with_state(1)
        mem["timeline_candidates"] = [
            {"event": "老周当年放走了林寒的父亲", "characters": ["老周"], "reader_known": False}]
        _finalize_chapter(book, 1, mem)

        items = [c for c in conflicts.list_open(book) if c["kind"] == "timeline_candidate"]
        assert len(items) == 1
        assert not timeline.timeline_path(book).exists()  # 未确认不得落盘

        conflicts.resolve(items[0]["id"], "adopt_ai")
        data = timeline.read(book)
        assert data["exists"] and data["truth"][0]["编号"] == "T-001"
        assert data["truth"][0]["读者已知"] == "否"

    def test_t29_assembly_injects_constraints_for_present_characters(self, home, book_env):
        from ainovel import assembler, timeline

        book, _ = book_env
        timeline.timeline_path(book).write_text(_TIMELINE_OK, encoding="utf-8")
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        preview = assembler.assemble(book, 1).preview()
        bible_slot = [s for s in preview["slots"] if s["name"] == "bible_core"][0]
        assert "【时间线约束】" in bible_slot["content"]
        assert "T-002" in bible_slot["content"]
        assert "林寒 不知情" in bible_slot["content"]
        # 只为本章出场角色注入，不把全表刷进来
        assert "老周 不知情" not in bible_slot["content"]


# ---------------------------------------------------------------------------
# T-26 ~ T-29  F-06 双时间线
# ---------------------------------------------------------------------------

_TIMELINE_OK = """# 时间线

## 一、作者真相（故事世界实际发生）

| 编号 | 章 | 事件 | 涉及角色 | 读者已知 | 知情角色 |
|---|---|---|---|---|---|
| T-001 | 1 | 林寒父母死于货柜爆炸 | 林寒 | 是 | 林寒,老周 |
| T-002 | 3 | 老周其实是幕后主使 | 老周 | 否 | 老周 |

## 二、读者已知（按章释放）

| 章 | 本章向读者释放的真相 |
|---|---|
| 1 | T-001 |
"""


class TestTimeline:
    def test_t26_duplicate_id_blocks(self, home, book_env):
        from ainovel import timeline

        book, _ = book_env
        timeline.timeline_path(book).write_text(
            _TIMELINE_OK.replace("| T-002 | 3 |", "| T-001 | 3 |"), encoding="utf-8")
        issues = timeline.validate(book)
        dup = [i for i in issues if "编号重复" in i["message"]]
        assert dup and dup[0]["level"] == "block"
        assert dup[0]["line"] > 0

    def test_t27_unknown_informed_character_blocks(self, home, book_env):
        from ainovel import timeline

        book, _ = book_env
        timeline.timeline_path(book).write_text(
            _TIMELINE_OK.replace("| 老周 | 否 | 老周 |", "| 老周 | 否 | 张不存在 |"),
            encoding="utf-8")
        issues = timeline.validate(book)
        assert any("张不存在" in i["message"] and i["level"] == "block" for i in issues)

    def test_t28_candidates_wait_for_human(self, home, book_env):
        from ainovel import conflicts, timeline

        book, _ = book_env
        mem = _memory_with_state(1)
        mem["timeline_candidates"] = [
            {"event": "老周当年放走了林寒的父亲", "characters": ["老周"], "reader_known": False}]
        _finalize_chapter(book, 1, mem)

        items = [c for c in conflicts.list_open(book) if c["kind"] == "timeline_candidate"]
        assert len(items) == 1
        assert not timeline.timeline_path(book).exists()  # 未确认不得落盘

        conflicts.resolve(items[0]["id"], "adopt_ai")
        data = timeline.read(book)
        assert data["exists"] and data["truth"][0]["编号"] == "T-001"
        assert data["truth"][0]["读者已知"] == "否"

    def test_t29_assembly_injects_constraints_for_present_characters(self, home, book_env):
        from ainovel import assembler, timeline

        book, _ = book_env
        timeline.timeline_path(book).write_text(_TIMELINE_OK, encoding="utf-8")
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        preview = assembler.assemble(book, 1).preview()
        bible_slot = [s for s in preview["slots"] if s["name"] == "bible_core"][0]
        assert "【时间线约束】" in bible_slot["content"]
        assert "T-002" in bible_slot["content"]
        assert "林寒 不知情" in bible_slot["content"]
        # 只为本章出场角色注入，不把全表刷进来
        assert "老周 不知情" not in bible_slot["content"]


# ---------------------------------------------------------------------------
# T-30 ~ T-34  F-07 外部书稿挂载
# ---------------------------------------------------------------------------

def _make_native_book(root: Path, name: str = "已有书") -> Path:
    d = root / name
    (d / "chapters").mkdir(parents=True)
    (d / "characters").mkdir()
    (d / "config.json").write_text(json.dumps({"chapter_word_range": [2200, 2800]},
                                              ensure_ascii=False), encoding="utf-8")
    (d / "meta.md").write_text("---\ntitle: 已有书\ngenre: 都市\ntarget_words: 200000\n"
                               "status: drafting\n---\n说明\n", encoding="utf-8")
    (d / "chapters" / "001_正文.md").write_text(
        "---\nchapter_no: 1\nversion: 1\nstatus: final\nword_count: 12\n---\n他推开门。\n",
        encoding="utf-8")
    return d


class TestAttach:
    def test_t30_attach_native_book(self, home, book_env):
        from ainovel import attach

        src = _make_native_book(home / "外部")
        before = sorted(p.name for p in src.rglob("*"))
        res = attach.attach("外挂书", src, scaffold=False)
        after = sorted(p.name for p in src.rglob("*"))

        assert res["detected"]["layout"] == "native"
        assert res["detected"]["chapters"] == 1
        assert before == after           # 不复制、不生成任何文件
        assert chapters.list_chapters("外挂书") == [1]
        assert any(b["name"] == "外挂书" and b["external"] for b in books.list_books())

    def test_t31_flat_txt_detected(self, home, book_env):
        from ainovel import attach

        d = home / "裸稿"
        d.mkdir()
        (d / "全本.txt").write_text("第一章 夜雨\n正文\n", encoding="utf-8")
        res = attach.attach("裸稿书", d, scaffold=False)
        assert res["detected"]["layout"] == "flat-txt"
        assert res["detected"]["has_bible"] is False  # 缺失必须如实报告

    def test_t32_shallow_path_rejected(self, home, book_env):
        from ainovel import attach

        r = client.post("/api/books/attach", json={"name": "越界", "path": "D:/1-work"})
        assert r.status_code == 400
        with pytest.raises(attach.AttachError):
            attach.attach("越界", "D:/1-work")

    def test_t33_scaffold_never_overwrites(self, home, book_env):
        from ainovel import attach

        d = home / "半成品"
        d.mkdir()
        (d / "STORY_BIBLE.md").write_text("# 我自己写的圣经\n", encoding="utf-8")
        target = d / "STORY_BIBLE.md"
        before = (target.stat().st_mtime_ns, target.read_text(encoding="utf-8"))

        res = attach.attach("半成品书", d, scaffold=True)

        after = (target.stat().st_mtime_ns, target.read_text(encoding="utf-8"))
        assert before == after
        assert "STORY_BIBLE.md" not in res["scaffolded"]
        assert "OUTLINE.md" in res["scaffolded"]

    def test_t34_detach_keeps_files(self, home, book_env):
        from ainovel import attach

        src = _make_native_book(home / "外部2", "待摘书")
        attach.attach("待摘书", src, scaffold=False)
        assert client.post("/api/books/待摘书/detach", json={"delete_files": False}).status_code == 200
        assert src.exists() and (src / "chapters").exists()
        assert all(b["name"] != "待摘书" for b in books.list_books())


# ---------------------------------------------------------------------------
# T-35 ~ T-40  F-08 导入现存 txt
# ---------------------------------------------------------------------------

def _make_txt(path: Path, count: int = 12, skip: int | None = None,
              encoding: str = "utf-8") -> Path:
    lines = []
    for i in range(1, count + 1):
        if skip and i == skip:
            continue
        lines.append(f"第{i}章 夜雨{i}")
        lines.append("他推开门，雨灌进领口。" * 60)
        lines.append("")
    path.write_text("\n".join(lines), encoding=encoding)
    return path


class TestImport:
    def test_t35_import_twelve_chapters(self, home, book_env):
        from ainovel import importer

        book, _ = book_env
        txt = _make_txt(home / "全本.txt", 12)
        res = importer.commit(book, txt, mode="draft", confirm_warnings=True)
        assert res["imported"] == 12
        assert chapters.list_chapters(book) == list(range(1, 13))
        assert statemachine.current_status(book, 3) == "pending_finalize"

    def test_t36_preview_writes_nothing(self, home, book_env):
        from ainovel import importer

        book, _ = book_env
        txt = _make_txt(home / "全本.txt", 3)
        bdir = books.book_dir(book)
        before = sorted(str(p.relative_to(bdir)) for p in bdir.rglob("*"))
        pv = importer.preview(txt)
        after = sorted(str(p.relative_to(bdir)) for p in bdir.rglob("*"))
        assert before == after
        assert len(pv["chapters"]) == 3
        assert pv["total_chars"] > 0

    def test_t37_gap_requires_confirmation(self, home, book_env):
        from ainovel import importer

        book, _ = book_env
        txt = _make_txt(home / "缺章.txt", 12, skip=7)
        pv = importer.preview(txt)
        assert any(w["kind"] == "gap" for w in pv["warnings"])
        r = client.post(f"/api/books/{book}/import/novel/commit",
                        json={"file_path": str(txt), "mode": "draft"})
        assert r.status_code == 400
        with pytest.raises(importer.ImportError_):
            importer.commit(book, txt, mode="draft")
        res = importer.commit(book, txt, mode="draft", confirm_warnings=True)
        assert res["imported"] == 11

    def test_t38_gb18030_decoded(self, home, book_env):
        from ainovel import importer

        book, _ = book_env
        txt = _make_txt(home / "gbk.txt", 2, encoding="gb18030")
        pv = importer.preview(txt)
        assert pv["encoding"] in ("gb18030", "utf-8", "utf-16")
        importer.commit(book, txt, mode="draft", confirm_warnings=True)
        body = chapters.read_prose(book, 1)["_body"]
        assert body.startswith("他推开门，雨灌进领口。")

    def test_t39_resume_from_job_file(self, home, book_env):
        from ainovel import importer

        book, _ = book_env
        txt = _make_txt(home / "断点.txt", 6)
        items = importer.split_chapters(importer.read_text(txt)[0],
                                        importer.guess_pattern(importer.read_text(txt)[0]))
        fp = importer._file_fingerprint(txt)
        job = importer._job_path(book, fp)
        importer._save_job(job, {"file_fingerprint": fp, "done": [1, 2, 3]})
        for it in items[:3]:  # 模拟已写完的三章
            chapters.write_prose(book, it["no"], {
                "chapter_no": it["no"], "version": 1, "status": "draft",
                "word_count": it["chars"], "finalized_at": None}, it["body"])
            statemachine.ensure_chapter_row(book, it["no"], it["title"])
            statemachine.set_status_direct(book, it["no"], "pending_finalize")

        res = importer.commit(book, txt, mode="draft", confirm_warnings=True)
        assert res["skipped"] == 3
        assert res["imported"] == 3
        assert chapters.list_chapters(book) == [1, 2, 3, 4, 5, 6]

    def test_t40_imported_chapters_listed_for_memory(self, home, book_env):
        from ainovel import importer

        book, _ = book_env
        txt = _make_txt(home / "成稿.txt", 3)
        importer.commit(book, txt, mode="finalized", confirm_warnings=True)
        pending = pipeline.memory_card_pending(book)
        assert [p["no"] for p in pending] == [1, 2, 3]
        assert all(p["reason"] == "missing" for p in pending)
        assert all(p["has_card"] is False for p in pending)
        # 导入的事实性标记：状态是 finalized，且写了快照（否则回滚对导入章失效）
        assert statemachine.current_status(book, 1) == "finalized"
        snap = client.get(f"/api/books/{book}/chapters/1/snapshots/1")
        assert snap.status_code == 200


# ---------------------------------------------------------------------------
# T-41 ~ T-45  F-09 草稿恢复
# ---------------------------------------------------------------------------

class _PartialLLM:
    """流式吐 500 字后断网：模拟"钱已经花了，稿只写了一半"。"""

    def chat(self, *, stream_cb=None, **_kw):
        from ainovel import llm

        if stream_cb:
            for _ in range(5):
                stream_cb("他把伞收起来，抖了抖水。" * 10)
        raise llm.LLMError("模拟断网")


class TestDraftRecovery:
    def test_t41_interrupted_generation_keeps_draft(self, home, book_env):
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        with pytest.raises(Exception):
            pipeline.generate_prose(book, 1, client=_PartialLLM())

        assert statemachine.current_status(book, 1) == "error"
        info = pipeline.draft_info(book, 1)
        assert info["exists"] is True and info["chars"] > 0
        assert client.get(f"/api/books/{book}/chapters/1/draft").json()["exists"] is True

    def test_t42_adopt_writes_prose_and_goes_to_mech(self, home, book_env):
        book, _ = book_env
        mock = MockLLM()
        pipeline.generate_outline(book, 1, client=mock)
        pipeline.confirm_outline(book, 1)
        with pytest.raises(Exception):
            pipeline.generate_prose(book, 1, client=_PartialLLM())

        r = client.post(f"/api/books/{book}/chapters/1/draft/adopt",
                        json={"force": True})
        assert r.status_code == 200
        assert r.json()["status"] == "mech_check"
        assert not chapters.draft_path(book, 1).exists()
        assert chapters.read_prose(book, 1)["_body"].strip()
        with db.tx() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM generation_log WHERE stage='draft_adopt'").fetchone()
        assert row["c"] == 1

    def test_t43_adopt_without_draft_is_404(self, home, book_env):
        book, _ = book_env
        mock = _ready_chapter(book)
        statemachine.mark_error(book, 1, "模拟失败")
        r = client.post(f"/api/books/{book}/chapters/1/draft/adopt", json={"force": True})
        assert r.status_code == 404

    def test_t44_short_draft_needs_force_and_still_fails_mech(self, home, book_env):
        book, _ = book_env
        mock = _ready_chapter(book)
        statemachine.mark_error(book, 1, "模拟失败")
        chapters.write_draft(book, 1, "他推开门。" * 60)  # ~300 字

        r = client.post(f"/api/books/{book}/chapters/1/draft/adopt", json={})
        assert r.status_code == 422

        r2 = client.post(f"/api/books/{book}/chapters/1/draft/adopt", json={"force": True})
        assert r2.status_code == 200
        res = pipeline.run_mech_check(book, 1, client=mock)
        assert any(h.rule == "M-01" and h.level == "block" for h in res.hits)

    def test_t45_stale_draft_requires_force(self, home, book_env):
        import os
        import time

        book, _ = book_env
        mock = _ready_chapter(book)
        statemachine.mark_error(book, 1, "模拟失败")
        draft = chapters.draft_path(book, 1)
        chapters.write_draft(book, 1, "旧草稿内容。" * 100)
        old = time.time() - 3600
        os.utime(draft, (old, old))

        info = pipeline.draft_info(book, 1)
        assert info["stale"] is True
        r = client.post(f"/api/books/{book}/chapters/1/draft/adopt", json={})
        assert r.status_code == 422
        r2 = client.post(f"/api/books/{book}/chapters/1/draft/adopt", json={"force": True})
        assert r2.status_code == 200
