# 审计报告 04 · 里程碑完成度 / 工程可用性 / 文档一致性

> 审计员：milestone-auditor ｜ 时间：2026-10-26 ｜ 任务：task-4
> 审计方式：**只读**。未修改任何源码/文档/测试；未运行 `run.py`；未运行 `scripts/e2e_test.py` 与 `scripts/e2e_stage2.py`（真实 LLM，会耗 token 且写真实 `books/` `data/`）。
> 允许的实测均已在本机执行并记录原始输出：`python -m pytest tests/`、`python scripts/smoke.py`，以及若干**在临时 `AINOVEL_HOME` 下**的隔离实验（不触碰仓库真实数据）。
>
> 范围分工说明：本题只做**跨里程碑视角 + 工程可用性 + 文档一致性**。规格03 逐条实现细节见 `.audit/02-pipeline.md`，UI/API 契约矩阵见 `.audit/03-ui.md`，档案层/提示词条目级核对见 `.audit/01-data-prompts.md`。本报告引用它们已有结论处会标注「（详见 0x）」。

---

## 0. 结论速览

| 维度 | 结论 |
|------|------|
| 里程碑整体推进度 | **M0 ≈ 85%（两处与开发计划不符且未入偏差表）· M1 ≈ 70% · M2 ≈ 95% · M3 ≈ 85% · M4 ≈ 25% · M5 已提前实现（计划内标注延后）· v2 未开始（计划内标注延后）** |
| 代码可运行性 | ✅ `pytest 104 passed / 16.00s`、`smoke ALL SMOKE OK`、真实书 `books/流程测试书` 已有 11 章、8 章定稿、真实 LLM 日志 96 条 —— **主线闭环确实跑通过** |
| 工程可用性 | ⚠️ 3 个 P1 级问题：① 设置页保存连接会把真 API key 覆盖成占位符；② 打包产物 `dist/ai-novel` 是坏的（旧 UI + 旧提示词，启动后无 UI）；③ 模型单价表不含实际使用模型 → 成本恒为 0，M4 成本验收线失效 |
| 文档一致性 | ⚠️ README 严重滞后 6 处（74 项 / Gemini 原生 SSE / 打包"未实测" / "档案编辑页已实现" / 悬空的"交付报告" / 页面清单齐全） |
| 真正没做完 | **【未实现】4 项 · 【延后决策】2 项 · 【文档没写清楚】6 项** —— 见 §7 |

---

## 1. 实测结果（原始输出）

### 1.1 `python -m pytest tests/`

```
........................................................................ [ 69%]
................................                                         [100%]
104 passed, 3 warnings in 16.00s
```

- **104 项全绿，与 README:37 与 AGENTS.md:14 声称的"104 项"一致** ✅
- 3 条 warning 全部有价值：
  - `ainovel/api.py:27: DeprecationWarning: on_event is deprecated` —— 启动钩子用的是 FastAPI 旧 API（`@app.on_event("startup")`），FastAPI 后续版本移除后会崩；技术债，非当前故障。
  - 2 条 starlette/httpx 测试客户端弃用告警，不影响。
- 测试分布（`def test_` 计数，部分参数化展开到 104）：`test_pipeline 14 / test_mechcheck 13 / test_llm 9 / test_guards 9 / test_frontmatter 7 / test_api 6 / test_characters 6 / test_mdtable 6 / test_prompts 6 / test_assembler 5 / test_statemachine 5 / test_db_rebuild 3 / test_style 2`
- **覆盖盲区**：`test_pipeline.py` 无任何 V6 卷末审计用例（grep `audit|V6` 在 tests/ 下只命中 `test_prompts.py:7` 的 ID 白名单）。V6 是 M3 交付项却没有回归测试。

### 1.2 `python scripts/smoke.py`

```
outline ok, hook: {'content': '暗处有人记下了他的名字', 'type': '选择'}
status: outline_locked
prose: {'word_count': 2541, 'version': 1, 'finish_reason': 'stop', 'mode': 'whole'}
mech blocks: [] warns: 11
status: llm_review
review verdict: pass status: pending_finalize
finalize: 1 summary: 林寒在青云坊验出新令牌的异常。 conflicts: 0
final status: finalized
ALL SMOKE OK
```

- ✅ 闭环 7 步全通；烟雾测试确实在临时目录跑（`scripts/smoke.py:9-12` 用 `tempfile.mkdtemp` + `AINOVEL_HOME`），**不污染仓库**，与 README:38 / AGENTS.md:15 描述一致。
- 复跑两次输出完全一致（确定性），说明 MockLLM 无隐式状态泄漏。

### 1.3 两个 E2E 脚本（只读判读，未运行）

| 脚本 | 性质 | 判据 | 风险 |
|------|------|------|------|
| `scripts/e2e_test.py`（215 行） | **真实 LLM 全链路 E2E** | `scripts/e2e_test.py:15` `httpx.Client(base_url="http://127.0.0.1:8765")`；`:69-74` 调 `/ai/generate` 真实生成；`:79-121` G1/G2/G3/G5 真跑；`:132-192` 第1章真生成+审校+定稿；`:213-214` 汇报真实成本 | 必须先 `python run.py` 起服务；会**写真实 `books/流程测试书` 与 `data/`**；消耗真实 token；`BOOK = "流程测试书"` 是仓库里现有的书，`:62` 甚至会 `DELETE` 建书 |
| `scripts/e2e_stage2.py`（99 行） | 同上，第2章闭环 + 润色 + V6 审计 | `scripts/e2e_stage2.py:9-11` 同上；`:82` 调 `/polish`；`:92-94` 调 `/audit/2` | 同上 |

- 两者**都被 git 跟踪**（`git ls-files scripts` → 3 个脚本全部入库），但 README 与 AGENTS 的「命令」章节**完全没提这两个脚本**。AGENTS.md:37 只写"测试一律用 mockllm，禁止真实网络/LLM 调用"——这条约束针对 `tests/`，但仓库里躺着两个真花钱的脚本且无任何警告说明 → 【文档没写清楚】P2。
- 历史运行日志证明了它们被真实跑过：`data/e2e.log`（末行 `E2E ALL PASS ✅`，`TOTAL cost: 0.00 元 / 18 次调用`）、`data/e2e2.log`（末行 `STAGE2 ALL PASS ✅`）。

### 1.4 `requirements.txt` 与真实 import 的一致性

`requirements.txt` 共 5 行：`fastapi>=0.110 / uvicorn>=0.29 / httpx>=0.27 / PyYAML>=6.0 / pytest>=8.0`。

AST 扫描 `run.py + ainovel/ + tests/ + scripts/` 的全部 import 后，非标准库依赖为：
`fastapi, httpx, pydantic, pytest, uvicorn, yaml`

| 判定 | 项 | 说明 |
|------|----|------|
| ✅ 用了也列了 | `fastapi` / `uvicorn` / `httpx` / `pytest` / `PyYAML`(→`yaml`) | 5 个包全部真实使用，**没有"列了但没用"的包** |
| ⚠️ 用了没直接列 | `pydantic` | `ainovel/api.py:15` `from pydantic import BaseModel`。pydantic 是 fastapi 的传递依赖，`pip install -r` 一定能装上，所以**不会炸**；但 AGENTS.md:3 宣称"Python 依赖仅 requirements.txt 里 5 个包"，实际直接 import 了第 6 个包。属"表述不精确"，P3。 |

### 1.5 隔离实验（临时 `AINOVEL_HOME`，不触碰真实数据）

| 实验 | 目的 | 结果 |
|------|------|------|
| A. 设置掩码往返 | 验证「保存连接」是否保住 key | ❌ **真 key 被写成占位符**（详见 §5.2） |
| B. G4 采纳 | 验证关系网是否真写回角色卡 | ❌ 已存在角色被 `continue` 跳过，关系网**全部丢弃**（详见 §4.2） |
| C. G5 变量完整性 | 复刻前端 `aiArchive('G5')` 的变量 | ❌ `G5: 未提供变量 ['世界观摘要', '角色卡摘要']` → 404（详见 §4.2） |
| D. W2/W4/W5 触发 | 确认调用次数与字数 | ⚠️ W2/W5 走 MockLLM 落盘 10 字；真实模型下同样必然低于 M-01 下限（详见 §4.3） |
| E. 裁决队列护栏 | 复核红线是否真的拦得住 | ✅ `裁决队列非空（3 条待处理），禁止开新章细纲` |
| F. 定稿章节复检 | 用当前词库重跑 M-01~M-11 | ❌ 已定稿的 001/002/011 仍有阻断（详见 §4.5） |

---

## 2. 里程碑矩阵

判据说明：**已完成**=代码+可触发入口+有测试或真实运行记录；**部分**=核心有但缺入口/缺一环；**未做**=无代码或无入口；**延后**=开发计划或 README 原文明确写了延后。
「依据原文」列给出文档原句，用于区分"合理延后"与"遗漏"。

### M0 · 脚手架与基础设施

| # | 任务点（docs/开发计划.md:107-116） | 判定 | 证据 | 依据原文 / 说明 |
|---|-----------------------------------|------|------|----------------|
| 1 | Tauri 2 + Vue 3 + TS + Vite 项目初始化，Windows 构建跑通 | ❌ 未做（被规格04 覆盖） | `grep -r Tauri` 全仓只命中 `docs/开发计划.md:26,108,109,114`；无 `src-tauri/`、无 `package.json` 的 tauri 依赖 | 开发计划:26「Tauri 2 + Vue 3 + TypeScript，Windows 桌面单机」 vs 实际 `run.py:34` `uvicorn.run("ainovel.api:app")` + `webbrowser.open`。**规格04 全文无 Tauri，只描述 Web UI**（04-UI与验收规格.md:28「服务方式：ainovel/api.py 的静态挂载」），开发计划:6「冲突时规格文档优先」→ 技术上可解释。但 README「与规格的对应与偏差」9 条里**没有这一条** → 【文档没写清楚】 |
| 2 | 数据层：Tauri fs 读写 + 书籍目录结构 + SQLite 建表（含 FTS5 中文方案） | 部分 | 目录结构 `books.py:29-101`；建表 `db.py:54-110`；FTS5 `fts.py:12-37`；trigram 实测 `sqlite 3.49.1 / trigram: OK` | Tauri fs 读写不存在（改用 Python `pathlib`+`atomic.py`）——随第 1 条一起被规格04 覆盖。**SQLite/FTS5 部分完整且已实测** |
| 3 | LLM 适配层：Provider 接口 + DeepSeek（OpenAI 兼容）+ **Gemini（原生协议）** 两实现 | 部分 | 只有 `llm.py:46 OpenAICompatClient` 一个实现；`llm.py:140-146 client()` 是唯一连接工厂 | ❌ **Gemini 原生协议从未实现**。README:70 已把口径改成「Gemini 等模型一律走兼容协议」，但 README:103 M0 检查单仍写「Gemini 连通 ✅ 代码就绪（**原生 SSE 流式**，base URL 可改走代理）」——**README 内部自相矛盾**，且偏差表未记录"原生协议 → 统一兼容协议"这一决策 |
| 4 | 按阶段模型配置（config.json）/ 流式输出 / 自定义 base URL | ✅ | `books.py:66-72` 五档默认；`pipeline.py:47-50` 取 `default_model` 优先；`llm.py:60-94` SSE 解析；`llm.py:49-51` base_url | 真实运行印证：`data/settings.json` `default_model=gemini-3.8-flash-high` 覆盖了所有档位，`generation_log` 96 条全部该模型 |
| 5 | 设置页：API key 配置、连通性测试 | ⚠️ 有但会写坏 key | `web/src/views/SettingsView.vue:45-60`；`api.py:83-97`；`settings.py:121-130` | 功能在，但「保存连接」流程会把真 key 覆盖为占位符（§5.2，P1） |
| — | 验收：`pnpm tauri build` 产出可运行 exe | ❌ | 无 tauri，改用 `ainovel.spec` + PyInstaller；但产物是坏的（§5.1） | |
| — | 验收：两个 key 各发一条测试消息 | 部分 | 只有一个 OpenAI 兼容通道 | 与第 3 条同源 |
| — | 验收：建表完成，生成历史能写入 | ✅ | `generation_log` 96 条真实记录；`review_records` 28 条；`snapshots` 8 条 | |

### M1 · 档案层

| # | 任务点（docs/开发计划.md:118-128） | 判定 | 证据 | 说明 |
|---|-----------------------------------|------|------|------|
| 1 | 多书项目管理（创建/删除/切换） | ✅ | `books.py:29-131`；`api.py:139-160`；`ShelfView.vue` 新建向导+删除二次确认 | 另加 P0 路径穿越守卫 `paths.py:40-63`（`test_guards.py` 9 项） |
| 2 | 六类档案编辑器：meta / Bible / 角色 / 地点 / 伏笔 / 大纲 | ⚠️ 4/6 | Bible `OverviewView.vue:348-355`；角色 `:490-503`；伏笔 `:179-200`；大纲同上 | ❌ **meta 无前端入口**（`web/src/api/index.ts` 全文无 `/meta` 调用，后端 `api.py:205-217` 有 GET/PUT）；❌ **地点无前端入口，且后端只有 GET**（`api.py:303-305` 无 PUT；`outline.upsert_location` 仅被 V4 回写调用 `pipeline.py:983`）→ 地点表四列只能被自动写入，用户改不了 |
| 3 | 档案 schema 校验，非法结构被拦 | ✅ | `frontmatter.py` 全量；`books.py:18-24` META_SCHEMA；`characters.py:17-21` | |
| 4 | AI 辅助填充：T2/T3/T4/T5 四条生成链（生成→人工改定→落盘） | ⚠️ 三条可用，G4/G5 有缺陷 | 按钮 `OverviewView.vue:50-63`（G1/G2/G3/G4/G5 五个）；后端 `api.py:338-458` | **G1/G2/G3 可用**；**G5 前端变量不全 → 直接 404**；**G4 采纳时丢弃已有角色的关系网**；详见 §4.2。真实日志佐证：`generation_log` 里 `archive G1/G2/G3/G5 各 1 次，G4 = 0 次` → **G4 从未被真实跑通过** |
| — | 验收：四条 AI 生成链各跑通一次并人工改定入库 | ❌ 未达成 | 同上；`flow 测试书/characters/char-刀疤强.md` 等有内容但关系网络节仍「（待填）」 | |
| — | 验收：文件树与 UI 双向一致、重启不丢 | ✅ | 文件为准 `db.rebuild()` `db.py:212-257`；`test_db_rebuild.py` 3 项 | |

### M2 · 单章闭环（核心管线）

| # | 任务点（docs/开发计划.md:129-144） | 判定 | 证据 | 说明 |
|---|-----------------------------------|------|------|------|
| 1-9 | 状态机 / 细纲+确认关 / 装配器 / 正文生成 / 零 token 机检 / LLM 审校 ≤2 轮 / 记忆卡回写 / 定稿快照 / 断点恢复 | ✅ 主体完成 | `statemachine.py` 12 态 19 迁移；`pipeline.py` 820 行 8 大节；11 章真实数据、8 章定稿 | 逐条细节见 `.audit/02-pipeline.md` |
| 10 | 异常处理：**`error` 状态 UI 可一键重试** | ⚠️ 后端有、前端无 | `api.py:686-692` `POST /retry` 存在，但 `web/src/api/index.ts` 与 `*.vue` **无任何调用**（grep `/retry` = 0 命中） | 规格03 §3「仍败 → error 状态，UI 可一键重试」→ 用户卡在 error 状态时无按钮可点。属孤儿接口 |

真实运行证据（`data/ainovel.db`，只读）：

```
chapters: 1..11；finalized 8（1,2,5,7,8,9,10,11）；human_review 1（ch3）；mech_check 1（ch4）；pending_finalize 1（ch6）
snapshots: 8 条（每定稿章 1 个 v1）
generation_log: ok 94 / error 2；真实模型 gemini-3.8-flash-high 88 次
review_records: mech 17 / llm 5 / polish 3 / audit 0
```

→ **M2 闭环不是纸面，是真实跑过的**（11 章正文文件、`data/assembly_previews/` 11 个预览、`data/failed_outputs/` 3 个失败样本都在）。

### M3 · 质量层强化

| # | 任务点（docs/开发计划.md:146-157） | 判定 | 证据 | 说明 |
|---|-----------------------------------|------|------|------|
| 1 | rubric 五维固化、分数列表可见 | ✅ | `prompt V1` + `review_records.scores_json`；`WorkbenchView.vue:584-600` 五维进度条 | 真实分数：ch1 `{ai_flavor:95, plot:94, pacing:93, prose:92, hook:96}` |
| 2 | 去AI味第二三层：T8-A 整章润色槽位 + T8-C 自检 | ✅ | `pipeline.py:779-827 polish()` V2→V3 两段调用；`api.py:695-701`；UI 按钮 `WorkbenchView.vue:615` | 护栏齐：字数 ±10%（`pipeline.py:802`）、机检全绿（`:805-810`）、丢弃不动原稿 |
| 3 | 卷末跨章审计（T9-D，V6） | ✅（记录未入库） | `pipeline.py:1020-1069 audit_volume`；自动触发 `:1005-1017 _maybe_audit` ← `finalize():930`；手动路由 `api.py:704-710`；UI `OverviewView.vue:421-426` | 真实产物：`books/流程测试书/audits/卷1_审计.md`（5768 字节，含程序算回收率 `0/6` + LLM 解读）。⚠️`review_records.kind='audit'` **0 行**——审计结果只落 md，不记 DB（`db.py:94` 允许 audit 值），属规范内小缺口 |
| 4 | 黄金三章专项（T7-C）前 3 章自动注入 | ✅ | `pipeline.py:366-367` `prompt_id = "W3" if no <= 3 else "W1"`；UI 标记 `WorkbenchView.vue:424` | README:94 偏差 7 已记录等价实现 |
| 5 | 对话专场（T7-B）/ 突破场景（T7-D）/ 高潮场景专项**槽位** | ⚠️ 接上了，但语义与"槽位"不符且实际不好用 | `prompt_id` 参数 `pipeline.py:345-348`；场景专属变量 `:449-483`；UI 下拉 `WorkbenchView.vue:40-42`；路由校验 `api.py:565` | 开发计划 §4 表（:198-200）写的是「**对话润色槽位** / **突破场景槽位** / **高潮场景槽位**」——"槽位"指装配器里可插入的一段。实现是**整章替换 prompt**：选 W2 就用 W2 写整章。后果见 §4.3（字数必然低于 M-01 下限）。**不是"定义了提示词但不会触发"——是"触发了但语义跑偏、产出必被 M-01 拦"** |

### M4 · 验收跑书（v1 完成线）

| # | 任务点（docs/开发计划.md:159-168 / spec04:57-67） | 判定 | 证据 | 说明 |
|---|-----------------------------------------------|------|------|------|
| 1 | 第一本书立项：都市选题、**定对标书**、定书名/简介/金手指 | ❌ 未做 | `books/流程测试书/meta.md`：`benchmark_book: ''`、body 仍是 `（待填：一句话故事核、核心卖点、金手指概述）`；书名是"流程测试书" | 人工任务，代码层无法替代。但 spec04:59 把它作为验收第 1 条 |
| 2 | 跑完第一卷 ≥10 章 × 2500 字 | 部分 | 11 章，字数 2472-3370；**定稿 8 章** | 未定稿 3 章：ch3 `human_review`、ch4 `mech_check`、ch6 `pending_finalize`（DB 实查）→ "闭环不断档"未完全达成 |
| 3 | 留 3 个固定回归案例（同细纲输入，版本升级后重跑对比） | ❌ 未做 | `grep -r 'regress\|baseline\|回归\|案例\|一键重跑' ainovel/ web/src/` → **唯一命中 `characters.py:143` 的 `realm_regress`（境界倒退，无关）** | 数据原料其实齐了：装配快照 `data/assembly_previews/*/*.json`（11 个）存在、`prompt_digest` 入 `generation_log`（88/88 非空）、细纲在文件里。**但"登记 3 个案例 + 一键重跑对比"的机制完全不存在**：无路由、无 UI 入口、无数据表 |
| — | 验收 3：定稿版本机械检查全绿（阻断 0） | ❌ 未达成 | 用当前 `config.chapter_word_range` + `lint_whitelist` 复检 8 个 final 章节：`001 blocks=3`（M-02 微微/缓缓）、`002 blocks=3`（M-02 微微）、`011 blocks=2`（**M-01 3370>3000** + M-02 陡然） | 定稿时这两条是**靠人工豁免放行**的：8 条 `exempt` 日志理由全是「E2E …放行」。也就是说验收线 3 实际未达成，是用豁免绕过去的 |
| — | 验收 5：单章成本 ≤1 元 | ❌ 无法验证 | `generation_log` 96 条 `cost_cents` **全为 0**；`settings.prices` 只有 `deepseek-chat/reasoner + gemini-2.5-pro/flash`，**没有实际使用的 `gemini-3.8-flash-high`** → `settings.cost_cents():135-136` 查不到价格直接 `return 0` | 第三方中转站模型名千变万化，README 又明确鼓励用户填任意中转模型名（README:19），**成本核算对这批用户恒为 0.00 元**，M4 成本验收线（含 spec03 §4「单章>1元标黄」）整条失效。P1 |
| — | 验收 6：裁决队列清零 | ❌ 未达成 | `data/conflicts.json`：29 条中 **3 条 open**（全部 `new_setting` 待补录设定，ch11） | 且已触发护栏：`guard()` 会禁止开新章细纲（实测确认），所以这本书当前**无法继续写第 12 章**，除非先清队列 |
| — | 验收 9：导出 txt 合并完整无乱码 | ✅ | `api.py:802-818` POST + `Content-Disposition` UTF-8；前端 blob 下载 `api/index.ts:192-207` | 只导 `status=final`（8 章） |
| — | 验收 7 相关：md 导出 | ⚠️ spec04:42 写"txt/md" | 只有 `/export/txt` 与 `/export/zip`，**无 md 路由** | UI/API 细节归 03 |

### M5 · 文风指纹

**开发计划原文：`### M5 · 文风指纹（后期增强，已确认延后）`（docs/开发计划.md:170）** → **延后是明确决策**。

但实际完成度**超出计划**：

| 任务点（开发计划:171-178） | 判定 | 证据 |
|---------------------------|------|------|
| 语料导入(txt) → LLM 文风分析(T10-A) → 特征卡落盘 | ✅ 已实现 | `style.py` 全量；`api.py:745-758` F1；UI `OverviewView.vue:357-380`；真实语料 `books/流程测试书/style/语料.txt`（14864 B）+ `特征卡.md`（7586 B）；`generation_log style/F1 = 2 次` |
| 特征卡注入装配器（新增槽位 4.5） | ✅ 已实现 | `assembler.py:298-306` 注入 F2 到 system 段；`pipeline.py:304-322` 语料 few-shot 摘录 `W1` 的 `〖范文样本〗`；`style_enabled: true` 已在书 config |
| 用回归案例对比注入前后质量差异 | ❌ 依赖 M4 回归案例 → 未做 | 同 M4 第 3 条 |
| 验收：注入后连续 5 章风格自检无漂移 | ⚠️ 无程序化漂移检测 | `pipeline.py:325-342 _style_points` 只是把特征卡两个 H2 节塞进 V1 审校当"文风要点"参照，没有跨章漂移度量 |

**判定：M5 = 【延后决策】（原文明确）**，但代码已提前落地约 80%；差的是"回归对比"与"漂移检测"两块，且它们**同时依赖 M4 的回归案例机制**。

### v2 ·（跑完第一卷后）

**开发计划原文：`### v2 ·（跑完第一卷后）`（docs/开发计划.md:180）**，列了：拆文引擎、向量 RAG 升级、数据看板、发布辅助。

- 代码里 `grep -r '拆文'` → 只命中 `docs/` 调研报告，`ainovel/` **零命中**。
- **判定：v2 全部【延后决策】。** 这是"跑完第一卷后"的前置条件尚未满足（M4 未完成）导致的合理未开始。

### 里程碑矩阵小结

```
M0 ████████░░ 85%   2 处偏离开发计划（Tauri / Gemini 原生协议）未进偏差表
M1 ███████░░░ 70%   meta/地点无编辑入口；G4/G5 采纳链有缺陷；G4 从未真跑通
M2 █████████░ 95%   主线扎实且真实跑过；error 状态无 UI 重试入口
M3 ████████░░ 85%   V2/V3/V6 全接；W2/W4/W5 语义跑偏；V6 结果未入库
M4 ██░░░░░░░░ 25%   书没立项、回归案例机制 0、验收 3/5/6 全未达成
M5 已提前实现（计划内标注延后，不算欠账）
v2 未开始（计划内标注延后，不算欠账）
```

---

## 3. 任务点名的六项重点核查（逐条给答案）

### 3.1 M0 的 Tauri 2 桌面 —— 实际是 FastAPI + 浏览器，偏差有没有记录？

**答：是偏差，README 没有记录。**

- 开发计划:26「Tauri 2 + Vue 3 + TypeScript，Windows 桌面单机，零云部署」；:108「Tauri 2 + Vue 3 + TS + Vite 项目初始化」；:114 验收「`pnpm tauri build` 产出可运行 exe」。
- 实际：`run.py:34` `uvicorn.run("ainovel.api:app", host="127.0.0.1")`；`:25-32` `webbrowser.open(url)` 起系统浏览器。无 `src-tauri/`、无 Rust、无 tauri 依赖。
- 规格04 全文没有 "Tauri" 一词（`grep` 0 命中），只描述 Web UI + `api.py` 静态挂载 → 按开发计划:6「冲突时规格文档优先」这是**可解释的技术选型收敛**。
- **但**：README:82-96「与规格的对应与偏差（重要）」9 条里，第 9 条只记录了"前端从 vanilla JS 改为 Vue3"，**完全没有"桌面壳从 Tauri 改为 FastAPI+浏览器"这一条**。AGENTS.md 也没有。→ 【文档没写清楚】，且这直接导致 M0 验收（`pnpm tauri build`）无从判断是否达成。

### 3.2 M0 的 Gemini "原生协议"适配 —— 核实 llm.py 实际怎么做，README 偏差有没有记录？

**答：只有统一 OpenAI 兼容协议；README 改了正文口径但没有改 M0 检查单，现在 README 自己前后矛盾。**

- `llm.py` 187 行，**只有一个客户端类** `OpenAICompatClient`（:46），固定打 `{base_url}/chat/completions`（:64），固定 `Bearer` 头（:63），固定解析 `data:` SSE（:75-97）。**没有第二个实现，没有 `generateContent`，没有 Gemini 原生协议。**
- `llm.py:1` 模块 docstring 仍写「DeepSeek（OpenAI 兼容）/ **Gemini（原生 SSE）**」→ **代码注释也未同步**。
- README 内部矛盾：
  - README:70（偏差表外，目录结构说明）：「llm/mockllm **OpenAI 兼容客户端（官方/中转站通用，Gemini 等模型一律走兼容协议）** + 测试 Mock」—— 这是 commit `e71b025` 改过的正确口径。
  - README:103（M0 检查单）：「Gemini 连通 ｜ ✅ 代码就绪（**原生 SSE 流式**，base URL 可改走代理）」—— **仍是旧口径，且打 ✅**。
- `git blame README.md -L 100,106` 显示第 103 行自首个 commit `67047d1` 从未被改过；`git show e71b025 -- README.md` 只改了第 37 行（74→104 项）和第 70 行。
- **偏差表 9 条里没有"取消 Gemini 原生协议适配器"这一条** → 【文档没写清楚】。这条影响 M0 验收第 2 项「两个 key 各发一条测试消息」（实际只有一个通道），也让 M4「DeepSeek 规划 + Gemini 正文 双适配器」的架构假设在文档层面失真。

### 3.3 M0 Windows 打包 exe —— dist/ build/ 里到底有什么，ainovel.spec 是否可用？

**答：`ainovel.spec` 本身可用，`dist/` `build/` 里确实有构建产物，但产物是坏的（旧 UI + 旧提示词 + 旧 DB），且 README「未在本机实测」这句话已经过时。**

实测目录（只读列举）：

```
dist/ai-novel/ai-novel.exe            8,601,209 B   2026-10-25 10:58:09
dist/ai-novel/_internal/              python312.dll + 依赖（含 yaml/, pydantic-2.14.1.dist-info 等）
dist/ai-novel/prompts/                23 个 .md（5 个层目录）—— 全部 version: 1
dist/ai-novel/lint/                   banned_words.txt 920 B（仓库 914 B）、另两个一致
dist/ai-novel/web/                    app.js / index.html / style.css  ← 旧 vanilla UI
dist/ai-novel/data/                   ainovel.db(4096) + -shm + -wal + settings.json（api_key 为空 → 干净）
build/ainovel/                        ai-novel.exe + .pkg + Analysis/EXE/PKG/COLLECT toc + PYZ + warn + xref
```

**五个具体问题：**

1. **打包产物无 UI。** `api.py:840-842` 挂载的是 `project_root()/"web"/"dist"`。PyInstaller frozen 时 `paths.py:19-20` `project_root()` = exe 所在目录 = `dist/ai-novel` → 期望 `dist/ai-novel/web/dist/`。**实际 `dist/ai-novel/web/` 下没有 `dist` 子目录**（只有旧 vanilla 三件套）。因此启动后 `_web_dist.exists()` 为 False，走 `api.py:843-851` 兜底，`GET /` 返回一段 JSON 提示 → **exe 打不开界面**。
2. **提示词是 version 1 旧版。** 对比：`W1 repo=4034B/dist=987B`、`V1 repo=4140B/dist=2118B`、`S1 repo=3116B/dist=1593B`、`G1 repo=2263B/dist=1716B`；frontmatter 全部 `version: 1`，而仓库里是 `S1/W1=4, V2=3, 其余 2`。即打包后再没更新过。
3. **打包时间早于首个 git commit。** `dist` 时间戳 `2026-10-25 10:58`，首个 commit `67047d1` 是 `2026-10-25 23:06` → 产物**不可能**包含此后 9 个 commit 的任何改动（含整个 Vue3 前端重写、22 项安全回归测试）。
4. **DB 是旧 schema。** `dist/ai-novel/data/ainovel.db` 只有 5 张表（books/chapters/generation_log/review_records/snapshots），**没有 `chapters_fts`**；`_internal` 里也没有可用的旧数据。
5. **README 与 ainovel.spec 的说明基本对得上，但都停留在"怎么打"，没说"打完要刷 prompts/lint/web/dist"。** `ainovel.spec:4` 注释：「产物：dist/ai-novel/ai-novel.exe（prompts/ lint/ web/dist 随 exe 同目录分发，见 README）」；README:107 也写了「然后把 `prompts/ lint/ web/dist` 拷到 `dist/ai-novel/` 下」。**说明文字是对的，只是从没按它重打过。**

**README:105 现状**：「Windows 打包 exe ｜ ⚠️ 提供 `ainovel.spec` + 打包说明；**PyInstaller 构建未在本机实测**（见下）」——**这句已经过时**：`build/` 里的 `Analysis-00.toc`、`EXE-00.toc`、`PYZ-00.pyz`、`warn-ainovel.txt`、`xref-ainovel.html` 是完整成功构建的残留物，说明构建在本机跑过（且成功）。真实状态是"构建过，但产物过期且不可用"，比"未实测"严重得多。→ 【文档没写清楚】P1。

> 另：`build/ainovel/warn-ainovel.txt`（16 KB）里是一堆 `missing module named pwd/grp/termios...`（POSIX 模块，Windows 上属正常噪声），未见 `ainovel.*` 或 `uvicorn.*` 缺失 → 打包**模块层面**没有明显缺失，问题纯粹在 data files 版本。

### 3.4 M1 四条 AI 生成链（G1-G5）是否都能从 UI 触发生成？

**答：五个按钮都在，但只有 G1/G2/G3 真正可用；G5 点下去必然 404，G4 能生成但采纳时结果被丢弃，且 G4 从未被真实运行过。**

前端按钮：`web/src/views/OverviewView.vue:50-63` 定义 `G_TARGETS{G1,G2,G3,G4,G5}` + `G_LABELS`，`:340` `v-for` 渲染 5 个按钮 → 点击调 `aiArchive(pid)` → `:146` `aiGenerate(book, pid, vars)` → `POST /api/books/{book}/ai/generate` → `api.py:338-348` `pipeline._call(...)` → `prompts.render()`。

**隔离实验 C 实测（临时 HOME）：**

- `G1` 需要 `题材/故事核/既有设定摘要/对标书` → 前端 `:121-133` 全传 ✅
- `G2` 需要 `基础设定/既有力量体系/题材` → 前端全传 ✅（多了 2 个无害变量）
- `G3` 需要 `角色需求清单/世界观摘要` → 前端全传 ✅
- `G5` 需要 `单章字数区间/角色卡摘要/目标总章数/世界观摘要/logline` → 前端 `:116-120` **只传了 3 个**，缺 `世界观摘要`、`角色卡摘要`：

```
请求处理失败: G5: 未提供变量 ['世界观摘要', '角色卡摘要']
G5 (前端 variables) -> 404 {'detail': "G5: 未提供变量 ['世界观摘要', '角色卡摘要']"}
```

  因为 `prompts.py:106-108` 渲染后仍有未填变量就 `raise PromptError` → `api.py:345-346` 转 404。**用户点"G5 全书大纲"按钮，100% 拿到 404 弹窗，无法生成大纲。**这是 M1 最硬的一个断点（大纲是全书的起点，没有它连第 1 章细纲都生成不了 —— `pipeline.py:97-98` `chapter_row is None` → `PipelineError`）。

- `G4` 变量齐（只需 `角色卡合集`），能生成。**但采纳链有缺陷**，见 §4.2。

**"四条"的口径**：开发计划:123 写「T2 世界观、T3 力量体系、T4 角色卡、T5 全书大纲**四条**生成链」；spec04:59 却说「G1-G5 **四条**」（列了 5 个 ID）。实际代码/UI 是 **G1-G5 五条**（+G6 细纲）。→ 【文档没写清楚】P3：spec04 的"G1-G5 四条"是笔误。

### 3.5 M3 的 W2/W4/W5/V2/V3/V6 —— 是"定义了提示词但管线里根本不会自动触发"吗？

**答：不完全是。逐个查的结果是：V2/V3/V6 都已接线（V6 还能自动触发），W2/W4/W5 能手动触发但语义跑偏导致产出必被拦，真正完全悬空的只有 T1。**

| 提示词 | 代码调用点 | 触发方式 | 判定 |
|--------|-----------|---------|------|
| **V2** | `pipeline.py:797` `_call(..., "polish", "V2", ...)` ← `polish():779` ← `api.py:695` ← UI 按钮 `WorkbenchView.vue:615` | 手动（"槽位"语义，spec03 §1 管线总览不含 V2） | ✅ 已接 |
| **V3** | `pipeline.py:813` `_call(..., "polish", "V3", ...)`，紧随 V2 自动执行 | V2 后自动 | ✅ 已接 |
| **V6** | `pipeline.py:1053` `_call(..., "audit", "V6", ...)` ← `audit_volume():1020` ← `_maybe_audit():1014` ← `finalize():930` | **自动**（卷最后一章定稿后，`_volume_last_chapter` 判定）+ 手动路由 `api.py:704` + UI `OverviewView.vue:426` | ✅ 已接，双向入口 |
| **W2** | `pipeline.py:388` `_call(..., "prose", prompt_id, ...)`，`prompt_id` 由 `api.py:565` 白名单传入 ← UI 下拉 `WorkbenchView.vue:40` | 手动下拉选择；**无自动识别"本章是对话场景"的逻辑** | ⚠️ 可触发，但产出必被 M-01 拦（§4.3） |
| **W4** | 同上；专属变量 `_scene_special_vars():480-482` | 手动下拉 | ⚠️ 同上 |
| **W5** | 同上；专属变量 `:483` | 手动下拉 | ⚠️ 同上 |
| **T1**（选题六问） | **无任何调用点** | 只能直接 POST `/ai/generate` 且 `prompt_id="T1"`（UI 无入口，`G_TARGETS` 不含 T1） | ❌ 完全悬空 |

- T1 依据：`grep -rn '"T1"' ainovel/ web/src/ scripts/` → **0 命中**；唯一出现是 `tests/test_prompts.py:7` 的 ID 白名单。开发计划 §4 表（:209）写「豆包 T1 ｜ 选题六问 ｜ **立项对话（工具外或简单页）** ｜ M4」→ **"工具外"是允许的**，所以严格说**不算 bug**，但 README:84 宣称「规格02 提示词组织/…」已按规格实现、README:74 宣称 23 条提示词可用，实际 T1 无入口 → 【文档没写清楚】P2。
- **自动触发的问题不存在**（W2/W4/W5 本来就不是"自动"任务，规格02 只说它们在 prose 档 M3）。任务假设需要修正为：**"这 5 条里 V2/V3/V6 已接、W2/W4/W5 接了但不好用、T1 无入口"**。

### 3.6 M4 的"3 个固定回归案例留存 + 一键重跑对比"是否实现？

**答：完全未实现，连数据模型都没有。但"原料"其实已经齐了。**

- 检索证据（三种写法全查）：
  - `grep -rn 'regress|baseline|回归|案例|一键重跑' ainovel/ web/src/` → 唯一命中 `characters.py:143` 的 `Conflict("realm_regress", ...)`（境界倒退检测，与回归案例无关）。
  - `grep -n 'regression|golden_case|replay|对比|基准' ainovel/ web/src/` → 0 命中（`golden3` 是黄金三章，无关）。
  - `api.py` 61 个路由（`grep -c '@app\.'`）里**没有任何**案例登记/重跑路由。
- 数据模型：`db.py:54-110` 五张表，无 `regression_cases` / `baselines` 类表。
- 规格要求：spec04:65「7. 3 个回归案例留存（第 1/5/10 章各一：细纲 + 装配快照 + prompt digest），可一键重跑对比」。
- 现状：**三样原料都已落盘**——① 细纲 `books/<书>/chapters/00N_细纲.md`；② 装配快照 `data/assembly_previews/<书>/00N_v1.json`（11 个，含 13 槽位内容 + token 估算）；③ `prompt_digest`（`generation_log` 88/88 非空，SHA-256，`prompts.py:57`）。**缺的只是"挑出 3 章登记为基线 + 用当前提示词重跑 + 逐项 diff"的编排层。**
- 连带影响：M5 验收「用 M4 的回归案例对比注入前后质量差异」同样无法做 → 这也是 M5 唯一真正缺的一环。

---

## 4. 逐项证据展开：五个"看着做了实际不可用"的案例

> 这是本次审计最有价值的部分——都属于"文件存在 / 按钮存在 / 函数存在，但端到端不可用"。

### 4.1 【P1】设置页「保存连接」会把真 API key 覆盖成占位符

**链路：**
1. `settings.py:121-130 masked()`：给 UI 的 `api_key` 被替换成字面量字符串 `"已配置（留空保持不变…）"`，另给 `has_key: true`。
2. `web/src/views/SettingsView.vue:45-53` 前端保存逻辑：
   ```ts
   const cur = await getSettings()
   const key = keyInput.value.trim()
   // 输入框留空或仍是掩码占位则保留原 key
   const apiKey = !key || key.includes('…') ? (cur.providers?.openai?.api_key ?? '') : key
   await putSettings({ providers: { openai: { api_key: apiKey, base_url: ... } }, ... })
   ```
   **`cur.providers.openai.api_key` 是从 `masked()` 拿的，值就是占位符 `"已配置（留空保持不变…）"`**；代码注释写"保留原 key"，实际保留的是**占位符**。
3. `api.py:77-80 put_settings()` 直接 `settings.update(payload.settings)` → `_deep_merge` 把占位符写进 `data/settings.json` 并落盘。

**隔离实验 A 实测（临时 HOME，未触碰真实 settings.json）：**
```
stored key A: sk-REALKEY-1234567890
GET masked: 已配置（留空保持不变…） | has_key: True
前端将提交的 api_key: 已配置（留空保持不变…）
PUT status: 200
stored key B: 已配置（留空保持不变…）
被写坏? True
```

**后果：** 用户在设置页改 base_url、改默认模型、或只是点一下「保存连接」，**真 key 就被永久覆盖成占位符** → 之后所有 LLM 调用返回 401（`llm.py:72-74`），且 UI 上仍显示"已配置"，极难自查。当前 `data/settings.json` 里 key 仍在（未被覆盖过，说明用户最近没点保存），属**待引爆**状态。

**为什么测试没抓到：** `tests/test_guards.py:91-94` 只断言 `masked` 结果里不含 `sk-secret`（单向：不回显），**没有反向断言"往返一轮后 key 不变"**。测试方向缺失。

### 4.2 【P1】G4 关系网 / G5 全书大纲：AI 生成链从 UI 走不通或结果被丢弃

**(a) G5 缺 2 个必需变量 → 404**（隔离实验 C）
```
G5 (前端 variables) -> 404 {'detail': "G5: 未提供变量 ['世界观摘要', '角色卡摘要']"}
```
`prompts/generate/G5.md` 需要 5 个变量；`OverviewView.vue:116-120` 只构造 3 个（`目标总章数`/`单章字数区间`/`logline`）。而 `G5 = 全书大纲 = M1 的交付核心`，没有大纲 → `pipeline.generate_outline():97-98` 直接 `PipelineError("OUTLINE.md 章节总表缺第 N 章行")` → 整条管线起点断掉。
（注：真实书 `流程测试书` 的 OUTLINE.md 是 7321 字节且 12 章齐全，说明历史上是用 E2E 脚本绕过 UI 灌进去的 —— `scripts/e2e_test.py:102-108` 手工拼了 6 个变量。）

**(b) G4 采纳把结果丢了**（隔离实验 B）
`api.py:436-458 adopt_characters()`：
```python
if name in characters.list_characters(book):
    continue          # ← 已存在的角色直接跳过，一行不写
```
实测（两个已有角色 + 一段标准 G4 输出）：
```
adopt G4 -> 200 {'ok': True, 'created': []}
林寒 关系网络节含苏婉? False
林寒 relationships() -> []
```
→ **G4 的全部产出被静默丢弃，接口还返回 `ok: true, created: []`**。规格02 §4「G4 关系网：【全集】提示词5 原文；结果合并写回各角色的"关系网络"节」**未实现**；`api.py:452` 那行注释「关系网络矩阵（G4）合并到各角色"关系网络"节」是**失实注释**（实际只是把 `card` 整体覆盖到 body，且只对**新建**角色生效）。
真实数据印证：`books/流程测试书/characters/char-林寒.md`（3447 B，有内容）的关系网络节仍是 `（待填）`；`generation_log` 里 `archive/G4` **0 次**。

### 4.3 【P2】W2/W4/W5 被当成"整章生成"，产出必被 M-01 字数规则拦下

`pipeline.py:345-410 generate_prose()` 把 `prompt_id` 直接当整章写作提示词用。而 W2（对话专场）/W4（突破场景）/W5（高潮场景）按开发计划 §4 的原意是**装配器里的一个"槽位"（场景级注入）**，不是整章模板。

隔离实验 D（MockLLM，计数真实调用次数）：
```
W1: 调用次数=1 返回={'word_count': 2541, ...}
W2: 调用次数=2 返回={'word_count': 10, ...}
W4: 调用次数=2 返回={'word_count': 2541, ...}
W5: 调用次数=2 返回={'word_count': 10, ...}
```

代码层面可解释：
- W2/W5 的 prompt 里**没有 `〖字数区间〗` 变量**（`grep` 变量清单：W2 = `本场景潜台词要求, 本场景人物状态, 系统段, 章合同`；W5 = `对抗双方状态与底牌, 系统段, 已埋伏笔清单, 章合同, 装配上下文`）→ 模型不会按 2200-3000 字写。
- `pipeline.py:391-398` 兜底：字数越界 → 重跑一次（`extra_instruction` 里强调字数），但**重跑后不再校验**，直接 `:407 chapters.write_prose()` 落盘。
- 结果：W2/W5 落盘的正文字数必然远低于 `config.chapter_word_range[0]` → `mechcheck.M-01`（阻断级）→ `run_mech_check()` 自动修复一轮 → 仍阻断 → `human_review`。

**这一点在真实数据里有旁证**：8 条 `exempt` 日志的理由全部是人工放行，其中 `ch5` 是「字数超16字放行」、其余是「E2E …放行」——说明字数规则频繁需要人工兜。**但严格说：真实运行里 `prompt_id` 从未传过 W2/W4/W5**（`generation_log` 里 `prose` 只有 `W1 15 次 / W3 5 次`），所以这是"代码路径可推断的缺陷"，尚未被真实使用暴露 —— 一旦用户按 UI 下拉选了 W2/W4/W5 就会踩。

### 4.4 【P2】成本核算对第三方中转站模型名恒为 0，M4 成本验收线整条失效

- `settings.py:133-141 cost_cents()`：`prices.get(model)` 查不到 → **`return 0`**，不告警、不记 unknown。
- `settings.py:30-35 DEFAULTS["prices"]` 只有 4 个模型：`deepseek-chat / deepseek-reasoner / gemini-2.5-pro / gemini-2.5-flash`。
- 实际使用：`default_model = "gemini-3.8-flash-high"`（`data/settings.json`；`generation_log` 88 条全此模型）→ **每次调用 cost=0**。

实测数据：
```
== genlog by status ==
{'status': 'ok', 'c': 94, 'cost': 0}
{'status': 'error', 'c': 2, 'cost': 0}
```

- 连带失效：spec03 §4「单章 >1 元标黄（验收线）」的 `api.py:180` `cost_over_yuan = cost_cents > 100` 永远 False；`OverviewView.vue:441` 的 `cost-warn` class 永不触发；spec04:63 验收第 5 条无法验证。
- 而 README:19 明确把"第三方中转站模型名（如 `gemini-2.5-pro`）"作为推荐用法，实际中转站的模型名（如 `gemini-3.8-flash-high`）不在默认表里 → **目标用户群的成本面板一律是 0.00 元**。
- 修法很轻（查不到的模型在设置页可见地提示"未配置单价"），但当前是**静默吞掉**，属"看着有实际没有"。

### 4.5 【P2】已定稿章节按当前规则并非"机械检查全绿"

用书自己的 `config.json`（`chapter_word_range: [2200, 3000]`、`lint_whitelist: ["骤然","猛地"]`）+ 当前 `lint/` 词库，对 8 个 `status: final` 的章节重跑 `mechcheck.check()`（只读）：

```
001_正文.md status=final blocks=3 [('M-02', 29, '微微'), ('M-02', 33, '缓缓'), ('M-02', 115, '缓缓')]
002_正文.md status=final blocks=3 [('M-02', 5, '微微'), ('M-02', 59, '微微'), ('M-02', 209, '微微')]
005_正文.md status=final blocks=0
007_正文.md status=final blocks=0
008_正文.md status=final blocks=0
009_正文.md status=final blocks=0
010_正文.md status=final blocks=0
011_正文.md status=final blocks=2 [('M-01', 0, '3370'), ('M-02', 175, '陡然')]
```

→ **8 章里 3 章未过**（37.5%），其中 ch11 还超字数上限 370 字（3370 > 3000）。而 spec04:61 验收第 3 条要求「最终定稿版本机械检查全绿（阻断 0 命中）」。

**成因不是 bug 而是流程**：`db` 里 `stage='exempt'` 8 条，理由是人工放行；`human_review → approve(豁免) → pending_finalize → finalize`。豁免是产品红线允许的（规格03 §2.5 三选项之一），但**这意味着"验收线 3 未达成"应如实记录**，而 README:84 却写「规格03 管线各环节/机械检查/…**已按规格实现**」——把"机制实现"与"验收线达成"混为一谈。

### 4.6 附：三处数据/配置不一致（同源冲突）

| 位置 | `chapter_word_range` | 说明 |
|------|---------------------|------|
| `docs/specs/01-数据与档案规格.md:39,56` | `[2200, 2800]` | 规格基准 |
| `books/流程测试书/meta.md` | `[2200, 2800]` | 与规格一致 |
| `books/流程测试书/config.json` | `[2200, 3000]` | **实际生效的那份**（`books.load_config()`） |
| `chapters/001|011_细纲.md` 的 `word_range` | `[2200, 3000]` | 由 `pipeline.py:176` `fm2["word_range"] = wr` 从 config 覆盖写入 |

`meta.md` 里的 `chapter_word_range` **没有任何代码读取**（`books.read_meta` 只用于展示 `api.py:182`），用户改 meta 里的字数区间不会生效——而规格01 §2.1 把该字段定义在 meta 的 frontmatter 里。属"两处真相"隐患，P3。

---

## 5. 工程可用性

### 5.1 打包（P1，见 §3.3 详述）

`ainovel.spec` 语法正确、依赖收集完整（`build/ainovel/warn-ainovel.txt` 无本项目模块缺失）、`dist/ai-novel/_internal/` 含 python312 + yaml + pydantic + sqlite3.dll 齐备。**问题不在 spec，在产物没有随代码刷新**：

- `dist/ai-novel/web/` 是旧 vanilla UI，**缺 `web/dist/`** → exe 启动后无界面（`api.py:840-851`）。
- `dist/ai-novel/prompts/` 全 version 1 → 提示词体系落后 2-3 个版本。
- `dist/ai-novel/data/ainovel.db` 无 `chapters_fts` 表。
- 产物时间 2026-10-25 10:58 < 首个 commit 23:06。

**结论：仓库里存在"打包能跑通过但产物不可用"的状态，README:105「未在本机实测」是错误描述。**

### 5.2 设置与密钥（P1，见 §4.1 详述）

`saveProviders()` 会破坏真 key。安全性本身没问题（掩码不回显、`data/` 被 gitignore），但**可用性**上这是最容易被用户踩的坑：设置页任何一个"保存"动作都可能静默废掉 key。

### 5.3 离线与依赖

- ✅ 全部 104 项测试 + smoke 均离线可跑（MockLLM，无网络）。
- ✅ `web/node_modules` 已存在、`web/dist` 已构建（2026-10-26 10:07，比源码新）。
- ✅ Python 侧无缺失依赖（pydantic 由 fastapi 传递带入）。
- ⚠️ 无 CI（无 `.github/`、无 `Makefile`/`tox`/`nox`）→ 质量门完全靠人工自觉；AGENTS.md 的"pytest 全绿为验收线"没有自动执行者。
- ⚠️ `web/dist/` 被 `.gitignore:4` 的 `dist/` 规则覆盖（`git check-ignore -v web/dist` → `.gitignore:4:dist/`）→ 符合 spec04:26「`web/dist/` 已被现有 `dist/` 规则覆盖」的有意设计，但意味着**任何分发/打包前必须本地 `npm run build`**，否则就是 §5.1 那个坑。

### 5.4 真实数据完整性

`books/流程测试书/` 结构完整（meta/Bible/OUTLINE/PLOT_POINTS/LOCATIONS/config + style/ + audits/ + chapters/ 30 文件 + characters/ 10 文件）；`data/` 有 db / assembly_previews(11) / failed_outputs(3) / summaries / conflicts.json。**没有发现半写坏的文件**（`atomic.write_text` 生效）。

---

## 6. 文档一致性：文档漂移表

| # | 文档位置 | 文档原文 | 实际 | 判定 | 严重度 |
|---|---------|---------|------|------|--------|
| 1 | `README.md:79` | `tests/              pytest（74 项）` | **104 项** | 过时（:37 已写 104，同一文件两处矛盾） | P2 |
| 2 | `README.md:103` | ｜Gemini 连通｜✅ 代码就绪（**原生 SSE 流式**，base URL 可改走代理）｜ | `llm.py` 只有一个 `OpenAICompatClient`，无原生协议 | **错误**，且与 `README.md:70` 自相矛盾 | P1 |
| 3 | `README.md:105` | ｜Windows 打包 exe｜⚠️ …PyInstaller 构建**未在本机实测**｜ | `build/` `dist/` 有完整成功构建产物（时间 2026-10-25 10:58） | 过时；真实问题是"产物过期不可用"而非"未实测" | P1 |
| 4 | `README.md:84` | **已按规格实现**：…规格01 **全部**文件格式/表结构/状态机/防损规则；…规格04 页面清单（书架/书总览/**档案编辑**/章节工作台/裁决/设置/导出） | 无独立"档案编辑"页（路由仅 6 条，`web/src/router/index.ts:8-14`）；meta 与地点无编辑入口 | **夸大**：把"机制实现"写成"全部实现" | P1 |
| 5 | `README.md:86` | 偏差与修正（**详见交付报告**） | 仓库内**不存在**名为"交付报告"的文件（`grep -r 交付报告` 仅命中 README 自身） | 悬空引用 | P2 |
| 6 | `README.md:107` | 打包：…然后把 `prompts/ lint/ web/dist` 拷到 `dist/ai-novel/` 下 | 实际 `dist/ai-novel/web/` 是旧 vanilla，无 `web/dist`；prompts 是 v1 | 说明正确但从未执行 → 现状与说明不符 | P1 |
| 7 | `README.md:70` vs `:103` | 前者「Gemini 等模型一律走兼容协议」，后者「原生 SSE 流式」 | 前者对 | 同文件前后矛盾 | P1（与 #2 同源） |
| 8 | `docs/开发计划.md:26,108,114` | Tauri 2 + Vue3 + TS，`pnpm tauri build` | FastAPI + 浏览器（`run.py:34`） | 偏离未进 README 偏差表 | P2 |
| 9 | `docs/开发计划.md:110` | LLM 适配层：…**Gemini（原生协议）** 两实现 | 单一 OpenAI 兼容实现 | 偏离未进 README 偏差表 | P2 |
| 10 | `docs/specs/04-UI与验收规格.md:59` | 建书 → **G1-G5 四条**生成链跑通 | G1/G2/G3/G4/G5 共 5 条（+G6） | 数量笔误 | P3 |
| 11 | `docs/specs/04-UI与验收规格.md:42` | 导出：章节合并导出 **txt/md**、全书打包 zip | 只有 `/export/txt` 与 `/export/zip`，无 md | 缺 1 项 | P2 |
| 12 | `docs/开发计划.md:198-200` | T7-B/T7-D/提示词9 → **对话润色槽位 / 突破场景槽位 / 高潮场景槽位** | 实现为"整章替换 prompt"（`pipeline.py:345-410`） | 语义偏离；且导致产出必被 M-01 拦（§4.3） | P2 |
| 13 | `AGENTS.md:3` | Python 依赖**仅** requirements.txt 里 5 个包 | 直接 import 第 6 个包 `pydantic`（`api.py:15`） | 表述不精确 | P3 |
| 14 | `README.md` / `AGENTS.md` 命令章节 | 只提 `pytest` + `smoke.py` | 仓库另有 2 个**真实 LLM + 写真实数据**的 E2E 脚本（`scripts/e2e_test.py`、`e2e_stage2.py`），无任何说明或警告 | 危险脚本无文档 | P2 |
| 15 | `ainovel/llm.py:1` | docstring「DeepSeek（OpenAI 兼容）/ **Gemini（原生 SSE）**」 | 只有 OpenAI 兼容 | 代码注释过时 | P3 |
| 16 | `ainovel/api.py:452` | 注释「关系网络矩阵（G4）合并到各角色"关系网络"节」 | 该分支只对**新建**角色生效，已存在角色被 `continue` 跳过（`:449-450`） | 失实注释 | P2 |

**漂移规律**：所有数字类（104 项）在最近一次 commit `e71b025` 里被修正过一处、漏改一处；所有"架构选型类"（Tauri / Gemini 原生协议）在 README 偏差表里**系统性缺失** —— 偏差表记录的全是"实现层的取舍"（FTS book_id、rebuild 保留、黄金三章走 W3…），没有记录"与开发计划架构不符的选型变更"。这是文档体系的一个结构性缺口：**开发计划 → 规格 → 代码 三方，只有前两者互相同步，后者与开发计划的偏离无处登记**。

---

## 7. 真正没做完的清单

### 7.1 按「影响用户能否跑完第一卷」排序

| 排序 | 事项 | 分类 | 影响 | 证据 |
|------|------|------|------|------|
| **1** | 设置页「保存连接」覆盖真 API key | 【未实现】正确性缺陷 | **直接断言路**：用户改完 base_url 就无法生成任何内容，且 UI 显示"已配置"难以自查 | `settings.py:121-130` + `SettingsView.vue:45-53` + 实验 A |
| **2** | G5 全书大纲按钮 404（缺 2 变量） | 【未实现】 | **管线起点断**：无大纲 → 无法生成第 1 章细纲（`pipeline.py:97-98`） | `OverviewView.vue:116-120` vs `prompts/generate/G5.md` + 实验 C |
| **3** | 3 个回归案例 + 一键重跑对比 | 【未实现】 | M4 验收第 7 条 + M5 全部验收的外部依赖；影响"版本升级后质量是否退化"这个核心诉求 | 全仓 grep 0 命中（§3.6）；数据原料已具备 |
| **4** | 裁决队列 3 条 open 未清 | 【未实现】（运营动作） | **当前实车被锁**：`guard()` 禁止开第 12 章细纲（实验 E 已验证拦截生效） | `data/conflicts.json` 3 open；`conflicts.py:118-124` |
| **5** | 定稿版本机械检查未全绿（8 章中 3 章有阻断） | 【未实现】验收线 | M4 验收第 3 条未达成；后续任何"全绿"承诺都要打折 | §4.5 复检结果 |
| **6** | 成本核算恒为 0（未配置单价的模型静默返回 0） | 【未实现】 | M4 验收第 5 条无法验证；"单章>1元标黄"永不触发 | `settings.py:133-141` + `generation_log` 96 条 cost=0 |
| **7** | G4 关系网采纳丢弃结果（已存在角色） | 【未实现】 | 世界观的"关系张力"信息进不了档案，影响后续装配质量 | `api.py:449-450` + 实验 B |
| **8** | 打包产物不可用（旧 UI / 旧提示词 / 无 web/dist） | 【未实现】 | 想分发给别人用（开源副产品定位）完全走不通 | §3.3 五条证据 |
| **9** | meta 档案与地点档案无前端编辑入口 | 【未实现】 | M1 验收"文件树与 UI 双向一致"不成立；地点表只能自动写 | `api.py:303-305`（仅 GET）+ `api/index.ts` 无 `/meta` `/locations` |
| **10** | error 状态无 UI 一键重试 | 【未实现】 | 规格03 §3 明写"UI 可一键重试"；LLM 报错后用户只能刷新猜测 | `api.py:686-692` 有路由，前端 0 调用 |
| **11** | W2/W4/W5 当作整章生成，产出必被 M-01 拦 | 【未实现】语义偏离 | 场景专项实际不可用；且用户看不出为什么生成 800 字就变红 | §4.3 + 实验 D |
| **12** | 第一卷立项：定对标书、填简介/金手指 | 【未实现】（人工任务） | M4 验收第 1 条；但这是产品动作不是代码缺陷 | `books/流程测试书/meta.md` 仍「（待填…）」、`benchmark_book: ''` |
| **13** | T1 选题六问无 UI 入口 | 【文档没写清楚】 | 开发计划允许"工具外"，但 README 宣称 23 条可用 | `grep '"T1"'` 代码 0 命中 |
| **14** | 第 3/4/6 章卡在中间状态未定稿 | 【未实现】（运营动作） | "10 章连续闭环"只到 8 章 | DB `chapters` 实查 |

### 7.2 三类划分总表

**【未实现】**（13 项）
1. 设置页保存覆盖 API key（§4.1）
2. G5 前端变量不全 → 404（§4.2a）
3. G4 关系网采纳丢弃（§4.2b）
4. 3 个回归案例 + 一键重跑（§3.6）
5. 成本核算对未配置模型恒 0（§4.4）
6. 打包产物过期不可用（§3.3）
7. meta 档案前端编辑（§2 M1#2）
8. 地点档案前端编辑 + 后端 PUT（§2 M1#2）
9. error 状态 UI 一键重试（§2 M2#10）
10. 独立"档案编辑"页（spec04:38 vs 实际 6 路由）
11. md 导出（spec04:42）
12. W2/W4/W5 场景槽位语义（§4.3）
13. V6 审计结果未入 `review_records(kind='audit')`（§2 M3#3）

**【延后决策】**（有原文依据，合理，不算欠账）
1. **M5 文风指纹** —— `docs/开发计划.md:170`「### M5 · 文风指纹（**后期增强，已确认延后**）」。且实际已提前实现约 80%（语料→F1→特征卡→F2 注入 全部可用，见 §2 M5）。
2. **v2 全部（拆文引擎 / 向量 RAG / 数据看板 / 发布辅助）** —— `docs/开发计划.md:180`「### v2 ·（**跑完第一卷后**）」。前置条件（M4）未满足。

**【文档没写清楚】**（8 项）
1. Tauri → FastAPI + 浏览器：未入 README 偏差表（§3.1）
2. Gemini 原生协议 → 统一 OpenAI 兼容：未入偏差表，且 README:103 与 :70 自相矛盾（§3.2）
3. README:79 "74 项" vs 实际 104
4. README:105 "打包未在本机实测"（实际构建过，问题是产物过期）
5. README:86 "详见交付报告"（无此文件）
6. README:84 "档案编辑页/规格01 全部文件格式已按规格实现"（夸大）
7. README/AGENTS 未提 `scripts/e2e_test.py`、`scripts/e2e_stage2.py`（真实 LLM、写真实数据、会被误跑）
8. spec04:59 "G1-G5 四条"（实为 5 条）；AGENTS.md:3 "仅 5 个包"（实有 pydantic）

> **重要边界**：README「与规格的对应与偏差」里已列出的 9 条（原文档已修正、13 槽位对齐、状态机补充迁移、崩溃恢复停靠、FTS book_id、rebuild 保留历史、黄金三章走 W3、rebuild 状态合并、前端 Vue3 重写）**都是已定案的有意决策，不是 bug**。本报告未把它们列为问题；其中第 9 条（Vue3 重写）已核实与代码一致。问题只出在**该登记而未登记**的 2 条架构偏离（Tauri / Gemini 原生协议）。

---

## 8. 附录：本报告的一手证据索引

**命令类**
- `python -m pytest tests/` → `104 passed, 3 warnings in 16.00s`
- `python scripts/smoke.py` → `ALL SMOKE OK`（复跑两次一致）
- `python -c "import sqlite3; ..."` → `sqlite 3.49.1` / `trigram: OK`
- `git check-ignore -v data/settings.json books/流程测试书/meta.md web/node_modules web/dist` → 分别命中 `.gitignore:7 / :9 / :11 / :4`
- `git ls-files data books` → 只有 `data/.gitkeep`、`books/.gitkeep`
- `git grep -E 'sk-[A-Za-z0-9]{16,}'`（仅 tracked 文件）→ 0 命中
- `grep -rn 'Tauri'` → 仅 `docs/开发计划.md`
- `grep -rn 'regress|baseline|回归|案例|一键重跑'` → 仅 `characters.py:143`（无关）
- `grep -rn '"T1"' ainovel/ web/src/ scripts/` → 0 命中
- `grep -c '@app\.' ainovel/api.py` → 61 个路由

**隔离实验**（全在 `tempfile.mkdtemp()` 的 `AINOVEL_HOME` 下，不触碰仓库数据）
- A 设置掩码往返 → 真 key 变占位符
- B G4 采纳 → `created: []`，关系网丢失
- C G5 变量 → `404 未提供变量 ['世界观摘要', '角色卡摘要']`
- D W2/W4/W5 调用计数 → 2 次调用 / 落盘 10 字
- E 裁决护栏 → `裁决队列非空（3 条待处理），禁止开新章细纲`
- F 定稿章节复检 → 001/002/011 各有 2-3 条阻断

**只读数据检查**
- `data/ainovel.db`（mode=ro）：chapters 11 行、snapshots 8、generation_log 96（ok 94/error 2、cost 全 0、88 条 gemini-3.8-flash-high）、review_records 28（audit 0）
- `data/conflicts.json`：29 条，3 open（new_setting）
- `data/assembly_previews/`：11 个 JSON；`data/failed_outputs/`：3 个
- `books/流程测试书/`：30 章文件 + 10 角色 + 1 审计报告 + style 语料/特征卡

**未执行（遵守任务约束）**
- `python run.py` —— 会写真实 `books/` `data/` 并开浏览器
- `python scripts/e2e_test.py` / `e2e_stage2.py` —— 真实 LLM 调用，耗 token 且写真实书
- 任何对源码/文档/测试的修改 —— 本报告是本次审计唯一写入的文件（`.audit/04-milestones.md`）
