"""P1 六项缺陷的隔离端到端验证（临时 AINOVEL_HOME，不触碰仓库 books/ data/）。

跑法：python .audit/verify_p1_archive_api.py
用 tempfile.mkdtemp() 建临时 home 并拷贝 prompts/lint，全程走 FastAPI TestClient。
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="ainovel-p1-verify-"))
(TMP / "prompts").mkdir()
shutil.copytree(REPO / "prompts", TMP / "prompts", dirs_exist_ok=True)
shutil.copytree(REPO / "lint", TMP / "lint")
import os  # noqa: E402

os.environ["AINOVEL_HOME"] = str(TMP)

sys.path.insert(0, str(REPO))
from fastapi.testclient import TestClient  # noqa: E402

from ainovel import books, chapters, characters, outline, pipeline, statemachine  # noqa: E402
from ainovel.api import app  # noqa: E402
from ainovel.mockllm import MockLLM  # noqa: E402

c = TestClient(app)
fails: list[str] = []


def check(label: str, cond: bool, extra: str = "") -> None:
    print(("  OK   " if cond else "  FAIL ") + label + (f"  [{extra}]" if extra else ""))
    if not cond:
        fails.append(label)


def make_book(name: str) -> str:
    books.create_book(name, genre_template="urban")
    characters.create(name, "林寒", role="主角")
    return name


print(f"临时 home: {TMP}\n")

# ---------------------------------------------------------------- 1. G5 合并
print("1) G5 采纳合并式（不覆盖伏笔台账 / 不覆盖 logline）")
b = make_book("验证书一")
outline.write_outline(b, {"logline": "用户写的 logline", "ending_direction": "牺牲小我",
                          "volumes": []},
                     rows=[{"章": str(i), "卷": "1", "本章目标": "t", "冲突来源": "c",
                            "爽点/揭露": "s", "钩子类型": "悬念", "重要度★": "★3"}
                           for i in range(1, 4)])
outline.write_plot_points(b, [
    {"编号": "FO-001", "内容": "玄铁令发热", "埋设章": "1", "推进章": "7",
     "预计回收章": "15", "状态": "building", "备注": ""},
    {"编号": "FO-002", "内容": "老周隐瞒身世", "埋设章": "2", "推进章": "5",
     "预计回收章": "9", "状态": "paid", "备注": ""},
])
g5 = ("## 卷规划\n| 卷号 | 卷名 | 起止章 | 核心冲突 | 本卷目标 | 卷末高潮 | 卷末钩子(出卷悬念) |\n"
      "|---|---|---|---|---|---|---|\n| 1 | 坊市 | 1-3 | cc | g | cl | ho |\n\n"
      "## 章节总表\n| 章 | 卷 | 本章目标 | 冲突来源 | 爽点/揭露 | 钩子类型 | 重要度★ |\n"
      "|---|---|---|---|---|---|---|\n"
      "| 1 | 1 | 新1 | 新c1 | 新s1 | 悬念 | ★3 |\n"
      "| 2 | 1 | 新2 | 新c2 | 新s2 | 选择 | ★4 |\n"
      "| 3 | 1 | 新3 | 新c3 | 新s3 | 反转 | ★5 |\n\n"
      "## 伏笔总表\n| 编号 | 内容 | 埋设章 | 推进章 | 预计回收章 | 备注 |\n"
      "|---|---|---|---|---|---|\n"
      "| F1 | 玄铁令发热 | 1 |  | 15 |  |\n"
      "| F2 | 父亲留下的信 | 3 |  | 20 |  |\n\n"
      "## 爽点节奏总谱\n\n新节奏文本\n")
r = c.post(f"/api/books/{b}/adopt/outline", json={"content": g5}).json()
print(f"     返回: {r}")
rows = {x["编号"]: x for x in outline.read_plot_points(b)}
check("FO-001 状态保留 building", rows.get("FO-001", {}).get("状态") == "building")
check("FO-001 推进章保留 7", rows.get("FO-001", {}).get("推进章") == "7")
check("FO-002 未被删除且仍 paid",
      "FO-002" in rows and rows["FO-002"]["状态"] == "paid")
check("新内容拿 FO-003 而不是复用 FO-001", rows.get("FO-003", {}).get("内容") == "父亲留下的信")
check("新增/保留计数正确", r.get("foreshadows_added") == 1 and r.get("foreshadows_kept") == 1)
check("logline 未被 meta 占位串覆盖",
      outline.read_outline(b)["logline"] == "用户写的 logline")
check("ending_direction 未被清空", outline.read_outline(b)["ending_direction"] == "牺牲小我")
check("节奏总谱正常写入", "新节奏文本" in outline.read_rhythm(b))

# ---------------------------------------------------------------- 2. G4 写回
print("\n2) G4 关系网合并写回「关系网络」节")
g4 = ("===角色:林寒===\n## 关系网络\n| 对象 | 关系 | 不稳定因素 |\n|---|---|---|\n"
      "| 老周 | 师徒 | 老周隐瞒身世 |\n\n"
      "===角色:沈九===\n## 关系网络\n| 对象 | 关系 | 不稳定因素 |\n|---|---|---|\n"
      "| 林寒 | 宿敌 | 旧仇未清 |\n")
r2 = c.post(f"/api/books/{b}/adopt/characters", json={"content": g4}).json()
print(f"     返回: {r2}")
rels = characters.relationships(b, "林寒")
check("已有角色的关系行写进关系网络节",
      {"对象": "老周", "关系": "师徒", "不稳定因素": "老周隐瞒身世"} in rels)
check("新角色也有关系网络节",
      characters.relationships(b, "沈九")[0]["对象"] == "林寒")
check("已有角色卡未被整体覆盖",
      characters.read(b, "林寒")["role"] == "主角"
      and "## 表层信息" in characters.read(b, "林寒")["_body"])
r2b = c.post(f"/api/books/{b}/adopt/characters", json={"content": g4}).json()
check("重复采纳不重复追加", len(characters.relationships(b, "林寒")) == len(rels))

# ---------------------------------------------------------------- 3/4/6
print("\n3/4) 手改正文 PUT /prose + 快照回看 GET /snapshots/{version}")
from ainovel.mockllm import make_prose  # noqa: E402

mock = MockLLM()
pipeline.generate_outline(b, 1, client=mock)
pipeline.confirm_outline(b, 1)
pipeline.generate_prose(b, 1, client=mock)
check("生成后状态 mech_check", statemachine.current_status(b, 1) == "mech_check")
# 手改内容必须落在字数区间内，否则 M-01 会（正确地）阻断到 human_review
hand = make_prose((2200, 2800)) + "\n手改补一句：令牌发烫。"
r3 = c.put(f"/api/books/{b}/chapters/1/prose", json={"content": hand})
print(f"     手改返回: {r3.status_code} word_count={r3.json().get('word_count')}")
check("手改 200 且字数正确",
      r3.status_code == 200
      and r3.json()["word_count"] == len("".join(hand.split())))
p = chapters.read_prose(b, 1)
check("正文已改 / status=draft / version 不变",
      p["_body"] == hand and p["status"] == "draft" and p["version"] == 1)

pipeline.run_mech_check(b, 1, client=mock)
pipeline.run_llm_review(b, 1, client=mock)
pipeline.finalize(b, 1, client=mock)
check("定稿后状态 finalized", statemachine.current_status(b, 1) == "finalized")
r3b = c.put(f"/api/books/bad/{b}/chapters/1/prose", json={"content": "x"}) if False else \
    c.put(f"/api/books/{b}/chapters/1/prose", json={"content": "篡改"})
print(f"     定稿后手改: {r3b.status_code} {r3b.json()}")
check("定稿后手改被拒 400", r3b.status_code == 400 and "已定稿" in r3b.json()["detail"])

vs = c.get(f"/api/books/{b}/chapters/1/versions").json()
r4 = c.get(f"/api/books/{b}/chapters/1/snapshots/{vs[0]['version']}")
print(f"     快照返回: {r4.status_code} keys={sorted(r4.json())}")
check("快照正文可取回", r4.status_code == 200 and r4.json()["content"])
check("快照 label/created_at 非空",
      bool(r4.json()["label"]) and bool(r4.json()["created_at"]))
check("不存在的快照 404",
      c.get(f"/api/books/{b}/chapters/1/snapshots/99").status_code == 404)

print("\n5) 地点档案 PUT /locations")
loc = [{"名称": "青云坊", "特征": "潮湿坊市", "方位/距离": "城南三里",
        "首现章": "1", "关联剧情": "验令"},
       {"名称": "乱葬岗", "特征": "荒坟", "方位/距离": "东南三里",
        "首现章": "3", "关联剧情": "埋伏"}]
r5 = c.put(f"/api/books/{b}/locations", json={"rows": loc})
got = c.get(f"/api/books/{b}/locations").json()
print(f"     PUT {r5.status_code} {r5.json()} → GET {got}")
check("地点写入可读回一致", r5.status_code == 200 and got == loc)
raw_loc = (books.book_dir(b) / "LOCATIONS.md").read_text(encoding="utf-8")
check("落盘为 5 列固定表",
      "| 名称 | 特征 | 方位/距离 | 首现章 | 关联剧情 |" in raw_loc)

print("\n6) Markdown 导出 POST /export/md")
r6 = c.post(f"/api/books/{b}/export/md")
md = r6.text
print(f"     状态 {r6.status_code} content-type={r6.headers['content-type']} "
      f"disposition={r6.headers['content-disposition']}")
check("200 + text/markdown", r6.status_code == 200
      and r6.headers["content-type"].startswith("text/markdown"))
check("文件名 .md", ".md" in r6.headers["content-disposition"])
check("二级标题 ## 第1章", "## 第1章" in md)
check("正文原样带出（手改内容）", "令牌发烫" in md)
txt = c.post(f"/api/books/{b}/export/txt").text
check("txt 导出仍然正常（未回归）", "第1章" in txt)

print("\n" + "=" * 60)
if fails:
    print(f"FAILED {len(fails)}: {fails}")
    sys.exit(1)
print("全部通过：6 项缺陷端到端验证 OK")
shutil.rmtree(TMP, ignore_errors=True)
