"""角色状态回写与冲突检测（规格02 P-V4 回写规则）。"""
from ainovel import characters, outline


def test_auto_apply_update(book_env):
    book, _ = book_env
    applied, conflicts = characters.apply_status_updates(book, 3, [
        {"name": "林寒", "changes": {"realm": "炼气二层", "emotion": "警觉"}},
    ])
    assert applied == ["林寒"] and conflicts == []
    d = characters.read(book, "林寒")
    assert d["status"]["realm"] == "炼气二层"
    assert d["status"]["updated_chapter"] == 3


def test_realm_regress_conflict(book_env):
    book, _ = book_env
    # 先把林寒推到筑基（在力量体系顺序中高于炼气）
    characters.apply_status_updates(book, 5, [{"name": "林寒", "changes": {"realm": "筑基"}}])
    applied, conflicts = characters.apply_status_updates(book, 6, [
        {"name": "林寒", "changes": {"realm": "炼气一层"}},
    ])
    assert applied == []
    assert any(c.kind == "realm_regress" for c in conflicts)
    # 档案未被覆盖
    assert characters.read(book, "林寒")["status"]["realm"] == "筑基"


def test_dead_reappear_conflict(book_env):
    book, _ = book_env
    characters.apply_status_updates(book, 4, [{"name": "老周", "changes": {"alive": False}}])
    applied, conflicts = characters.apply_status_updates(book, 7, [
        {"name": "老周", "changes": {"alive": True}},
    ])
    assert applied == []
    assert any(c.kind == "dead_reappear" for c in conflicts)


def test_missing_char_conflict(book_env):
    book, _ = book_env
    applied, conflicts = characters.apply_status_updates(book, 2, [
        {"name": "路人甲", "changes": {"emotion": "惊恐"}},
    ])
    assert applied == []
    assert any(c.kind == "missing_char" for c in conflicts)


def test_conflict_queue_and_guard(book_env):
    book, _ = book_env
    from ainovel import conflicts, pipeline

    characters.apply_status_updates(book, 8, [{"name": "老周", "changes": {"alive": False}}])
    _, cs = characters.apply_status_updates(book, 9, [{"name": "老周", "changes": {"alive": True}}])
    conflicts.enqueue(cs)
    open_items = conflicts.list_open(book)
    assert len(open_items) == 1

    # 队列非空 → 禁止开下一章细纲
    try:
        pipeline.generate_outline(book, 10)
        raise AssertionError("应当被 guard 拦截")
    except RuntimeError as e:
        assert "裁决队列" in str(e)

    # 采纳 AI（复活）→ 解决并写回
    item = conflicts.resolve(open_items[0]["id"], "adopt_ai")
    assert item["resolved"]
    assert conflicts.list_open(book) == []
    assert characters.read(book, "老周")["status"]["alive"] is True


def test_locations_and_outline_helpers(book_env):
    book, _ = book_env
    from ainovel import outline as O

    O.upsert_location(book, "青云坊", "坊市", chapter_no=1, plot="验令")
    locs = O.read_locations(book)
    assert any(l["名称"] == "青云坊" for l in locs)
    # 去重
    O.upsert_location(book, "青云坊", "坊市")
    assert sum(1 for l in O.read_locations(book) if l["名称"] == "青云坊") == 1
    row = O.chapter_row(book, 1)
    assert row and row["本章目标"] == "验令1"
    assert O.total_chapters(book) == 12
