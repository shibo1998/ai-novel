import faulthandler, os, sys, tempfile
faulthandler.dump_traceback_later(120, exit=True)
import shutil
from pathlib import Path

root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))
# 冒烟跑在临时 HOME，不污染仓库的 books/ 与 data/
home = Path(tempfile.mkdtemp(prefix="ainovel-smoke-"))
shutil.copytree(root / "prompts", home / "prompts")
shutil.copytree(root / "lint", home / "lint")
os.environ["AINOVEL_HOME"] = str(home)

from ainovel import bible, books, characters, outline, pipeline, statemachine
from ainovel.mockllm import MockLLM

books.create_book("测试书", genre_template="urban", target_words=30000)
book = "测试书"
bible.write(book, {
    "基础设定": "当代都市，灵气复苏初期。",
    "力量体系": "| 大境界 | 子境界 | 突破条件 | 能力边界(不能做什么) | 战力对标 | 寿元 | 代价与风险 | 突破异象 |\n"
               "|---|---|---|---|---|---|---|---|\n"
               "| 炼气 | 一层/二层/三层 | 引气入体百日 | 不能御物飞行 | 三名壮汉 | 不变 | 经脉受损 | 无 |\n"
               "| 筑基 | 初期 | 灵物筑基 | 不能隔空取物 | 十名炼气 | +50年 | 心魔 | 灵气外泄 |",
    "势力格局": "青云坊：中立坊市。",
    "历史脉络": "三年前灵气复苏。",
    "隐藏规则": "| 规则内容 | 隐藏等级 | 计划揭晓章节 |\n|---|---|---|\n| 玄铁令是钥匙 | 高 | 45 |",
    "术语表": "| 术语 | 含义 |\n|---|---|\n| 玄铁令 | 上古信物 |",
    "日常细节": "潮湿、市井。",
})
characters.create(book, "林寒", role="主角")
characters.create(book, "老周", role="重要配角")
outline.write_outline(
    book,
    {"logline": "为了查清父母失踪真相，林寒必须潜入青云坊，但玄铁令正在引来杀手",
     "ending_direction": "",
     "volumes": [{"no": 1, "name": "坊市", "chapters": [1, 12], "core_conflict": "坊市争夺",
                  "goal": "立足", "climax": "乱葬岗对决", "hook_out": "身世线索"}]},
    rows=[{"章": str(i), "卷": "1", "本章目标": "验令" + str(i), "冲突来源": "护卫",
           "爽点/揭露": "线索" + str(i), "钩子类型": ["悬念", "选择", "反转"][i % 3], "重要度★": "3"}
          for i in range(1, 13)],
    rhythm="1-2 小打脸，12 卷终")
outline.write_plot_points(book, [{"编号": "FO-001", "内容": "玄铁令发热", "埋设章": "1",
                                  "推进章": "", "预计回收章": "15", "状态": "planted", "备注": ""}])

mock = MockLLM()
fm = pipeline.generate_outline(book, 1, client=mock)
print("outline ok, hook:", fm["hook"])
pipeline.confirm_outline(book, 1)
print("status:", statemachine.current_status(book, 1))

r = pipeline.generate_prose(book, 1, client=mock)
print("prose:", r)

hits = pipeline.run_mech_check(book, 1, client=mock)
blocks = [h for h in hits if h.level == "block"]
print("mech blocks:", [(h.rule, h.word) for h in blocks][:5], "warns:", len(hits) - len(blocks))
print("status:", statemachine.current_status(book, 1))

rev = pipeline.run_llm_review(book, 1, client=mock)
print("review verdict:", rev["verdict"], "status:", statemachine.current_status(book, 1))

fin = pipeline.finalize(book, 1, client=mock)
print("finalize:", fin["snapshot_version"],
      "summary:", fin["writeback"]["event_summary"][:20],
      "conflicts:", len(fin["writeback"]["conflicts"]))
print("final status:", statemachine.current_status(book, 1))
print("ALL SMOKE OK")
