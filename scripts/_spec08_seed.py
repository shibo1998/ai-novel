"""规格08 阶段A 浏览器补验的临时实例准备（08-A01/A02）。

在系统临时目录建一套隔离 home：建临时书、写 3000 字正文与命中样稿，
不碰仓库真实 books/、data/。只给 `scripts/spec08_browser_check.mjs` 用。

用法：python scripts/_spec08_seed.py <home目录>
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # 允许 `python scripts/_spec08_seed.py` 直接跑


def main() -> None:
    home = Path(sys.argv[1]).resolve()
    os.environ["AINOVEL_HOME"] = str(home)
    home.mkdir(parents=True, exist_ok=True)

    from ainovel import atomic, books, chapters, outline
    from ainovel import frontmatter as FM

    name = "规格08临时书"
    titles = {b.get("title") for b in books.list_books()}
    if name not in titles:
        books.create_book(name, target_words=30000, chapter_word_range=[2200, 2800])

    # 章节总表：3 章，便于 A-T04 的"受影响章号"手验
    rows = [{"章": str(i), "卷": "1", "本章目标": f"目标{i}", "冲突来源": f"冲突{i}",
             "爽点/揭露": f"爽点{i}", "钩子类型": "悬念", "重要度★": "3"} for i in (1, 2, 3)]
    outline.write_outline(
        name,
        {"logline": "为了查清真相，主角必须潜入坊市，但令牌引来杀手",
         "ending_direction": "查清父母失踪",
         "volumes": [{"no": 1, "name": "坊市", "chapters": [1, 3],
                      "core_conflict": "坊市争夺", "goal": "立足", "climax": "对决",
                      "hook_out": "身世"}]},
        rows=rows, rhythm="1-2 小打脸，3 章终")
    outline.write_plot_points(name, [
        {"编号": "FO-001", "内容": "玄铁令发热", "埋设章": "1", "推进章": "",
         "预计回收章": "3", "状态": "planted", "备注": ""},
    ])

    # 3000 字正文（含连续空行、段内换行、重复引用与一条机检命中）
    para = "林寒把令牌压在掌心，指腹下的纹路一点点发烫，像是有人在骨头里点了一盏灯。他没有回头，只把脚步放得更轻。"
    blocks: list[str] = []
    total = 0
    i = 0
    while total < 3000:
        i += 1
        line = f"{para}（第{i}段）"
        blocks.append(line)
        total += len(line)
    body_parts = []
    for idx, line in enumerate(blocks, 1):
        if idx == 3:
            body_parts.append(line + "\n\n\n")      # 连续空行
        elif idx == 5:
            body_parts.append(line[:20] + "\n" + line[20:])  # 段内换行
        elif idx == 7:
            body_parts.append(line)                 # 与第 1 段重复引用
        else:
            body_parts.append(line)
    body = "\n\n".join(body_parts) + "\n"
    # 让正文以 CRLF 落盘，验证行号/定位在 CRLF 下仍按原行工作
    path = chapters.prose_path(name, 1)
    atomic.write_text(path, FM.dump(
        {"chapter_no": 1, "version": 1, "status": "draft",
         "word_count": chapters.count_words(body), "finalized_at": None}, body))
    raw = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    path.write_bytes(raw)

    # 第 2 章只有细纲（未写正文），第 3 章什么都没有 → 覆盖工作台三种状态
    print(f"seeded {name}: chars={chapters.count_words(body)} bytes={path.stat().st_size}")
    _seed_conflicts("规格08假书B")
    print(f"HOME={home}")


def _seed_conflicts(other: str) -> None:
    """A-T07/A-T08 用：两本假书共 50 条不同类型的待处理冲突。

    类型分布：40 条可采纳 new_setting、5 条 timeline_candidate、5 条未知字段（只有
    keep_archive，用于验证"仅说明/未知项不得自动批量关闭"）。
    """
    from ainovel import books, characters, conflicts

    if other not in {b.get("title") for b in books.list_books()}:
        books.create_book(other, target_words=30000)

    if len(conflicts.list_open()) >= 50:
        print(f"conflicts already {len(conflicts.list_open())}, skip")
        return

    books_ = ["规格08临时书", other]
    pend: list = []
    for i in range(1, 41):
        pend.append(characters.Conflict(
            "new_setting", books_[i % 2], (i % 3) + 1,
            f"待补录设定：批量术法{i:02d}：效果{i}", None,
            {"term": f"批量术法{i:02d}", "description": f"效果{i}"}))
    for i in range(1, 6):
        pend.append(characters.Conflict(
            "timeline_candidate", books_[i % 2], i,
            f"时间线候选事件{i}", None,
            {"chapter_no": i, "event": f"时间线候选事件{i}", "characters": [], "reader_known": True}))
    for i in range(1, 6):
        pend.append(characters.Conflict(
            "unknown_field", books_[i % 2], i,
            f"未知字段 mood{i}", None, "紧张", char="林寒", field=f"mood{i}"))
    conflicts.enqueue(pend)
    print(f"conflicts enqueued: {len(conflicts.list_open())}")


if __name__ == "__main__":
    main()
