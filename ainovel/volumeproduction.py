"""（规格10 §2.3/§4）卷候选发动机与串行单章生产；人工发布由 production 负责。"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Callable

from . import (bible, books, chapters, characters, conflicts, db, draftcontext,
               frontmatter as FM, llm, mechcheck, outline, pipeline, planning, prompts, statemachine)
from .mdtable import dump_table, parse_table
from .operations import writing

Bundle = dict[str, Any]


class VolumeProductionError(pipeline.PipelineError):
    """候选非法或单章不能安全推进；不得将其当作成功检查点。"""

    code = "PRODUCTION_INVALID"


def _numbers(numbers: Any) -> list[int]:
    if (not isinstance(numbers, list) or not 1 <= len(numbers) <= 50
            or any(type(no) is not int or no <= 0 for no in numbers)
            or numbers != list(range(numbers[0], numbers[0] + len(numbers)))):
        raise VolumeProductionError("章号必须为服务端分配的 1—50 个连续正整数")
    return numbers


def _missing(value: Any) -> bool:
    return not str(value or "").strip() or bool(re.fullmatch(r"[（(]待[^）)]*[）)]", str(value).strip()))


def _table(text: str, columns: list[str]) -> list[dict[str, str]]:
    cols, rows = parse_table(text, columns[0])
    if cols != columns:
        raise VolumeProductionError(f"表头必须为 {'｜'.join(columns)}")
    return rows


def _clean_fm(od: dict[str, Any]) -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in od.items() if not k.startswith("_") and k != "chapter_rows"}


def _projection(book: str, files: dict[str, str]) -> dict[str, Any]:
    root = books.book_dir(book)
    sections = bible._parse_sections(files.get("STORY_BIBLE.md", (root / "STORY_BIBLE.md").read_text(encoding="utf-8")
                                             if (root / "STORY_BIBLE.md").exists() else ""))
    cards = {name: {key: value for key, value in card.items() if key != "_file"}
             for name, card in characters.read_all(book).items()}
    for path, text in files.items():
        if path.startswith("characters/"):
            fm, body = FM.parse(text)
            cards[fm["name"]] = dict(fm, _body=body)
    plot_text = files.get("PLOT_POINTS.md", (root / "PLOT_POINTS.md").read_text(encoding="utf-8")
                          if (root / "PLOT_POINTS.md").exists() else outline.render_plot_points([]))
    locations_text = files.get("LOCATIONS.md", (root / "LOCATIONS.md").read_text(encoding="utf-8")
                               if (root / "LOCATIONS.md").exists() else dump_table(outline.LOC_COLS, []))
    return {"bible": sections, "characters": cards, "plot_rows": _table(plot_text, outline.PP_COLS),
            "locations": _table(locations_text, outline.LOC_COLS)}


def _ask(book: str, pid: str, variables: dict[str, Any], parse: Callable[[str], Any],
         client: Any = None, instruction: str = "") -> Any:
    feedback = instruction
    for _attempt in range(3):
        res, _prompt, _text = pipeline._call(book, None, f"prepare_{pid}", pid, variables,
                                            client=client, extra_instruction=feedback)
        try:
            if not res.text.strip() or res.finish_reason in {"length", "max_tokens", "max_output_tokens"}:
                raise VolumeProductionError(f"{pid} 输出为空或截断")
            return parse(res.text)
        except (ValueError, FM.FrontmatterError, bible.BibleError, llm.LLMError,
                planning.PlanningInvalid, VolumeProductionError) as error:
            feedback = instruction + "\n" + pipeline._feedback_instruction(str(error))
    raise VolumeProductionError(f"{pid} 三次未通过候选校验：{feedback}")


def _cards(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    marks = list(re.finditer(r"(?m)^===角色:([^=\n]+)===\s*\n", text))
    for index, mark in enumerate(marks):
        name = mark[1].strip()
        body = text[mark.end():marks[index + 1].start() if index + 1 < len(marks) else len(text)].strip()
        if name in result:
            raise VolumeProductionError("G3/G4 角色标记重复")
        result[name] = body
    if not result:
        raise VolumeProductionError("缺少 ===角色:真实姓名=== 分卡标记")
    return result


def _assets(book: str, request: dict[str, Any], client: Any) -> dict[str, str]:
    root = books.book_dir(book)
    meta = books.read_meta(book)
    files: dict[str, str] = {}
    existing = bible.read(book) if (root / "STORY_BIBLE.md").exists() else {}
    missing = [section for section in books.BIBLE_SECTIONS if _missing(existing.get(section))]
    generated: dict[str, str] = {}
    if missing:
        def parse_bible(text: str) -> dict[str, str]:
            data = {key: value for key, value in bible._parse_sections(text).items() if key in books.BIBLE_SECTIONS}
            if any(_missing(data.get(section)) for section in missing if section != "力量体系"):
                raise VolumeProductionError("G1 缺少必要的固定世界观节")
            bible.render(data)
            return data
        generated = _ask(book, "G1", {
            "题材": meta["genre"], "故事核": request.get("logline") or outline.read_outline(book).get("logline") or "",
            "对标书": meta.get("benchmark_book") or "（无）", "既有设定摘要": json.dumps(existing, ensure_ascii=False),
        }, parse_bible, client)
        if "力量体系" in missing and not parse_table(generated.get("力量体系", ""), "大境界")[1]:
            def parse_power(text: str) -> str:
                data = re.sub(r"(?ms)^##\s*[^\n]*自检[^\n]*\n.*", "", text).strip()
                cols, rows = parse_table(data, "大境界")
                if cols != bible.POWER_COLS or not rows:
                    raise VolumeProductionError("G2 力量体系缺少完整八列表")
                return data
            generated["力量体系"] = _ask(book, "G2", {"题材": meta["genre"],
                "基础设定": generated.get("基础设定") or existing.get("基础设定") or "",
                "既有力量体系": generated.get("力量体系") or ""}, parse_power, client)
        merged = dict(existing)
        merged.update({section: generated[section] for section in missing})
        files["STORY_BIBLE.md"] = bible.render(merged)
    sections = bible._parse_sections(files["STORY_BIBLE.md"]) if "STORY_BIBLE.md" in files else existing
    if not characters.list_characters(book):
        def parse_chars(text: str) -> dict[str, str]:
            result = _cards(text)
            for name, card in result.items():
                fm, _body = FM.parse(card)
                FM.validate(fm, characters.CHAR_SCHEMA)
                if fm.get("name") != name:
                    raise VolumeProductionError("G3 姓名与分卡标记不一致")
                _validate_character(fm, card, sections)
            if not any(FM.parse(card)[0]["role"] == "主角" for card in result.values()):
                raise VolumeProductionError("G3 必须提供真实姓名的主角")
            return result
        cards = _ask(book, "G3", {"世界观摘要": json.dumps(sections, ensure_ascii=False),
            "角色需求清单": "为故事核设计必要角色（真实姓名、定位、初始状态），不使用主角/角色1等占位名。故事核："
                + str(request.get("logline") or outline.read_outline(book).get("logline") or "")
                + "；本卷要求：" + str(request.get("requirements") or "")}, parse_chars, client,
            "每个分卡标记后先输出档案 YAML frontmatter：name/role/status；role 取主角/女主/反派/重要配角/龙套；"
            "status 含 realm/location/injuries/inventory/emotion/alive/known_info/updated_chapter，初始 updated_chapter=0。")
        if len(cards) > 1:
            relations = _ask(book, "G4", {"角色卡合集": "\n\n".join(cards.values())}, _cards, client)
            if set(relations) != set(cards):
                raise VolumeProductionError("G4 关系网角色范围不一致")
            for name, text in relations.items():
                _cols, rows = parse_table(text, "对象")
                if _cols != ["对象", "关系", "不稳定因素"] or any(row.get("对象") not in cards for row in rows):
                    raise VolumeProductionError("G4 关系表头或对象非法")
                fm, body = FM.parse(cards[name])
                body = re.sub(r"(?ms)^##\s*关系网络\s*\n.*?(?=^##\s|\Z)",
                              lambda _m: "## 关系网络\n\n" + dump_table(_cols, [[row[c] for c in _cols] for row in rows]) + "\n\n", body)
                cards[name] = FM.dump(fm, body)
        files.update({f"characters/char-{characters.safe_name(name)}.md": text for name, text in cards.items()})
    if not (root / "PLOT_POINTS.md").exists():
        files["PLOT_POINTS.md"] = outline.render_plot_points([])
    if not (root / "LOCATIONS.md").exists():
        files["LOCATIONS.md"] = "# 地点档案\n\n" + dump_table(outline.LOC_COLS, []) + "\n"
    initial_places = {FM.parse(text)[0]["status"].get("location") for path, text in files.items()
                      if path.startswith("characters/")}
    initial_places.discard("")
    initial_places.discard(None)
    old_locations = outline.read_locations(book) if (root / "LOCATIONS.md").exists() else []
    known_places = {row["名称"] for row in old_locations}
    if initial_places - known_places:
        rows = old_locations + [{"名称": place, "特征": "候选角色卡初始位置", "方位/距离": "",
                                 "首现章": str(request["numbers"][0]), "关联剧情": "初始角色位置"}
                                for place in sorted(initial_places - known_places)]
        files["LOCATIONS.md"] = "# 地点档案\n\n" + dump_table(outline.LOC_COLS, [[row[c] for c in outline.LOC_COLS] for row in rows]) + "\n"
    return files


def _realm_order(sections: dict[str, str]) -> list[str]:
    _, rows = parse_table(sections.get("力量体系", ""), "大境界")
    result: list[str] = []
    for row in rows:
        for field in ("大境界", "子境界"):
            for value in re.split(r"[·/、,，\s]+", row.get(field, "").replace("*", "")):
                if value and value not in result:
                    result.append(value)
    return result


def _validate_character(fm: dict[str, Any], text: str, sections: dict[str, str]) -> None:
    FM.validate(fm, characters.CHAR_SCHEMA)
    name = fm["name"]
    if (not name or name != characters.safe_name(name) or ".." in name or name.endswith(".")
            or re.fullmatch(r"主角|女主|反派|角色\d*|待定|.*[<>{}].*", name)):
        raise VolumeProductionError("角色必须为安全的真实姓名，不能使用占位符")
    status = fm["status"]
    if set(characters.STATUS_FIELDS) - set(status) or set(status) - set(characters.STATUS_FIELDS):
        raise VolumeProductionError("角色初始 status 必須完整且无未知字段")
    if type(status["alive"]) is not bool or type(status["updated_chapter"]) is not int or status["updated_chapter"] != 0:
        raise VolumeProductionError("新角色生死/更新时间非法")
    for field in ("realm", "location", "emotion"):
        if not isinstance(status[field], str):
            raise VolumeProductionError("角色初始文本状态非法")
    for field in ("injuries", "inventory", "known_info"):
        if not isinstance(status[field], list) or any(not isinstance(value, str) for value in status[field]):
            raise VolumeProductionError("角色初始列表状态非法")
    realm = status["realm"]
    if realm and realm not in pipeline._NON_REALM_LABELS and mechcheck.realm_index(realm, _realm_order(sections)) is None:
        raise VolumeProductionError("角色初始境界不在力量体系内")
    _, body = FM.parse(text)
    if not body.strip() or "## 表层信息" not in body or "## 声音特征" not in body:
        raise VolumeProductionError("角色卡缺少必要正文节")


def validate_contract(book: str, fm: dict[str, Any], body: str, no: int,
                      projection: dict[str, Any], plots: list[dict[str, Any]], prev_hook: str = "") -> None:
    """（规格10 §2.3）候选合同的角色、范围、FO 顺序及嵌套结构校验。"""
    FM.validate(fm, chapters.OUTLINE_CONTRACT_SCHEMA)
    if type(fm.get("chapter_no")) is not int or fm["chapter_no"] != no or fm.get("confirmed") is not False:
        raise VolumeProductionError("候选合同章号非法或 confirmed 不是 false")
    row = next((row for row in projection["outline"]["chapter_rows"] if outline._row_no(row.get("章")) == no), None)
    if row is None or type(fm.get("volume")) is not int or str(fm["volume"]) != str(row["卷"]):
        raise VolumeProductionError("合同卷号与章纲不一致")
    names = set(projection["characters"])
    if fm["pov"] not in names or not isinstance(body, str) or not body.strip():
        raise VolumeProductionError("合同 POV 无角色档案或正文为空")
    if mechcheck._bad_word_range(fm["word_range"]) or fm["word_range"] != books.load_config(book)["chapter_word_range"]:
        raise VolumeProductionError("合同字数范围与本书目标不一致")
    if not fm["scenes"] or any(not isinstance(scene, dict) for scene in fm["scenes"]):
        raise VolumeProductionError("合同场景清单非法")
    for scene in fm["scenes"]:
        if any(not isinstance(scene.get(key), str) or not scene[key].strip() for key in ("goal", "conflict", "end_state")):
            raise VolumeProductionError("场景目标/冲突/结果缺失")
        if not isinstance(scene.get("chars"), list) or not scene["chars"]:
            raise VolumeProductionError("场景缺少出场角色")
        for char in scene["chars"]:
            if not isinstance(char, dict) or char.get("name") not in names or not isinstance(char.get("state"), str):
                raise VolumeProductionError("场景角色或状态非法")
    hook = fm["hook"]
    if hook.get("type") not in {"悬念", "选择", "反转"} or not isinstance(hook.get("content"), str) or not hook["content"].strip():
        raise VolumeProductionError("合同钩子非法")
    if prev_hook and hook["type"] == prev_hook:
        raise VolumeProductionError("相邻合同钩子类型不得重复")
    for key in ("purpose", "must_include"):
        if not isinstance(fm.get(key), list) or any(not isinstance(value, str) for value in fm[key]):
            raise VolumeProductionError(f"合同 {key} 必须为文本列表")
    if any(not isinstance(fm["emotion_arc"].get(key), str) for key in ("from", "via", "to")):
        raise VolumeProductionError("情绪弧线缺少起始/途经/结束")
    draftcontext.apply_fo_ops(plots, fm.get("foreshadow_ops") or [], no)
    pipeline._validate_outline_banned(fm, book)


def _g5(book: str, scope: str, target: dict[str, Any], projection: dict[str, Any],
        requirements: str, client: Any) -> dict[str, Any]:
    target = planning.normalize_target(scope, target)
    def parse(text: str) -> dict[str, Any]:
        candidate = planning.parse_candidate(text, scope)
        if candidate["target"] != target:
            raise VolumeProductionError("G5 回显目标与服务端范围不一致")
        return candidate["data"]
    return _ask(book, "G5", planning.generate_variables(book, scope, target, requirements, projection=projection), parse, client)


def prepare(book: str, request: dict[str, Any], *, client: Any = None,
            progress: Callable[[str, int | None], None] | None = None) -> Bundle:
    """（规格10 §2.3）准备联合候选，只调用模型/记日志，正式档案零写入。"""
    numbers = _numbers(request.get("numbers"))
    if (request.get("mode") not in {"prepare", "append", "new_volume"}
            or type(request.get("count")) is not int or request["count"] != len(numbers)):
        raise VolumeProductionError("准备模式或 count 与范围不一致")
    vno = request.get("volume_no")
    if type(vno) is not int or vno <= 0:
        raise VolumeProductionError("卷号必须为正整数")
    if set(numbers) & set(planning.protected_numbers(book)):
        raise VolumeProductionError("候选范围包含已写/已锁定章节")
    conflicts.guard(book)
    for pid, required in (("G6", "〖批次计划说明〗"), ("G5", "卷联合候选")):
        if required not in prompts.get(pid, book).body:
            raise VolumeProductionError(f"PROMPT_UPGRADE_REQUIRED: {pid} 不兼容卷级候选投影")
    existing_outline = outline.read_outline(book)
    existing_volumes = existing_outline.get("volumes") or []
    current_volume = next((v for v in existing_volumes if str(v.get("no")) == str(vno)), None)
    if request["mode"] == "append" and (not current_volume or not current_volume.get("chapters")
            or numbers[0] != current_volume["chapters"][1] + 1):
        raise VolumeProductionError("追加必须紧接目标卷尾，不挪动历史章号")
    if request["mode"] == "new_volume":
        maximum = max([outline._row_no(r.get("章")) or 0 for r in existing_outline["chapter_rows"]]
                      + [v["chapters"][1] for v in existing_volumes if v.get("chapters")]
                      + chapters.list_chapters(book) + [0])
        if current_volume is not None or vno != max([int(v["no"]) for v in existing_volumes] + [0]) + 1 or numbers[0] != maximum + 1:
            raise VolumeProductionError("新卷必须使用全局预留范围之后的下一卷/连续章号")
    with draftcontext.scoped(book, check_stop=progress):
        files = _assets(book, request, client)
        projection = _projection(book, files)
        od = copy.deepcopy(outline.read_outline(book))
        od.pop("_body", None)
        if _missing(od.get("logline")):
            od["logline"] = str(request.get("logline") or "").strip()
        if not od["logline"]:
            raise VolumeProductionError("新书缺少一句话故事核")
        p = od.setdefault("planning", {"schema_version": 1, "enabled": True, "global": {}, "units": [], "adoptions": {}})
        if p.get("schema_version", 1) != 1:
            raise VolumeProductionError("不支持的规划 schema")
        p["enabled"] = True
        p.setdefault("global", {})
        p.setdefault("units", [])
        p.setdefault("adoptions", {})
        projection["outline"] = od
        volumes = od.setdefault("volumes", [])
        vol = next((v for v in volumes if str(v.get("no")) == str(vno)), None)
        if vol is None:
            vol = {"no": vno, "name": f"第{vno}卷", "chapters": [numbers[0], numbers[-1]], "detail_level": "rough"}
            volumes.append(vol)
        old_range = vol.get("chapters") or numbers[::max(1, len(numbers) - 1)]
        if len(old_range) != 2:
            old_range = [numbers[0], numbers[-1]]
        vol["chapters"] = [min(old_range[0], numbers[0]), max(old_range[-1], numbers[-1])]
        requirements = str(request.get("requirements") or "") + f"\n本次只准备第{numbers[0]}-{numbers[-1]}章，共{len(numbers)}章。"
        if any(_missing(vol.get(key)) for key in ("goal", "core_conflict", "climax", "hook_out")):
            data = _g5(book, "volume", {"volume_no": vno, "unit_id": None, "chapters": None}, projection, requirements, client)
            if data["chapter_count"] != len(numbers):
                raise VolumeProductionError("G5 卷章数与服务端范围不一致")
            for key in planning.VOLUME_DETAIL_FIELDS:
                if _missing(vol.get(key)):
                    vol[key] = data["volume_detail"].get(key, "")
            skeletons = data["unit_skeletons"]
            cursor = numbers[0]
            new_units = []
            for skeleton in skeletons:
                rng = skeleton.get("chapters") or [cursor, cursor + int(skeleton.get("chapter_count") or 1) - 1]
                if (len(rng) != 2 or any(type(n) is not int for n in rng) or rng[0] != cursor
                        or rng[1] < cursor or rng[1] > numbers[-1]):
                    raise VolumeProductionError("G5 单元骨架须连续完整覆盖服务端范围")
                name = skeleton["name"]
                new_units.append({"id": planning.planning_unit_id(book, vno, name, *rng), "volume_no": vno,
                                  "name": name, "chapters": rng, "detail_level": "rough",
                                  "goal": skeleton.get("goal", ""), "obstacle": skeleton.get("obstacle", "")})
                cursor = rng[1] + 1
            if cursor != numbers[-1] + 1:
                raise VolumeProductionError("G5 单元骨架缺章")
            if not any(set(range(u["chapters"][0], u["chapters"][1] + 1)) & set(numbers)
                       for u in p["units"] if u.get("chapters")):
                p["units"].extend(new_units)
        vol["detail_level"] = "expanded"
        # 仅创建未映射的目标范围单元；任何跨目标边界的旧单元都完整复用，不替换骨架。
        for start in range(numbers[0], numbers[-1] + 1, 10):
            end = min(start + 9, numbers[-1])
            cursor = start
            while cursor <= end:
                existing_unit = next((u for u in p["units"] if u.get("chapters") and u["chapters"][0] <= cursor <= u["chapters"][1]), None)
                if existing_unit:
                    if str(existing_unit.get("volume_no")) != str(vno):
                        raise VolumeProductionError("目标章号已属于其他卷单元")
                    unit = existing_unit
                    hi = min(end, unit["chapters"][1])
                else:
                    hi = end
                    next_starts = [u["chapters"][0] for u in p["units"] if u.get("chapters") and cursor < u["chapters"][0] <= end]
                    if next_starts:
                        hi = min(next_starts) - 1
                    name = f"{vol.get('name') or '本卷'}·{cursor}-{hi}"
                    unit = {"id": planning.planning_unit_id(book, vno, name, cursor, hi), "volume_no": vno,
                            "name": name, "chapters": [cursor, hi], "detail_level": "rough"}
                    p["units"].append(unit)
                uid = unit["id"]
                if unit.get("detail_level") != "expanded":
                    if not set(range(unit["chapters"][0], unit["chapters"][1] + 1)) <= set(numbers):
                        raise VolumeProductionError("旧未细化单元跨出本批范围，不能顺带改变")
                    target = {"volume_no": vno, "unit_id": uid, "chapters": None}
                    data = _g5(book, "unit", target, projection, requirements, client)
                    merged = planning.merge_candidate(book, "unit", target, data, projection=projection)
                    od = dict(merged["new_fm"], chapter_rows=merged["rows"], _rhythm=merged["rhythm"])
                    projection["outline"] = od
                    p = od["planning"]
                target = {"volume_no": vno, "unit_id": uid, "chapters": [cursor, hi]}
                data = _g5(book, "chapters", target, projection, requirements, client)
                merged = planning.merge_candidate(book, "chapters", target, data, projection=projection)
                od = dict(merged["new_fm"], chapter_rows=merged["rows"], _rhythm=merged["rhythm"])
                projection["outline"] = od
                p = od["planning"]
                cursor = hi + 1
        contracts: list[dict[str, Any]] = []
        for no in numbers:
            contracts.append(pipeline.prepare_outline(book, no, projection=projection, previous_contracts=contracts, client=client))
        bundle = {"schema_version": 1, "outline": {"fm": _clean_fm(od), "rows": od["chapter_rows"],
                                                   "rhythm": od.get("_rhythm") or ""},
                  "files": files, "outlines": contracts,
                  "notes": ["联合候选尚未采纳，全部细纲 confirmed=false；计划承接不是已写原文。"]}
        validate_bundle(book, bundle, numbers)
        return bundle


def _safe_path(book: str, path: str) -> Path:
    if (not isinstance(path, str) or not re.fullmatch(
            r"STORY_BIBLE\.md|PLOT_POINTS\.md|LOCATIONS\.md|characters/char-[^\\/:*?\"<>|\s]+\.md", path)
            or ".." in path):
        raise VolumeProductionError("候选档案路径不在受控白名单")
    root = books.book_dir(book).resolve()
    dest = root / path
    if not dest.resolve().is_relative_to(root):
        raise VolumeProductionError("候选路径越出本书（符号链接/目录穿越）")
    return dest


def _outside_rhythm(text: str, numbers: list[int]) -> str:
    pattern = r"(?ms)^### 章纲批次 (U-[0-9a-f]{32}):([0-9]+)-([0-9]+)\s*\n.*?(?=^###|^##\s|\Z)"
    def keep(match: re.Match[str]) -> str:
        lo, hi = int(match[2]), int(match[3])
        return "" if set(range(lo, hi + 1)) <= set(numbers) else match[0]
    return re.sub(pattern, keep, text).strip()


def _render_outline(fm: dict[str, Any], rows: list[dict[str, str]], rhythm: str, old_body: str) -> str:
    """（规格10 §2.3）共用标准渲染，并字面保留非规划节/作者附注。"""
    rendered = outline.render_outline(fm, rows, rhythm)
    _fm, body = FM.parse(rendered)
    if not old_body.strip():
        return rendered
    result = old_body
    for title in ("章节总表", "爽点节奏总谱"):
        pattern = rf"(?ms)^##\s*{title}\s*\n.*?(?=^##\s|\Z)"
        section = re.search(pattern, body)
        if section is None:
            raise VolumeProductionError("大纲标准渲染缺少固定节")
        if len(re.findall(pattern, result)) > 1:
            raise VolumeProductionError("旧大纲固定节重复，需先修复")
        if re.search(pattern, result):
            result = re.sub(pattern, lambda _m: section[0], result)
        else:
            result = result.rstrip() + "\n\n" + section[0]
    return FM.dump(fm, result)


def validate_bundle(book: str, bundle: Bundle, numbers: list[int]) -> dict[str, str]:
    """（规格10 §2.3）纯校验/渲染全部发布文件；仅用户 confirm 才会实际写入。"""
    numbers = _numbers(numbers)
    if (not isinstance(bundle, dict) or set(bundle) != {"schema_version", "outline", "files", "outlines", "notes"}
            or type(bundle.get("schema_version")) is not int or bundle["schema_version"] != 1):
        raise VolumeProductionError("候选包 schema 非法")
    if not isinstance(bundle["notes"], list) or any(not isinstance(note, str) for note in bundle["notes"]):
        raise VolumeProductionError("候选 notes 必须为文本列表")
    data = bundle["outline"]
    if not isinstance(data, dict) or set(data) != {"fm", "rows", "rhythm"} or not isinstance(data["fm"], dict) or not isinstance(data["rows"], list) or not isinstance(data["rhythm"], str):
        raise VolumeProductionError("候选 outline 结构非法")
    if not isinstance(bundle["files"], dict) or not isinstance(bundle["outlines"], list):
        raise VolumeProductionError("候选 files/outlines 结构非法")
    if set(numbers) & set(planning.protected_numbers(book)):
        raise VolumeProductionError("目标章存在正文/记忆卡或已锁细纲，禁止覆盖")
    fm = copy.deepcopy(data["fm"])
    if any(key.startswith("_") for key in fm) or "chapter_rows" in fm:
        raise VolumeProductionError("outline.fm 不得夹带读接口投影字段")
    FM.validate(fm, outline.OUTLINE_SCHEMA)
    rows = data["rows"]
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != set(outline.CHAPTER_COLS) or any(not isinstance(v, str) for v in row.values()):
            raise VolumeProductionError("章纲必须完整七列文本且无多余字段")
        no = outline._row_no(row["章"])
        if no is None or no <= 0 or no in seen or not row["卷"].isdigit() or row["钩子类型"] not in {"悬念", "选择", "反转"}:
            raise VolumeProductionError("章纲章号/卷号/钩子非法或重复")
        if not re.fullmatch(r"[★☆]?[1-5]|[★]{1,5}", row["重要度★"]):
            raise VolumeProductionError("章纲重要度非法")
        seen.add(no)
    if not set(numbers) <= seen:
        raise VolumeProductionError("候选章纲范围不完整")
    old = outline.read_outline(book)
    old_fm = _clean_fm(old)
    old_rows = [r for r in old["chapter_rows"] if outline._row_no(r.get("章")) not in numbers]
    new_rows = [r for r in rows if outline._row_no(r.get("章")) not in numbers]
    if old_rows != new_rows:
        raise VolumeProductionError("候选改变了非目标旧章行")
    target_volumes = {row["卷"] for row in rows if outline._row_no(row["章"]) in numbers}
    if len(target_volumes) != 1:
        raise VolumeProductionError("联合候选只能针对一个卷")
    vno = next(iter(target_volumes))
    p = fm.get("planning")
    if (not isinstance(p, dict) or type(p.get("schema_version")) is not int or p["schema_version"] != 1
            or p.get("enabled") is not True or not isinstance(p.get("units"), list)
            or not isinstance(p.get("adoptions"), dict) or not isinstance(p.get("global"), dict)):
        raise VolumeProductionError("候选 planning 结构非法")
    old_p = old_fm.get("planning") or {}
    if p["adoptions"] != (old_p.get("adoptions") or {}) or p["global"] != (old_p.get("global") or {}):
        raise VolumeProductionError("候选不得伪造采纳记录或改变旧全书规划")
    allowed_planning = {"schema_version", "enabled", "global", "units", "adoptions"}
    for key in (set(p) | set(old_p)) - allowed_planning:
        if p.get(key) != old_p.get(key):
            raise VolumeProductionError("候选越权改变规划附加字段")
    for key in set(old_fm) | set(fm):
        if key in {"volumes", "planning"}:
            continue
        if old_fm.get(key) != fm.get(key) and not (key == "logline" and _missing(old_fm.get(key)) and fm.get(key)):
            raise VolumeProductionError(f"候选越权改变全书字段 {key}")
    volumes = fm.get("volumes")
    if not isinstance(volumes, list):
        raise VolumeProductionError("候选卷列表非法")
    occupied: set[int] = set()
    volume_nos: set[str] = set()
    for vol in volumes:
        if not isinstance(vol, dict) or str(vol.get("no")) in volume_nos:
            raise VolumeProductionError("卷结构或卷号重复")
        vn = str(vol.get("no"))
        if not vn.isdigit() or int(vn) <= 0:
            raise VolumeProductionError("卷号非法")
        volume_nos.add(vn)
        rng = vol.get("chapters") or []
        if rng:
            if len(rng) != 2 or any(type(n) is not int or n <= 0 for n in rng) or rng[0] > rng[1]:
                raise VolumeProductionError("卷范围非法")
            span = set(range(rng[0], rng[1] + 1))
            if occupied & span:
                raise VolumeProductionError("卷范围重叠，禁止挪动后卷")
            occupied |= span
        previous = next((v for v in old_fm.get("volumes") or [] if str(v.get("no")) == vn), None)
        if vn != vno and vol != previous:
            raise VolumeProductionError("非目标旧卷被改变或新增")
        allowed = {"no", "name", "chapters", "detail_level", "direction", "estimated_chapters"} | set(planning.VOLUME_DETAIL_FIELDS)
        if set(vol) - allowed - set(previous or {}):
            raise VolumeProductionError("候选卷包含越权附加字段")
        if vn == vno and (not rng or vol.get("detail_level") != "expanded"
                         or any(not isinstance(vol.get(k), str) or _missing(vol.get(k)) for k in ("goal", "core_conflict", "climax", "hook_out"))):
            raise VolumeProductionError("目标卷缺少必要详情")
        if previous and vn == vno:
            for key, value in previous.items():
                if key == "chapters":
                    if value and (rng[0] != value[0] or rng[1] < value[1] or not set(range(value[1] + 1, rng[1] + 1)) <= set(numbers)):
                        raise VolumeProductionError("旧卷范围被挪动/越权扩展")
                elif key == "detail_level" or _missing(value):
                    continue
                elif vol.get(key) != value:
                    raise VolumeProductionError("候选改变了旧卷有效内容")
        if vn == vno and not set(numbers) <= set(range(rng[0], rng[1] + 1)):
            raise VolumeProductionError("目标章号不在本卷")
    if {str(v.get("no")) for v in old_fm.get("volumes") or []} - volume_nos:
        raise VolumeProductionError("候选删除旧卷")
    unit_ids: set[str] = set()
    covered: set[int] = set()
    for unit in p["units"]:
        planning._check_unit(unit)
        uid = unit["id"]
        if uid in unit_ids:
            raise VolumeProductionError("单元 ID 重复")
        unit_ids.add(uid)
        rng = unit.get("chapters") or []
        if len(rng) != 2:
            raise VolumeProductionError("单元缺少章号范围")
        span = set(range(rng[0], rng[1] + 1))
        if covered & span:
            raise VolumeProductionError("单元范围重叠")
        covered |= span
        vol = next((v for v in volumes if str(v["no"]) == str(unit.get("volume_no"))), None)
        if not vol or not vol.get("chapters") or not span <= set(range(vol["chapters"][0], vol["chapters"][1] + 1)):
            raise VolumeProductionError("单元越出所属卷")
        previous = next((u for u in old_p.get("units") or [] if u.get("id") == uid), None)
        allowed = {"id", "volume_no", "name", "chapters", "detail_level"} | set(planning.UNIT_DETAIL_FIELDS)
        if set(unit) - allowed - set(previous or {}):
            raise VolumeProductionError("候选单元包含越权附加字段")
        if span & set(numbers):
            if unit.get("detail_level") != "expanded":
                raise VolumeProductionError("目标单元尚未细化")
            planning._validate_scope_data("unit", {"unit_detail": {k: unit[k] for k in planning.UNIT_DETAIL_FIELDS if k in unit}})
        if previous is not None and unit != previous:
            if not span <= set(numbers) or any(unit.get(k) != previous.get(k) for k in ("id", "name", "chapters", "volume_no")):
                raise VolumeProductionError("旧单元越权改变")
        if previous is None and (not span <= set(numbers) or str(unit.get("volume_no")) != vno):
            raise VolumeProductionError("新增单元越出目标范围")
    if {u["id"] for u in old_p.get("units") or []} - unit_ids or not set(numbers) <= covered:
        raise VolumeProductionError("旧单元丢失或目标范围未完整覆盖")
    if _outside_rhythm(data["rhythm"], numbers) != _outside_rhythm(str(old.get("_rhythm") or ""), numbers):
        raise VolumeProductionError("候选改变了非目标旧节奏正文")
    for path, text in bundle["files"].items():
        dest = _safe_path(book, path)
        if not isinstance(text, str):
            raise VolumeProductionError("候选档案必须为完整文本")
        before = dest.read_text(encoding="utf-8") if dest.exists() else ""
        if path == "STORY_BIBLE.md":
            sections = bible._parse_sections(text)
            if not sections or bible.validate_sections(sections):
                raise VolumeProductionError("候选 Bible 节或固定表头非法")
            if (text.lstrip().startswith("---") or any(_missing(sections.get(s)) for s in books.BIBLE_SECTIONS)
                    or not parse_table(sections.get("力量体系", ""), "大境界")[1]):
                raise VolumeProductionError("候选 Bible 不完整或夹带 frontmatter")
            old_sections = bible._parse_sections(before)
            if before and not any(_missing(bible._parse_sections(before).get(key)) and not _missing(value)
                                  for key, value in sections.items()):
                raise VolumeProductionError("候选 Bible 没有缺失必要内容，必须复用现档案")
            for key in set(sections) | set(old_sections):
                if not _missing(old_sections.get(key)) and sections.get(key) != old_sections[key]:
                    raise VolumeProductionError("候选越权修改已有 Bible 内容")
                if key not in old_sections and key not in books.BIBLE_SECTIONS:
                    raise VolumeProductionError("候选 Bible 添加非必要节")
        elif path == "PLOT_POINTS.md":
            pp = _table(text, outline.PP_COLS)
            if pp or dest.exists():
                raise VolumeProductionError("候选不能将未来 FO 计划写成真实台账状态")
        elif path == "LOCATIONS.md":
            locs = _table(text, outline.LOC_COLS)
            if before:
                previous_locations = _table(before, outline.LOC_COLS)
                if locs == previous_locations:
                    raise VolumeProductionError("候选地点没有缺失必要内容，必须复用现档案")
                if any(row not in locs for row in previous_locations):
                    raise VolumeProductionError("候选不能越权改变现有地点表")
        elif dest.exists():
            raise VolumeProductionError("候选角色档案已存在，禁止覆盖")
    projection = _projection(book, bundle["files"])
    projection["outline"] = dict(fm, chapter_rows=rows, _rhythm=data["rhythm"])
    for path, text in bundle["files"].items():
        if path.startswith("characters/"):
            char_fm, _body = FM.parse(text)
            _validate_character(char_fm, text, projection["bible"])
            if path != f"characters/char-{characters.safe_name(char_fm['name'])}.md":
                raise VolumeProductionError("角色文件名与姓名不一致")
    if "LOCATIONS.md" in bundle["files"]:
        old_locations = outline.read_locations(book) if (books.book_dir(book) / "LOCATIONS.md").exists() else []
        initial = {card.get("status", {}).get("location") for path, text in bundle["files"].items()
                   if path.startswith("characters/") for card in [FM.parse(text)[0]]}
        loc_rows = projection["locations"]
        if len({row["名称"] for row in loc_rows}) != len(loc_rows):
            raise VolumeProductionError("候选地点名称重复")
        if any(row not in old_locations and row.get("名称") not in initial for row in loc_rows):
            raise VolumeProductionError("候选添加了非必要/无角色来源的地点")
    contracts = bundle["outlines"]
    if len(contracts) != len(numbers) or any(not isinstance(c, dict) or set(c) != {"no", "fm", "body"} for c in contracts):
        raise VolumeProductionError("候选合同包结构或数量不完整")
    if [c["no"] for c in contracts] != numbers:
        raise VolumeProductionError("候选合同必须顺序完整覆盖服务端章号")
    plots = projection["plot_rows"]
    previous_hook = ""
    if numbers[0] > 1 and chapters.outline_path(book, numbers[0] - 1).exists():
        previous_hook = chapters.read_outline_contract(book, numbers[0] - 1)["hook"]["type"]
    result = dict(bundle["files"])
    for contract in contracts:
        no = contract["no"]
        validate_contract(book, contract["fm"], contract["body"], no, projection, plots, previous_hook)
        plots = draftcontext.apply_fo_ops(plots, contract["fm"].get("foreshadow_ops") or [], no)
        previous_hook = contract["fm"]["hook"]["type"]
        path = chapters.outline_path(book, no)
        if not path.resolve().is_relative_to(books.book_dir(book).resolve()):
            raise VolumeProductionError("细纲路径越出本书")
        result[path.relative_to(books.book_dir(book)).as_posix()] = FM.dump(contract["fm"], contract["body"])
    # 这里只构造真实用户联合确认将采纳的凭据，绝不写盘或声称准备阶段已确认。
    adoptions = p["adoptions"]
    for key in list(adoptions):
        match = re.fullmatch(r"chapters:(U-[0-9a-f]{32}):([0-9]+)-([0-9]+)", key)
        if match:
            span = set(range(int(match[2]), int(match[3]) + 1))
            if span & set(numbers):
                if not span <= set(numbers):
                    raise VolumeProductionError("本批跨出既有采纳批次边界，请选完整范围或追加新单元")
                del adoptions[key]
    def accept(key: str, content: Any, parent: str) -> None:
        content_digest = planning.digest(content)
        previous = adoptions.get(key) or {}
        if previous.get("content_digest") == content_digest and previous.get("parent_digest") == parent:
            return
        adoptions[key] = {"content_digest": content_digest, "parent_digest": parent,
                          "accepted_at": None, "source": "human"}
    global_projection = planning.project_global(fm, p)
    if planning.digest(global_projection) != (adoptions.get("global") or {}).get("content_digest"):
        accept("global", global_projection, "")
    volume_projection = planning.project_volume(fm, p, vno)
    accept(f"volume:{vno}", volume_projection, planning.digest(global_projection))
    for unit in p["units"]:
        lo, hi = unit["chapters"]
        if not set(range(lo, hi + 1)) & set(numbers):
            continue
        unit_projection = planning.project_unit(p, unit["id"])
        accept(f"unit:{unit['id']}", unit_projection, planning.digest(volume_projection))
        cursor = max(lo, numbers[0])
        while cursor <= min(hi, numbers[-1]):
            outer_end = numbers[0] + ((cursor - numbers[0]) // 10 + 1) * 10 - 1
            end = min(outer_end, hi, numbers[-1])
            key = f"chapters:{unit['id']}:{cursor}-{end}"
            accept(key, planning.project_chapters(projection["outline"], unit["id"], cursor, end), planning.digest(unit_projection))
            cursor = end + 1
    fm["planning"] = p
    result["OUTLINE.md"] = _render_outline(fm, rows, data["rhythm"], old.get("_body") or "")
    return result


def _review(book: str, no: int, fingerprint: str) -> dict[str, Any]:
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM review_records WHERE book_id=? AND chapter_no=? AND kind='llm' "
                           "AND fingerprint=? ORDER BY id DESC LIMIT 1", (books.book_id(book), no, fingerprint)).fetchone()
    if row is None or row["verdict"] != "pass":
        raise VolumeProductionError("当前正文没有有效的通过审校记录")
    result = dict(row)
    for key in ("scores", "issues"):
        result[key] = json.loads(result.get(f"{key}_json") or ("{}" if key == "scores" else "[]"))
    return result


def generate_chapter(book: str, no: int, job_dir: str | Path, *, client: Any = None) -> dict[str, Any]:
    """（规格10 §4）已人工确认章串行生成到 pending_finalize；零 finalize/confirm/豁免。"""
    with writing(book), draftcontext.scoped(book, job_dir, no):
        conflicts.guard(book)
        review_prompt = prompts.get("V1", book)
        if any(f"〖{key}〗" not in review_prompt.body for key in ("批次临时上下文", "上一章结尾原文")):
            raise VolumeProductionError("PROMPT_UPGRADE_REQUIRED: V1 缺少批次临时事实/前文输入")
        contract = chapters.read_outline_contract(book, no)
        if contract.get("confirmed") is not True:
            raise VolumeProductionError("本章细纲尚未人工确认")
        if contract.get("chapter_no") != no:
            raise VolumeProductionError("已确认合同章号与文件不一致")
        source_outline = draftcontext.outline_fingerprint(book, no)
        context = draftcontext.current(book, no)
        draftcontext.apply_fo_ops(context["plot_rows"], contract.get("foreshadow_ops") or [], no)
        status = statemachine.current_status(book, no)
        if status == "error":
            statemachine.transition(book, no, "retry")
            status = statemachine.current_status(book, no)
        if status == "outline_locked":
            if chapters.prose_path(book, no).exists() or chapters.draft_path(book, no).exists():
                raise VolumeProductionError("存在旧正文或半成品，请先人工救援，不自动覆盖")
            pipeline.generate_prose(book, no, client=client)
            status = statemachine.current_status(book, no)
        if status == "pending_finalize":
            statemachine.transition(book, no, "recheck")
            status = "mech_check"
        check: mechcheck.CheckResult | None = None
        if status == "mech_check":
            check = pipeline.run_mech_check(book, no, client=client)
            status = statemachine.current_status(book, no)
            if check.verdict != "pass" or status != "llm_review":
                raise VolumeProductionError(f"第{no}章机检阻断/未生效，状态 {status}")
        if status == "llm_review":
            pipeline.run_llm_review(book, no, client=client)
        if statemachine.current_status(book, no) != "pending_finalize":
            raise VolumeProductionError(f"第{no}章未通过闭环，状态 {statemachine.current_status(book, no)}")
        # 有限修复可能改正文；以当前正文重算，旧机检对象不能充当新检查依据。
        if source_outline != draftcontext.outline_fingerprint(book, no):
            raise VolumeProductionError("生成期间已确认合同发生变化")
        current_check = pipeline._check(book, no, books.load_config(book)["chapter_word_range"])
        if current_check.verdict != "pass":
            raise VolumeProductionError("最终机检已阻断或未生效")
        prose = chapters.read_prose(book, no)
        pipeline._assert_current_check(book, no, prose["_body"])
        review = _review(book, no, mechcheck.fingerprint(prose["_body"]))
        if prose.get("status") != "draft":
            raise VolumeProductionError("批次生成结果不是待审草稿，禁止冒充成功")
        draftcontext.collect(book, job_dir, no, client=client)
        return {"no": no, "fingerprint": mechcheck.fingerprint(prose["_body"]),
                "outline_fingerprint": draftcontext.outline_fingerprint(book, no), "status": "pending_finalize",
                "check": current_check.to_dict(), "review": review}
