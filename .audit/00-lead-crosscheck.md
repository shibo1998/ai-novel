# Lead 交叉核查（可复现实证，独立于三份专项审计）

> 核查时间：2026-10-26 ｜ 方式：只读 + 临时目录 AINOVEL_HOME + TestClient/MockLLM
> 全部结论均由本文件记录的命令实际跑出，不是推断。

## 0. 基线

| 项 | 实测结果 |
|----|---------|
| `python -m pytest tests/` | **104 passed in 14.54s** ✅ |
| `python scripts/smoke.py` | **ALL SMOKE OK**（outline→confirm→prose 2541字→mech warns 11/block 0→review pass→finalize→finalized）✅ |
| `web/dist` 构建时间 | 2026-10-26 10:07:44（与 src 同步，非陈旧）✅ |
| `dist/ai-novel/`（PyInstaller） | 2026-10-25 10:58 构建，内含 `web/app.js`（**旧 vanilla 前端**）⚠️ |

---

## 1. 阻断级：G5 全书大纲生成 100% 失败（用户主路径第一步就断）

**现象**：OverviewView 点「G5 全书大纲」→ 必失败。

**证据**
- `prompts/generate/G5.md:8` 声明 inputs 含 `世界观摘要, 角色卡摘要, logline, 目标总章数, 单章字数区间`
- `web/src/views/OverviewView.vue:116-120`（G5 分支）只传 3 个：`目标总章数 / 单章字数区间 / logline`
- `ainovel/prompts.py:88-92` 严格渲染：缺变量直接 `PromptError`
- 实测（TestClient 打真实路由）：
  ```
  POST /api/books/测试书/ai/generate {"prompt_id":"G5", ...}
  → 404 {'detail': 'G5: 未提供变量 ['世界观摘要', '角色卡摘要']'}
  ```
- 连带后果：`ainovel/pipeline.py:178` 细纲生成要求 OUTLINE 已有该章行 → `第1章/outline/generate → 400 OUTLINE.md 章节总表缺第 1 章行`。**唯一能把章节行写进 OUTLINE 的自动入口就是 G5 采纳**（`api.py:373-433`），G5 一断，新书无法从 UI 走通闭环。

**影响**：M4「建书 → G 链 → 第一卷 10 章闭环」的第一步不可用。测试全绿是因为 `tests/test_prompts.py` 只校验 23 个 ID 存在，没有一条测试打 `/ai/generate`（grep `ai/generate` 在 tests/ 下 0 命中）。

**修法（一处）**：`OverviewView.vue` G5 分支补 `角色卡摘要`、`世界观摘要`（用 `getBible` + `listCharactersFull` 取值，与 G1/G3 分支同法）。

---

## 2. 高危：保存大纲会静默清空「爽点节奏总谱」并往 frontmatter 里写 `_body`

**现象**：书总览页「编辑大纲行 → 保存大纲」，节奏总谱正文被替换为「（待规划）」。

**证据**
- `web/src/views/OverviewView.vue:189-195`：`getOutlineFull` → 改 `chapter_rows` → `putOutlineFull(od)`（把 GET 回来的整个对象原样回传，含 `_body`）
- `ainovel/api.py:276-283` `put_outline`：只 `pop("chapter_rows")` 和 `pop("_rhythm")`；`_rhythm` 前端从不上送 → `rhythm=""` → `outline.py:41` 写 `（待规划）`；`_body` 未剥离 → `outline.py:29 FM.validate` 通过 → 落进 YAML frontmatter

**实测（临时书）**
```
BEFORE  ## 爽点节奏总谱 / 这是爽点节奏总谱的正文，非常重要
PUT     200 {'ok': True}
AFTER   ## 爽点节奏总谱 / （待规划）
        frontmatter keys: ['_body', 'ending_direction', 'logline', 'volumes']   ← _body 污染
爽点节奏总谱正文是否保留: False
```
`_body` 长度 243 且反复保存不增长（内容稳定，但永久污染 frontmatter，且每次保存都覆盖节奏）。

**同类问题（角色卡）**：`api.py:251-259` `put_character` 只 `pop("_body")`，但 `characters.read()` 还会返回 `_file`（`characters.py:100`）。前端 `OverviewView.vue:224-226` 只删 `_body`，整包回传 →
```
实测：frontmatter 出现 _file: C:\...\books\测试书\characters\char-林寒.md
```
**影响**：档案被写入本机绝对路径，换机器/改目录后档案带脏字段；且写入的是运行时内部字段，属规格01 §2 schema 外污染。

---

## 3. 高危：审校「任一维 <70 → fix」未由程序强制（rubric 守门形同虚设）

**规格**：规格02 P-V1 改造点5、规格03 §6 表：任一维 <70 → 触发 fix。

**实测（MockLLM 给出 pass + ai_flavor=50）**
```
verdict: pass  scores: {'ai_flavor': 50, 'plot': 95, 'pacing': 95, 'prose': 95, 'hook': 95}
状态: pending_finalize        ← 直接跳到定稿，没有任何 fix
```
**证据**：`pipeline.py:746-772` 只按 LLM 自己给的 `verdict` 分支；`pipeline.py:723-725` 的「防橡皮章」只在**全维≥90 且零问题**时触发复审，低分 pass 完全不管。代码里没有任何 `scores[...] < 70` 判断。
**影响**：P-V1 prompt 里写的「<70 必须有 fix」靠模型自觉；模型若判 pass 而打分塌陷，程序放行。这是产品红线（追读欲望）守门失效。

---

## 4. 中危：规格明确要求的 UI 能力缺失（页面清单 §2 / §1）

| 规格要求 | 现状 | 证据 |
|---------|------|------|
| 机械检查报告「点击跳转正文位置」 | 只渲染 `行{{ i.line }}`，**无 @click** | `WorkbenchView.vue:546-556`（整个 v-for 无点击绑定） |
| 定稿关「与上版本 diff」 | 无任何 diff 视图 | grep `diff` 仅命中 `pipeline.py:599/616`（越权护栏内部用）与注释 |
| 「版本列表可回看任意快照」 | `GET /versions` 只返回 version/label/created_at；**没有取快照正文的接口**，`get_snapshot()` 只在 `human_rewrite` 内部用 | `pipeline.py:886-901`、`api.py:668-670` |
| 人工关「手改正文」 | **没有任何正文保存路由**；`api.py` 里 prose 全是读 | `api.py:476/478/574/809/814`，无 PUT prose |
| error 状态「UI 可一键重试」 | `POST /retry` 后端有（`api.py:686`），前端 **0 调用** | grep `retry` 在 `web/src` 下 0 命中 |
| 地点档案编辑（六类档案之一） | `PUT /locations` **路由不存在**，前端也无入口 | `api.py:303-305` 只有 GET |
| meta.md 编辑（六类档案之一） | `PUT /meta` 后端有，前端 **0 调用** | grep `/meta` 在 `web/src` 下 0 命中 |
| 「结构化字段用表单」（力量体系表、角色动态状态） | 力量体系=整本 Markdown textarea；角色动态状态=**裸 JSON textarea** | `OverviewView.vue:350-353`、`OverviewView.vue:495-496` |
| 大纲卷规划 / 节奏总谱编辑 | 只能编辑章行表；`volumes`、`rhythm` 无编辑入口 | `OverviewView.vue:454-471` |
| 导出 md（规格04 §1 明确 txt/md） | 只有 `/export/txt` 与 `/export/zip` | `api.py:802/821` |
| 设置页「阈值调整」 | `settings.mechcheck` 有 9 个阈值 + 注释「M0 设置页可调」，**设置页无对应控件** | `settings.py:36-46`；`SettingsView.vue` 全文无 mechcheck |

---

## 5. 中危：规格03 §5 崩溃恢复「保留半成品草稿」未实现

`stream_cb` 全程只推给前端，**任何中途都不落盘**；只在 `pipeline.py:407` 生成完成后一次性 `write_prose`。
- 实测 grep：`半成品|draft\.v|中断` 在 ainovel/ 下 0 命中。
- 状态机侧是可续的（`generate_prose` 允许 `st == "generating"` 继续，`pipeline.py:351-354`），但**已生成的几千字文本丢失**，用户只能重来。
- 规格03 §5 原文：「generating 中断 → 保留半成品草稿为 draft.vN，重开提示"继续生成或重来"」。

---

## 6. 低危：卷末审计不写 review_records（kind='audit' 定义了但从未使用）

`db.py:94` 的 CHECK 允许 `kind IN ('mech','llm','polish','audit')`，实际 `add_review` 调用只有 3 处：`pipeline.py:508(mech)/743(llm)/825(polish)`。`audit_volume`（`pipeline.py:1020-1069`）只写 `books/<书>/audits/卷N_审计.md`，不入库。
影响：审计结果无法在 UI 侧按章/版本检索；属设计未完成，不影响主流程。

---

## 7. 低危：M3 场景专项可达性 OK，T1/M4 立项对话不可达

- W2/W4/W5 可手动选（`WorkbenchView.vue:38-43` promptOptions）、V2+V3 有「整章润色」按钮、V6 有手动入口 —— 均已在 UI 可达 ✅
- **T1（选题六问，M4 立项）**：prompts 有、`prompts.render("T1", ...)` 在 ainovel/ 下 **0 调用**，前端无入口。实测 `render("T1", {})` → `PromptError: 未提供变量 ['对标书','市场观察','题材方向']`。按开发计划 T1 属 M4，可视为延后，但 README 未声明。

---

## 8. 打包产物陈旧（分发即坏）

`dist/ai-novel/` 构建于 **2026-10-25 10:58**，内含 `web/app.js`（vanilla 版）。Vue 版于 10-26 落地并改了静态挂载为 `web/dist`、导出改 POST。因此该 exe 分发出去是**旧前端 + 旧 API 契约**，与仓库代码不一致。README「M0 检查单」已自陈「未在本机实测」，但未提示产物已过期。

---

## 9. 结论排序（对「能否跑完第一卷」的影响）

1. **G5 必失败** → 新书无法起步（P0）
2. **大纲保存丢节奏总谱 + `_body`/`_file` 污染档案** → 数据损坏（P0）
3. **rubric <70 不强制 fix** → 质量守门失效（P1，产品红线）
4. **无手改正文 / 无快照回看 / 无机检跳转 / 无 diff** → 人工关能力不全（P1，规格04 §2）
5. **locations、meta 无编辑入口** → 「六类档案可编辑」不成立（P1）
6. **崩溃丢半成品** → 长章生成体验受损（P2）
7. **T1 无入口、导出无 md、阈值无 UI、audit 不入库、打包陈旧**（P2/P3）
