"""章节状态机（规格01 §4）。"""
import pytest

from ainovel import statemachine


def test_happy_path(book_env):
    book, _ = book_env
    statemachine.ensure_chapter_row(book, 1)
    assert statemachine.current_status(book, 1) == "awaiting_outline"
    assert statemachine.transition(book, 1, "generate_outline") == "outline_draft"
    assert statemachine.transition(book, 1, "confirm") == "outline_locked"
    assert statemachine.transition(book, 1, "start_generate") == "generating"
    assert statemachine.transition(book, 1, "complete") == "mech_check"
    assert statemachine.transition(book, 1, "pass") == "llm_review"
    assert statemachine.transition(book, 1, "pass") == "pending_finalize"
    assert statemachine.transition(book, 1, "finalize") == "finalized"
    assert statemachine.transition(book, 1, "rewrite") == "generating"


def test_fix_loop(book_env):
    book, _ = book_env
    statemachine.ensure_chapter_row(book, 2)
    for ev in ("generate_outline", "confirm", "start_generate", "complete"):
        statemachine.transition(book, 2, ev)
    assert statemachine.transition(book, 2, "blocked") == "lint_fix"
    assert statemachine.transition(book, 2, "recheck") == "mech_check"
    assert statemachine.transition(book, 2, "blocked_after_fix") == "human_review"
    assert statemachine.transition(book, 2, "approve") == "pending_finalize"


def test_invalid_transition_raises(book_env):
    book, _ = book_env
    statemachine.ensure_chapter_row(book, 3)
    with pytest.raises(statemachine.StateError):
        statemachine.transition(book, 3, "finalize")  # awaiting_outline 直接定稿


def test_error_retry_returns_prev(book_env):
    book, _ = book_env
    statemachine.ensure_chapter_row(book, 4)
    statemachine.transition(book, 4, "generate_outline")
    statemachine.mark_error(book, 4, "boom")
    assert statemachine.current_status(book, 4) == "error"
    assert statemachine.transition(book, 4, "retry") == "outline_draft"


def test_outline_regen_allowed(book_env):
    book, _ = book_env
    statemachine.ensure_chapter_row(book, 5)
    statemachine.transition(book, 5, "generate_outline")
    assert statemachine.transition(book, 5, "generate_outline") == "outline_draft"


def test_human_review_can_recheck(book_env):
    """人工复核后修好，必须能回到机检重新判定。

    没有这条出边时：机检阻断 → human_review → 定点修复干净了也没处验证，
    只能填理由「豁免」放行，把已经合格的章记成一条豁免记录，证据链反而被污染。
    """
    book, _ = book_env
    statemachine.ensure_chapter_row(book, 6)
    for ev in ("generate_outline", "confirm", "start_generate", "complete"):
        statemachine.transition(book, 6, ev)
    statemachine.transition(book, 6, "blocked")
    statemachine.transition(book, 6, "recheck")
    assert statemachine.transition(book, 6, "blocked_after_fix") == "human_review"
    # 关键：human_review 能重新进机检
    assert statemachine.transition(book, 6, "recheck") == "mech_check"
    # 且重新进机检后仍能正常往下走
    assert statemachine.transition(book, 6, "pass") == "llm_review"


def test_human_review_can_recheck(book_env):
    """人工复核后修好，必须能回到机检重新判定。

    没有这条出边时：机检阻断 → human_review → 定点修复干净了也没处验证，
    只能填理由「豁免」放行，把已经合格的章记成一条豁免记录，证据链反而被污染。
    """
    book, _ = book_env
    statemachine.ensure_chapter_row(book, 6)
    for ev in ("generate_outline", "confirm", "start_generate", "complete"):
        statemachine.transition(book, 6, ev)
    statemachine.transition(book, 6, "blocked")
    statemachine.transition(book, 6, "recheck")
    assert statemachine.transition(book, 6, "blocked_after_fix") == "human_review"
    # 关键：human_review 能重新进机检
    assert statemachine.transition(book, 6, "recheck") == "mech_check"
    # 且重新进机检后仍能正常往下走
    assert statemachine.transition(book, 6, "pass") == "llm_review"
