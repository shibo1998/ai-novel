# AI 网文创作工作台

依据 `docs/specs/` 规格01—10实现的本地单机工具：文件档案 + SQLite 索引/状态机 + 24 个提示词文件（历史规格称“23条”，ID集合未增删）+ 单章闭环与卷级自动出稿 + Vue3 Web UI。规格10已完成离线工程验收；真实副本、真模型对照与文学质量判读仍待单独授权。

## 快速开始

```bash
pip install -r requirements.txt
cd web && npm install && npm run build && cd ..   # 构建前端（产物 web/dist/，首次或前端变更后）
python run.py            # 打开 http://127.0.0.1:8765
```

首次使用：进入 **设置** 页填 API key 与 base_url（OpenAI 兼容协议，官方/第三方中转站通用）→ 可选填「默认模型」一键覆盖全部档位 → 点"测试连接"。

## 作者三步（规格10）

书架建书后进入**卷工作台**：填一句话故事、本卷要求与章数（默认10，1—50）→ **准备本卷**；通读整卷细纲和档案差异，一次人工确认 → 自动逐章出稿、检查与有限修复；最后逐章阅读，标记通读并选择连续章段 → 二次确认人工定稿。

**没想法也能开始（规格11）**：新书首卷第一步可点“AI 给我 3 个故事方案”，自动沿用建书时已选题材；偏好可空，候选可比较、换一批或选用。选用只填写一句话故事和本卷要求，不改书名/题材/章数，不自动准备或确认；你可继续改，再手动准备本卷。已有卷/章节或未结束任务不在此换整本故事。候选只在本页，停止等待/网络未知可能仍产生费用，重试需显式确认，刷新不自动再调用。详见 `docs/规格11实施验收.md`。

任务完成只表示**待审稿**完成，不等于定稿。暂停/恢复与刷新找回都以服务端文件任务为准，不自动重发未知模型请求；敏感词、越权修复、裁决队列和失效来源会停下。追加本卷与新增下一卷是独立操作，不能覆盖旧稿或重排章号。旧分层规划、档案编辑和单章工作台保留高级入口。详见 `docs/使用手册.md` 与 `docs/规格10实施验收.md`。

## 第三方中转配置（OpenAI 兼容）

只支持 OpenAI 兼容协议（`/v1/chat/completions`），官方 API 和 one-api/new-api 类中转站填法相同。两个入口任选：

**入口 A：设置页（推荐）**——「API 连接」面板填三格：API key、base_url（**到 `/v1` 为止**，如 `https://your-relay.com/v1`，不要带 `/chat/completions`）、默认模型（中转站实际支持的模型名，如 `gemini-2.5-pro`），点 保存连接 → 测试连接。

**入口 B：直接改文件** `data/settings.json`：

```json
{
  "providers": {
    "openai": { "api_key": "你的中转key", "base_url": "https://your-relay.com/v1" }
  },
  "default_model": "gemini-2.5-pro"
}
```

**模型名解析顺序**：`default_model` 非空 → 所有档位（plan/prose/polish/review/longctx）全用它；留空 → 用各书 `books/<书名>/config.json` 的 `model_stages`（按档位区分模型时用这个）。

## 测试

```bash
python -m pytest tests/          # 全绿为验收线；管线全链路用 MockLLM，不耗 token
python scripts/smoke.py          # 端到端冒烟（跑在临时目录，不污染仓库）
cd web && npm run typecheck      # 前端严格类型检查（vue-tsc）
cd web && npm run lint:check     # 前端 ESLint 检查
cd web && npm run test:offline   # 前端离线状态/交互回归，不启动服务
cd web && npm run build          # 生成 web/dist，禁止手改构建产物
```

> ⚠️ **`scripts/e2e_test.py` 与 `scripts/e2e_stage2.py` 不是测试，别随手跑**：
> 它们打真实 LLM（消耗 token）、写真实的 `books/流程测试书` 与 `data/`，
> 且 `e2e_test.py` 开头会 **DELETE 已存在的同名书**。它们是留档的历史全链路验证脚本，
> 只在明确知道后果时手动执行。日常验证请用上面的 pytest + smoke。

## 单章闭环（规格03 §1）

```
细纲生成(P-G6) → 人工确认关 → 上下文装配(13槽位) → 正文生成(P-W1/W3)
  → 机械检查(M-01~M-11) → [阻断→P-V5→重查] → LLM审校(P-V1五维)
  → [fix→P-V5→重查重审 ≤2轮] → 人工定稿关(二次确认) → 快照 → 记忆卡回写(P-V4)
  → (卷末) 跨章审计(P-V6)
```

- 敏感词（M-04）命中：阻断且**禁止自动改写**，直接转人工（红线必须人审）
- 台账/档案冲突进**裁决队列**，队列非空禁止开下一章细纲
- 裁决冲突检测：境界倒退（按 Bible 力量体系顺序）、死者复现、未知角色、非法伏笔操作
- V5 越权护栏：未涉及问题句段改动率 >10% → 丢弃输出，转人工走完整重生成
- 所有 LLM 调用记 `generation_log`（prompt_digest / tokens / cost_cents），豁免必填理由入 log

## 目录结构

```
ainovel/            Python 包（核心引擎 + FastAPI）
  paths/settings    路径与全局设置（data/settings.json）
  atomic/frontmatter 原子写 / YAML frontmatter schema 校验（失败拒绝写回）
  db/fts            SQLite 5 表 + FTS5（trigram，不可用自动降级 bigram）
  books/bible/characters/outline/chapters  档案读写（规格01 §2 全部格式）
  prompts           提示词库（digest/书级覆盖/〖变量〗严格渲染）
  mechcheck         机械检查 M-01~M-11（lint 词库同源）
  statemachine      章节状态机（规格01 §4）
  assembler         13 槽位装配器（预算截断顺序 + 预览落盘）
  llm/mockllm       OpenAI 兼容客户端（官方/中转站通用，Gemini 等模型一律走兼容协议）+ 测试 Mock
  pipeline          管线编排（细纲→正文→机检→审校→定稿→回写→卷末审计）
  production/productionapi  文件卷任务、联合确认、恢复与顺序人工定稿
  volumeproduction/draftcontext  候选投影、自动逐章闭环与隔离临时记忆
  conflicts         人工裁决队列
  api               全部 HTTP 路由 + SSE
prompts/            24 个提示词文件（文件化，可整体替换、书级覆盖）
lint/               禁词/敏感词/对话后缀禁式（每行一个，# 注释）
web/                前端 UI（Vue3 + Vite 源码；构建产物 web/dist/ 由 FastAPI 静态挂载）
books/<书名>/       数据：档案 + 章节 + config.json（单一真相源）
data/               SQLite、设置、裁决队列、装配预览（可随时重建）
tests/              pytest（全离线；当次数量见实施验收报告）
```

## 与规格的对应与偏差（重要）

**已按规格实现**：规格01 全部文件格式/表结构/状态机/防损规则；规格02 提示词组织/版本/digest/温度总表/词库同源；规格03 管线各环节/机械检查/异常处理/成本/断点恢复/rubric锚点；规格04 页面清单（书架/书总览/档案编辑/章节工作台/裁决/设置/导出）。

**偏差与修正（详见 `.audit/README.md` 审计报告）**：

1. ~~【豆包】【全集】原文档不在仓库~~ **已修正（2026-10-25）**：两份原文档一直在 `docs/` 根目录（初版实现时误判缺失，因只检查了 `docs/specs/`）。**23 条提示词已全部按原文替换/融合**：S0/S1/S2=豆包系统层原文；G1=T2、G2=T3、G3=T4、G4=【全集】提示词5、G5=T5、G6=T6+T6-B、T1=T1+T1-B；W1=T7、W2=T7-B、W3=T7-C、W4=T7-D、W5=【全集】提示词9；V1=T9-B七项+提示词10+rubric；V2=T8-A七步+T8-B对照表；V3=T8-C清单；V4=T9-C；V5=新写（依据§7.4，规格02 明示为唯一新写）；V6=T9-D；F1=T10-A、F2=T10-B；lint 禁词表=S1 词表+T8-B 左列
2. ~~上游《开发计划.md》缺失~~ **已修正（2026-10-25）**：文档在 `docs/开发计划.md`。13 槽位已按其 §2.3 原文表对齐（系统角色/全局硬规则/题材适配/Bible条目/力量体系行/角色状态卡/卷节奏/活跃伏笔/前情摘要/前文检索/上章末尾/细纲合同/输出要求）；角色卡按规格03 保持永不截断（开发计划的 ≤150 字×≤4 人预算被规格03 覆盖）
3. 状态机补了图上未画但语义必需的迁移：`mech_check 修复一轮仍阻断→human_review`、`llm_review pass→pending_finalize`、`outline_draft 重新生成细纲`
4. 章节中段瞬态崩溃后的恢复停靠点：`generating 中断→mech_check`（重跑机检即可续）、文件损坏→human_review
5. FTS 表加 `book_id` 列（多本书共存必需）；trigram 模式下 2 字关键词（角色名）走 Python 子串兜底
6. rebuild 保留 generation_log/snapshots/review_records（运行历史不在文件里，DB 文件丢失则该历史不可恢复）
7. 前 3 章直接走 W3 黄金三章专项提示词（规格02 P-W1 改造点3 的等价实现）
8. 卷首新建章节的 `pending_finalize` 与 `mech_check` 在 rebuild 时不可区分 → 统一停靠 mech_check
9. **前端 UI 技术栈重做定案（2026-10-26，已实施）**：初版按规格04"免构建"意图以 vanilla JS/CSS 交付，后定案重写为 Vue3 + TypeScript + vue-router + pinia + element-plus + vite + sass + tailwindcss（补充项与共存约束见规格04 §0）。迁移原则：只换前端壳，后端 API 契约 / 页面清单 / 人工确认关不变；构建产物 `web/dist/` 由 FastAPI 静态挂载，PyInstaller 分发对象同步改为 `web/dist`。Vue3 版已上线替换 vanilla 版（`web/` 为 Vite 工程根）；导出从 GET `window.open` 改为 POST blob 下载（修正旧版 405 隐患）。
10. **P0 缺陷修复（2026-10-26，见 `.audit/README.md` 审计报告）**：三处新增状态机出边 + 四项数据/契约加固，均为「规格要求但原实现会导致用户卡死或静默丢数据」的修正，不是行为变更：
    - `generating --fail--> error`、`generating --abort--> outline_locked`、`finalized --unlock--> pending_finalize`。原实现 `generating` 只有 `complete` 一条出边，一次 LLM 网络抖动就让该章永久锁死（`retry` 事件非法、UI 无入口），只能删库——规格03 §3 要求"仍败 → error 状态，UI 可一键重试"。`pipeline.generate_prose` 同步加异常兜底，失败必落 `error`。
    - `human_rewrite` 改为**先校验状态、先取正文来源，最后才落盘**；`base_version=None` 时沿用当前正文开新分支（原来写空 body 覆盖同一路径，human_review 恰好无快照，一点即清空全文）。
    - 档案写盘统一剥离读接口专用字段（`outline.write_outline` 剥 `_body`/`_rhythm`，`characters.write` 剥 `_file`/`_body`）；`PUT /outline` 未提供 `_rhythm` 时沿用文件里已有的「爽点节奏总谱」（原来每次保存都被重置为「（待规划）」）。
    - `settings.update` 拒绝把 UI 掩码占位串当真实 api_key 落盘（原来二次保存会永久写坏真 key，且 `has_key` 仍为 true，UI 无感）。
    - 回归用例见 `tests/test_p0_regressions.py`（16 项）。
11. **桌面外壳：Tauri 2 → FastAPI + 浏览器（有意决策，补登记）**：开发计划 §0 原定 Tauri 2 + Vue 3 桌面应用，实际交付为本地 FastAPI 服务（`run.py` 起 uvicorn 并自动开浏览器，127.0.0.1:8765）。依据：开发计划第 6 行「冲突时规格文档优先」，而规格04 全文无 Tauri 要求、只约定"本地单机、零云部署"；单进程 Python 后端 + 静态前端已满足该约束，且省掉 Rust 工具链与跨端打包维护成本。分发形态是 PyInstaller 单目录 + exe。
12. **Gemini 走 OpenAI 兼容协议，不实现原生适配器（有意决策，补登记）**：开发计划 M0 原列「DeepSeek（OpenAI 兼容）+ Gemini（原生协议）两个适配器」，实际统一为 `OpenAICompatClient` 单实现（`ainovel/llm.py`）。依据：主流中转站（one-api/new-api 等）与 Gemini 官方 OpenAI 兼容端点都支持 `/v1/chat/completions`，单实现即可覆盖，减少一套流式解析与重试逻辑的维护面；代价是失去 Gemini 原生独有参数（如 safetySettings 细粒度控制），当前用不到。
13. **P1 缺陷修复（2026-10-26）**：rubric 守门由程序强制（任一维 <70 或存在 blocking → 必 fix，模型不能把结果判得更松）；定稿后正文受快照保护（`/fix` 不再能静默改写 finalized 章节）；流式生成中途周期性落盘半成品草稿 `<no>_正文.draft.md`（崩溃可续，规格03 §5）；装配器截断顺序按规格原文校正为三项（`fts_recall → recaps → bible_core`）并补齐逐槽 token 估算；V6 审计结论写入 `review_records(kind='audit')`；未配置单价的模型首次调用记告警日志（不再静默 0 成本）。回归用例见 `tests/test_p1_pipeline.py`。
14. **P1 API 契约与档案层修复（2026-10-26）**：G5 大纲采纳改为**合并式**（按内容匹配保留原编号与原状态，未提及的伏笔行一律保留，不再从 FO-001 重编覆盖人工台账）；G4 关系网真正写回各角色「关系网络」节（同「对象」行已存在则不覆盖人工内容）；新增 `PUT /chapters/{no}/prose`（人工手改正文，`pending_finalize` 态改完强制回退 `mech_check` 重跑机检，`finalized` 一律拒绝）、`GET /chapters/{no}/snapshots/{version}`（快照正文回看）、`PUT /locations`（地点档案可编辑）、`POST /export/md`（Markdown 合并导出）、`GET /books/{book}/audits`（卷末审计报告回看）；`adopt_outline` 不再用建书占位串覆盖用户已填的 logline/ending_direction。回归用例见 `tests/test_p1_archive_api.py`。
15. **稳定性修复 / 技术债清理（2026-10-26）**：Bible 固定表格节写回时校验表头（力量体系 8 列、隐藏规则 3 列、术语表 2 列）——原先三个列常量定义后零引用，列名写错会让 `power_rows()`/`realm_order()` 返回空、**境界倒退检测静默失效**，属于"不报错的失效"；G6 细纲 `plant` 操作补 FO 编号格式校验（原先任意字符串可入台账）；W2/W4/W5 场景专项补齐 `〖字数区间〗` 硬约束（原先只给 W1/W3 传，三个专项被当整章调用却无任何字数约束）；`db.rebuild()` 的 UPSERT 补 `status` 更新（原地重建现在能按文件真相修好已污染状态）；`@app.on_event` 迁移到 `lifespan`（原 API 已被 FastAPI 弃用）；`settings.DEFAULTS.prices` 补中转站常见模型别名。回归用例见 `tests/test_p2_stability.py`。
16. **定稿返工链路补齐（2026-10-26，文档撰写时发现）**：`statemachine` 早有 `finalized --unlock--> pending_finalize` 边、`/fix` 与 `/prose` 的拒绝文案也让用户"先解锁"，但**既没有 unlock 路由也没有 UI 按钮**——用户被指向一个不存在的地方，定稿返工实际是死路。现补 `POST /chapters/{no}/unlock`（状态回退 + 同步把正文文件 `status` 从 `final` 改回 `draft`，否则文件自带的 final 标记仍会让下游认定它已定稿）+ 工作台「解锁返工」按钮；同时 meta 表单的「单章字数区间」保存时同步写入 `config.json`（管线只读 config，原先在 meta 改这一项完全不生效）。回归用例见 `tests/test_p1_archive_api.py::TestProseEdit`。
17. **续修三处（2026-10-26 下午）**：① `finalize` 前端改传 `timeout: 0`（原先 30s 默认超时，而该接口含 V4 记忆卡回写 + 末章 V6 审计两次 LLM，真实模型必超时 → 前端假报错、后端已定稿）；② 新增只读端点 `GET /books/{book}/plot_points/overdue` + 书总览「伏笔台账」常驻预警条（`planted` 超 30 章未推进；原先只有 G6 落盘那一刻一次性回 warns，刷新即消失）；③ **Bible 表头校验改为归一化比对**——真实书 `隐藏规则` 写作 `隐藏等级(低/中/高)`，原严格全等判定会误报"缺列"，导致该书**任何一节的圣经保存都被拦死**（校验本意是拦列名漂移到别的语义，不是拦写法差异）。归一化只忽略括号补充／markdown 强调／空白与全半角括号差异，`隐藏级别` 这类真漂移仍报错。回归用例见 `tests/test_p1_archive_api.py::TestOverdueForeshadows` 与 `tests/test_p2_stability.py::TestBibleHeaderValidation`。
18. **全项目审查修复（2026-10-27，详见 `docs/审查修复批次.md`）**：审校输出强校验、审校与定稿绑定当前机检指纹，正文/润色/修复后旧结论失效；人工豁免绑定正文且不放行未生效检查。同书写入使用进程内互斥和正文修订号（不支持多进程协同写库）。记忆卡先抽取再应用、异常回滚原档案与索引；定稿回写失败保留 finalized 并提供补做入口。C-01 润色保真检查、C-05 状态卡提议/人工确认已落地；未确认提议不覆盖 `STATE.md`。双时间线两张表可维护且非法输入拒绝写盘。外挂摘除保留历史，rebuild 扫内置/外挂并清孤儿章节、恢复记忆卡状态；导入保留原章号且拒绝覆盖已有稿件。上传校验书名与路径边界。前端旧页面保存冲突保留编辑内容。
19. **生成文风与安全去 AI 味（规格06，2026-10-27）**：去除固定文体配额，正文与运行元数据分离，实际 system/user、组合请求摘要与预算可回看；书级文风开关贯穿写作/长度调整/审校/润色，范例按完整段落确定性选择。F1只生成未保存草案。M-12/D系列保留历史标签但仅作建议，不单独阻断或触发自动修复。`POST /polish` 明确改为只生成候选：V2定点补丁、程序保真和V3原稿/候选对照均通过后，仍须人工差异预览采纳；过期双指纹、并发修订与恢复标记保护正式稿。旧V2/V3覆盖必须显式升级接口，不静默替换。现有保真阈值、敏感词人工处理、V5护栏和人工定稿关保持；实现与离线验收见 `docs/规格06实施验收.md`，真实模型读感效果另行试验。
20. **前端体验整改（规格07，2026-10-28）**：档案、词库与 G5 采纳改为强 ETag + `If-Match`（412 `RESOURCE_CHANGED` 保留本地输入）；工作台/总览增加 `planned/exists/planning_error` 预检及 `OUTLINE_ROW_MISSING`/`OUTLINE_INVALID`/`CONFLICTS_PENDING`/`STATE_NOT_ALLOWED` 稳定错误。书列表与总览共用文件派生进度（定稿章/规划章、定稿字数、最后编辑时间），损坏返回 `null + warning`，不新增 DB 列。冲突裁决仍为三种 action，但 `manual_value` 按领域类型真实写回并核对，成功回执 `applied/kept/acknowledged`；写失败不清队列，回执失败可幂等重试；新增 `allowed_actions/manual_schema` 能力字段。前端收敛为唯一档案编辑器与总览只读导航，补齐深链高亮、行号映射、SSE 缓冲、任务停止与错误聚合、脏数据离开和批量逐项结果。状态机、人工关、M-04/V5 红线、提示词、档案格式与 SQLite 表结构均未改；实现与离线/临时实例验收见 `docs/规格07实施验收.md`。
21. **G5 采纳影响预览（规格08 阶段A，2026-10-28）**：新增**纯计算**端点 `POST /api/books/{book}/preview/outline`，返回旧/新 OUTLINE 与 PLOT_POINTS 全文、已有正文真实章数、受影响章号交集、增删改章号与伏笔增改数；不写档、不记凭据、不调模型，因此不要求 `If-Match`（并发保护仍由 adopt 的 ETag 负责）。"已有正文章数"改为按 `chapters/*_正文.md`（含未定稿与 `.draft.md`）真实章号去重统计——原前端按章节总表的「状态」列统计，而该表七列里没有状态列，恒得 0。解析与伏笔合并抽到 `ainovel/g5impact.py`，`adopt_outline` 与预览共用，保证 diff 里看到的就是将要写入的内容；`outline.render_outline/render_plot_points` 同一渲染。读不出来的文件返回 `null` + 中文 warning，不假计 0。候选被编辑后旧预览作废，须重建差异才能采纳。验收见 `docs/规格08实施验收.md`。
22. **内容规划数据层（规格08 阶段B 第一步，2026-10-28）**：新增 `ainovel/planning.py`，只做 OUTLINE.md 里**可选** `planning` 元数据的解析校验、分层内容指纹（`project_global/volume/unit/chapters` 集中定义 + 规范化 JSON SHA-256）、四层就绪状态（`missing/unadopted/ready/stale/invalid`）、估算与保护集。缺失 `planning` 的书一律 legacy，读写都不替它启用、不自动采纳；未知 `schema_version` 报 `PLANNING_SCHEMA_UNSUPPORTED` 而不降级为 legacy；就绪状态是**规划读模型**，不进 `statemachine.STATES`，也不新增 DB 表列。用例 `tests/test_spec08_planning.py`（10 项）。
23. **内容规划服务层/提示词/页面（规格08 阶段B 收口，2026-10-28）**：补齐五端点 `GET /api/books/{book}/planning` + `POST .../planning/enable|generate|preview|adopt`（强 If-Match，缺则 428；稳定错误码 `PLANNING_*` → 422/409/404/428；影子合并预览纯计算，采纳重算预览比对 `preview_digest` 并写后读回核对；单元 ID 用 uuid5 确定性派生，预览/重预览/采纳复现）。rolling 书上 G5 全量生成/采纳、`PUT /outline`、`adopt_outline` 一律 409 `PLANNING_SCOPE_REQUIRED`（legacy 的 G5 旧调用由服务端补默认变量，行为不变）；建书显式 `planning_mode: legacy/rolling`。保护集（已有正文/记忆卡/已确认细纲）拦截章行改写。G5 v2→v3（legacy 四部分输出不变；rolling 只输出 `{scope,target,data}` 严格 JSON 包络）、G6 v4→v5（新增「所属剧情单元/本章规划边界」，装配当前单元闭包）；`next_actions` 逐卷推进不越过前卷。前端新增 `/book/:name/planning` 规划页（树+批次+候选可编辑+预览 diff+人工采纳）与书架/总览/工作台/菜单入口；element-plus 补中文 locale（确认框不再显示 Cancel/OK）。legacy 书与真实数据零写入。验收见 `docs/规格08实施验收.md` §10。
24. **规划层境界口径硬约束（2026-10-29，真模型对比暴露后修）**：`§6.4` 双 arm 实跑发现——G5 章纲作用域没有任何境界口径约束，模型把「以凝煞境化煞入水」写进章纲行，rolling 方案对该行更忠实，导致正文出现「凝煞境」而角色卡/状态快照仍为「感气境初入」，被 V1 审校扣 plot 分。现将两条硬约束注入 G5 的「规划上下文」（volume/unit/chapters，global 不注入）：①境界名只能取 `STORY_BIBLE` 力量体系表内的名称；②已生效境界取 `STATE.md` 最近快照，取不到只给清单不猜，并禁止越级/禁止叙述未发生的突破。只改 `planning.generate_variables` 的变量装配，不动提示词文件，**G5 仍为 v3**；回归 `test_g5_variables_carry_realm_guard`。验收与两轮对比数据见 `docs/规格08实施验收.md` §12/§13。
25. **W1 加「上限」而非模板（2026-10-29，用户读稿反馈「B 版更好但有 AI 味」）**：实测对比章的 AI 味是可量化的——≤8 字成单句段占 29%、段落均长 24 字、无 80 字以上段落、比喻词 5.1/千字（人类常见 ≤2）、数字报点 3 次。W1 v6 的【表达】只有「不强制固定停顿」、没有任何上限，模型便一路顶着上限写。**W1 v6→v7** 新增【上限（不是模板，是天花板）】四条：连续 ≤10 字短段 ≤3 段、每千字比喻 ≤2 处且喻体优先取本书设定内事物、一名词一修饰语、全章数字节拍 ≤2 次。实测同章重写：比喻 5.1→3.5/千字、短段 29%→25%、≥60 字段落 3→6、数字报点 3→1、五维 470→476，但破折号 4→7、机检 warn 11→16（规则互相挤压）。另确认真实 `polish` 对该章返回 `NO_CHANGE`（V2 只改问题句段，兜不住段落节拍/比喻密度这类结构特征）。详见 `docs/规格08实施验收.md` §14。
26. **候选范例与透明计量（规格09 A+B，2026-10-29）**：复用书级文风链路，提供六组可追溯手选候选与完整范围装配回归；不会自动采纳卡、扫描全局素材或启用真实书。deslop结果追加独立`metrics`，原工作台显示空行段落均长/中位数/P90/短段占比与全文显式比喻线索密度。旧D-14保护区、score/hits/惩罚、机检/模型摘要不变，新统计不扣分、不阻断、不触发自动改稿，也不进入模型提示。词表缺失/非法显示不可用，不假计0；旧结果缺字段兼容。W1及其他提示词本批不改；真实书应用、副本回归与同章真模型对比仍须另行授权，离线通过不代表小说质量已改善。验收结果见 `docs/规格09实施验收.md`。
27. **卷级自动生产与墨韵式工作台（规格10，2026-10-30）**：默认入口改为作者三步，一次人工动作联合采纳整卷候选并确认全部细纲；不是后台自动确认。文件任务保存准备缓存、检查点、临时上下文、依赖指纹与提交回执，重启活动阶段只标中断，不自动再次计费。后续章使用真实前文与有来源的本批临时事实，生成期间不写正式角色/状态/伏笔/记忆/FTS；成功章停在待定稿，通读与顺序定稿仍由用户明确触发。追加/新卷保持历史编号与保护集，来源改动令受影响后缀失效；确认/定稿故障可核对恢复，不覆盖第三方内容、不重调已完成V4。G5/G6为v4/v6，现有提示词ID集合和W1 v7句子规则/路由/温度不变。M-04、V5>10%、队列和未生效门禁保留；文风metrics不加入自动润色。离线与临时Mock浏览器验收见 `docs/规格10实施验收.md`；真实书副本、真模型对照与作者读感未授权，不宣称质量改善。
28. **同题材AI故事候选（规格11，2026-10-30）**：新书首卷准备前无需先有想法；自动沿用建书题材，T1 v3一次返回3份故事卡，显式换批与选用由作者决定。选用仅填写logline/requirements，不改正式书名/题材/章数，也不创建卷任务。生成前后与选用前核对上下文版本，坏配置/档案/候选拒绝，旧T1报告默认兼容，旧书覆盖需人工升级。真实兼容客户端本入口禁止隐式重试且要求完整SSE结束；未知请求显式确认再次费用后才重试。同书互斥、跨书晚到、替换期间改输入、卸载和任务响应性有回归。正式文件/DB/章节状态与W1/G5/G6不改，工程证据见 `docs/规格11实施验收.md`；实际故事质量、真实副本与真模型对照未授权。
29. **多模型编排（规格12，2026-10-30）**：修掉「模型定死」——`pipeline._call` 与 `assembler.render_request` 原先各写一遍 `default_model or model_stages[stage]`，全局默认模型压过书级档位（实测某书 `plan` 配的 deepseek-chat 一次未生效）。现统一为 `ainovel/models.py::resolve()` 五层下探（书级档位 → 全局档位 → 兜底 → 代码默认 → 常量），两处调用点共用，装配摘要与实际请求的模型名由测试锁定一致。`settings.default_model` 降级改名为 `fallback_model`（旧键读时迁移、首次保存删除，幂等），新增全局 `model_defaults` 五档。建书接口支持 `model_stages`，建书弹窗可选规划/写作模型；书级配置页五档改下拉并显示**生效来源**；新增 `GET /api/models` 拉中转站模型清单（不可达返回 `ok:false`，前端退回手填，不阻断）。`generation_log` 增 `provider` / `model_source` 两列。`GET /config` 的 `model_effective` 是只读派生字段，`PUT` 落盘前剥离，不写入 `config.json`。**行为变更**：既有书的书级 `model_stages` 开始真正生效。本批不做多端点、提示词级覆盖与自动故障转移，理由见规格12 §0.2；真模型 1 章对比待授权。
30. **多接口编排（规格13，2026-10-31）**：`settings.providers` 支持多个接口，每个有自己的 `base_url` / `api_key` / `label`；档位值写 `端点名@模型名`（如 `backup@gemini-3.8-flash-high`）即走对应接口，无前缀仍用默认接口 `openai`，**旧数据零迁移**。分隔符取 `@` 而非 `:`——中转站真实存在 `gemma4:31b`、`nemotron-3-nano:30b` 这类自带冒号的模型名，冒号会把它们误判成端点前缀；`@` 前必须是已配置的接口名才生效。`llm.client(provider)` 按端点选连接，`pipeline._call` 把 `provider` 传给客户端并写入 `generation_log.provider`（不再恒为 `openai`），批产检查点缓存键含端点避免换站命中旧缓存。价格先按 `端点@模型` 精确匹配、再回落裸模型名。设置页可增删改接口（默认接口不可删，删除走 `remove_providers` 显式列表，因为深合并删不掉键）；档位选择器改为「接口 + 模型」两段，存值仍是字符串。**密钥只存 `data/settings.json`，绝不写进书目录**。不做自动故障转移（静默换端点会让成本与可复现性失真），理由见规格13 §0.2。

## M0 检查单（规格04 §4）状态

| 项 | 状态 |
|----|------|
| DeepSeek 连通 | ✅ 代码就绪（OpenAI 兼容流式），设置页填 key 后"测试连接"验证 |
| Gemini 连通 | ✅ 代码就绪（统一走 OpenAI 兼容协议，见偏差 11；base URL 可改走代理/中转） |
| FTS5 trigram 中文检索 | ✅ 已实测（SQLite 3.49.1 trigram 可用；bigram 降级路径也有实现与提示） |
| Windows 打包 exe | ✅ 已重打（2026-10-26 15:16）并实测启动：`dist/ai-novel/ai-novel.exe` 内含 Vue3 `web/dist`、当前版本 prompts/lint，`GET /` 与 `/api/prompts` 均 200。注意 `dist/`、`build/` 在 `.gitignore` 里，重新构建不会进版本控制 |
| 单章成本可见 | ✅ 已接通（单价表含 deepseek/gemini 常见档位；中转站自定义模型名需在设置页「单价」补一行，否则该模型成本按 0 计并在服务端日志记告警） |

打包：`pip install pyinstaller && pyinstaller ainovel.spec`，然后把 `prompts/ lint/ web/dist` 拷到 `dist/ai-novel/` 下（数据目录 books/data 自动在 exe 旁创建）。
**注意 `dist/` 与 `build/` 都在 `.gitignore` 里**，重新构建后它们不会进版本控制；`web/dist` 由 `npm run build` 生成，同样被忽略。

## lint 词库说明

`lint/*.txt` 为**种子词表**（原文档 S1/T8-B 不在仓库）。设置页可在线编辑，与 S1 提示词同源注入——改完词库后新生成的正文即受新词表约束。
