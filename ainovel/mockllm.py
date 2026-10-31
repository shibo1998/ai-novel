"""MockLLM：测试与离线联调。按提示词特征路由，产出可通过程序校验的输出。"""
from __future__ import annotations

import re
from typing import Any, Callable

from . import llm, mechcheck


class MockLLM:
    def __init__(self):
        self.calls: list[dict[str, str]] = []
        self.outline_factory: Callable[[int, str], str] | None = None
        self.prose_factory: Callable[[int, tuple[int, int]], str] | None = None
        self.review_verdicts: list[dict[str, Any]] = []
        self.memory_factory: Callable[[int], dict[str, Any]] | None = None
        self.fix_overreach: bool = False
        self.fail_next: Exception | None = None

    # ------------------------------------------------------------------
    def chat(self, *, model: str, system: str, user: str, temperature: float,
             stream_cb=None, retry: bool = True, require_complete: bool = False,
             provider: str = "") -> llm.LLMResult:
        if self.fail_next:
            err, self.fail_next = self.fail_next, None
            raise err
        self.calls.append({"model": model, "provider": provider, "system": system, "user": user})
        text = self._route(system, user)
        if stream_cb:
            step = max(1, len(text) // 40)
            for i in range(0, len(text), step):
                stream_cb(text[i:i + step])
        return llm.LLMResult(
            text=text,
            input_tokens=llm.est_tokens(system + user),
            output_tokens=llm.est_tokens(text),
            finish_reason="stop", model=model, provider=provider,
        )

    # ------------------------------------------------------------------
    def _route(self, system: str, user: str) -> str:
        if "- 输出模式：结构化故事候选" in user:
            return self._default_story_ideas(user)
        if "- 输出模式：选题六问报告" in user:
            return "## 选题六问报告\n\n（离线Mock选题报告，等待作者选择，不写正文）"
        if "资深世界观架构师" in user:
            return self._default_bible()
        if "资深玄幻/仙侠修炼体系设计师" in user:
            from . import bible
            from .mdtable import dump_table

            return dump_table(bible.POWER_COLS, [["炼气", "一层/二层/三层", "引气入体", "不能飞行", "壮汉", "百年", "经脉受损", "无"]])
        if "资深角色设计师" in user:
            return self._default_cards()
        if "设计他们之间的关系网络" in user:
            return ("===角色:林寒===\n| 对象 | 关系 | 不稳定因素 |\n|---|---|---|\n| 老周 | 合作 | 隐瞒线索 |\n"
                    "===角色:老周===\n| 对象 | 关系 | 不稳定因素 |\n|---|---|---|\n| 林寒 | 合作 | 不信任 |\n")
        if "- 规划作用域：" in user and "target 回显：" in user:
            return self._default_planning(user)
        if "单章细纲" in user:
            no = self._grab_no(user)
            if self.outline_factory:
                return self.outline_factory(no, user)
            text = self._default_outline(no)
            volume = re.search(r"所属卷目标：[^\n]*\"no\"\s*:\s*(\d+)", user)
            return text.replace("volume: 1", f"volume: {volume[1]}") if volume else text
        if ("本章上下文" in user or "黄金三章" in user or "专写本场景对话" in user
                or "突破场景" in user or "高潮对决" in user):
            no = self._grab_no(user)
            rng = self._grab_range(user)
            if self.prose_factory:
                return self.prose_factory(no, rng)
            return make_prose(rng)
        if "服务端原稿分块JSON" in user:  # 规格06 V2：默认没有必要改动
            return _to_json({"edits": []})
        if "【保真六维】" in user:  # 规格06 V3：只有真实新旧片段才可给证据
            before_match = re.search(r"【原稿全文】\n(.*?)\n【润色后全文】", user, re.S)
            after_match = re.search(r"【润色后全文】\n(.*?)\n【服务端真实编辑清单】", user, re.S)
            before = before_match.group(1).strip() if before_match else ""
            after = after_match.group(1).strip() if after_match else ""
            keys = ("events", "character_decisions", "knowledge", "settings_numbers_costs",
                    "time_space_relations", "foreshadow_ending")
            return _to_json({"clean": True, "verdict": "pass", "new_issues": [],
                             "style_verdict": "improved" if before != after else "unchanged",
                             "style_evidence": [{"before": before[:50], "after": after[:50],
                                                 "reason": "离线测试候选对照"}],
                             "fidelity": {key: {"verdict": "pass", "reason": "离线保真测试"}
                                          for key in keys}})
        if "只修复问题清单中" in user or "只修复问题清单涉及" in user:
            return self._default_fix(user)
        if "===润色全文===" in user:  # 兼容旧书级V2测试，产品入口会提前检查版本
            m = re.search(r"【待润色正文】\n(.*?)\n\n", user, re.DOTALL)
            body = m.group(1) if m else user
            return ("===润色全文===\n" + body + "\n\n===改动清单===\n"
                    "| ai_flavor | 章中 | 句式调整 |")
        if "复核润色结果" in user or "[W0]" in user:  # V3
            return "```json\n" + _to_json({"clean": True, "new_issues": [], "verdict": "pass"}) + "\n```"
        if "分析以下语料" in user or "维度拆解" in user:  # F1 文风分析
            return ("## 句法特征\n短句为主，均句长 12-18 字，短句占比 40%。\n\n"
                    "## 可执行参数\n| 参数 | 数值区间 |\n| 句长 | 10-20字 |\n")
        if "五维审校" in user or "评分锚点" in user:
            v = self.review_verdicts.pop(0) if self.review_verdicts else {
                "scores": {"ai_flavor": 92, "plot": 90, "pacing": 88, "prose": 90, "hook": 91},
                "issues": [], "verdict": "pass",
            }
            return "```json\n" + _to_json(v) + "\n```"
        if "记忆卡" in user and "抽取" in user:
            no = self._grab_no(user)
            data = self.memory_factory(no) if self.memory_factory else {
                "char_updates": [], "new_chars": [], "new_locations": [],
                "foreshadow_ops": [], "new_settings_pending": [],
                # 与真实 V4 对齐：结构化增量 + 时间线候选（不产出会导致 M-13~M-15 报"未查"）
                "state_delta": [{
                    "character": "林寒", "location": "青云坊", "physical": "右肩旧伤未愈",
                    "mental": "警觉", "power_level": "炼气", "arc_stage": 0,
                    "knowledge": ["玄铁令遇灵气会发热"], "goals": ["查清令牌来历"],
                }],
                "timeline_candidates": [],
                "event_summary": "林寒在青云坊验出新令牌的异常。", "time_elapsed": "半日",
            }
            data.setdefault("chapter_no", no)
            return "```json\n" + _to_json(data) + "\n```"
        if "只修复问题清单涉及" in user:
            return self._default_fix(user)
        if "卷末审计" in user:
            return "# 审计\n\n（Mock 审计报告）"
        return "（Mock 默认输出）"

    def _grab_no(self, text: str) -> int:
        m = (re.search(r"章号\s+(\d+)\s*/", text) or re.search(r"第?(\d+)章", text)
             or re.search(r"chapter_no[：:\s]*(\d+)", text))
        return int(m.group(1)) if m else 1

    def _grab_range(self, text: str) -> tuple[int, int]:
        ms = re.findall(r"(\d{3,5})\s*-\s*(\d{3,5})", text)
        if not ms:
            return (2200, 2800)
        lo, hi = ms[-1]  # 取最后一个（场景任务等更具体的指令在文末）
        return (int(lo), int(hi))
    def _default_story_ideas(self, user: str) -> str:
        """（规格11 §6）四题材离线候选，换批用不同身份/冲突；非质量证据。"""
        match = re.search(r"题材方向：(urban|xuanhuan|romance|suspense)", user)
        genre = match[1] if match else "urban"
        sources = {
            "urban": [("修表人的旧城", "修表学徒", "挽救将被拆除的老店", "开发商藏起了旧契约"),
                      ("最后一班配送", "失业配送员", "找回被冒领的救命药", "每名签收人都否认自己在场"),
                      ("街巷账本", "社区会计", "守住居民合办的食堂", "账本里出现不存在的支出"),
                      ("城边的回声", "电台实习生", "调查停播节目的来信", "当事人要求他保持沉默"),
                      ("夜校的灯", "夜校老师", "帮助学生保住工作", "工厂正隐瞒事故"),
                      ("地铁末站", "维修工", "找到失联的师傅", "检修记录被改写")],
            "xuanhuan": [("废炉守灯", "守炉杂役", "救出困在古炉中的师妹", "宗门将古炉认作祭器"),
                         ("山契行者", "山神信使", "找回被卖掉的山契", "买主能篡改山民记忆"),
                         ("断脉医师", "无灵脉医师", "治好城中反复出现的怪病", "病源是护城阵的代价"),
                         ("渡桥拾骨", "渡桥匠", "接回被流放的家人", "桥每过一人便索取一段寿命"),
                         ("不鸣的钟", "守钟弟子", "查清灵钟停鸣的真相", "长老以禁令掩盖山门裂缝"),
                         ("雪原契师", "驯兽学徒", "解救被契约束缚的异兽", "解除契约会暴露自己的身世")],
            "romance": [("雨夜合租", "修复古籍的女孩", "保住祖母留下的书店", "合租的律师受雇负责清退她"),
                        ("旧信未寄", "乡村医生", "寻找失踪病人的家属", "同行记者是曾误解她的旧友"),
                        ("灯光之外", "剧场替补演员", "争取自己的第一场主角戏", "导演必须履行一份不公平合同"),
                        ("归港之日", "渔村教师", "重建停办的小学", "工程师前任接手了拆迁项目"),
                        ("春天的修补", "服装学徒", "完成亡母未完成的礼服", "委托人不肯公开婚约真相"),
                        ("四季记事", "植物研究员", "抢救将毁的试验田", "合作伙伴背负着事故责任")],
            "suspense": [("消失的门牌", "户籍调查员", "找到档案中的失踪街道", "住户声称那条街从未存在"),
                        ("深夜修复室", "录音修复师", "辨认母亲最后一次求救", "录音里藏着尚未发生的案发声"),
                        ("渡口无名客", "退休法医", "确认漂来尸体的身份", "指纹属于仍活着的人"),
                        ("空白证言", "法庭速记员", "补全被删掉的庭审记录", "每位证人都改变了说法"),
                        ("最后的值班表", "护士", "查清消失病人的去向", "值班表上出现了自己的旧签名"),
                        ("雨幕寻人", "出租车司机", "找到乘客遗留行李的主人", "失主正在新闻里被宣告死亡")],
        }
        previous = re.search(r"既有候选摘要：(.*)", user)
        offset = 3 if previous and previous[1].strip() != "[]" else 0
        ideas = []
        for title, protagonist, goal, obstacle in sources[genre][offset:offset + 3]:
            ideas.append({"title": title, "logline": f"{protagonist}为了{goal}必须追查线索，但{obstacle}。",
                          "synopsis": f"{protagonist}发现日常秩序背后的秘密，为了{goal}开始调查。阻碍迫使其在真相与安全之间作出选择。",
                          "protagonist": protagonist, "core_conflict": f"{goal}，但{obstacle}。",
                          "selling_point": "身份反差与具体选择推动冲突，不作市场保证", "volume_goal": goal,
                          "volume_hook": f"第一卷取得关键证据，却发现{obstacle}背后还有新的交易。"})
        return _to_json({"schema_version": 1, "genre_template": genre, "ideas": ideas})


    def _default_bible(self) -> str:
        from . import bible
        from .mdtable import dump_table

        return bible.render({
            "基础设定": "灵气复苏初期，主角在青云坊查找玄铁令的来历。",
            "力量体系": dump_table(bible.POWER_COLS, [["炼气", "一层/二层/三层", "引气入体", "不能飞行", "壮汉", "百年", "经脉受损", "无"]]),
            "势力格局": "青云坊护卫控制坊市入口。", "历史脉络": "三年前灵气复苏。",
            "隐藏规则": dump_table(bible.HIDDEN_COLS, [["令牌是钥匙", "高", "15"]]),
            "术语表": dump_table(bible.TERM_COLS, [["玄铁令", "上古信物"], ["青云坊", "中立坊市"]]),
            "日常细节": "坊市雨多，货物靠挑担运输。",
        })

    def _default_cards(self) -> str:
        from . import frontmatter as FM

        cards = []
        for name, role in (("林寒", "主角"), ("老周", "重要配角")):
            status = {"realm": "炼气", "location": "青云坊", "injuries": [], "inventory": [],
                      "emotion": "警觉", "alive": True, "known_info": [], "updated_chapter": 0}
            body = "\n\n".join(f"## {section}\n\n{name}谨慎行事，先验证线索再做决定。" for section in
                               ("表层信息", "声音特征", "核心三要素", "内在矛盾", "过往创伤", "标志行为",
                                "关系网络", "行为底线", "弧光预设", "对话试金石"))
            cards.append(f"===角色:{name}===\n" + FM.dump({"name": name, "role": role, "status": status}, body))
        return "\n\n".join(cards)

    def _default_planning(self, user: str) -> str:
        import json

        target = json.loads(re.search(r"target 回显：(\{[^\n]+\})", user)[1])
        scope = re.search(r"- 规划作用域：\s*(\w+)", user)[1]
        if scope == "volume":
            rng = re.search(r"本次只准备第(\d+)-(\d+)章", user)
            lo, hi = int(rng[1]), int(rng[2])
            data = {"chapter_count": hi - lo + 1, "volume_detail": {"core_conflict": "坊市争夺", "goal": "验证令牌",
                    "climax": "取回线索", "hook_out": "令牌背后另有主人", "ending_state": "计划拿到线索"},
                    "unit_skeletons": [{"name": "验令", "chapters": [lo, hi], "goal": "验证令牌", "obstacle": "护卫"}]}
        elif scope == "unit":
            data = {"unit_detail": {"goal": "验证令牌", "obstacle": "护卫阻拦", "choices": ["暂不暴露"],
                    "outcome": "拿到线索", "can_reveal": ["令牌发热"], "must_hold": ["令牌主人"], "story_lines": ["主线"]}}
        else:
            lo, hi = target["chapters"]
            data = {"chapter_rows": [{"章": str(no), "卷": str(target["volume_no"]), "本章目标": f"验令{no}",
                    "冲突来源": "护卫", "爽点/揭露": f"线索{no}", "钩子类型": ["悬念", "选择", "反转"][no % 3],
                    "重要度★": "3"} for no in range(lo, hi + 1)], "rhythm_note": "每章线索推进，保留令牌主人。"}
        return _to_json({"scope": scope, "target": target, "data": data})

    def _default_outline(self, no: int) -> str:
        hook_type = ["悬念", "选择", "反转"][no % 3]
        return f"""---
chapter_no: {no}
volume: 1
stage: 发展
emotion_task: 小扬
purpose: [推进主线]
core_event: "林寒在青云坊验证玄铁令的异常反应并做出关键决策"
scenes:
  - {{ goal: "检验玄铁令", conflict: "坊市护卫阻拦", chars: [{{ name: "林寒", state: "警觉" }}], end_state: "拿到线索" }}
  - {{ goal: "脱身", conflict: "被人盯上", chars: [{{ name: "林寒", state: "受伤" }}], end_state: "暂时安全" }}
emotion_arc: {{ from: "压抑", via: "试探", to: "小扬" }}
must_include: ["新信息揭露：玄铁令对灵气有反应", "关键决策：暂不暴露"]
hook: {{ content: "暗处有人记下了他的名字", type: "{hook_type}" }}
foreshadow_ops: []
word_range: [2200, 2800]
pov: 林寒
confirmed: false
---
### 场景清单
1. 场景一：检验玄铁令（约1200字）
2. 场景二：脱身（约1200字）
"""

    def _default_fix(self, user: str) -> str:
        m = re.search(r"【正文】\n(.*?)\n(?:\n)?【问题清单", user, re.DOTALL)
        body = m.group(1) if m else user
        if self.fix_overreach:
            # 越权重写：未涉及问题的句段大改（用于触发管线的 10% 护栏）
            return "改写后的句子。" * max(20, len(body) // 30)
        # 默认修复：去掉禁词/敏感词/禁式（只动命中行，护栏由管线校验）
        for w in mechcheck.load_banned_words() + mechcheck.load_sensitive_words():
            body = body.replace(w, "")
        for pat in mechcheck.load_dialog_patterns():
            body = pat.sub("说", body)
        return body


def _to_json(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 可通过机械检查的填充正文生成器（避开禁词/禁式/句式规则）
# ---------------------------------------------------------------------------

_SENTS = [
    "雨点砸在铁皮棚上，林寒把玄铁令贴在掌心。",
    "令牌烫了一下。",
    "他停住脚。",
    "坊市尽头传来护卫换岗的梆子声，三长一短。",
    "怀里的令牌又烫了第二下，比刚才更清楚。",
    "林寒退进货架阴影，数着脚步声等护卫走远。",
    "令牌背面浮出一道细纹，像干涸河床的裂口。",
    "他按住裂口，灵气顺着指缝往里钻。",
    "半息之后，裂口合拢，令牌凉了下去。",
    "街角有人咳嗽了一声。",
    "林寒没回头，拎起麻袋混进挑担的人流里。",
    "出了青云坊南门，雨还没停。",
    "他在桥洞下停步，把令牌翻到背面。",
    "裂口的位置挪了，朝东南偏了半寸。",
    "东南三里，是乱葬岗。",
    "麻袋里的粗盐压着肩，他换了只手。",
    "第二声咳嗽在桥那头响起，比刚才近。",
    "林寒把令牌塞回怀口，贴着心口。",
    "过桥的人不多，撑伞的老汉走在他前面。",
    "老汉的伞骨断了三根，伞面塌下一角。",
    "出了桥洞，雨小了。",
    "乱葬岗的方向有火光，一点，像香头。",
    "他蹲在路边系鞋带，数到三十。",
    "火光灭了一息，又亮起来。",
    "不是香头，是有人在打火折子。",
    "林寒直起身，拐进岔路。",
    "岔路尽头的土地庙塌了半边。",
    "他绕到庙后，墙上有人用炭画了个圈。",
    "圈里一个字：令。",
    "字迹很新，被雨打湿也不晕。",
    "林寒看了一炷香的功夫，伸手把圈抹了。",
    "抹完之后，掌心沾的炭是热的。",
]


def make_prose(word_range: tuple[int, int], pov_action: str = "林寒") -> str:
    """拼出落在区间中值、段落≤120字、句式有落差的填充正文。"""
    target = (word_range[0] + word_range[1]) // 2
    paras: list[str] = []
    i = 0
    while sum(len(p) for p in paras) < target:
        chunk = []
        n = 0
        while n < 90:
            s = _SENTS[i % len(_SENTS)]
            chunk.append(s)
            n += len(s)
            i += 1
        paras.append("".join(chunk))
    text = "\n".join(paras)
    over = sum(len(p) for p in paras) - target
    if over > 60 and len(paras[-1]) > over + 10:
        paras[-1] = paras[-1][: len(paras[-1]) - over]
        text = "\n".join(paras)
    return text
