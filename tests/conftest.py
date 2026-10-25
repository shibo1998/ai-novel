"""测试夹具：每个测试独立 AINOVEL_HOME（拷贝 prompts/lint），重置 DB 连接。"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    shutil.copytree(REPO / "prompts", h / "prompts")
    shutil.copytree(REPO / "lint", h / "lint")
    monkeypatch.setenv("AINOVEL_HOME", str(h))
    _reset_db()
    yield h
    _reset_db()


def _reset_db():
    from ainovel import db, fts

    if db._CONN is not None:
        db._CONN.close()
    db._CONN = None
    fts._TRIGRAM_OK = None


@pytest.fixture
def book_env(home):
    """标准测试书：完整 Bible/角色/大纲/台账。返回 (书名, home)。"""
    from ainovel import bible, books, characters, outline

    books.create_book("测试书", genre_template="urban", target_words=30000)
    name = "测试书"
    bible.write(name, {
        "基础设定": "当代都市，灵气复苏初期。",
        "力量体系": (
            "| 大境界 | 子境界 | 突破条件 | 能力边界(不能做什么) | 战力对标 | 寿元 | 代价与风险 | 突破异象 |\n"
            "|---|---|---|---|---|---|---|---|\n"
            "| 炼气 | 一层/二层/三层 | 引气入体百日 | 不能御物飞行 | 三名壮汉 | 不变 | 经脉受损 | 无 |\n"
            "| 筑基 | 初期 | 灵物筑基 | 不能隔空取物 | 十名炼气 | +50年 | 心魔 | 灵气外泄 |"
        ),
        "势力格局": "青云坊：中立坊市。",
        "历史脉络": "三年前灵气复苏。",
        "隐藏规则": "| 规则内容 | 隐藏等级 | 计划揭晓章节 |\n|---|---|---|\n| 玄铁令是钥匙 | 高 | 45 |",
        "术语表": "| 术语 | 含义 |\n|---|---|\n| 玄铁令 | 上古信物 |",
        "日常细节": "潮湿、市井。",
    })
    characters.create(name, "林寒", role="主角")
    characters.create(name, "老周", role="重要配角")
    outline.write_outline(
        name,
        {"logline": "为了查清父母失踪真相，林寒必须潜入青云坊，但玄铁令正在引来杀手",
         "ending_direction": "",
         "volumes": [{"no": 1, "name": "坊市", "chapters": [1, 12], "core_conflict": "坊市争夺",
                      "goal": "立足", "climax": "乱葬岗对决", "hook_out": "身世线索"}]},
        rows=[{"章": str(i), "卷": "1", "本章目标": "验令" + str(i), "冲突来源": "护卫",
               "爽点/揭露": "线索" + str(i), "钩子类型": ["悬念", "选择", "反转"][i % 3],
               "重要度★": "3"} for i in range(1, 13)],
        rhythm="1-2 小打脸，12 卷终",
    )
    outline.write_plot_points(name, [
        {"编号": "FO-001", "内容": "玄铁令发热", "埋设章": "1", "推进章": "",
         "预计回收章": "15", "状态": "planted", "备注": ""},
    ])
    return name, home
