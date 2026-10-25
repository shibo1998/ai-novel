"""Markdown 表格工具与台账状态机（规格01 §2.5）。"""
from ainovel import outline
from ainovel.mdtable import dump_table, parse_table


def test_parse_table():
    text = "# t\n\n| 编号 | 状态 |\n|---|---|\n| FO-001 | planted |\n| FO-002 | paid |\n"
    cols, rows = parse_table(text, "编号")
    assert cols == ["编号", "状态"]
    assert rows[1]["编号"] == "FO-002"


def test_dump_parse_roundtrip():
    rows = [["A", "B"], ["1", "2"]]
    text = dump_table(["c1", "c2"], rows)
    cols, got = parse_table(text, "c1")
    assert got == [{"c1": "A", "c2": "B"}, {"c1": "1", "c2": "2"}]


def test_pp_transition_validation(book_env):
    book, _ = book_env
    old = [{"编号": "FO-001", "状态": "paid"}]
    new = [{"编号": "FO-001", "状态": "building"}]
    errs = outline.validate_transitions(old, new)
    assert errs and "回退" in errs[0]
    assert outline.validate_transitions(
        [{"编号": "FO-001", "状态": "planted"}], [{"编号": "FO-001", "状态": "building"}]) == []


def test_pp_apply_ops_advance_and_pay(book_env):
    book, _ = book_env
    errs, _ = outline.apply_foreshadow_ops(book, 3, [{"op": "advance", "fo_id": "FO-001"}])
    assert errs == []
    rows = {r["编号"]: r for r in outline.read_plot_points(book)}
    assert rows["FO-001"]["状态"] == "building"

    errs, _ = outline.apply_foreshadow_ops(book, 5, [{"op": "pay", "fo_id": "FO-001"}])
    assert errs == []
    assert {r["编号"]: r for r in outline.read_plot_points(book)}["FO-001"]["状态"] == "paid"

    # paid 不能再动
    errs, _ = outline.apply_foreshadow_ops(book, 6, [{"op": "advance", "fo_id": "FO-001"}])
    assert errs


def test_pp_apply_ops_unknown_ref_and_plant(book_env):
    book, _ = book_env
    errs, _ = outline.apply_foreshadow_ops(book, 2, [{"op": "pay", "fo_id": "FO-999"}])
    assert any("不存在" in e for e in errs)
    errs, _ = outline.apply_foreshadow_ops(book, 2, [{"op": "plant", "fo_id": "FO-002", "note": "新埋"}])
    assert errs == []
    assert any(r["编号"] == "FO-002" for r in outline.read_plot_points(book))


def test_pp_overdue_warning(book_env):
    book, _ = book_env
    rows = outline.read_plot_points(book)
    rows[0]["埋设章"] = "1"
    rows[0]["状态"] = "planted"
    outline.write_plot_points(book, rows)
    warns = outline.overdue_warnings(outline.read_plot_points(book), 40)
    assert warns and "FO-001" in warns[0]
