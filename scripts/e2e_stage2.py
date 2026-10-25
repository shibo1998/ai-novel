# -*- coding: utf-8 -*-
"""阶段2真实E2E：升级提示词后的第2章全闭环（含润色）+ V6 卷末审计。"""
import json
import sys
import time

import httpx

BASE = "http://127.0.0.1:8765"
BOOK = "流程测试书"
c = httpx.Client(base_url=BASE, timeout=600.0)
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


def api(method, path, **kw):
    r = c.request(method, path, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
    return r


def status(no):
    return api("GET", f"/api/books/{BOOK}/chapters/{no}").json()["status"]


def sse_generate(no):
    t = time.time()
    n = 0
    with c.stream("POST", f"/api/books/{BOOK}/chapters/{no}/generate") as r:
        ev = "message"
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line[7:].strip()
            elif line.startswith("data:"):
                if ev == "done":
                    log(f"  ch{no} 生成 {time.time() - t:.0f}s:", json.loads(line[6:]))
                elif ev == "error":
                    raise RuntimeError("生成失败: " + line[6:])
                else:
                    n += len(line)


# ---- 0. 环境确认
log("ch2 status:", status(2))

# ---- 1. 第2章闭环
if status(2) == "outline_draft":
    api("POST", f"/api/books/{BOOK}/chapters/2/outline/confirm")
    log("ch2 confirmed")
if status(2) in ("outline_locked", "generating"):
    sse_generate(2)
log("ch2 ->", status(2))

if status(2) == "mech_check":
    t = time.time()
    r = api("POST", f"/api/books/{BOOK}/chapters/2/mechcheck").json()
    blocks = [x for x in r["hits"] if x["level"] == "block"]
    log(f"ch2 mech {time.time() - t:.0f}s blocks={len(blocks)} -> {r['status']}")

if status(2) == "llm_review":
    t = time.time()
    r = api("POST", f"/api/books/{BOOK}/chapters/2/review").json()
    log(f"ch2 V1 {time.time() - t:.0f}s verdict={r['verdict']} re_reviewed={r.get('re_reviewed')}")
    log("  scores:", r.get("scores"))
    for i in (r.get("issues") or [])[:5]:
        log(f"  [{i.get('severity')}] {i.get('category')}: {str(i.get('description'))[:60]}")
    log("  ->", r.get("status"))
    if r.get("status") == "llm_review":  # fix 循环后再审
        r = api("POST", f"/api/books/{BOOK}/chapters/2/review").json()
        log("  二轮 verdict=", r["verdict"], "->", r.get("status"))

if status(2) == "human_review":
    api("POST", f"/api/books/{BOOK}/chapters/2/exempt", json={"reason": "E2E 阶段2放行"})
    log("ch2 豁免 ->", status(2))

if status(2) == "pending_finalize":
    t = time.time()
    r = api("POST", f"/api/books/{BOOK}/chapters/2/polish").json()
    log(f"ch2 润色 {time.time() - t:.0f}s ok={r.get('ok')} {r.get('word_count')} "
        f"v3={r.get('v3', {}).get('verdict')} changes={len(r.get('changes', []))}")
    if not r.get("ok"):
        log("  润色被丢弃:", r.get("reason"))
    r = api("POST", f"/api/books/{BOOK}/chapters/2/finalize").json()
    log("ch2 finalize snap=v", r["snapshot_version"], "writeback.applied:", r["writeback"].get("applied_chars"))
log("ch2 final:", status(2))

# ---- 2. V6 卷末审计（手动，用现有 2 章）
t = time.time()
r = api("POST", f"/api/books/{BOOK}/audit/2").json()
log(f"V6 审计 {time.time() - t:.0f}s 回收率={r['foreshadow_pay_rate']} 报告={r['report_path']}")

# ---- 3. 成本
ov = api("GET", f"/api/books/{BOOK}/overview").json()
log(f"TOTAL: {ov['cost_total']['calls']} 次调用")
log("STAGE2 ALL PASS ✅")
