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
             stream_cb=None) -> llm.LLMResult:
        if self.fail_next:
            err, self.fail_next = self.fail_next, None
            raise err
        self.calls.append({"model": model, "system": system, "user": user})
        text = self._route(system, user)
        if stream_cb:
            step = max(1, len(text) // 40)
            for i in range(0, len(text), step):
                stream_cb(text[i:i + step])
        return llm.LLMResult(
            text=text,
            input_tokens=llm.est_tokens(system + user),
            output_tokens=llm.est_tokens(text),
            finish_reason="stop", model=model,
        )

    # ------------------------------------------------------------------
    def _route(self, system: str, user: str) -> str:
        if "单章细纲" in user:
            no = self._grab_no(user)
            if self.outline_factory:
                return self.outline_factory(no, user)
            return self._default_outline(no)
        if ("本章上下文" in user or "黄金三章" in user or "专写本场景对话" in user
                or "突破场景" in user or "高潮对决" in user):
            no = self._grab_no(user)
            rng = self._grab_range(user)
            if self.prose_factory:
                return self.prose_factory(no, rng)
            return make_prose(rng)
        if "===润色全文===" in user:  # V2
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
        m = re.search(r"第?(\d+)章", text) or re.search(r"chapter_no[：:\s]*(\d+)", text)
        return int(m.group(1)) if m else 1

    def _grab_range(self, text: str) -> tuple[int, int]:
        ms = re.findall(r"(\d{3,5})\s*-\s*(\d{3,5})", text)
        if not ms:
            return (2200, 2800)
        lo, hi = ms[-1]  # 取最后一个（场景任务等更具体的指令在文末）
        return (int(lo), int(hi))

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
        m = re.search(r"【正文】\n(.*?)\n\n【问题清单", user, re.DOTALL)
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
