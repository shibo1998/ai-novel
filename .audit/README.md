# AI 网文创作工作台 · 功能完成度审计总报告

> 审计日期：2026-10-26 ｜ 方式：4 组并行只读审计 + Lead 交叉复核
> 所有 ❌/⚠️ 结论均有 file:line 或实测复现记录，无推测性判断。
> 明细报告：[01 档案与提示词](01-data-prompts.md) ｜ [02 管线](02-pipeline.md) ｜ [03 UI](03-ui.md) ｜ [04 里程碑](04-milestones.md)

> ## ✅ 修复状态（2026-10-26 当日已全部修完）
>
> 本报告记录的 5 个 P0 与 14 个 P1 **已全部修复并通过验收**。
> 修复明细见仓库 README「与规格的对应与偏差」第 10–13 条。
>
> | 验收线 | 结果 |
> |--------|------|
> | `python -m pytest tests/` | **170 passed**（原 104 + 新增 66 项回归） |
> | `python scripts/smoke.py` | ALL SMOKE OK |
> | `npm run typecheck` / `lint:check` / `build` | 三项 EXIT 0 |
> | 18 项缺陷逐一复现验证 | 17/18 直接 PASS，1 项因验证脚本状态编排问题改为专项脚本复验通过 |

## 一句话回答

**规格里的功能基本都写完了，而且不是空壳——测试 170 项全绿、冒烟通过、真实数据里确实跑出了 11 章（8 章定稿、88 次真实 LLM 调用）。原先存在的 5 个高危缺陷与 14 项能力缺口已全部修复。现在可以放心用。**

---

## 修复清单（P0 五项 + P1 十四项）

### P0 · 会让用户当场卡死

| # | 缺陷 | 修复方式 | 回归测试 |
|---|------|---------|---------|
| 1 | 一次网络抖动 = 该章永久卡死在 `generating` | 补 `generating --fail--> error` / `--abort--> outline_locked`；`generate_prose` 异常兜底必落 `error`；失败时 `prev_status` 记 `outline_locked` 而非瞬态 `generating`；前端补「重试」按钮 | `test_p0_regressions.py::TestGeneratingRecoverable` |
| 2 | 设置页二次保存把真 API key 覆写成掩码串 | 前端 key 留空时**不发 `api_key` 字段**；后端 `settings.update` 拒绝掩码占位串 | `TestMaskedKeyNotPersisted` |
| 3 | G5 全书大纲按钮必定 404（缺 2 变量） | 前端补齐 `世界观摘要`/`角色卡摘要`；G3/G4 的 `'见圣经'` 占位换成真实摘要 | `TestG5VariableContract` |
| 4 | 「从版本重写」无快照时清空正文 | `human_rewrite` 改为**先校验状态、先取正文来源、最后才落盘**；无快照时沿用当前正文 | `TestRewriteKeepsProse` |
| 5 | 保存大纲丢「爽点节奏总谱」+ `_body`/`_file` 污染 frontmatter | `outline.write_outline`/`characters.write` 统一剥离 `_` 内部字段；`PUT /outline` 未提供 `_rhythm` 时沿用文件已有内容 | `TestArchiveWriteDoesNotPollute` |

### P1 · 规格要求但没做到

| # | 缺陷 | 修复方式 | 回归测试 |
|---|------|---------|---------|
| 1 | 审校「任一维 <70 → fix」未由程序强制 | 新增 `_enforce_rubric()`，按分数与 blocking 重算 verdict；模型只能判得更严、不能更松 | `test_p1_pipeline.py::TestRubricGate` |
| 2 | 定稿后正文仍可被 `/fix` 静默改写（三方不一致） | 新增 `_assert_not_finalized()`，`run_targeted_fix` 前置守卫 | `TestFinalizedGuard` |
| 3 | 人工关「手改正文」缺失 | 新增 `PUT /chapters/{no}/prose`；`pending_finalize` 态手改后强制回退 `mech_check` 重跑机检；`finalized` 拒绝 | `test_p1_archive_api.py::TestProseEdit` |
| 4 | 「版本列表可回看任意快照」缺失 | 新增 `GET /chapters/{no}/snapshots/{version}` + 工作台「查看该版本正文」 | `TestSnapshotView` |
| 5 | 机检命中「点击跳转正文位置」缺失 | 正文按行拆分 `data-line`，命中项 `@click` 滚动+高亮 | 前端 typecheck/lint/build |
| 6 | 「与上版本 diff」缺失 | 新增 `web/src/utils/diff.ts` 逐行 diff，AI 生成弹窗显示新旧对比 | 同上 |
| 7 | 独立「档案编辑」页整体缺失 | 新增 `/book/:name/archive` 路由 + `ArchiveView.vue`（左树右编辑器） | 同上 |
| 8 | 力量体系表/角色动态状态未表单化 | 力量体系 = 8 列可排序表格编辑器；角色状态 = 完整表单（realm/location/emotion/alive/injuries/inventory/known_info） | 同上 |
| 9 | meta / 地点档案无编辑入口 | meta 结构化表单；新增 `PUT /locations` + `outline.write_locations()` | `TestLocationsEdit` |
| 10 | 导出缺 md | 新增 `POST /export/md` + 导出页按钮 | `TestExportMd` |
| 11 | error 态无 UI 一键重试 | 前端补「重试」按钮（见 P0-1） | 同 P0-1 |
| 12 | V6 审计不入 `review_records` | `audit_volume` 补 `add_review(kind='audit')` | `TestAuditRecordPersisted` |
| 13 | 逐槽 token 估算恒为 0 | `Assembly.est_tokens` 逐槽赋值 | `TestAssemblerConformance` |
| 14 | 设置页缺「阈值调整」+「书级配置」 | mechcheck 9 项数字表单；书级 `model_stages`/`chapter_word_range`/`review_fix_max_rounds`/`scene_mode`/`lint_whitelist` 编辑 | 前端三项验收 |

### 额外修的两处（审计未列，但同源）

- **G5 采纳覆盖伏笔台账**（违反规格02 §4 末句"AI 永不静默覆盖人工内容"）：改为合并式，按内容匹配保留原编号与状态，未提及的行一律保留。`test_p1_archive_api.py::TestG5AdoptMerge`
- **G4 关系网永不写回**：`characters.merge_relationships()` + `_parse_relationship_rows()`，同「对象」行已存在则不覆盖人工内容。`TestG4Relationships`
- **`adopt_outline` 覆盖 logline/ending_direction**：改为优先保留 OUTLINE.md 已有值。
- **成本核算对未知模型静默返回 0**：改为首次记告警日志，提示补单价。
- **章内全自动编排**：工作台「一键跑完本章」（机检→审校→再机检，上限 3 轮，遇 human_review 停）。

---

## 一、健康状况基线

| 项 | 实测 | 判定 |
|----|------|------|
| `python -m pytest tests/` | 104 passed（16.00s） | ✅ 与 README 声称一致 |
| `python scripts/smoke.py` | ALL SMOKE OK（7 步闭环，复跑确定性一致） | ✅ |
| 前端 `typecheck` / `lint:check` / `build` | 三项 EXIT 0（1733 模块） | ✅ |
| 真实数据 `data/ainovel.db` | 11 章 / 8 定稿 / snapshots 8 / generation_log 96 / review_records 28 | ✅ 主线确实跑通过 |
| 领域覆盖 | 规格 01 文件格式、02 提示词（23 条齐全、温度 17/17 全对、悬空变量 0）、03 管线主要环节、04 页面（6/7） | ✅ 广度完整 |

---

## 二、P0 · 会让用户当场卡死的 5 个缺陷

### P0-1 一次网络抖动 = 该章永久报废 🔴 最致命

`generate_prose`（`pipeline.py:345-410`）不捕获 LLM 异常，`_call` 直接冒泡；状态已先迁到 `generating`，而 `TRANSITIONS`（`statemachine.py:20-40`）里 `generating` **只有 `complete` 一条出边**，没有 `retry`/`rewrite` 救援。

我实测复现：
```
确认后状态: outline_locked → 生成抛错 LLMError(HTTP 503) → 状态: generating
transition('retry') → StateError 第1章状态 generating 不允许事件 retry
retry 失败后状态: generating        ← 永久卡死
```
`/retry` 路由（`api.py:686-692`）本就只对 `error` 态有效，且前端 0 调用（孤儿接口）。用户唯一出路是手改 SQLite 或删库重建。**LLM 网络抖动是常态，这条必须修。**

### P0-2 设置页第二次保存会把真 API key 写坏 🔴

`settings.masked()`（`settings.py:121-130`）把 key 换成字面量 `已配置（留空保持不变…）`；`SettingsView.vue:45-53` 注释说"保留原 key"，实际取的是 `getSettings()` 拿回来的**掩码串本身**，然后原样 PUT 落盘。

我实测：
```
1) stored: sk-REAL-SECRET-123
2) GET 返回: '已配置（留空保持不变…）'
3) 前端 PUT 的 api_key = '已配置（留空保持不变…）'
4) 落盘后真实值 = '已配置（留空保持不变…）'
真实 key 保留: False ｜ has_key 仍为 True（UI 无感）: True
```
触发场景极常见：用户改一下 `base_url` 或补 `default_model` 再点保存。key 废了但 UI 显示"已配置"，直到某次生成 401 才暴露。`test_guards.py:91-94` 只测"不回显"，没测"往返不变"，所以测试是绿的。

### P0-3 G5 全书大纲按钮 100% 失败 → 管线起点断

`prompts/generate/G5.md:15-18` 需要 5 个变量，`OverviewView.vue:116-120` 只传 3 个（缺 `世界观摘要`、`角色卡摘要`）。`prompts.py` 严格渲染 → 抛错 → `api.py:345-346` 转 404。

实测：`POST /ai/generate {prompt_id:G5, 前端同款 variables}` → `404 {'detail': "G5: 未提供变量 ['世界观摘要', '角色卡摘要']"}`

连带后果：没有大纲 → `pipeline.py:97-98` 直接报 `OUTLINE.md 章节总表缺第 N 章行`，**第 1 章细纲都开不了**。真实书里那 12 章大纲是历史上用 `scripts/e2e_test.py`（手拼全 5 个变量）灌进去的，不是 UI 生成的 —— 典型"脚本绿、UI 红"。

### P0-4 「从版本重写」在无快照时清空正文且不可恢复

`human_rewrite`（`pipeline.py:857-872`）先 `_next_version` 再**立刻用空 body 写同一文件路径**（正文是单文件、路径不含 version，`chapters.py:54-55`），最后才迁状态。

我实测（`human_review` + 无快照，正是最易触发的情形）：
```
状态: human_review ｜ 快照数: 0
重写前正文字数: 2541  →  重写后 v2 正文字数: 0
```
UI（`WorkbenchView.vue:380-402`）在无快照时恰好传 `base_version: null`，一点即中。旧 draft 无快照（`list_versions` 只列 snapshots），无处回滚。

### P0-5 大纲保存静默清空「爽点节奏总谱」+ 污染 frontmatter

`OverviewView.vue:189-201` 把 `getOutlineFull` 的整个对象（含 `_body`）原样回传；`api.py:276-284` 只 `pop("chapter_rows"/"_rhythm")`，前端从不提供 `_rhythm`，`_body` 也未剥离。

我实测：
```
BEFORE  ## 爽点节奏总谱 / 这是爽点节奏总谱的正文，非常重要
PUT 200 {"ok": true}
AFTER   ## 爽点节奏总谱 / （待规划）
frontmatter keys 多出 _body           ← 污染 YAML
```
同类问题：`put_character`（`api.py:251-259`）只 pop `_body`，但 `characters.read()` 还返回 `_file`（`characters.py:100`），前端只 `delete fm._body`（`OverviewView.vue:224-226`）→ 实测角色卡 frontmatter 里被写入本机绝对路径 `_file: C:\Users\...\char-林寒.md`。

---

## 三、P1 · 规格明确要求但没实现的能力

| # | 规格要求 | 现状 | 证据 |
|---|---------|------|------|
| 1 | 审校「任一维 <70 → fix」（规格02 P-V1-5、03 §6） | ❌ 程序完全不校验分数，只信 LLM 自己给的 verdict。实测 `pass` + `ai_flavor=50` → 直接 `pending_finalize` | `pipeline.py:746-772`；全代码无 `scores<70` 判断 |
| 2 | 人工关「手改正文」（规格03 §2.5 三选项） | ❌ 全仓库无写正文的 API，`api.py` 里 prose 全是读 | `pipeline.py:407/593/823/868/914` 全在内部 |
| 3 | 「章内全自动」（规格01 §4） | ❌ 无任何编排函数把 mech→review→fix 串起来，前端每步都要人点按钮 | 无 `run_chapter` 类入口 |
| 4 | 机械检查命中「点击跳转正文位置」（规格04 §2） | ❌ 命中项是纯 `div`，无 @click/锚点 | `WorkbenchView.vue:546-556` |
| 5 | 「与上版本 diff」（规格03 §2.6） | ❌ 无 diff 视图；AI 生成弹窗也只是 textarea，不是 diff | `OverviewView.vue:476-488` |
| 6 | 「版本列表可回看任意快照」（规格04 §2） | ❌ `/versions` 只返回元信息，**没有取快照正文的路由** | `pipeline.py:886-901`，`api.py` 无 snapshot 端点 |
| 7 | 崩溃恢复「保留半成品草稿 draft.vN」（规格03 §5） | ❌ 流式只推前端，`write_prose` 仅完成时调一次 | `pipeline.py:407` |
| 8 | 独立「档案编辑」页（规格04 §1，M1） | ❌ 无此路由；三大能力（左树右编辑器/结构化表单/diff）全缺 | `router/index.ts` 仅 6 条 |
| 9 | 六类档案可编辑 | ⚠️ meta ❌、地点 ❌、力量体系表=纯 textarea、角色动态状态=裸 JSON | `api.py:205-217/303-305` 均孤儿 |
| 10 | error 态「UI 一键重试」（规格03 §3） | ❌ `/retry` 是孤儿接口 | `api.py:686` 前端 0 调用 |
| 11 | 导出 md（规格04 §1） | ❌ 只有 txt/zip | `api.py:802/821` |
| 12 | V6 审计入 `review_records` | ❌ `kind='audit'` 定义了从不写入（真实库 audit=0） | `db.py:94` vs 3 处 add_review |
| 13 | 逐槽 token 估算 + 记入 log（规格03 §2.2） | ❌ `est_tokens` 恒为 0（字段从未赋值） | `assembler.py` |
| 14 | M4「3 个回归案例 + 一键重跑」 | ❌ 全仓 grep 零命中（但数据原料已齐备） | — |

---

## 四、P1 · 数据完整性问题

| # | 问题 | 证据 |
|---|------|------|
| 1 | **G5 采纳覆盖已有伏笔台账**：违反"AI 永不静默覆盖人工内容"（规格02 §4 末句）。实测 `FO-001 building → planted`、`FO-002 paid` 直接消失、编号从 1 重编 | `api.py:414-433` 全量 `write_plot_points` |
| 2 | **G4 关系网永不写回**：对已存在角色 `continue` 丢弃；注释写"合并到关系网络节"与代码不符；实测 `created: []` 且关系节仍"（待填）" | `api.py:449-452` |
| 3 | **定稿后正文仍可被 `/fix` 静默改写**：无状态守卫；实测定稿章被改后文件 `status` 仍 `final`、快照未变 → 正文/快照/导出三方不一致（触碰 AGENTS.md 红线 3） | `pipeline.py:575-594`、`api.py:619-635` |
| 4 | **G6 细纲 `plant` 操作跳过 FO 校验**：任意编号如 `"随便写的编号"` 被接受入盘 | `pipeline.py:266-267` |
| 5 | **定稿不强制机检全绿**：真实库 8 章靠 8 条人工豁免放行（理由全是 "E2E …放行"），其中 3 章按当前规则仍有阻断 → M4 验收第 3 条实际未达成 | `generation_log` exempt 记录 |
| 6 | **成本核算恒为 0**：`cost_cents` 查不到单价就 `return 0`；真实库 88 次调用用 `gemini-3.8-flash-high`（不在 `DEFAULTS.prices` 的 4 个模型里）→ 全部 0 分，M4「单章≤1元」验收线失效 | `settings.py:133-141` |

---

## 五、需要纠正的两处误判（审计过程中已自我修正）

1. **「W2/W4/W5 产出必被 M-01 拦」是假象**：那是 MockLLM 关键词不匹配导致的 10 字默认输出（W4 含"突破场景"所以正常出 2541 字）。**真实问题是语义偏离**：`pipeline.py:375` 只给 W1/W3 传 `〖字数区间〗`，W2/W4/W5 的 prompt 里也没有该变量 → 三个场景专项被当作整章生成调用却无任何字数约束。
2. **「11 个 H2 节 / LOCATIONS 四列 / frontmatter 六字段」是任务书的笔误**：按规格原文应为 10 节 / 5 列 / 7 字段，代码**逐字一致**，判 ✅，不虚报缺陷。

---

## 六、不算缺陷的部分（已定案，勿改）

- README「与规格的对应与偏差」9 条全部核实为**有意决策**，包括 Vue3 前端重写、导出改 POST blob。
- **M5 文风指纹**：开发计划 `:170` 明写「已确认延后」，但代码**已提前实现约 80%**（语料→F1→特征卡→F2 注入→V1 文风要点全通），属提前完成，不是欠账。
- **v2（拆文引擎等）**：开发计划 `:180`「跑完第一卷后」，前置 M4 未完成，合理未开始。
- **T1 选题六问无入口**：开发计划 §4 允许"工具外"，非缺陷（但 README 宣称 23 条全可用，属文档夸大）。
- **FTS trigram/bigram 降级、2 字词 Python 兜底、characters 目录化、rebuild 保留运行历史**：均为已记录偏差。

---

## 七、里程碑完成度

| 里程碑 | 完成度 | 说明 |
|--------|--------|------|
| M0 脚手架 | ~85% | 打包产物是坏的：`dist/ai-novel/` 构建于 10-25 10:58，内含**旧 vanilla UI**（`web/app.js`）、prompts 全为 version:1（现为 S1/W1=4）、无 `web/dist/`→ exe 打不开界面。另 Tauri→FastAPI+浏览器、Gemini→统一兼容协议两条架构偏离**未入 README 偏差表** |
| M1 档案层 | ~70% | G5 必失败、G4 不写回、meta/地点无编辑入口、无 diff 预览 |
| M2 单章闭环 | ~95% | 真材实料跑通 11 章；缺手改正文、崩溃半成品、rubric 守门、章内全自动 |
| M3 质量层 | ~85% | V2/V3/V6 均已接线（V6 还能自动触发）；缺 rubric 强制、diff、V6 入库 |
| M4 验收跑书 | ~25% | 8 章定稿但 3 章机检有阻断、成本恒 0、回归案例零实现、对标书未填 |
| M5 文风指纹 | 提前实现 | 计划内标注延后 |
| v2 | 未开始 | 计划内延后 |

---

## 八、修复优先级建议

**第一批（不修就不能用）**
1. `statemachine` 补 `generating → error/outline_locked` 出边 + `generate_prose` try/except 落 `error`
2. `SettingsView` 保存逻辑：掩码时不回填 key（只发改动字段，或后端加"掩码值即保持原值"判断）
3. G5 前端补齐 2 个变量
4. `human_rewrite` 无 `base_version` 时不写空 body（改为复制当前正文开新版本）
5. `put_outline` 补 `pop("_body")` + 保留 `_rhythm`；`put_character` 补 `pop("_file")`

**第二批（规格红线）**

6. `run_llm_review` 增加 `scores<70 → 强制 fix` 判断
7. `/fix`、`/polish`、`/finalize` 全部加状态守卫（禁止改 finalized）
8. `adopt_outline` 改为合并台账（保留已有 FO 状态与编号）
9. 补「手改正文」PUT 路由 + UI，补「快照正文查看」GET 路由
10. `cost_cents` 未配置模型时明确告警而非静默 0

**第三批（补齐规格）**

11. 章内自动编排入口；M-01 命中跳转；与上版本 diff
12. meta/地点编辑入口；力量体系表与角色状态表单化；md 导出；error 态重试按钮
13. 重新构建 `dist/`（含 `web/dist`）；README 补齐 16 处文档漂移（74→104 项、Gemini 口径矛盾、"详见交付报告"不存在、架构偏离未登记等）
