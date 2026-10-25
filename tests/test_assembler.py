"""13 槽位装配器：永不截断槽位 + 截断顺序 + 预览（规格03 §2.2）。"""
from ainovel import assembler, pipeline, settings, statemachine
from ainovel.mockllm import MockLLM


def _prep(book):
    pipeline.generate_outline(book, 1, client=MockLLM())
    pipeline.confirm_outline(book, 1)


def test_all_slots_present(book_env):
    book, _ = book_env
    _prep(book)
    a = assembler.assemble(book, 1)
    for slot in assembler.SLOT_TITLES:
        assert slot in a.slots, slot
    pv = a.preview()
    assert len(pv["slots"]) == 13
    assert pv["total_est_tokens"] > 0


def test_never_truncate_slots(book_env):
    book, _ = book_env
    _prep(book)
    # 极小预算：可砍槽全砍空，永不截断槽位保留完整内容（允许超预算）
    settings.update({"assembly_budget_tokens": 50})
    a = assembler.assemble(book, 1)
    for slot in assembler.NEVER_TRUNCATE:
        assert a.slots.get(slot), slot
    for slot in assembler.CUT_ORDER:
        assert a.slots.get(slot) in ("", None) or "已截断" in a.slots[slot]
    protected_sum = sum(assembler.est_tokens(a.slots[s]) for s in assembler.NEVER_TRUNCATE if s != "system")
    assert a.total_est_tokens <= protected_sum + 60  # 仅剩截断标记的开销


def test_truncation_order(book_env):
    book, _ = book_env
    _prep(book)
    # 预算刚好够：先砍 fts，再砍 recent，再砍 bible
    settings.update({"assembly_budget_tokens": 100000})
    a_full = assembler.assemble(book, 1)
    total = a_full.total_est_tokens
    # 设一个砍掉 fts 后即可容纳的预算
    fts_cost = assembler.est_tokens(a_full.slots["fts_recall"])
    settings.update({"assembly_budget_tokens": total - fts_cost + 1})
    a = assembler.assemble(book, 1)
    assert "fts_recall" in a.truncated
    assert "recaps" not in a.truncated
    assert "outline_contract" not in a.truncated


def test_char_cards_and_contract(book_env):
    book, _ = book_env
    _prep(book)
    a = assembler.assemble(book, 1)
    assert "林寒" in a.slots["char_cards"]
    assert "core_event" in a.slots["outline_contract"] or "玄铁令" in a.slots["outline_contract"]
    assert "卷" in a.slots["volume_rhythm"]
    assert "pov" in a.slots["output_rules"] and "字数" in a.slots["output_rules"]
    assert a.slots["recaps"]



def test_fts_recall_short_keyword(book_env):
    """两字关键词（角色名）在 trigram 模式下靠子串兜底命中。"""
    book, _ = book_env
    _prep(book)
    pipeline.generate_prose(book, 1, client=MockLLM())
    pipeline.run_mech_check(book, 1, client=MockLLM())
    pipeline.run_llm_review(book, 1, client=MockLLM())
    pipeline.finalize(book, 1, client=MockLLM())  # 定稿才入 FTS

    pipeline.generate_outline(book, 2, client=MockLLM())
    pipeline.confirm_outline(book, 2)
    a = assembler.assemble(book, 2)
    # 第 2 章应能检索到第 1 章正文片段（含"林寒"）
    assert "林寒" in a.slots["fts_recall"] or "第1章" in a.slots["fts_recall"]
