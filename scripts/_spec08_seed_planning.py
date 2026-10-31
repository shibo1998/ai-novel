"""规格08 阶段B 浏览器实测的离线种子：在临时 home 造一本完整规划链的 rolling 书。

全程零模型调用：候选直接喂给 planning.adopt_preview（同服务层代码路径）。
"""
from __future__ import annotations

import json
import os
import sys


def main() -> None:
    home = sys.argv[1]
    os.environ["AINOVEL_HOME"] = home
    from ainovel import books, db, planning

    db._CONN = None

    name = "规划滚动书"
    if name in {b.get("title") for b in books.list_books()}:
        print("already seeded")
        return
    books.create_book(name, target_words=300_000, chapter_word_range=[2200, 2800])
    planning.enable(name)

    def adopt(scope: str, target: dict, data: dict) -> None:
        content = json.dumps({"scope": scope, "target": target, "data": data},
                             ensure_ascii=False)
        version = planning.version_etag(name)
        preview = planning.compute_preview(name, scope, target, content, version)
        result = planning.adopt_preview(name, scope, target, content, version,
                                        preview["preview_digest"])
        print(f"adopted {scope}: added={result['added_numbers']} "
              f"changed={result['changed_numbers']} units={result['units_added']}")

    empty = {"volume_no": None, "unit_id": None, "chapters": None}
    adopt("global", empty, {
        "logline": "为了夺回玄铁令，林寒必须闯青云坊，但杀手一路追杀",
        "ending_direction": "查清父母失踪真相",
        "global": {"reader_promise": "逆袭爽感", "selling_points": ["打脸", "底牌流"],
                   "mainline": "底层→门阀→宗门", "stage_boundaries": ["坊市止于立足"],
                   "reserved_reveals": ["父辈真相"], "estimated_chapters_override": None},
        "rough_volumes": [
            {"no": 1, "name": "青云坊", "direction": "立足坊市", "estimated_chapters": 8},
            {"no": 2, "name": "黑风寨", "direction": "卷入旧案", "estimated_chapters": 12},
        ]})

    tree = planning.read_model(name)["tree"]
    vol1 = next(v for v in tree["volumes"] if str(v["no"]) == "1")
    print("卷1 现有单元（应为空）:", vol1["units"])

    adopt("volume", {"volume_no": 1, "unit_id": None, "chapters": None}, {
        "chapter_count": 8,
        "volume_detail": {"core_conflict": "坊市争夺", "goal": "拿到通行凭证",
                          "climax": "乱葬岗对决", "hook_out": "令牌刻字",
                          "ending_state": "计划：结识线人，拿到半张图"},
        "unit_skeletons": [
            {"name": "验令风波", "chapters": [1, 3], "goal": "查出令牌异常",
             "obstacle": "护卫盘查"},
            {"name": "杀手追缉", "chapters": [4, 6], "goal": "摆脱追杀",
             "obstacle": "黑衣人围堵"},
            {"name": "坊市立足", "chapters": [7, 8], "goal": "取得凭证",
             "obstacle": "坊主刁难"},
        ]})

    tree = planning.read_model(name)["tree"]
    vol1 = next(v for v in tree["volumes"] if str(v["no"]) == "1")
    uid = vol1["units"][0]["id"]

    adopt("unit", {"volume_no": 1, "unit_id": uid, "chapters": None}, {
        "unit_detail": {"goal": "查出令牌遇灵气发热的异常", "obstacle": "护卫盘查与杀手盯梢",
                        "choices": ["硬闯验令", "借药师之力"],
                        "outcome": "确认令牌是钥匙，拿到半张图",
                        "can_reveal": ["令牌遇灵气发热", "坊市有暗市"],
                        "must_hold": ["父辈真相", "令牌是钥匙"],
                        "story_lines": ["主线"]}})

    adopt("chapters", {"volume_no": 1, "unit_id": uid, "chapters": [1, 3]}, {
        "chapter_rows": [
            {"章": "1", "卷": "1", "本章目标": "夜探药铺验令", "冲突来源": "护卫",
             "爽点/揭露": "令牌发热", "钩子类型": "悬念", "重要度★": "5"},
            {"章": "2", "卷": "1", "本章目标": "杀手入坊", "冲突来源": "黑衣人",
             "爽点/揭露": "身手压制", "钩子类型": "选择", "重要度★": "3"},
            {"章": "3", "卷": "1", "本章目标": "乱葬岗脱身", "冲突来源": "围堵",
             "爽点/揭露": "令牌刻字", "钩子类型": "反转", "重要度★": "4"},
        ], "rhythm_note": "1-3 密集小打脸，第3章首个反转"})

    model = planning.read_model(name)
    state = model["readiness"]["layers"]
    print("readiness global:", state["global"]["status"])
    print("readiness vol1:", [v["status"] for v in state["volumes"] if str(v.get("no")) == "1"])
    print("readiness units:", [(u["name"], u["status"]) for u in state["units"]])
    print("readiness chapters:", [(c["key"], c["status"]) for c in state["chapters"]])
    print("next:", model["next_actions"][0]["message"])
    print("seeded", name)


if __name__ == "__main__":
    main()
