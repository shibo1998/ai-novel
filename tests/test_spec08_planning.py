"""规格08 阶段B 数据层用例（§6.3 P-T01/P-T03/P-T04/P-T06/P-T14）。

只测 `ainovel/planning.py`：规划元数据解析、分层指纹、就绪状态、估算与保护集。
全部用临时 AINOVEL_HOME，不读写真实 books/、data/，不调模型。
"""
from __future__ import annotations

import hashlib
import json

import pytest

from ainovel import atomic, books, chapters, outline, planning
from ainovel import frontmatter as FM

UID1 = "U-00000000000000000000000000000001"
UID2 = "U-00000000000000000000000000000002"


def _planning(**over) -> dict:
    base = {
        "schema_version": 1,
        "enabled": True,
        "global": {"reader_promise": "爽感", "selling_points": ["逆袭"],
                   "mainline": "底层→门阀", "stage_boundaries": ["坊市"],
                   "reserved_reveals": ["父辈真相"], "estimated_chapters_override": None},
        "units": [
            {"id": UID1, "volume_no": 1, "name": "单元一", "detail_level": "expanded",
             "chapters": [1, 3], "goal": "立足", "obstacle": "护卫", "choices": ["验令"],
             "outcome": "拿到线索", "can_reveal": ["令牌异常"], "must_hold": ["父辈真相"],
             "story_lines": ["主线"]},
            {"id": UID2, "volume_no": 1, "name": "单元二", "detail_level": "rough",
             "chapters": [4, 6]},
        ],
        "adoptions": {},
    }
    base.update(over)
    return base


def _write_book(name: str = "规划书", *, with_planning: bool = True,
                rows: list[dict[str, str]] | None = None, **over) -> str:
    if name not in {b.get("title") for b in books.list_books()}:
        books.create_book(name, target_words=1_000_000, chapter_word_range=[2200, 2800])
    fm = {"logline": "为了查清真相，林寒必须潜入坊市，但令牌引来杀手",
          "ending_direction": "查清父母失踪",
          "volumes": [{"no": 1, "name": "坊市", "direction": "立足坊市",
                       "estimated_chapters": 6, "detail_level": "expanded",
                       "chapters": [1, 6], "core_conflict": "坊市争夺",
                       "goal": "立足", "climax": "对决", "hook_out": "身世",
                       "ending_state": "计划：拿到玄铁令"}]}
    if with_planning:
        fm["planning"] = _planning(**over)
    outline.write_outline(name, fm, rows=rows if rows is not None else [
        {"章": str(i), "卷": "1", "本章目标": f"目标{i}", "冲突来源": f"冲突{i}",
         "爽点/揭露": f"爽点{i}", "钩子类型": "悬念", "重要度★": "3"} for i in range(1, 7)
    ], rhythm="1-2 小打脸")
    return name


def _contract(no: int, confirmed: bool) -> dict:
    return {"chapter_no": no, "volume": 1, "stage": "引入", "emotion_task": "压",
            "core_event": "验令", "scenes": [], "emotion_arc": {}, "hook": {"type": "悬念"},
            "word_range": [2200, 2800], "pov": "林寒", "confirmed": confirmed}


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# P-T01：缺 planning 的旧书不写文件、不新建采纳状态
# ---------------------------------------------------------------------------

def test_legacy_book_remains_legacy_without_writes(home):
    name = _write_book(with_planning=False)
    path = books.book_dir(name) / "OUTLINE.md"
    before = _sha(path)

    assert planning.read_planning(name)["mode"] == "legacy"
    assert planning.readiness(name)["mode"] == "legacy"
    assert planning.tree(name)["mode"] == "legacy"
    # 读一遍不得改写 OUTLINE，也不得凭空塞 enabled/采纳凭据
    assert _sha(path) == before
    raw = (books.book_dir(name) / "OUTLINE.md").read_text(encoding="utf-8")
    assert "planning:" not in raw


# ---------------------------------------------------------------------------
# P-T03：只有粗范围时已规划章数仍为 0
# ---------------------------------------------------------------------------

def test_rough_ranges_do_not_create_planned_chapters(home):
    name = _write_book(rows=[])  # 章节总表为空，但单元有范围
    tree = planning.tree(name)
    assert tree["chapter_rows"] == 0
    assert tree["chapter_numbers"] == []
    # 单元范围仍然可见，供人工展开；只是不产生正式章行
    assert tree["volumes"][0]["units"][0]["chapters"] == [1, 3]
    assert chapters.list_chapters(name) == []


# ---------------------------------------------------------------------------
# P-T04：估算与已规划章数分离
# ---------------------------------------------------------------------------

def test_estimates_are_distinct_from_planned_counts(home):
    name = _write_book()  # 目标 100 万，区间 2200-2800 → 中值 2500
    est = planning.estimates(name)
    assert est["estimated_chapters"] == 400
    assert est["source"] == "auto"
    # 已规划章数按已采纳/正式章行算，绝不把估算当分母
    assert planning.tree(name)["chapter_rows"] == 6

    # 作者覆盖值优先
    _write_book("规划书")  # 重建
    info = planning.read_planning("规划书")["planning"]
    info["global"]["estimated_chapters_override"] = 120
    outline.write_outline("规划书", {
        "logline": "x", "ending_direction": "y",
        "volumes": [{"no": 1, "name": "坊市", "dir": "d"}], "planning": info,
    }, rows=[], rhythm="")
    est2 = planning.estimates("规划书")
    assert est2["estimated_chapters"] == 120
    assert est2["source"] == "manual"
    assert est2["auto_chapters"] == 400

    # 区间无效 → 无法估算，不用默认值假装有效
    meta = {k: v for k, v in books.read_meta("规划书").items() if not k.startswith("_")}
    meta["chapter_word_range"] = ["a", "b"]
    books.write_meta("规划书", meta)
    est3 = planning.estimates("规划书")
    assert est3["estimated_chapters"] is None
    assert est3["source"] == "unknown"


# ---------------------------------------------------------------------------
# P-T06：采纳依赖用内容指纹；深化单元不反向污染上层
# ---------------------------------------------------------------------------

def test_adoption_dependencies_use_content_digests(home):
    name = _write_book()
    fm = planning.read_outline_full(name)
    info = planning.read_planning(name)["planning"]
    gd = planning.digest(planning.project_global(fm, info))
    vd = planning.digest(planning.project_volume(fm, info, "1"))
    ud = planning.digest(planning.project_unit(info, UID1))
    info["adoptions"] = {
        "global": {"content_digest": gd, "parent_digest": "", "accepted_at": "t", "source": "manual"},
        "volume:1": {"content_digest": vd, "parent_digest": gd, "accepted_at": "t", "source": "manual"},
        f"unit:{UID1}": {"content_digest": ud, "parent_digest": vd, "accepted_at": "t", "source": "manual"},
    }
    outline.write_outline(name, {
        "logline": fm["logline"], "ending_direction": fm["ending_direction"],
        "volumes": fm["volumes"], "planning": info,
    }, rows=fm["chapter_rows"], rhythm=fm["_rhythm"])

    state = planning.readiness(name)["layers"]
    assert state["global"]["status"] == "ready"
    assert [v["status"] for v in state["volumes"]] == ["ready"]
    assert state["units"][0]["status"] == "ready"

    # 只深化单元详情：单元变待采纳，卷/全书仍就绪（不反向污染）
    info2 = json.loads(json.dumps(info))
    info2["units"][0]["outcome"] = "拿到线索，但暴露身份"
    outline.write_outline(name, {
        "logline": fm["logline"], "ending_direction": fm["ending_direction"],
        "volumes": fm["volumes"], "planning": info2,
    }, rows=fm["chapter_rows"], rhythm=fm["_rhythm"])
    state2 = planning.readiness(name)["layers"]
    assert state2["global"]["status"] == "ready"
    assert [v["status"] for v in state2["volumes"]] == ["ready"]
    assert state2["units"][0]["status"] == "unadopted"
    # 也不自动重写下层：单元内容还是作者刚写的那份
    assert planning.read_planning(name)["planning"]["units"][0]["outcome"] == "拿到线索，但暴露身份"

    # 上游变化（改全书方向）→ 已就绪的卷变"上游变化待复核"
    info3 = json.loads(json.dumps(info))
    fm3 = dict(fm)
    fm3["logline"] = "换一个方向"
    outline.write_outline(name, {
        "logline": fm3["logline"], "ending_direction": fm3["ending_direction"],
        "volumes": fm3["volumes"], "planning": info3,
    }, rows=fm["chapter_rows"], rhythm=fm["_rhythm"])
    state3 = planning.readiness(name)["layers"]
    assert state3["global"]["status"] == "unadopted"
    assert [v["status"] for v in state3["volumes"]] == ["stale"]
    assert state3["volumes"][0]["message"].startswith("上游变化待复核")


# ---------------------------------------------------------------------------
# P-T14：结构损坏不成为 ready
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("broken,code", [
    ({"schema_version": 99}, "PLANNING_SCHEMA_UNSUPPORTED"),
    ({"enabled": "false"}, "PLANNING_INVALID"),
    ({"units": [{"id": "not-a-uuid", "volume_no": 1, "name": "x", "chapters": [1, 2]}]},
     "PLANNING_RANGE_INVALID"),
    ({"units": [{"id": UID1, "volume_no": 1, "name": "x", "chapters": [5, 2]}]},
     "PLANNING_RANGE_INVALID"),
])
def test_planning_invalid_is_closed(home, broken, code):
    name = _write_book(**broken)
    with pytest.raises(planning.PlanningInvalid) as error:
        planning.read_planning(name)
    assert error.value.code == code
    state = planning.readiness(name)
    assert state["mode"] == "invalid"
    assert planning.tree(name)["mode"] == "invalid"
    # 损坏不等于空规划：不返回可写空树
    assert "layers" not in state or not state["layers"]


def test_planning_empty_skeleton_is_not_ready(home):
    name = _write_book(**{"global": {}}, units=[], adoptions={})
    state = planning.readiness(name)["layers"]
    assert state["global"]["status"] == "unadopted"
    assert state["units"] == []


# ---------------------------------------------------------------------------
# 保护集（§4.4）
# ---------------------------------------------------------------------------

def test_protected_numbers_come_from_files(home):
    name = _write_book()
    atomic.write_text(chapters.prose_path(name, 2), FM.dump(
        {"chapter_no": 2, "version": 1, "status": "draft", "word_count": 10,
         "finalized_at": None}, "草稿正文"))
    atomic.write_text(chapters.memory_path(name, 3), FM.dump(
        {"chapter_no": 3, "version": 1, "facts": []}, "记忆"))
    atomic.write_text(chapters.outline_path(name, 4), FM.dump(_contract(4, True), "细纲"))
    atomic.write_text(chapters.outline_path(name, 5), FM.dump(_contract(5, False), "未确认细纲"))

    assert planning.protected_numbers(name) == [2, 3, 4]


# ---------------------------------------------------------------------------
# 服务层端点用例（规格08 §6.3 阶段B：P-T02/P-T05/P-T07/P-T08/P-T09/P-T10/P-T11/P-T16）
# 走 TestClient + fake pipeline._call（不联网、不真模型），全部临时 home。
# ---------------------------------------------------------------------------
from fastapi.testclient import TestClient  # noqa: E402

from ainovel.api import app as _app
from ainovel import llm as _llm
from ainovel import pipeline as _pipeline
from ainovel import prompts as _prompts

client = TestClient(_app)


def _hash(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _get_etag(book: str) -> str:
    r = client.get(f"/api/books/{book}/planning")
    assert r.status_code == 200, r.text
    return r.headers["etag"]


def _candidates() -> dict[str, str]:
    return {
        "global": json.dumps({
            "scope": "global", "target": {"volume_no": None, "unit_id": None, "chapters": None},
            "data": {
                "logline": "为了夺回令牌，林寒必须闯坊市，但杀手一路追杀",
                "ending_direction": "查清父母失踪真相",
                "global": {"reader_promise": "逆袭爽感", "selling_points": ["打脸"],
                           "mainline": "底层→门阀", "stage_boundaries": ["坊市止于立足"],
                           "reserved_reveals": ["父辈真相"], "estimated_chapters_override": None},
                "rough_volumes": [
                    {"no": 1, "name": "坊市", "direction": "立足坊市", "estimated_chapters": 6},
                    {"no": 2, "name": "宗门", "direction": "卷入宗门", "estimated_chapters": 8},
                ]}}, ensure_ascii=False),
        "volume": json.dumps({
            "scope": "volume", "target": {"volume_no": 1, "unit_id": None, "chapters": None},
            "data": {"chapter_count": 6,
                     "volume_detail": {"core_conflict": "坊市争夺", "goal": "立足",
                                       "climax": "对决", "hook_out": "身世",
                                       "ending_state": "计划：拿到线索"},
                     "unit_skeletons": [
                         {"name": "单元一", "chapters": [1, 3], "goal": "验令", "obstacle": "护卫"},
                         {"name": "单元二", "chapters": [4, 6], "goal": "夺证", "obstacle": "杀手"},
                     ]}}, ensure_ascii=False),
        "unit": json.dumps({
            "scope": "unit", "target": {"volume_no": 1, "unit_id": "PLACEHOLDER", "chapters": None},
            "data": {"unit_detail": {
                "goal": "验出令牌异常", "obstacle": "护卫盘查",
                "choices": ["硬闯/借道"], "outcome": "拿到线索",
                "can_reveal": ["令牌遇灵气发热"], "must_hold": ["父辈真相"],
                "story_lines": ["主线"]}}}, ensure_ascii=False),
        "chapters": json.dumps({
            "scope": "chapters",
            "target": {"volume_no": 1, "unit_id": "PLACEHOLDER", "chapters": [1, 3]},
            "data": {"chapter_rows": [
                {"章": "1", "卷": "1", "本章目标": "验令", "冲突来源": "护卫",
                 "爽点/揭露": "令牌发热", "钩子类型": "悬念", "重要度★": "3"},
                {"章": "2", "卷": "1", "本章目标": "脱身", "冲突来源": "杀手",
                 "爽点/揭露": "身手", "钩子类型": "选择", "重要度★": "3"},
                {"章": "3", "卷": "1", "本章目标": "夺证", "冲突来源": "杀手",
                 "爽点/揭露": "线索", "钩子类型": "反转", "重要度★": "4"},
            ], "rhythm_note": "1-3 密集小打脸"}}, ensure_ascii=False),
    }


def _fake_call_factory(candidates: dict[str, str], calls: list[dict]):
    def fake_call(book, no, stage, prompt_id, variables, client=None,
                  stream_cb=None, extra_instruction=""):
        calls.append({"prompt_id": prompt_id, "variables": dict(variables),
                      "stage": stage, "book": book})
        scope = str(variables.get("规划作用域", ""))
        key = next((k for k in candidates if scope.startswith(k)), None)
        text = candidates[key] if key else "（Mock 默认输出）"
        if "PLACEHOLDER" in text:
            uid = str(variables.get("规划上下文", ""))
            import re as _re
            m = _re.search(r"U-[0-9a-f]{32}", uid)
            text = text.replace("PLACEHOLDER", m.group(0) if m else "U-" + "0" * 32)
        p = _prompts.get("G5", book)
        return _llm.LLMResult(text=text, input_tokens=10, output_tokens=10,
                              finish_reason="stop", model="mock"), p, text
    return fake_call


def test_create_mode_is_explicit_and_offline(home, monkeypatch):
    """P-T02：显式传 rolling 只初始化空规划（零模型调用）；省略保持 legacy。"""
    calls: list[dict] = []
    monkeypatch.setattr(_pipeline, "_call", _fake_call_factory(_candidates(), calls))
    r = client.post("/api/books", json={"name": "滚动新书", "planning_mode": "rolling"})
    assert r.status_code == 200, r.text
    assert calls == []  # 零模型调用
    g = client.get("/api/books/滚动新书/planning").json()
    assert g["mode"] == "rolling"
    assert g["readiness"]["layers"]["global"]["status"] == "unadopted"
    r2 = client.post("/api/books", json={"name": "旧式新书"})
    assert r2.status_code == 200
    assert client.get("/api/books/旧式新书/planning").json()["mode"] == "legacy"


def test_legacy_book_planning_get_is_readonly(home):
    """P-T01：legacy 书 GET /planning 不写文件。"""
    name = _write_book(name="旧模式书", with_planning=False)
    before = _hash(books.book_dir(name) / "OUTLINE.md")
    r = client.get(f"/api/books/{name}/planning")
    assert r.status_code == 200
    assert r.json()["mode"] == "legacy"
    assert r.headers.get("etag")
    assert _hash(books.book_dir(name) / "OUTLINE.md") == before


def test_enable_requires_if_match_and_preserves_legacy_content(home):
    """P-T10：显式启用不改旧稿；缺 If-Match → 428；重复 enable 幂等。"""
    name = _write_book(name="启用书", with_planning=False)
    before_rows = books.book_dir(name) / "OUTLINE.md"
    original = before_rows.read_text(encoding="utf-8")
    r = client.post(f"/api/books/{name}/planning/enable", json={"mode": "rolling"})
    assert r.status_code == 428, r.text
    etag = _get_etag(name)
    r = client.post(f"/api/books/{name}/planning/enable", json={"mode": "rolling"},
                    headers={"If-Match": '"sha256-wrong"'})
    assert r.status_code == 412
    r = client.post(f"/api/books/{name}/planning/enable", json={"mode": "rolling"},
                    headers={"If-Match": etag})
    assert r.status_code == 200, r.text
    after = before_rows.read_text(encoding="utf-8")
    # 章节表与节奏正文保留；只多了 planning 元数据
    assert "## 章节总表" in after and "目标1" in after
    assert "planning:" in after
    snapshot = _hash(before_rows)
    etag2 = _get_etag(name)
    r = client.post(f"/api/books/{name}/planning/enable", json={"mode": "rolling"},
                    headers={"If-Match": etag2})
    assert r.status_code == 200  # 幂等：不清采纳记录
    assert _hash(before_rows) == snapshot


def test_legacy_write_bypass_rejected_for_rolling(home):
    """P-T09：rolling 书上旧全量 G5 / 全量采纳 / PUT outline 全部 409。"""
    name = _write_book(name="防绕过书")
    for action in ("adopt/outline",):
        r = client.post(f"/api/books/{name}/{action}", json={"content": "## 章节总表"})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "PLANNING_SCOPE_REQUIRED"
    r = client.put(f"/api/books/{name}/outline", json={"logline": "x"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PLANNING_SCOPE_REQUIRED"
    r = client.post(f"/api/books/{name}/ai/generate",
                    json={"prompt_id": "G5", "variables": {}})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PLANNING_SCOPE_REQUIRED"


def test_preview_and_adopt_are_scoped(home, monkeypatch):
    """P-T07：生成/预览/采纳只改选中 scope；无关字段与 PLOT_POINTS 字节不变。"""
    calls: list[dict] = []
    monkeypatch.setattr(_pipeline, "_call", _fake_call_factory(_candidates(), calls))
    r = client.post("/api/books", json={"name": "链路书", "planning_mode": "rolling"})
    assert r.status_code == 200
    name = "链路书"
    pp_before = _hash(books.book_dir(name) / "PLOT_POINTS.md")

    # ---- 全书层 ----
    etag = _get_etag(name)
    r = client.post(f"/api/books/{name}/planning/generate",
                    json={"scope": "global", "target": {"volume_no": None, "unit_id": None,
                                                        "chapters": None}, "requirements": ""},
                    headers={"If-Match": etag})
    assert r.status_code == 200, r.text
    gen = r.json()
    assert gen["prompt_id"] == "G5" and gen["prompt_version"] == _prompts.get("G5", name).fm["version"]
    assert "规划作用域" in calls[0]["variables"]
    r = client.post(f"/api/books/{name}/planning/preview",
                    json={"scope": "global", "target": gen["target"], "content": gen["content"],
                          "input_version": gen["input_version"]},
                    headers={"If-Match": etag})
    assert r.status_code == 200, r.text
    preview = r.json()
    assert preview["preview_digest"]
    r = client.post(f"/api/books/{name}/planning/adopt",
                    json={"scope": "global", "target": gen["target"], "content": gen["content"],
                          "input_version": gen["input_version"],
                          "preview_digest": preview["preview_digest"]},
                    headers={"If-Match": etag})
    assert r.status_code == 200, r.text
    adopted = r.json()
    assert adopted["updated_scope"] == "global"
    assert _hash(books.book_dir(name) / "PLOT_POINTS.md") == pp_before

    # ---- 卷层（global 未采纳时被门禁拦 → 现已采纳，应放行）----
    etag = _get_etag(name)
    r = client.post(f"/api/books/{name}/planning/generate",
                    json={"scope": "volume", "target": {"volume_no": 1, "unit_id": None,
                                                        "chapters": None}, "requirements": ""},
                    headers={"If-Match": etag})
    assert r.status_code == 200, r.text
    vol_gen = r.json()
    r = client.post(f"/api/books/{name}/planning/preview",
                    json={"scope": "volume", "target": vol_gen["target"],
                          "content": vol_gen["content"], "input_version": vol_gen["input_version"]},
                    headers={"If-Match": etag})
    assert r.status_code == 200, r.text
    vol_preview = r.json()
    assert vol_preview["impact"]["volume_range"] == [1, 6]
    assert len(vol_preview["impact"]["units_added"]) == 2  # 程序分配的单元 ID
    r = client.post(f"/api/books/{name}/planning/adopt",
                    json={"scope": "volume", "target": vol_gen["target"],
                          "content": vol_gen["content"], "input_version": vol_gen["input_version"],
                          "preview_digest": vol_preview["preview_digest"]},
                    headers={"If-Match": etag})
    assert r.status_code == 200, r.text
    uid = r.json()["units_added"][0]

    # ---- 单元层 ----
    etag = _get_etag(name)
    unit_target = {"volume_no": 1, "unit_id": uid, "chapters": None}
    r = client.post(f"/api/books/{name}/planning/generate",
                    json={"scope": "unit", "target": unit_target, "requirements": ""},
                    headers={"If-Match": etag})
    assert r.status_code == 200, r.text
    unit_gen = r.json()
    r = client.post(f"/api/books/{name}/planning/preview",
                    json={"scope": "unit", "target": unit_target, "content": unit_gen["content"],
                          "input_version": unit_gen["input_version"]},
                    headers={"If-Match": etag})
    assert r.status_code == 200, r.text
    unit_preview = r.json()
    r = client.post(f"/api/books/{name}/planning/adopt",
                    json={"scope": "unit", "target": unit_target, "content": unit_gen["content"],
                          "input_version": unit_gen["input_version"],
                          "preview_digest": unit_preview["preview_digest"]},
                    headers={"If-Match": etag})
    assert r.status_code == 200, r.text

    # ---- 章纲批次 ----
    etag = _get_etag(name)
    ch_target = {"volume_no": 1, "unit_id": uid, "chapters": [1, 3]}
    r = client.post(f"/api/books/{name}/planning/generate",
                    json={"scope": "chapters", "target": ch_target, "requirements": ""},
                    headers={"If-Match": etag})
    assert r.status_code == 200, r.text
    ch_gen = r.json()
    r = client.post(f"/api/books/{name}/planning/preview",
                    json={"scope": "chapters", "target": ch_target, "content": ch_gen["content"],
                          "input_version": ch_gen["input_version"]},
                    headers={"If-Match": etag})
    assert r.status_code == 200, r.text
    ch_preview = r.json()
    assert ch_preview["impact"]["added_numbers"] == [1, 2, 3]
    r = client.post(f"/api/books/{name}/planning/adopt",
                    json={"scope": "chapters", "target": ch_target, "content": ch_gen["content"],
                          "input_version": ch_gen["input_version"],
                          "preview_digest": ch_preview["preview_digest"]},
                    headers={"If-Match": etag})
    assert r.status_code == 200, r.text
    assert r.json()["added_numbers"] == [1, 2, 3]

    # 全链路后：树里 3 行章纲、四层 ready、PLOT_POINTS 未动
    g = client.get(f"/api/books/{name}/planning").json()
    assert g["tree"]["chapter_rows"] == 3
    assert g["readiness"]["layers"]["global"]["status"] == "ready"
    assert _hash(books.book_dir(name) / "PLOT_POINTS.md") == pp_before
    assert "U-" in json.dumps(g["tree"]["volumes"][0]["units"][0]["id"])


def test_generation_gate_blocks_lower_layers(home, monkeypatch):
    """P-T06（API 面）：上游未采纳时下层 generate 被拒。"""
    calls: list[dict] = []
    monkeypatch.setattr(_pipeline, "_call", _fake_call_factory(_candidates(), calls))
    client.post("/api/books", json={"name": "门禁书", "planning_mode": "rolling"})
    name = "门禁书"
    etag = _get_etag(name)
    r = client.post(f"/api/books/{name}/planning/generate",
                    json={"scope": "volume", "target": {"volume_no": 1, "unit_id": None,
                                                        "chapters": None}, "requirements": ""},
                    headers={"If-Match": etag})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PLANNING_NOT_READY"
    # legacy scope 不被新端点接受
    r = client.post(f"/api/books/{name}/planning/generate",
                    json={"scope": "legacy", "target": {"volume_no": None, "unit_id": None,
                                                        "chapters": None}, "requirements": ""},
                    headers={"If-Match": etag})
    assert r.status_code == 422


def test_preview_input_version_preconditions(home, monkeypatch):
    """P-T08：输入版本变化 → 409 保留候选；显式 rebase 才按当前版本重建。"""
    calls: list[dict] = []
    monkeypatch.setattr(_pipeline, "_call", _fake_call_factory(_candidates(), calls))
    client.post("/api/books", json={"name": "版本书", "planning_mode": "rolling"})
    name = "版本书"
    etag = _get_etag(name)
    gen = client.post(f"/api/books/{name}/planning/generate",
                      json={"scope": "global", "target": {"volume_no": None, "unit_id": None,
                                                          "chapters": None}, "requirements": ""},
                      headers={"If-Match": etag}).json()
    # 正式档案发生变化（新章节文件也纳入版本）
    atomic.write_text(chapters.outline_path(name, 100),
                      FM.dump({"chapter_no": 100, "confirmed": False}, "占位"))
    etag2 = _get_etag(name)
    r = client.post(f"/api/books/{name}/planning/preview",
                    json={"scope": "global", "target": gen["target"], "content": gen["content"],
                          "input_version": gen["input_version"]},
                    headers={"If-Match": etag2})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PLANNING_INPUT_CHANGED"
    r = client.post(f"/api/books/{name}/planning/preview",
                    json={"scope": "global", "target": gen["target"], "content": gen["content"],
                          "input_version": gen["input_version"], "rebase": True},
                    headers={"If-Match": etag2})
    assert r.status_code == 200, r.text
    assert r.json()["baseline_changed"] is True
    # 缺 If-Match 的新端点一律 428
    r = client.post(f"/api/books/{name}/planning/preview",
                    json={"scope": "global", "target": gen["target"], "content": gen["content"],
                          "input_version": gen["input_version"]})
    assert r.status_code == 428


def test_adopt_rejects_stale_preview_and_edited_candidate(home, monkeypatch):
    """P-T07/P-T08（API 面）：预览后改候选 → digest 不符 409；版本变化 → 412。"""
    calls: list[dict] = []
    monkeypatch.setattr(_pipeline, "_call", _fake_call_factory(_candidates(), calls))
    client.post("/api/books", json={"name": "预检书", "planning_mode": "rolling"})
    name = "预检书"
    etag = _get_etag(name)
    gen = client.post(f"/api/books/{name}/planning/generate",
                      json={"scope": "global", "target": {"volume_no": None, "unit_id": None,
                                                          "chapters": None}, "requirements": ""},
                      headers={"If-Match": etag}).json()
    preview = client.post(f"/api/books/{name}/planning/preview",
                          json={"scope": "global", "target": gen["target"],
                                "content": gen["content"], "input_version": gen["input_version"]},
                          headers={"If-Match": etag}).json()
    edited = json.loads(gen["content"])
    edited["data"]["logline"] = "被人工编辑过的候选"
    r = client.post(f"/api/books/{name}/planning/adopt",
                    json={"scope": "global", "target": gen["target"],
                          "content": json.dumps(edited, ensure_ascii=False),
                          "input_version": gen["input_version"],
                          "preview_digest": preview["preview_digest"]},
                    headers={"If-Match": etag})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PLANNING_PREVIEW_CHANGED"
    # 版本变化（If-Match 不再匹配当前）
    atomic.write_text(chapters.outline_path(name, 101),
                      FM.dump({"chapter_no": 101, "confirmed": False}, "占位"))
    r = client.post(f"/api/books/{name}/planning/adopt",
                    json={"scope": "global", "target": gen["target"],
                          "content": gen["content"], "input_version": gen["input_version"],
                          "preview_digest": preview["preview_digest"]},
                    headers={"If-Match": etag})
    assert r.status_code == 412


def test_protected_chapters_and_incremental_adoption(home, monkeypatch):
    """P-T09/P-T11：受保护章行不可改；相同候选重放不重复追加。"""
    calls: list[dict] = []
    monkeypatch.setattr(_pipeline, "_call", _fake_call_factory(_candidates(), calls))
    client.post("/api/books", json={"name": "保护书", "planning_mode": "rolling"})
    name = "保护书"
    etag = _get_etag(name)
    gen = client.post(f"/api/books/{name}/planning/generate",
                      json={"scope": "global", "target": {"volume_no": None, "unit_id": None,
                                                          "chapters": None}, "requirements": ""},
                      headers={"If-Match": etag}).json()
    preview = client.post(f"/api/books/{name}/planning/preview",
                          json={"scope": "global", "target": gen["target"],
                                "content": gen["content"], "input_version": gen["input_version"]},
                          headers={"If-Match": etag}).json()
    client.post(f"/api/books/{name}/planning/adopt",
                json={"scope": "global", "target": gen["target"], "content": gen["content"],
                      "input_version": gen["input_version"],
                      "preview_digest": preview["preview_digest"]},
                headers={"If-Match": etag})
    etag = _get_etag(name)
    vol_gen = client.post(f"/api/books/{name}/planning/generate",
                          json={"scope": "volume", "target": {"volume_no": 1, "unit_id": None,
                                                              "chapters": None}, "requirements": ""},
                          headers={"If-Match": etag}).json()
    vol_preview = client.post(f"/api/books/{name}/planning/preview",
                              json={"scope": "volume", "target": vol_gen["target"],
                                    "content": vol_gen["content"],
                                    "input_version": vol_gen["input_version"]},
                              headers={"If-Match": etag}).json()
    adopted = client.post(f"/api/books/{name}/planning/adopt",
                          json={"scope": "volume", "target": vol_gen["target"],
                                "content": vol_gen["content"],
                                "input_version": vol_gen["input_version"],
                                "preview_digest": vol_preview["preview_digest"]},
                          headers={"If-Match": etag}).json()
    uid = adopted["units_added"][0]
    etag = _get_etag(name)
    unit_target = {"volume_no": 1, "unit_id": uid, "chapters": None}
    unit_gen = client.post(f"/api/books/{name}/planning/generate",
                           json={"scope": "unit", "target": unit_target, "requirements": ""},
                           headers={"If-Match": etag}).json()
    unit_preview = client.post(f"/api/books/{name}/planning/preview",
                               json={"scope": "unit", "target": unit_target,
                                     "content": unit_gen["content"],
                                     "input_version": unit_gen["input_version"]},
                               headers={"If-Match": etag}).json()
    client.post(f"/api/books/{name}/planning/adopt",
                json={"scope": "unit", "target": unit_target, "content": unit_gen["content"],
                      "input_version": unit_gen["input_version"],
                      "preview_digest": unit_preview["preview_digest"]},
                headers={"If-Match": etag})

    # 批次 [1,3] 采纳后写第 2 章正文 → 第 2 章受保护
    etag = _get_etag(name)
    ch_target = {"volume_no": 1, "unit_id": uid, "chapters": [1, 3]}
    ch_gen = client.post(f"/api/books/{name}/planning/generate",
                         json={"scope": "chapters", "target": ch_target, "requirements": ""},
                         headers={"If-Match": etag}).json()
    ch_preview = client.post(f"/api/books/{name}/planning/preview",
                             json={"scope": "chapters", "target": ch_target,
                                   "content": ch_gen["content"],
                                   "input_version": ch_gen["input_version"]},
                             headers={"If-Match": etag}).json()
    first = client.post(f"/api/books/{name}/planning/adopt",
                        json={"scope": "chapters", "target": ch_target,
                              "content": ch_gen["content"],
                              "input_version": ch_gen["input_version"],
                              "preview_digest": ch_preview["preview_digest"]},
                        headers={"If-Match": etag})
    assert first.status_code == 200, first.text
    atomic.write_text(chapters.prose_path(name, 2), FM.dump(
        {"chapter_no": 2, "version": 1, "status": "draft", "word_count": 10,
         "finalized_at": None}, "第2章正文"))

    # 改动受保护章行的批次 → 409 PLANNING_PROTECTED
    etag = _get_etag(name)
    bad = json.loads(ch_gen["content"])
    bad["data"]["chapter_rows"][1]["本章目标"] = "被规划改写的受保护行"
    r = client.post(f"/api/books/{name}/planning/preview",
                    json={"scope": "chapters", "target": ch_target,
                          "content": json.dumps(bad, ensure_ascii=False),
                          "input_version": etag},
                    headers={"If-Match": etag})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "PLANNING_PROTECTED"

    # 相同候选重放（重新预览后采纳）→ 不重复追加行
    etag = _get_etag(name)
    again_preview = client.post(f"/api/books/{name}/planning/preview",
                                json={"scope": "chapters", "target": ch_target,
                                      "content": ch_gen["content"], "input_version": etag},
                                headers={"If-Match": etag}).json()
    again = client.post(f"/api/books/{name}/planning/adopt",
                        json={"scope": "chapters", "target": ch_target,
                              "content": ch_gen["content"], "input_version": etag,
                              "preview_digest": again_preview["preview_digest"]},
                        headers={"If-Match": etag})
    assert again.status_code == 200, again.text
    fm = outline.read_outline(name)
    nos = [int(r2["章"]) for r2 in fm["chapter_rows"]]
    assert nos == [1, 2, 3]  # 没有重复行
    adoptions = fm["planning"]["adoptions"]
    chapter_keys = [k for k in adoptions if k.startswith(f"chapters:{uid}")]
    assert len(chapter_keys) == 1  # 记录更新而非重叠追加


def test_planning_prompt_versions_and_g6_closure(home):
    """P-T16：G5/G6 文件名与 ID 不变、版本递增、各作用域渲染无残留变量。"""
    g5 = _prompts.get("G5")
    g6 = _prompts.get("G6")
    assert g5.fm["version"] >= 3 and g5.fm["id"] == "G5"
    assert g6.fm["version"] >= 5 and g6.fm["id"] == "G6"
    assert "〖所属剧情单元〗" in g6.body and "〖本章规划边界〗" in g6.body

    name = _write_book(name="闭包书")
    unit_text, boundary = _pipeline._planning_closure(
        name, 2, outline.read_outline(name))
    assert "单元一" in unit_text and "必须保留" in unit_text
    assert "U-" in boundary  # 边界文本带单元 ID，可核对
    legacy_name = _write_book(name="闭包旧书", with_planning=False)
    unit_text2, boundary2 = _pipeline._planning_closure(
        legacy_name, 2, outline.read_outline(legacy_name))
    assert "旧模式无单元" in unit_text2 and "旧模式" in boundary2

    # 作用域变量装配 → G5 渲染无残留
    variables = planning.generate_variables(name, "global",
                                            {"volume_no": None, "unit_id": None, "chapters": None})
    _p, text = _prompts.render("G5", variables, name)
    assert "〖" not in text


def test_planning_rebuild_preserves_file_truth(home):
    """P-T15：删索引重建后 OUTLINE/planning 与就绪状态不变（文件是真相）。"""
    from ainovel import db as _db
    name = _write_book(name="重建书")
    before = (books.book_dir(name) / "OUTLINE.md").read_text(encoding="utf-8")
    state_before = planning.readiness(name)
    _db.rebuild()
    assert (books.book_dir(name) / "OUTLINE.md").read_text(encoding="utf-8") == before
    assert planning.readiness(name) == state_before


def test_next_actions_do_not_skip_forward_volumes(home):
    """顺序回归：卷1 未完成（单元未细化/章纲有缺口）时，不越过到卷2。"""
    name = _write_book(name="顺序书")
    fm = planning.read_outline_full(name)
    fm["volumes"] = [
        dict(fm["volumes"][0]),
        {"no": 2, "name": "门阀", "direction": "北上", "estimated_chapters": 6,
         "detail_level": "rough", "chapters": [7, 12]},
    ]
    info = planning.read_planning(name)["planning"]
    gd = planning.digest(planning.project_global(fm, info))
    vd = planning.digest(planning.project_volume(fm, info, "1"))
    ud = planning.digest(planning.project_unit(info, UID1))
    info["adoptions"] = {
        "global": {"content_digest": gd, "parent_digest": "", "accepted_at": "t",
                   "source": "manual"},
        "volume:1": {"content_digest": vd, "parent_digest": gd, "accepted_at": "t",
                     "source": "manual"},
        f"unit:{UID1}": {"content_digest": ud, "parent_digest": vd, "accepted_at": "t",
                         "source": "manual"},
    }
    rows = [{"章": str(i), "卷": "1", "本章目标": f"目标{i}", "冲突来源": f"冲突{i}",
             "爽点/揭露": f"爽点{i}", "钩子类型": "悬念", "重要度★": "3"}
            for i in range(1, 4)]  # 只有单元一 [1,3] 的章行，单元二 [4,6] 有缺口
    outline.write_outline(name, {
        "logline": fm["logline"], "ending_direction": fm["ending_direction"],
        "volumes": fm["volumes"], "planning": info,
    }, rows=rows, rhythm=fm["_rhythm"])

    # 阶段1：卷1 的单元二还没细化 → 建议单元二，而不是卷2
    actions = planning.next_actions(name)
    assert actions[0]["action"] == "generate" and actions[0]["scope"] == "unit"
    assert actions[0]["target"]["unit_id"] == UID2

    # 阶段2：单元二细化并采纳后，卷1 章纲 [4,6] 有缺口 → 建议批次 4-6，仍不是卷2
    info["units"][1]["goal"] = "打通北上门路"
    ud2 = planning.digest(planning.project_unit(info, UID2))
    info["adoptions"][f"unit:{UID2}"] = {
        "content_digest": ud2, "parent_digest": vd, "accepted_at": "t", "source": "manual"}
    outline.write_outline(name, {
        "logline": fm["logline"], "ending_direction": fm["ending_direction"],
        "volumes": fm["volumes"], "planning": info,
    }, rows=rows, rhythm=fm["_rhythm"])
    actions = planning.next_actions(name)
    assert actions[0]["action"] == "generate" and actions[0]["scope"] == "chapters"
    assert actions[0]["target"]["chapters"] == [4, 6]

    # 阶段3：卷1 全覆盖后，才轮到卷2 的卷纲
    rows += [{"章": str(i), "卷": "1", "本章目标": f"目标{i}", "冲突来源": f"冲突{i}",
              "爽点/揭露": f"爽点{i}", "钩子类型": "悬念", "重要度★": "3"}
             for i in range(4, 7)]
    outline.write_outline(name, {
        "logline": fm["logline"], "ending_direction": fm["ending_direction"],
        "volumes": fm["volumes"], "planning": info,
    }, rows=rows, rhythm=fm["_rhythm"])
    actions = planning.next_actions(name)
    assert actions[0]["action"] == "generate" and actions[0]["scope"] == "volume"
    assert actions[0]["target"]["volume_no"] == "2"


def test_g5_variables_carry_realm_guard(home):
    """§6.4 实测暴露：规划层会写出越级境界 → G5 上下文必须带境界口径硬约束。"""
    from ainovel import bible

    name = _write_book(name="境界书")
    bible.write(name, {
        "基础设定": "灵气复苏初期。",
        "力量体系": "| 大境界 | 子境界 | 突破条件 | 能力边界(不能做什么) | 战力对标 | 寿元 | 代价与风险 | 突破异象 |\n"
                   "|---|---|---|---|---|---|---|---|\n"
                   "| 感气境 | 初入/明目/洗髓 | 引气入体 | 不能外放 | 特种兵 | 百岁 | 经脉撕裂 | 无 |\n"
                   "| 凝煞境 | 淬骨/凝刃/化罡 | 结煞 | 不能硬抗重火力 | 小队 | 150年 | 煞气攻心 | 霜降 |",
        "势力格局": "青云坊。", "历史脉络": "三年前。",
        "隐藏规则": "| 规则内容 | 隐藏等级 | 计划揭晓章节 |\n|---|---|---|\n| 令牌是钥匙 | 高 | 45 |",
        "术语表": "| 术语 | 含义 |\n|---|---|\n| 玄铁令 | 信物 |",
        "日常细节": "潮湿。",
    })

    vars_unit = planning.generate_variables(
        name, "unit", {"volume_no": 1, "unit_id": UID1, "chapters": None})
    ctx = vars_unit["规划上下文"]
    assert "力量体系口径（硬约束）" in ctx
    assert "感气境" in ctx and "凝煞境" in ctx
    assert "禁止越级" in ctx

    # 章纲批次同样带（下层写错会一路污染正文）
    vars_ch = planning.generate_variables(
        name, "chapters", {"volume_no": 1, "unit_id": UID1, "chapters": [1, 2]})
    assert "力量体系口径（硬约束）" in vars_ch["规划上下文"]

    # 全书作用域不需要（粗纲不写个人境界）
    vars_global = planning.generate_variables(
        name, "global", {"volume_no": None, "unit_id": None, "chapters": None})
    assert "力量体系口径（硬约束）" not in vars_global["规划上下文"]


def test_g5_variables_carry_realm_guard(home):
    """§6.4 实测暴露：规划层会写出越级境界 → G5 上下文必须带境界口径硬约束。"""
    from ainovel import bible

    name = _write_book(name="境界书")
    bible.write(name, {
        "基础设定": "灵气复苏初期。",
        "力量体系": "| 大境界 | 子境界 | 突破条件 | 能力边界(不能做什么) | 战力对标 | 寿元 | 代价与风险 | 突破异象 |\n"
                   "|---|---|---|---|---|---|---|---|\n"
                   "| 感气境 | 初入/明目/洗髓 | 引气入体 | 不能外放 | 特种兵 | 百岁 | 经脉撕裂 | 无 |\n"
                   "| 凝煞境 | 淬骨/凝刃/化罡 | 结煞 | 不能硬抗重火力 | 小队 | 150年 | 煞气攻心 | 霜降 |",
        "势力格局": "青云坊。", "历史脉络": "三年前。",
        "隐藏规则": "| 规则内容 | 隐藏等级 | 计划揭晓章节 |\n|---|---|---|\n| 令牌是钥匙 | 高 | 45 |",
        "术语表": "| 术语 | 含义 |\n|---|---|\n| 玄铁令 | 信物 |",
        "日常细节": "潮湿。",
    })

    vars_unit = planning.generate_variables(
        name, "unit", {"volume_no": 1, "unit_id": UID1, "chapters": None})
    ctx = vars_unit["规划上下文"]
    assert "力量体系口径（硬约束）" in ctx
    assert "感气境" in ctx and "凝煞境" in ctx
    assert "禁止越级" in ctx

    # 章纲批次同样带（下层写错会一路污染正文）
    vars_ch = planning.generate_variables(
        name, "chapters", {"volume_no": 1, "unit_id": UID1, "chapters": [1, 2]})
    assert "力量体系口径（硬约束）" in vars_ch["规划上下文"]

    # 全书作用域不需要（粗纲不写个人境界）
    vars_global = planning.generate_variables(
        name, "global", {"volume_no": None, "unit_id": None, "chapters": None})
    assert "力量体系口径（硬约束）" not in vars_global["规划上下文"]
