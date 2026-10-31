"""规格08 §6.4 真模型 1 章旧/新对比（隔离副本，双 arm 同起点）。

目的：验证「rolling 分层规划 + G6 单元闭包」相对 legacy「无单元」是否真的更好/无变化/更差。

设计（两臂唯一变量是 G6 的装配上下文）：
- 同一本书副本、同一上章（第 13 章，已定稿）、同一模型、同一提示词文件；
- A 臂 legacy：OUTLINE 表手写第 14 章行 → G6（无单元闭包）→ W1/场景专项 → 机检 → V1 审校；
- B 臂 rolling：G5 生成并采纳 全书→卷3→单元→章纲批次（第 14 章行由 G5 产出，
  再原样注入 A 臂，保证两臂章纲行完全一致）→ G6（含单元闭包）→ 同 A 臂后续；
- 模型由真实配置提供，但**不把 API key 写进临时目录**：临时 home 的 settings.json
  只含 model/prices/mechcheck 等，客户端由原始 settings 现场构造并注入。

用法：
  python scripts/_spec08_model_compare.py                 # 全流程
  python scripts/_spec08_model_compare.py --resume <root> # 从已建好的临时根继续
子进程角色（内部使用）：--arm plan|addrow|gen --home <dir> --book <name> --out <json>
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
BOOK = "流程测试书"
PY = sys.executable
CHAPTER = 14


# ---------------------------------------------------------------------------
# 子进程：真实客户端（密钥只在内存里，不落临时目录）
# ---------------------------------------------------------------------------

def _real_client():
    raw = json.loads((ROOT / "data" / "settings.json").read_text(encoding="utf-8"))
    providers = raw.get("providers") or {}
    if not providers:
        raise SystemExit("原 settings.json 没有 providers，无法调用真实模型")
    _name, conf = next(iter(providers.items()))
    from ainovel.llm import OpenAICompatClient

    return OpenAICompatClient(conf["base_url"], conf["api_key"])


def _bind_real_client():
    import ainovel.llm as llm_mod

    real = _real_client()
    llm_mod.client = lambda: real  # 管线内部 `client or llm.client()` 走这里
    return real


def _settings_for_temp_home() -> dict:
    """复制与模型无关、但影响装配/机检/计价的配置；**不含任何密钥**。"""
    raw = json.loads((ROOT / "data" / "settings.json").read_text(encoding="utf-8"))
    return {
        "default_model": raw.get("default_model"),
        "prices": raw.get("prices") or {},
        "mechcheck": raw.get("mechcheck") or {},
        "assembly_budget_tokens": raw.get("assembly_budget_tokens"),
    }


def _log_rows(book: str, chapter_no: int | None) -> list[dict]:
    """从 generation_log 取实调证据（prompt 版本/digest/模型/tokens/成本/状态）。"""
    from ainovel import books, db

    conn = db.get_conn()
    sql = ("SELECT stage, prompt_id, prompt_version, prompt_digest, model, "
           "input_tokens, output_tokens, cost_millicents, status, error "
           "FROM generation_log WHERE book_id=? AND chapter_no IS ? ORDER BY id")
    return [dict(r) for r in conn.execute(sql, (books.book_id(book), chapter_no))]


# ---------------------------------------------------------------------------
# 子进程：plan —— B 臂的分层规划（G5 四次 + 人工采纳路径）
# ---------------------------------------------------------------------------

def child_plan(home: Path, out: Path) -> int:
    os.environ["AINOVEL_HOME"] = str(home)
    real = _bind_real_client()
    from ainovel import pipeline, planning

    evidence: dict = {"steps": [], "requirements": {}}
    book = BOOK

    def gen_adopt(scope: str, target: dict, requirements: str,
                  fix_logline: str | None = None) -> dict:
        target = planning.normalize_target(scope, target)
        gate = planning.generation_gate(book, scope, target)
        if not gate["allowed"]:
            raise RuntimeError(f"{scope} 门禁未通过：{gate}")
        last_error: Exception | None = None
        for attempt in range(1, 3):  # 输出越界/少行属模型波动：重生成一次，仍不行就如实失败
            etag = planning.version_etag(book)
            req = requirements if attempt == 1 else (
                requirements + "\n\n【上次输出被程序驳回】范围必须与要求逐字一致："
                "单元骨架从规定起始章连续覆盖、批次行号必须完整覆盖给定区间，不得越界或跳号。")
            variables = planning.generate_variables(book, scope, target, req)
            t0 = time.time()
            res, p, _user = pipeline._call(book, None, "planning", "G5", variables, client=real)
            elapsed = round(time.time() - t0, 1)
            content = res.text
            if fix_logline is not None:
                # 作者会在采纳前校正候选：logline/终点必须保持原文，避免两臂上下文不一致
                obj = json.loads(content)
                obj["data"]["logline"] = fix_logline
                obj["data"]["ending_direction"] = planning.read_outline_full(book)["ending_direction"]
                content = json.dumps(obj, ensure_ascii=False)
            try:
                preview = planning.compute_preview(book, scope, target, content, etag)
                adopted = planning.adopt_preview(book, scope, target, content, etag,
                                                 preview["preview_digest"])
            except planning.PlanningInvalid as error:
                last_error = error
                print(f"[plan] {scope} 第 {attempt} 次被驳回：{error.code} {error}",
                      flush=True)
                continue
            step = {"scope": scope, "target": target, "requirements": req,
                    "attempts": attempt, "elapsed_s": elapsed,
                    "prompt_version": p.fm.get("version"), "prompt_digest": p.digest,
                    "impact": preview.get("impact"),
                    "adopted": {k: adopted.get(k) for k in
                                ("added_numbers", "changed_numbers", "units_added")}}
            evidence["steps"].append(step)
            print(f"[plan] {scope} ok {elapsed}s 版本 v{p.fm.get('version')} "
                  f"第{attempt}次 impact={json.dumps(preview.get('impact'), ensure_ascii=False)}",
                  flush=True)
            return adopted
        raise RuntimeError(f"{scope} 两次生成均无法采纳：{last_error}")

    planning.enable(book)
    fm = planning.read_outline_full(book)
    logline = fm["logline"]
    vol_no = next((str(v["no"]) for v in fm["volumes"]
                   if v.get("chapters") and v["chapters"][0] <= CHAPTER <= v["chapters"][1]), "3")
    req_global = ("本书已写到第 13 章，前两卷内容已完成。只需给出后续走向：本轮只分卷不细化。"
                  "logline 与尽头方向必须原文照抄，不得改写。")
    gen_adopt("global", {}, req_global, fix_logline=logline)
    req_vol = (f"只展开卷 {vol_no}（第 {CHAPTER} 章之后的内容）：单元骨架必须从第 {CHAPTER} 章起连续覆盖本卷，"
               "不得回头改动第 13 章及更早的章行。")
    gen_adopt("volume", {"volume_no": int(vol_no)}, req_vol)

    tree = planning.read_model(book)["tree"]
    vol = next(v for v in tree["volumes"] if str(v["no"]) == vol_no)
    unit = next(u for u in vol["units"] if u["chapters"][0] <= CHAPTER <= u["chapters"][1])
    req_unit = (f"本单元是第 {unit['chapters'][0]}-{unit['chapters'][1]} 章，其中第 13 章已定稿、"
               f"第 {CHAPTER} 章是本次要写的第一章。信息边界要写清楚：哪些能在本章露出、哪些必须压住。")
    gen_adopt("unit", {"volume_no": int(vol_no), "unit_id": unit["id"]}, req_unit)

    lo = CHAPTER
    hi = min(unit["chapters"][1], CHAPTER + 2)
    req_ch = (f"本批只写第 {lo}-{hi} 章七列章纲；第 {CHAPTER} 章要有可直接写正文的目标/冲突/爽点/钩子；"
              "不得改写第 13 章及更早的行。")
    gen_adopt("chapters", {"volume_no": int(vol_no), "unit_id": unit["id"],
                           "chapters": [lo, hi]}, req_ch)

    full = planning.read_outline_full(book)
    row = next(r for r in full["chapter_rows"]
               if int(__import__("re").search(r"\d+", str(r["章"])).group()) == CHAPTER)
    evidence["row"] = row
    evidence["unit_closure"] = {
        "unit_id": unit["id"], "name": unit.get("name"),
        "chapters": unit["chapters"],
    }
    evidence["log"] = _log_rows(book, None)
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[plan] 完成，章行：{json.dumps(row, ensure_ascii=False)}", flush=True)
    return 0


# ---------------------------------------------------------------------------
# 子进程：addrow —— 把 B 臂 G5 产出的章行原样给 A 臂（两臂章纲行完全一致）
# ---------------------------------------------------------------------------

def child_addrow(home: Path, row_file: Path, out: Path) -> int:
    os.environ["AINOVEL_HOME"] = str(home)
    from ainovel import outline, planning

    row = json.loads(row_file.read_text(encoding="utf-8"))
    fm = planning.read_outline_full(BOOK)
    rows = list(fm.get("chapter_rows") or [])
    rows = [r for r in rows
            if int(__import__("re").search(r"\d+", str(r.get("章", ""))).group()) != CHAPTER]
    rows.append(row)
    rows.sort(key=lambda r: int(__import__("re").search(r"\d+", str(r["章"])).group()))
    outline.write_outline(BOOK, {
        "logline": fm["logline"], "ending_direction": fm["ending_direction"],
        "volumes": fm["volumes"],
    }, rows=rows, rhythm=fm.get("_rhythm") or "")
    after = planning.read_outline_full(BOOK)
    out.write_text(json.dumps({"row_count": len(after["chapter_rows"])},
                              ensure_ascii=False), encoding="utf-8")
    print(f"[addrow] A 臂章行 {len(after['chapter_rows'])} 行", flush=True)
    return 0


# ---------------------------------------------------------------------------
# 子进程：gen —— 单臂 G6 细纲 → 正文 → 机检 → 审校
# ---------------------------------------------------------------------------

def child_gen(home: Path, arm: str, out: Path) -> int:
    os.environ["AINOVEL_HOME"] = str(home)
    real = _bind_real_client()
    from ainovel import chapters, pipeline, planning, statemachine

    ev: dict = {"arm": arm, "chapter": CHAPTER, "steps": []}
    t_all = time.time()

    t0 = time.time()
    contract = pipeline.generate_outline(BOOK, CHAPTER, client=real)
    ev["outline"] = {"elapsed_s": round(time.time() - t0, 1), "contract": contract}
    print(f"[gen:{arm}] 细纲 {ev['outline']['elapsed_s']}s "
          f"hook={contract.get('hook')} scenes={len(contract.get('scenes') or [])}", flush=True)

    # 实验操作：细纲确认是人工关，这里由脚本代为确认，仅用于生成正文做对比；
    # 真实采纳仍由作者在 UI 上完成（规格08 §6.4「人工确认不能以自动动作代替」）。
    pipeline.confirm_outline(BOOK, CHAPTER)

    t0 = time.time()
    prose = pipeline.generate_prose(BOOK, CHAPTER, client=real)
    ev["prose"] = {"elapsed_s": round(time.time() - t0, 1), **prose}
    print(f"[gen:{arm}] 正文 {prose.get('word_count')} 字 {ev['prose']['elapsed_s']}s", flush=True)

    t0 = time.time()
    mech = pipeline.run_mech_check(BOOK, CHAPTER, client=real)
    ev["mech"] = {
        "elapsed_s": round(time.time() - t0, 1), "verdict": mech.verdict,
        "blocks": [{"rule": h.rule, "word": h.word, "message": h.message}
                   for h in mech.hits if h.level == "block"],
        "warns": [{"rule": h.rule, "word": h.word} for h in mech.hits if h.level == "warn"],
        "ineffective": [{"rule": i.rule, "reason": i.reason} for i in mech.ineffective],
        "status_after": statemachine.current_status(BOOK, CHAPTER),
    }
    print(f"[gen:{arm}] 机检 {mech.verdict} blocks={len(ev['mech']['blocks'])} "
          f"warns={len(ev['mech']['warns'])} status={ev['mech']['status_after']}", flush=True)

    status = statemachine.current_status(BOOK, CHAPTER)
    if status == "llm_review":
        t0 = time.time()
        review = pipeline.run_llm_review(BOOK, CHAPTER, client=real)
        ev["review"] = {"elapsed_s": round(time.time() - t0, 1), **review}
        print(f"[gen:{arm}] 审校 {review.get('verdict')} "
              f"scores={json.dumps(review.get('scores'), ensure_ascii=False)}", flush=True)
    else:
        ev["review"] = {"skipped": f"状态 {status} 未进入审校（机检阻断/转人工）"}

    body = chapters.read_prose(BOOK, CHAPTER)["_body"]
    prev = chapters.read_prose(BOOK, CHAPTER - 1)["_body"] if CHAPTER > 1 else ""
    names = []
    try:
        from ainovel import characters
        names = characters.list_characters(BOOK)
    except Exception:
        pass
    ev["text"] = {
        "chars": chapters.count_words(body),
        "opening": body[:220],
        "prev_tail": prev[-220:],
        "names_in_opening": [n for n in names if n in body[:220]],
        "names_in_prev_tail": [n for n in names if n in prev[-220:]],
    }

    # B 臂额外记录单元闭包与「信息边界」兑现情况（人工读之前先给客观线索）
    try:
        info = planning.read_planning(BOOK)
        if info["mode"] == "rolling":
            uid = next((u["id"] for u in info["planning"]["units"]
                        if u["chapters"][0] <= CHAPTER <= u["chapters"][1]), None)
            unit = next((u for u in info["planning"]["units"] if u["id"] == uid), None)
            if unit:
                ev["unit_boundary"] = {
                    "unit_id": uid, "name": unit.get("name"),
                    "goal": unit.get("goal"), "must_hold": unit.get("must_hold") or [],
                    "can_reveal": unit.get("can_reveal") or [],
                    "must_hold_hit_in_text": [m for m in (unit.get("must_hold") or [])
                                              if m and m in body],
                }
    except Exception as error:
        ev["unit_boundary"] = {"error": str(error)}

    ev["log"] = _log_rows(BOOK, CHAPTER)
    ev["total_elapsed_s"] = round(time.time() - t_all, 1)
    out.write_text(json.dumps(ev, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[gen:{arm}] 完成，总 {ev['total_elapsed_s']}s", flush=True)
    return 0


# ---------------------------------------------------------------------------
# 父进程：搭环境 → 顺序跑 → 汇总
# ---------------------------------------------------------------------------

def _prepare_home(root: Path, name: str) -> Path:
    home = root / name
    home.mkdir(parents=True)
    shutil.copytree(ROOT / "prompts", home / "prompts")
    shutil.copytree(ROOT / "lint", home / "lint")
    (home / "books").mkdir()
    shutil.copytree(ROOT / "books" / BOOK, home / "books" / BOOK)
    (home / "data").mkdir()
    (home / "data" / "settings.json").write_text(
        json.dumps(_settings_for_temp_home(), ensure_ascii=False, indent=2), encoding="utf-8")
    return home


def _run_child(args: list[str], home: Path, log: Path) -> None:
    env = dict(os.environ, AINOVEL_HOME=str(home), PYTHONIOENCODING="utf-8")
    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.run([PY, str(Path(__file__)), *args], env=env, cwd=str(ROOT),
                              stdout=fh, stderr=subprocess.STDOUT, text=True)
    print(log.read_text(encoding="utf-8")[-2000:], flush=True)
    if proc.returncode != 0:
        raise SystemExit(f"子进程失败（{proc.returncode}）：{args}　日志：{log}")


def _compare(plan: dict, a: dict, b: dict) -> dict:
    def scores(ev: dict) -> dict:
        r = ev.get("review") or {}
        return r.get("scores") or {}

    return {
        "outline_chars": {"legacy": len(json.dumps(a["outline"]["contract"], ensure_ascii=False)),
                          "rolling": len(json.dumps(b["outline"]["contract"], ensure_ascii=False))},
        "outline_elapsed_s": {"legacy": a["outline"]["elapsed_s"],
                              "rolling": b["outline"]["elapsed_s"]},
        "words": {"legacy": a["text"]["chars"], "rolling": b["text"]["chars"]},
        "prose_elapsed_s": {"legacy": a["prose"]["elapsed_s"], "rolling": b["prose"]["elapsed_s"]},
        "mech": {"legacy": a["mech"]["verdict"], "rolling": b["mech"]["verdict"],
                 "legacy_blocks": [h["rule"] for h in a["mech"]["blocks"]],
                 "rolling_blocks": [h["rule"] for h in b["mech"]["blocks"]],
                 "legacy_warns": len(a["mech"]["warns"]), "rolling_warns": len(b["mech"]["warns"])},
        "review_verdict": {"legacy": (a.get("review") or {}).get("verdict"),
                           "rolling": (b.get("review") or {}).get("verdict")},
        "scores": {"legacy": scores(a), "rolling": scores(b)},
        "continuity": {
            "legacy": {"names_in_opening": a["text"]["names_in_opening"],
                       "names_in_prev_tail": a["text"]["names_in_prev_tail"]},
            "rolling": {"names_in_opening": b["text"]["names_in_opening"],
                        "names_in_prev_tail": b["text"]["names_in_prev_tail"]},
        },
        "unit_boundary": b.get("unit_boundary"),
        "planning_steps": [{"scope": s["scope"], "elapsed_s": s["elapsed_s"],
                            "version": s["prompt_version"]} for s in plan["steps"]],
        "cost_millicents": {
            "planning": sum((r.get("cost_millicents") or 0) for r in plan.get("log") or []),
            "legacy_chapter": sum((r.get("cost_millicents") or 0) for r in a.get("log") or []),
            "rolling_chapter": sum((r.get("cost_millicents") or 0) for r in b.get("log") or []),
        },
    }


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="ainovel-model-compare-"))
    if "--resume" in sys.argv:
        root = Path(sys.argv[sys.argv.index("--resume") + 1])
    print(f"临时根：{root}", flush=True)

    home_a = root / "home_legacy"
    home_b = root / "home_rolling"
    if not home_a.exists():
        home_a = _prepare_home(root, "home_legacy")
    if not home_b.exists():
        home_b = _prepare_home(root, "home_rolling")

    plan_json, row_json, a_json, b_json = (root / "plan.json", root / "row.json",
                                           root / "arm_a.json", root / "arm_b.json")
    addrow_json = root / "addrow.json"

    if not plan_json.exists():
        print("== B 臂规划（G5 global/volume/unit/chapters）==", flush=True)
        _run_child(["--arm", "plan", "--home", str(home_b), "--out", str(plan_json)],
                   home_b, root / "plan.log")
    plan = json.loads(plan_json.read_text(encoding="utf-8"))
    row_json.write_text(json.dumps(plan["row"], ensure_ascii=False, indent=2), encoding="utf-8")

    print("== A 臂注入同一章纲行 ==", flush=True)
    _run_child(["--arm", "addrow", "--home", str(home_a), "--row", str(row_json),
                "--out", str(addrow_json)], home_a, root / "addrow.log")

    print("== A 臂（legacy）==", flush=True)
    _run_child(["--arm", "gen", "--home", str(home_a), "--arm-name", "legacy",
                "--out", str(a_json)], home_a, root / "arm_a.log")
    print("== B 臂（rolling）==", flush=True)
    _run_child(["--arm", "gen", "--home", str(home_b), "--arm-name", "rolling",
                "--out", str(b_json)], home_b, root / "arm_b.log")

    a = json.loads(a_json.read_text(encoding="utf-8"))
    b = json.loads(b_json.read_text(encoding="utf-8"))
    evidence = {"book": BOOK, "chapter": CHAPTER, "temp_root": str(root),
                "model": json.loads((ROOT / "data" / "settings.json").read_text(
                    encoding="utf-8")).get("default_model"),
                "plan": plan, "arm_legacy": a, "arm_rolling": b,
                "compare": _compare(plan, a, b)}
    out = ROOT / ".pi" / "baseline" / "spec08-model-compare.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print("== 汇总 ==", flush=True)
    print(json.dumps(evidence["compare"], ensure_ascii=False, indent=2), flush=True)
    print(f"证据：{out}", flush=True)
    return 0


if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--arm" in argv:
        i = argv.index("--arm")
        role = argv[i + 1]
        home = Path(argv[argv.index("--home") + 1])
        out = Path(argv[argv.index("--out") + 1])
        if role == "plan":
            raise SystemExit(child_plan(home, out))
        if role == "addrow":
            raise SystemExit(child_addrow(home, Path(argv[argv.index("--row") + 1]), out))
        if role == "gen":
            arm = argv[argv.index("--arm-name") + 1]
            raise SystemExit(child_gen(home, arm, out))
        raise SystemExit(f"未知角色：{role}")
    raise SystemExit(main())
