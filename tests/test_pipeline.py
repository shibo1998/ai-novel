"""管线全链路（MockLLM）：规格03 §1 的完整闭环 + 各异常分支。"""
import json
import re

import pytest

from ainovel import chapters, conflicts, db, mechcheck, pipeline, statemachine
from ainovel.mockllm import MockLLM, make_prose


def _run_to_pending(book, no, mock):
    pipeline.generate_outline(book, no, client=mock)
    pipeline.confirm_outline(book, no)
    pipeline.generate_prose(book, no, client=mock)
    pipeline.run_mech_check(book, no, client=mock)
    return pipeline.run_llm_review(book, no, client=mock)


def test_full_loop_pass(book_env):
    book, _ = book_env
    mock = MockLLM()
    rev = _run_to_pending(book, 1, mock)
    assert rev["verdict"] == "pass"
    assert statemachine.current_status(book, 1) == "pending_finalize"

    fin = pipeline.finalize(book, 1, client=mock)
    assert fin["snapshot_version"] == 1
    assert statemachine.current_status(book, 1) == "finalized"
    prose = chapters.read_prose(book, 1)
    assert prose["status"] == "final" and prose["finalized_at"]

    # 快照内容 = 正文
    assert pipeline.get_snapshot(book, 1, 1) == prose["_body"]
    # 记忆卡已落盘
    mem = chapters.read_memory(book, 1)
    assert mem["chapter_no"] == 1
    # generation_log 有完整证据链（prompt_digest 等）
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT stage, prompt_digest, cost_cents FROM generation_log "
            "WHERE book_id=? AND chapter_no=1 ORDER BY id", (1,)
        ).fetchall()
    stages = [r["stage"] for r in rows]
    assert "outline" in stages and "prose" in stages and "review" in stages and "memory" in stages
    assert all(r["prompt_digest"] for r in rows)


def test_mech_block_auto_fix_one_round(book_env):
    book, _ = book_env

    def prose_with_banned(no, rng):
        body = make_prose(rng)
        return body + "\n时光荏苒，一切如旧。\n"

    mock = MockLLM()
    mock.prose_factory = prose_with_banned
    pipeline.generate_outline(book, 1, client=mock)
    pipeline.confirm_outline(book, 1)
    pipeline.generate_prose(book, 1, client=mock)
    hits = pipeline.run_mech_check(book, 1, client=mock)
    # 自动 V5 修复一轮后应通过（mock 修复 = 删除禁词）
    assert not any(h.level == "block" for h in hits if h.rule == "M-02")
    assert statemachine.current_status(book, 1) == "llm_review"
    assert "时光荏苒" not in chapters.read_prose(book, 1)["_body"]


def test_sensitive_word_goes_straight_to_human(book_env):
    book, _ = book_env

    def prose_sensitive(no, rng):
        return make_prose(rng) + "\n他低声说起了国家领导人。"

    mock = MockLLM()
    mock.prose_factory = prose_sensitive
    pipeline.generate_outline(book, 1, client=mock)
    pipeline.confirm_outline(book, 1)
    pipeline.generate_prose(book, 1, client=mock)
    calls_before = len(mock.calls)
    hits = pipeline.run_mech_check(book, 1, client=mock)
    assert any(h.rule == "M-04" for h in hits)
    assert statemachine.current_status(book, 1) == "human_review"
    # 敏感词禁止自动改写：没有发生 V5 调用
    assert len(mock.calls) == calls_before


def test_review_fix_loop(book_env):
    book, _ = book_env
    mock = MockLLM()
    mock.review_verdicts = [
        {"scores": {"ai_flavor": 60}, "issues": [
            {"category": "ai_flavor", "severity": "blocking", "location": "章末",
             "quote": "他停住脚。", "description": "模板句", "suggestion": "改具体动作"}],
         "verdict": "fix"},
    ]
    rev = _run_to_pending(book, 1, mock)
    assert rev["verdict"] == "pass"  # 第二轮（默认 pass）
    assert statemachine.current_status(book, 1) == "pending_finalize"
    row = statemachine.get_row(book, 1)
    assert row["fix_rounds"] == 1


def test_fix_overreach_discards(book_env):
    book, _ = book_env
    mock = MockLLM()
    mock.fix_overreach = True
    mock.review_verdicts = [
        {"scores": {"plot": 60}, "issues": [
            {"category": "plot", "severity": "blocking", "location": "章中",
             "quote": "令牌烫了一下。", "description": "与档案冲突", "suggestion": "改"}],
         "verdict": "fix"},
    ]
    pipeline.generate_outline(book, 1, client=mock)
    pipeline.confirm_outline(book, 1)
    pipeline.generate_prose(book, 1, client=mock)
    pipeline.run_mech_check(book, 1, client=mock)
    rev = pipeline.run_llm_review(book, 1, client=mock)
    assert rev.get("note") and "越权" in rev["note"]
    assert statemachine.current_status(book, 1) == "human_review"
    # 正文未被越权输出覆盖
    assert "越权重写测试" not in chapters.read_prose(book, 1)["_body"]


def test_exempt_requires_reason(book_env):
    book, _ = book_env
    mock = MockLLM()
    _run_to_pending(book, 1, mock)
    pipeline.finalize(book, 1, client=mock)
    # finalized 后走重写 → human_review 不直接可达，这里用状态机驱动
    statemachine.transition(book, 1, "rewrite")
    pipeline.generate_prose(book, 1, client=mock)
    pipeline.run_mech_check(book, 1, client=mock)
    pipeline.run_llm_review(book, 1, client=mock)  # pass → pending
    pipeline.finalize(book, 1, client=mock)
    assert statemachine.current_status(book, 1) == "finalized"

    # 豁免路径：另开一章推到 human_review
    mock2 = MockLLM()
    mock2.review_verdicts = [{"scores": {"plot": 50}, "issues": [], "verdict": "fail"}]
    _run_to_pending(book, 2, mock2)
    assert statemachine.current_status(book, 2) == "human_review"
    with pytest.raises(pipeline.PipelineError):
        pipeline.exempt(book, 2, "  ")
    pipeline.exempt(book, 2, "作者判断为风格选择")
    assert statemachine.current_status(book, 2) == "pending_finalize"
    with db.tx() as conn:
        row = conn.execute(
            "SELECT error FROM generation_log WHERE stage='exempt' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert "风格选择" in row["error"]


def test_rewrite_from_snapshot_versions(book_env):
    book, _ = book_env
    mock = MockLLM()
    _run_to_pending(book, 1, mock)
    pipeline.finalize(book, 1, client=mock)
    v2 = pipeline.human_rewrite(book, 1, base_version=1)
    assert v2 == 2
    assert statemachine.current_status(book, 1) == "generating"
    assert chapters.read_prose(book, 1)["version"] == 2
    assert len(pipeline.list_versions(book, 1)) == 1  # v1 快照保留
    # 旧版本可回看
    assert pipeline.get_snapshot(book, 1, 1)


def test_outline_hook_repeat_rejected_and_retry(book_env):
    book, _ = book_env
    mock = MockLLM()

    def bad_then_good(no, user):
        if "驳回" not in user:
            # 两次都返回与上一章相同钩子类型时才触发；第 1 章无上一章 → 构造非法 hook.type
            return f"""---
chapter_no: {no}
volume: 1
stage: 发展
emotion_task: 小扬
purpose: [推进主线]
core_event: "测试"
scenes:
  - {{ goal: "g", conflict: "c", chars: [{{ name: "林寒", state: "s" }}], end_state: "e" }}
emotion_arc: {{ from: "a", via: "b", to: "c" }}
must_include: ["x"]
hook: {{ content: "h", type: "悬念" }}
foreshadow_ops: []
word_range: [2200, 2800]
pov: 林寒
confirmed: false
---
场景
"""
        # 第二次（回喂后）返回合法输出
        return MockLLM()._default_outline(no)

    # 第1章上一章钩子类型为空 → 不会触发重复校验；改测 pay 前置校验
    def outline_with_bad_pay(no, user):
        if "驳回" not in user:
            base = MockLLM()._default_outline(no)
            return base.replace(
                "foreshadow_ops: []",
                'foreshadow_ops:\n  - { op: pay, fo_id: "FO-001", note: "" }')
        return MockLLM()._default_outline(no)

    mock.outline_factory = outline_with_bad_pay
    fm = pipeline.generate_outline(book, 1, client=mock)
    assert fm["foreshadow_ops"] == []  # 回喂重试后用了合法输出
    assert statemachine.current_status(book, 1) == "outline_draft"


def test_assembly_preview_saved(book_env):
    book, _ = book_env
    mock = MockLLM()
    pipeline.generate_outline(book, 1, client=mock)
    pv = pipeline.build_preview(book, 1)
    assert pv["saved_to"] and "total_est_tokens" in pv
    assert len(pv["slots"]) == 13


def test_scene_special_prompt_override(book_env):
    """W2/W4/W5 场景专项：强制指定后走对应提示词。"""
    book, _ = book_env
    mock = MockLLM()
    pipeline.generate_outline(book, 1, client=mock)
    pipeline.confirm_outline(book, 1)
    pipeline.generate_prose(book, 1, client=mock, prompt_id="W4")
    w4_call = next(c for c in mock.calls if "突破场景" in c["user"])
    assert "力量体系相关行" in w4_call["user"] or "突破代价设定" in w4_call["user"]
    assert 2200 <= mechcheck.count_words(chapters.read_prose(book, 1)["_body"]) <= 2800


def test_polish_ok_and_reject(book_env):
    """V2+V3 润色链：通过时落盘并记 polish 审阅；字数漂移>10% 时丢弃。"""
    from ainovel import db as _db

    book, _ = book_env
    mock = MockLLM()
    _run_to_pending(book, 1, mock)

    r = pipeline.polish(book, 1, client=mock)
    assert r["ok"] and r["v3"]["verdict"] == "pass", r
    assert r["changes"] and r["changes"][0].startswith("|")
    with _db.tx() as conn:
        row = conn.execute(
            "SELECT verdict FROM review_records WHERE kind='polish' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row["verdict"] == "pass"

    # 漂移超限：V2 mock 输出正文 + 一大段新文字 → 丢弃且文件不变
    before = chapters.read_prose(book, 1)["_body"]
    orig = MockLLM._route

    def bloated(self, system, user):
        if "===润色全文===" in user:
            m = re.search(r"【正文】\n(.*?)\n\n", user, re.DOTALL)
            body = m.group(1) if m else user
            return "===润色全文===\n" + body + "\n新增的水字" * 400 + "\n\n===改动清单===\n| x | y | z |"
        return orig(self, system, user)

    MockLLM._route = bloated
    try:
        r2 = pipeline.polish(book, 1, client=mock)
    finally:
        MockLLM._route = orig
    assert not r2["ok"] and "字数浮动" in r2["reason"]
    assert chapters.read_prose(book, 1)["_body"] == before


def test_rubber_stamp_guard(book_env):
    """防橡皮章：pass+零问题+全维≥90 → 强制复审一次，采用第二轮结果。"""
    book, _ = book_env
    mock = MockLLM()
    _run_to_pending(book, 3, mock) if False else None
    # 手动推进到 llm_review
    pipeline.generate_outline(book, 1, client=mock)
    pipeline.confirm_outline(book, 1)
    pipeline.generate_prose(book, 1, client=mock)
    pipeline.run_mech_check(book, 1, client=mock)
    mock.review_verdicts = [
        {"scores": {"ai_flavor": 95, "plot": 95, "pacing": 95, "prose": 95, "hook": 95},
         "issues": [], "verdict": "pass"},  # 橡皮章
        {"scores": {"ai_flavor": 82, "plot": 85, "pacing": 80, "prose": 84, "hook": 86},
         "issues": [
             {"category": "prose", "severity": "warning", "location": "章中",
              "quote": "他停住脚。", "description": "动作描写重复", "suggestion": "换具体动作"},
             {"category": "pacing", "severity": "warning", "location": "章末",
              "quote": "夜风扑面而来。", "description": "收尾偏静", "suggestion": "提前一行切钩子"},
             {"category": "ai_flavor", "severity": "warning", "location": "章首",
              "quote": "劣质防盗门", "description": "形容词堆叠", "suggestion": "删一个形容词"}],
         "verdict": "pass"},  # 复审后：pass 但分数真实、带问题清单
    ]
    r = pipeline.run_llm_review(book, 1, client=mock)
    assert r.get("re_reviewed") is True
    assert r["verdict"] == "pass" and r["scores"]["ai_flavor"] == 82
    assert len(r["issues"]) == 3
    v1_calls = [c for c in mock.calls if "五维审校" in c["user"] or "评分锚点" in c["user"]]
    assert len(v1_calls) == 2  # 复审确实发生且未被再次触发
