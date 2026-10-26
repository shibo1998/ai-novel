# 审计 02 · M2 单章闭环管线（规格 03 全部条目）

> 审计对象：`docs/specs/03-管线规格.md`（唯一真相源）+ 规格 02 中与本管线直接相关的 P-G6/P-W1/P-V1/P-V4/P-V5 改造点
> 审计方式：只读。逐函数读实现 + grep 调用点 + 在临时目录（`AINOVEL_HOME` 指向 `%TEMP%`）用 MockLLM 实跑验证关键分支。未改动仓库任何源码/规格/测试。
> 基线：`python -m pytest tests/` → **104 passed**（与 lead、04 报告一致）；`python scripts/smoke.py` → **ALL SMOKE OK**。
> 交叉印证：lead 独立实测的下列结论已并入本报告——rubric<70 不强制 fix、崩溃不保留半成品、无「章内全自动」编排入口、V6 不入 review_records、M-01~M-11 实现完整。

判定图例：✅实现 / ⚠️部分实现 / ❌未实现 / 🔶有偏差 / ❓无法确认

---

## 1. 管线总览（规格03 §1，第 7-12 行）：各环节是否都接通

| 规格条款 | 判定 | 证据 | 说明 |
|---|---|---|---|
| 细纲生成 P-G6 | ✅ | `ainovel/pipeline.py:85-180`、`api.py:533-539` | 7 项输入（大纲行/卷目标/上章钩子/角色状态/活跃伏笔/章号/总章数）齐全 `pipeline.py:95-155`；`api.py` 有 POST 路由 |
| 人工确认关 | ✅ | `pipeline.py:275-277`、`chapters.py:94-98`、`api.py:542-548` | 置 `confirmed: true` + 迁移 `outline_locked` |
| 上下文装配 | ✅ | `pipeline.py:359-360`（正文前自动装配）、`pipeline.py:284-289`（预览） | 生成正文时自动 assemble 并落预览 |
| 正文生成 P-W1 | ✅ | `pipeline.py:345-410` | W1/W3 自动选择 `pipeline.py:366-367` |
| 机械检查 → [阻断→P-V5→重查] | ✅ | `pipeline.py:499-537` | 阻断→V5 一轮→重查；仍阻断→human_review（实测见 §5） |
| LLM 审校 P-V1 → [fix→P-V5→重查重审 ≤2轮] | ✅ | `pipeline.py:679-772` | 实测 2 轮后 `fix_rounds=2`、转 human_review |
| 人工定稿关 | ✅ | `pipeline.py:904-915`、`api.py:660-665` | 后端要求 `pending_finalize`；二次确认在前端 |
| 快照 | ✅ | `pipeline.py:917-919`、`db.py:175-189` | 定稿时写 snapshots（ON CONFLICT DO NOTHING，只增不改） |
| 记忆卡回写 P-V4 | ✅ | `pipeline.py:923`、`pipeline.py:938-998` | finalize 内同步调用，失败不改回滚状态（见 §8） |
| 卷末审计 P-V6 | ✅ | `pipeline.py:930`、`pipeline.py:1014-1069` | 卷最后一章定稿后自动触发（实测生成 `books/<书>/audits/卷1_审计.md`） |
| **「章内全自动」编排**（规格01 §4 原文：generating→mech_check→llm_review→targeted_fix 循环内无人工动作） | ❌ | 全仓库无编排函数：`pipeline.py` 顶层 `grep '^def '` 只有单步函数（85/275/284/345/499/679/904…），无 `run_chapter()` 之类；前端 `WorkbenchView.vue:536`（机检按钮）、`:564`（审校按钮）、`:620`（定稿按钮）均为人工点击 | **缺**：没有任何把 mech→review→fix 串起来的入口，每一步都要人点按钮。**影响**：与规格"章内全自动"直接冲突；用户跑一章要点 5-7 次按钮，且中途忘记点就永远停在中间态（见 §12 卡死问题） |

---

## 2. 细纲确认关（规格03 §2.1，第 16-19 行）

| 条款 | 判定 | 证据 | 说明 |
|---|---|---|---|
| 程序校验：FO 编号合法 | ⚠️ | `pipeline.py:258-273`（`_validate_contract_ops`）、`:166`（调用点） | `advance/pay` 会查台账存在性与 `pay 要求 building`（`:268-272`），**但 `plant` 在 `:266-267` 直接 `continue`，完全跳过校验，且全代码无 `FO-\d{3}` 格式校验**。实测：让 G6 返回 `foreshadow_ops: [{op: plant, fo_id: "随便写的编号"}]`，`generate_outline` **接受并写盘**（无报错）。**影响**：G6 可凭空造任意编号伏笔，后续 V4 回写台账时该编号与台账格式不一致，`apply_foreshadow_ops` 的 advance/pay 会报"台账中不存在"并进裁决队列 |
| 程序校验：钩子类型与上章不同 | ✅（有盲区） | `pipeline.py:259-261` | 与 `prev_hook_type` 比较，相同则抛错进重试。盲区：`prev_hook_type` 取自上一章细纲，读取异常被 `except Exception: pass`（`pipeline.py:111-121`）吞掉→**上章细纲缺失时该校验静默跳过** |
| 失败→重试 | ✅ | `pipeline.py:161-174`（`for attempt in range(3)`）+ `:170` 落 `data/failed_outputs/` | 3 次未过→`mark_error`→抛 `PipelineError`。测试覆盖 `tests/test_pipeline.py:166-207` |
| 人工关口：UI 表单化展示 frontmatter，可编辑 | ✅ | `web/src/views/WorkbenchView.vue:439-486`（stage/emotion_task/钩子类型下拉）、`:129-152`（原文编辑）；`api.py:511-530` | 表单 + 原文双通道 |
| `confirmed: true` 进入锁定，锁定后正文阶段不接受细纲变更 | ✅ | `api.py:513-516`（非 draft 状态拒绝 PUT 细纲）、`chapters.py:94-98` | 锁定后改细纲接口直接报错并提示走「改细纲重写」 |
| 改细纲 = 回到 outline_draft | ⚠️ | `pipeline.py:853-854`、`statemachine.py:35`（仅 `human_review → outline_draft`） | 实测：`outline_locked`/`llm_review`/`pending_finalize` 调 `edit_outline` 全部 `StateError`；只有 `human_review` 可达。**且回到 draft 后文件里 `confirmed` 仍为 True**（实测），`db._derive_status` 只看 `confirmed`（`db.py:296-302`）→ **删库重建后该章会被推导回 `mech_check`/`outline_locked`，丢掉"细纲待改"的语义**。**影响**：用户改写细纲后一旦 rebuild，章状态跳回旧进度 |

---

## 3. 上下文装配器（规格03 §2.2，第 21-26 行）

| 条款 | 判定 | 证据 | 说明 |
|---|---|---|---|
| 13 槽位 | ✅ | `assembler.py:34-41`（`SLOT_TITLES`）、`:146-248` 逐槽构建；`tests/test_assembler.py:11-19` 断言 13 槽 | 与 `docs/开发计划.md:86-100` 表逐条对齐 |
| FTS query 构造（核心事件关键词+角色名+场景地名） | ✅ | `assembler.py:98-124`（`_keywords`：角色名 + `core_event` 切词 + LOCATIONS 命中地名 + 场景 goal/conflict/end_state），`:224-226` | |
| top 8 片段、每片 ≤150 字 | ✅ | `assembler.py:43`（`MAX_FTS_PIECES=8`）、`fts.py:113`（`snippet[:150]`） | |
| **超预算截断顺序 = 前文检索 → 最近章摘要 → Bible 条目** | 🔶 | `assembler.py:32` `CUT_ORDER = ["fts_recall","recaps","bible_core","active_foreshadows","prev_ending"]` | 前三项与规格一致；**多砍了两项规格未授权的槽位**（活跃伏笔、上章末尾）→ 预算紧张时会砍掉「活跃伏笔线程」和「上一章末尾原文」，而规格把这两项视为常规保留内容。**影响**：长上下文时伏笔/衔接锚点可能整块消失，正文容易跑偏 |
| **永不截断 = 力量体系行、出场角色卡、细纲合同** | 🔶 | `assembler.py:30-31` `NEVER_TRUNCATE = {system, system_rules, genre, power_rows, char_cards, outline_contract, output_rules, volume_rhythm}` | 规格点名的三项都在 ✅；但**额外把 `volume_rhythm`（卷节奏）和 `output_rules`（输出要求）也设成永不截断**，规格并未给这两项豁免。**影响**：预算超限时，本可牺牲的卷节奏段占用预算，迫使上面两项（伏笔/上章末尾）被砍——与规格的优先级意图相反 |
| token 估算：中文字符 ×0.75 | ✅ | `assembler.py:81-82`（`round(len(non_ws)*0.75)`）、`:258-260` 汇总 | |
| **逐槽估算值记录**（"装配结果与估算值记入 log"） | ❌ | `assembler.py:55-67` 预览里 `est_tokens: self.est_tokens.get(name, 0)`，但 `Assembly.est_tokens` 字段**全代码从未赋值**（`grep est_tokens` 只有定义与读取）；`generation_log` 无装配记录（`pipeline.py:58-64` 只记 tokens/cost） | 实测预览：13 槽每槽 `est_tokens=0`，`total_est_tokens=562`。**影响**：UI 折叠面板每槽显示 "0t"，用户无法判断哪个槽吃预算；规格要求的"估算值入 log"完全缺失，事后无法复盘跑偏原因 |
| 装配预览数据结构（UI 折叠面板可查） | ✅ | `assembler.py:55-67`、`pipeline.py:284-301`（落 `data/assembly_previews/<书>/NNN_vN.json`）、`api.py:551-556`、`WorkbenchView.vue:510-528` | 生成正文前也会自动落一份（`pipeline.py:360`） |

---

## 4. 正文生成 P-W1（规格03 §2.3，第 28-31 行）

| 条款 | 判定 | 证据 | 说明 |
|---|---|---|---|
| 流式输出 | ✅ | `llm.py:66-109`（SSE `iter_lines`）、`api.py:559-592`（SSE 转发）、`WorkbenchView.vue:185-246`（fetch reader） | |
| 超时 300s | ✅ | `llm.py:20`（`TIMEOUT_SECONDS=300.0`）、`:70`（httpx timeout） | 注意这是单次请求超时，非整章累计 |
| finish_reason 截断检测 | ⚠️ | `pipeline.py:389-398`（整章模式检测 `fr=="length"`→重跑）、`:443-444`（场景模式只记 `fr`） | 场景模式下即使 `finish_reason=="length"` **也不重跑**；实测场景模式截断时返回值 `finish_reason='length'` 但无任何补救，后续只能被 M-01 拦 |
| 输出落 `chapters/NNN_正文.md`（status: draft, version 不变） | ✅ | `pipeline.py:401-407`、`chapters.py:110-114` | version 取 `chapters.current_version`（`:401-402`） |
| 字数越界自动重跑一次 | ⚠️ | `pipeline.py:391-398`（整章模式 ✅，实测触发 2 次 prose 调用） | **场景模式（`scene_mode=true`，`pipeline.py:384-386`→`:413-446`）完全没有字数校验与重跑**：实测 2 个场景各输出 200 字，整章 400 字直接落盘为 draft，只留待 M-01 阻断。**影响**：开了场景模式（规格02 W1 改造点允许）就丢掉"越界自动重跑"这条规格保障 |
| 字数约束注入 | ⚠️ | `pipeline.py:375-376`：**只有 W1/W3 注入 `〖字数区间〗`** | W2/W4/W5 的 prompt 里也无该变量（已逐条核对：`prompts/write/W2.md`、`W4.md`、`W5.md` 均无 `〖字数区间〗`）。**影响（语义偏离，非机检必然拦截）**：用 W2/W4/W5 场景专项时这三个提示词被当整章生成调用，却拿不到任何"整章 2200-2800"的约束（W4/W5 里只有"1200-1800字/1500字左右"的场景配额），生成结果与章合同 word_range 脱钩，只能靠事后 M-01 兜底 |

---

## 5. 机械检查 M-01~M-11（规格03 §2.4，第 33-49 行）

| 条款 | 判定 | 证据 | 说明 |
|---|---|---|---|
| M-01 字数阻断（config 可调） | ✅ | `mechcheck.py:82-86`；阈值来自书 `config.chapter_word_range`（`pipeline.py:504`） | 有 UI/API 改 config（`api.py:197-202`），但**设置页/书总览页无字数区间编辑控件**（`grep chapter_word_range` 前端 0 命中）→ 实际只能改文件 |
| M-02 禁词阻断 + 行号 + 词 | ✅ | `mechcheck.py:88-92`，词表 `lint/banned_words.txt`（54 行，`_load_lines` 过滤 `#`） | 书级白名单 `lint_whitelist` 可豁免（`pipeline.py:540-545`），敏感词不可豁免（`mechcheck.py:75`） |
| M-03 对话后缀阻断（正则） | ✅ | `mechcheck.py:94-99`，词表 `lint/dialog_suffix.txt`（2 条正则，含规格给的那条） | |
| M-04 敏感词阻断 + 禁止自动改写 | ✅ | `mechcheck.py:101-105`；`pipeline.py:517-520`（`has_sensitive` → 直接 `blocked_after_fix`→human_review，不调 V5）；测试 `tests/test_pipeline.py:67-83` | 实测无 V5 调用 ✅ |
| M-05~M-11 提醒级（级别/阈值/算法） | ✅ | `mechcheck.py:107-181`：M-05`:109-113`、M-06`:115-125`、M-07`:127-148`、M-08`:150-154`、M-09`:156-167`、M-10`:169-173`、M-11`:175-181`；阈值全部从 `settings.mechcheck` 读（`settings.py:37-47`） | 11 条全部实现且级别正确（1-4 block / 5-11 warn），有单测 `tests/test_mechcheck.py:11-73` |
| 提醒只记录不拦截 | ✅ | `mechcheck.py:185-186`（`has_block` 只看 block）、`pipeline.py:514` | 实测 smoke：11 条 warn 不阻断 |
| 阻断→自动 P-V5 一轮→重查→仍阻断转 human_review | ✅ | `pipeline.py:522-537` | 越权护栏触发时也转 human_review（`:526-529`） |
| 阈值"config 可调"的设置入口 | ⚠️ | `settings.py:37-47` 有默认值，`api.py:72-80` 可整体 PUT；`web/src/views/SettingsView.vue` 只做 key/base_url/模型/单价/词库（`:21-175`） | **无任何阈值编辑 UI**，用户要改 M-05~M-11 阈值必须手改 `data/settings.json` |

---

## 6. LLM 审校闭环 P-V1（规格03 §2.5，第 51-54 行）

| 条款 | 判定 | 证据 | 说明 |
|---|---|---|---|
| 注入：正文+疑似冲突清单+细纲合同+台账摘要 | ✅ | `pipeline.py:689-702`；清单生成 `pipeline.py:630-676`（角色状态 diff/伏笔编号核对/术语表比对） | 术语比对为"正文未出现"提示，提醒级合理 |
| verdict=fix → P-V5 → 重跑 2.4+2.5 | ✅ | `pipeline.py:751-767` | 重跑机检用 `auto_fix=False`（`:764`），阻断即转人工，符合"阻断不二次自动改写" |
| 最多 2 轮（config 可调） | ✅ | `pipeline.py:685`（`review_fix_max_rounds`，默认 2：`books.py:74`/`:153`）、`:751`（`rounds < max_rounds`）、递归 `:766` | **实测：给 4 连 fix，最终 review 调用 3 次、V5 调用 2 次、`fix_rounds=2`、状态 human_review**。递归不会无限：每次进 `run_llm_review` 都重读 DB 的 `fix_rounds`（`:686-687`），计数在递归前已 `set_fix_rounds`（`:762`）。⚠️ 小问题：`fix_rounds` 只在定点修复时自增，人工"定向修复"按钮（`api.py:619-635`）不走状态机也不清零 |
| verdict=fail / 轮次耗尽 → 人工关口 | ✅ | `pipeline.py:769-772`（`fail`/`rounds_exhausted` → human_review） | |
| 人工关口三选项：改细纲重写 / 手改正文 / 豁免（必填理由入 log） | ⚠️ | 改细纲 ✅ `pipeline.py:853-854`；豁免 ✅ `pipeline.py:844-850`（空理由抛错，理由写 `generation_log.error`，测试 `tests/test_pipeline.py:123-149`）；**手改正文 ❌**——全仓库无写正文的 API（`grep '@app.*prose'` 0 命中；`chapters.write_prose` 仅 5 处调用，全在 pipeline 内部：`pipeline.py:407/593/823/868/914`），前端也无正文编辑器（`WorkbenchView.vue` 只有只读 `<pre>`） | **缺**：规格明写的人工关口选项之一"手改正文"**完全不存在**。**影响**：审校报错但用户只想改两句话时，只能选"豁免"（绕过质量门）或"改细纲重写"（回退到细纲、重生成整章），没有中间路径——区块级修不了，只能整章重来 |
| **rubric：任一维 <70 触发 fix** | ❌ | `pipeline.py:746-772` 只按 LLM 自报的 `verdict` 分支；`:723-725` 的分数判断仅用于"全维≥90 的放水复审" | lead 已独立实测：返回 `verdict=pass` + `ai_flavor=50`，管线直接 `transition "pass" → pending_finalize`（我复核了 `pipeline.py:746-749` 确实无任何 `<70` 判断，全代码 `grep 'scores'` 也无阈值比较）。**影响**：规格 02 P-V1 改造点 5「任一维<70 → fix」形同虚设，质量门完全交给模型自评；模型只要写 pass，五维 20 分也能一路定稿 |
| 防橡皮章（pass+零问题+全维≥90 → 复审一次） | ✅（实现超出规格的额外护栏） | `pipeline.py:722-740`；测试 `tests/test_pipeline.py:268-296` | 但注意：这条护栏恰好在 rubric 失效时是唯一兜底，且它要求"全维≥90"——**低分 pass 完全不受约束** |

---

## 7. 人工定稿关（规格03 §2.6，第 56-58 行）

| 条款 | 判定 | 证据 | 说明 |
|---|---|---|---|
| 展示：五维分数、问题清单 | ✅ | `api.py:487-491`（工作台返回最新 mech/llm 记录）、`WorkbenchView.vue:565-606`（五维进度条+问题列表） | 只显示**最新一条**审校记录（`ORDER BY id DESC LIMIT 1`），上一轮分数不可见 |
| **展示：与上版本 diff** | ❌ | 全仓库无 diff 计算/展示：`grep -i diff ainovel/` 仅 `pipeline.py:599/616`（V5 越权护栏内部用 `difflib`）与注释；`api.py` 无 diff 路由；前端无 diff 组件 | **缺**：规格 §2.6 明写"展示与上版本 diff"。快照内容存在 `snapshots.content`，但没有 `GET .../diff` 也无法取单版本正文（`get_snapshot` 只有内部函数 `pipeline.py:886-892`，**无 API 暴露**）。**影响**：定稿关用户看不到"这轮改了什么"，只能凭记忆判断；改前改后只能整章肉眼比对 |
| 定稿 = 二次确认 → 快照 + status:final → 触发记忆卡回写 | ✅ | 前端二次确认 `WorkbenchView.vue:356-366`（两个 `ElMessageBox.confirm`）；后端 `pipeline.py:904-931` | 后端本身不校验"二次确认"（由 UI 保证），直接 POST 即可定稿——API 层无幂等/防误触 |
| 定稿后状态与文件一致性 | ✅ | `pipeline.py:910-919` | 实测 `status: final` + `finalized_at` + 快照内容 == 正文 |
| **定稿后正文不可再被静默改写**（人工关不许自动化，AGENTS.md 红线 3） | ❌ | `pipeline.py:575-594`（`run_targeted_fix` **无状态守卫**，直接 `chapters.write_prose`）；`api.py:619-635`（`POST /fix` 也不查状态） | 实测：`finalized` 章调 `run_targeted_fix` → 正文被改写、`status` 仍 `final`、`version` 仍 1、快照仍是旧文本、状态机仍 `finalized`。**影响**：已定稿章节的正文与快照/导出内容不一致（导出读正文文件 `api.py:809-814`，快照列表显示旧文本），人工关被绕过；且这是无痕修改（无新版本、无审校记录） |

---

## 8. 记忆卡回写 P-V4（规格03 §2.7，第 60-62 行）

| 条款 | 判定 | 证据 | 说明 |
|---|---|---|---|
| JSON diff 到档案：无冲突自动写 | ✅ | `pipeline.py:966`、`characters.py:105-163`（`apply_status_updates`） | 境界倒退/死者复现/未知字段→冲突（`:134-152`），有冲突的**角色整卡不写**（`:154-157`，避免半新半旧） |
| 冲突进裁决队列 | ✅ | `pipeline.py:967-975`（角色冲突 + 伏笔错误 + 待补录设定）、`conflicts.py:36-48` | |
| 全局摘要环形缓冲（最近 2 章 + 累计） | ✅ | `pipeline.py:990`、`summaries.py:31-46`（`RECENT_KEEP=2`，`CUMULATIVE_MAX=4000` 超长压缩头尾） | 实测 smoke 后 `summaries` 有 `recent`/`cumulative` |
| chapters_fts 索引更新 | ✅ | `pipeline.py:991`、`fts.py:56-63` | 注意只在**定稿**时入索引（`pipeline.py:991` 在 `writeback_memory` 内），中间态正文不参与前文检索——与规格"定稿后回写"一致 |
| 回写失败的处置 | ⚠️ | `pipeline.py:922-928`：`except` → `mark_error` + 返回 `{"writeback": {"error": ...}}`，**不回滚定稿** | 实测：让 V4 三次返回非 JSON → `finalize` 返回 `snapshot_version=1` 且 `writeback.error`，但状态机变成 **`error`**（正文已是 final、快照已写、记忆卡/摘要/FTS 全空）。此时 `POST /retry`（`api.py:686-692`）能把状态恢复到 `finalized`，**但没有补做回写的入口**（`writeback_memory` 只有 `finalize` 一处调用，`pipeline.py:923`）。**影响**：V4 一次失败就永久缺记忆卡/摘要/FTS，后续章节上下文缺这一段，且用户只有一个"重试"按钮回到 finalized，看不到也没法重跑回写 |
| 新角色/新地点/待补录 | ✅ | `pipeline.py:977-988`（`new_chars` 建龙套档、`new_locations` upsert、`new_settings_pending` 进裁决） | |

---

## 9. 卷末审计 V6（规格03 §2.8，第 64-67 行）

| 条款 | 判定 | 证据 | 说明 |
|---|---|---|---|
| 触发：某卷最后一章定稿后 | ✅ | `pipeline.py:930` → `_maybe_audit`（`:1014-1017`）→ `_volume_last_chapter`（`:1005-1011`） | 实测卷定义 `chapters:[1,2]` 时第 2 章定稿自动产出 `books/验证书/audits/卷1_审计.md` |
| 输入：本卷全部正文 + PLOT_POINTS 全表 + 主要角色弧光 + Bible | ✅ | `pipeline.py:1031-1052`（正文按卷区间读取并截断 12 万字、台账全表、主角/女主/反派弧光节、Bible 全文） | 正文截断 `[:120_000]`（`:1045`）无提示，超长卷会静默少读 |
| 输出：伏笔回收率（程序算 + LLM 解读） | ✅（程序部分） | `pipeline.py:1055-1059`（`paid/total`），写入报告头 `:1064`；V6 prompt 明示"回收率由程序预计算，你负责解读"（`prompts/review/V6.md:20`） | 回收率统计口径是所有 `状态=paid` 行，非本卷口径 |
| 输出：人物弧线/设定统一/下一卷铺垫清单 | ✅ | `prompts/review/V6.md:24-32`（四维审计 + 下一卷收线清单） | 由模型产出，无程序校验"四项是否都写了" |
| **审计结果入 review_records（可查询/可回溯）** | ❌ | `db.py:94` 的 CHECK 允许 `kind='audit'`，但全仓库 `add_review` 只有 3 处调用（`pipeline.py:507` mech、`:742` llm、`:824` polish），`audit_volume` 只写 md 文件（`:1061-1068`） | lead 已独立核对真库 `review_records` 中 audit=0 条。**影响**：审计只有一份 md 文件，UI 无入口查看历史审计（书总览页只能重新触发一次 `OverviewView.vue:296-311`），审计分数/结论不可对比；且 DB 是索引，md 一删就没了 |
| 审计失败不拖垮定稿 | ⚠️ | `pipeline.py:930` 未包 try：**审计 LLM 抛错会从 `finalize` 抛出**（实测 `RuntimeError` 冒到调用方），但此时定稿已落库（finalized + 快照） | **影响**：前端收到 500 提示"定稿失败"，实际已定稿（状态已 finalized、快照已写），用户会重复点定稿 → 报"状态 finalized 不能定稿"。且审计报告缺失无任何标记 |

---

## 10. 异常处理总表（规格03 §3，12 行逐条）

| 异常 | 判定 | 证据 | 说明 |
|---|---|---|---|
| API 超时/限流 → 指数退避 ×3，全程记 log | ✅（有缺口：失败调用不计成本之外的 token） | `llm.py:119-137`（`MAX_RETRIES=3`，`delay=1*2^attempt`，429/5xx/传输错可重试，4xx 不重试）、`:58-73`（每次 `_call` 记 `generation_log`，失败记 `status=error`） | 退避与日志均有；测试 `tests/test_llm.py:29-60`。**缺口**：`generation_log` 只在逻辑调用前后各记一行，重试的中间失败没有独立入账（规格"全程记 generation_log"按每次 HTTP 重试计则未满足） |
| 仍败 → error 状态，UI 可一键重试 | ❌ | `pipeline.py:162`（`_call` 抛错直接冒泡，`generate_outline` **不捕获**）、`:388`（`generate_prose` 同样不捕获）；实测：细纲阶段 LLM 抛错→状态仍 `awaiting_outline`（无 error）；正文阶段抛错→状态**卡在 `generating`** | `/retry` 路由存在（`api.py:686-692`）但**前端从未调用**（`grep retry web/src` 0 命中）——孤儿接口。`generating` 下 `retry` 事件非法（`statemachine.py:20-40` 无 `(generating,retry)`），实测 `StateError`。**影响（阻断级）**：正文生成中一次网络抖动/超时用尽 3 次，该章永久卡在 `generating`，状态机唯一出口是 `complete`，UI 无任何恢复按钮，用户只能删库 rebuild 或手工改 DB |
| JSON 解析失败 → 错误回喂重试 ×2，仍败 → error | ✅ | `pipeline.py:704-720`（V1，3 次尝试 + `_feedback_instruction` 回喂）、`:948-961`（V4 同）、`:161-174`（G6 同，含 `_dump_failed_output`） | 失败均 `mark_error`（V1/V4）+ 抛错。注：V4 的 `mark_error` 在 `finalize` 里被 `:926` 再次调用，最终状态是 error（见 §8） |
| 字数越界 → 自动重跑 1 次强调字数；仍越界 → 转人工 | ⚠️ | `pipeline.py:391-398`（整章模式 ✅）；`:384-386`+`:413-446`（场景模式 ❌，无字数校验/重跑，实测 400 字直接落盘） | "仍越界 → 转人工"由 M-01 阻断达成 ✅ |
| 阻断级 lint → 自动 P-V5 一轮；仍阻断 → human_review | ✅ | `pipeline.py:522-537` | |
| 台账冲突 → 进裁决队列；非空则禁止开下一章细纲 | ✅ | `pipeline.py:86`（`conflicts.guard`）、`conflicts.py:118-124`；测试 `tests/test_characters.py:48-69` | 守卫在 `generate_outline` 第一行，覆盖 UI/API |
| 敏感词 → 阻断 + 人工，禁止自动改写 | ✅ | `pipeline.py:517-520`（先判 `has_sensitive` 再决定是否 V5）；测试 `tests/test_pipeline.py:67-83` | V5 prompt 也写明敏感词原样保留（`prompts/review/V5.md:22`） |
| 返回空/截断 → 按 finish_reason 判定，重试 | ⚠️ | 空内容：`llm.py:99-100`（`raise LLMError("模型返回空内容")` → 走 `_with_retry` ✅）；截断：整章模式 ✅（`pipeline.py:391-398`），场景模式只记录（`:443-444`） | 场景模式下的截断补救缺失 |
| 应用崩溃 → 状态机落库，重开从当前位置续跑；未完成 generating 草稿保留 | ⚠️/❌ | 状态落 DB ✅（`db.py:61-72`、`statemachine.py:75-96`）；**半成品草稿 ❌**：`stream_cb` 只把片段推给前端（`api.py:569-571`），`write_prose` 仅在生成完成后调用一次（`pipeline.py:407`），崩溃即全丢（lead 已独立验证） | **缺**：规格 §5"generating 中断 → 保留半成品草稿为 draft.vN"完全没有落盘路径。**影响**：2500 字生成到 2400 字崩了，用户拿不回任何内容，只能整章重生成（成本与时间双倍） |
| 重开提示"继续生成或重来" | ❌ | 无任何"续跑/重来"提示逻辑：`grep '续跑\|重来\|resume' web/src ainovel` 0 命中；`WorkbenchView.vue:68-73` 加载后只按状态渲染 | **缺**：崩溃重开无引导。用户看到 `generating` 徽标但不知道该点哪个按钮（且正文按钮在 generating 下会因状态机报错） |

---

## 11. 成本核算（规格03 §4，第 82-86 行）

| 条款 | 判定 | 证据 | 说明 |
|---|---|---|---|
| generation_log 逐笔记 input/output tokens 与成本 | ✅（成本精度有缺陷） | `pipeline.py:55-73`、`db.py:123-147`、单价在 `settings.py:30-35`；`settings.cost_cents`（`:133-141`） | **缺陷**：`cost_cents` 是**整数分且每次调用各自 round**（`settings.py:138-141`）。实测一章 4 次调用：outline 1012/365 tokens → **0 分**、review 5306/82 → **0 分**、memory 2619/134 → **0 分**、prose 3073/1906 → 2 分；合计 2 分。真实成本被系统性低估（每次调用余数丢弃）。**影响**：单章>1元标黄阈值（100 分）在多数章节永远触不到，成本验收线形同虚设 |
| 书总览页：本章成本 / 全书累计 | ✅ | `api.py:164-189`（`cost_by_ch` + `cost_total`）、`OverviewView.vue:319-322`（全书）、`:439-444`（每章）、`WorkbenchView.vue:420-423`（本章） | |
| **书总览页：本卷累计** | ❌ | 全仓库无"本卷成本"聚合：`grep '本卷\|卷累计\|volume_cost'` 只命中 prompt 变量与 assembler 文本（`assembler.py:197`），`db.cost_summary` 仅按章/全书（`db.py:192-205`） | **缺**：规格 §4 明写"本卷累计"。**影响**：书总览只能看全书与单章，无法在卷内控成本（M4 验收"单章≤1元"复盘时缺卷维度） |
| 单章 >1 元标黄 | ✅ | `api.py:180`（`cost_over_yuan`）、`web/src/styles/main.scss:43-46`（`.cost-warn`）、`OverviewView.vue:441` | 受上面整数分缺陷影响，实际很难触发 |
| 修复循环的重试调用同样计入 | ✅ | 所有 LLM 调用统一走 `_call`→`log_generation`（`pipeline.py:39-73`），V5/V2/V3/V6 全走 `_call` | 实测 4 次调用全部入账；`stage` 区分 outline/prose/review/memory/fix/polish/audit |

---

## 12. 断点恢复语义（规格03 §5，第 88-92 行）

| 条款 | 判定 | 证据 | 说明 |
|---|---|---|---|
| 状态机状态 + 当前版本号持久化于 SQLite | ✅ | `db.py:61-72`（`chapters.status/current_version/fix_rounds/prev_status`）、`statemachine.py:75-96`、`:126-134` | 文件为准、状态可重建（`db.rebuild` `:212-257`） |
| generating 中断 → 保留半成品为 draft.vN | ❌ | 同 §10 崩溃行：`pipeline.py:407` 是唯一生成落盘点 | **缺**（lead 已验证） |
| review 中断 → 重跑审校 | ✅ | 审校无中间态：状态停在 `llm_review`，重开直接再点"跑审校"（`api.py:605-612`） | |
| **同章重写永远开新 version，不覆盖旧 draft——旧版本可从版本列表回看** | ⚠️/❌ | `pipeline.py:857-872`（`human_rewrite`）：先 `_next_version` 再**立刻以空 body 写同一个文件路径** `chapters/{no:03d}_正文.md`（`chapters.py:54-55` 路径不含 version），然后 `transition("rewrite")` | 实测三个问题： **(a) 不传 base_version 时旧正文被清空且不可恢复**——`human_review` 状态、无快照时调用 `human_rewrite(book,1,None)` → 立即写 0 字节 v2，旧 2568 字正文**永久丢失**（无快照、文件是唯一真相源）；**(b) 状态不合法时"先毁数据再报错"**——`mech_check`/`pending_finalize`/`finalized` 下调用，先写空文件再 `transition` 抛 `StateError`（实测 body 2568→0，状态未变），属于典型的非事务写；**(c) 旧 draft 本来就不保留**——磁盘上每章只有一个正文文件，`list_versions`（`:895-901`）只列 `snapshots`（定稿历史），未定稿过的旧 draft 无法回看。**影响（阻断级）**：UI"从版本重写"下拉在无快照时默认为空并传 `null`（`WorkbenchView.vue:380-402`），在 human_review 章上点一下即清空正文；且规格"旧版本可从版本列表回看"对 draft 不成立 |

---

## 13. rubric 五维锚点是否真的写进 V1 且被代码使用（规格03 §6，第 94-102 行）

| 条款 | 判定 | 证据 | 说明 |
|---|---|---|---|
| 五维锚点写进 P-V1 prompt | ✅ | `prompts/review/V1.md:39-44` 逐维给 `≥90 / 70-89 / <70` 三档描述，与规格 §6 表逐格对应（AI味/剧情一致性/节奏爽点/文笔画面/开篇钩子） | 另含 T9-B 七项检查（`:30-37`）、举证硬规则（`:46-49`）、裁决规则（`:51`）、JSON schema（`:53-65`） |
| 五维 JSON 被代码解析 | ✅ | `pipeline.py:709-715`（`extract_json` + `verdict` 必填 + `scores`/`issues` 默认值）、`:742-744` 入 `review_records`、`api.py:489-491`、`WorkbenchView.vue:583-590` | 解析与展示链路完整 |
| **分数被代码用于裁决（<70→fix）** | ❌ | 同 §6：`pipeline.py:746-772` 只看 `verdict` 字段；唯一用到分数的是放水复审 `:723-725`（≥90 且零问题） | lead 已独立实测（`ai_flavor=50` + `verdict=pass` → `pending_finalize`）。**影响**：锚点只是"写给模型看的建议"，程序侧零约束；模型自评 pass 即通关，M3"每章产出带五维分数的审查报告"有分数但不构成质量门 |
| 五维分数可见（M3 验收：分数列表可见） | ⚠️ | `WorkbenchView.vue:583-590` 只显示**最近一条**审校的分数条；书总览无分数列（`OverviewView.vue:428-451` 列只有状态/版本/修复轮/成本） | **缺**：无跨章分数列表/趋势，M3 验收"分数列表可见"未满足 |

---

## 附：其他发现（死代码 / 孤儿接口 / 一致性）

| 项 | 判定 | 证据 | 说明 |
|---|---|---|---|
| 死代码：`characters.read_all` | ❌（死代码） | 定义 `characters.py:49-50`；全仓库无调用点 | 无害但属"定义了没有调用点" |
| 死代码：`characters.relationships` | ❌（死代码） | 定义 `characters.py:173-180`；全仓库无调用点 | G4 关系网数据无任何消费方（`rg relationships` 仅定义处） |
| 死代码：`bible.update_section` | ❌（死代码） | 定义 `bible.py:40-43`；全仓库无调用点 | 单节更新改为 `PUT /bible` 整本覆盖（`api.py:229-235`） |
| 死代码：`chapters.count_words` | ❌（死代码） | 定义 `chapters.py:117-119`；全仓库无调用（全部用 `mechcheck.count_words`） | 重复实现，易分叉 |
| 孤儿接口：`POST /api/books/{book}/chapters/{no}/retry` | ⚠️ | 后端 `api.py:686-692`；前端 `web/src/api/index.ts` 无对应封装、`WorkbenchView.vue` 无按钮 | 规格 §3"UI 可一键重试"实际不可用 |
| 孤儿接口：`GET /api/books/{book}/config` + `PUT .../config` | ⚠️ | `api.py:192-202`；前端 0 调用 | 字数区间/审校轮数只能手改文件 |
| 孤儿接口：`POST /api/prompts` 相关只读、`GET /api/lint` 前端已用 | — | — | 不计缺陷 |
| 前端超时与长任务错配 | ⚠️ | `web/src/api/client.ts:8` 默认 30s；`finalizeChapter`/`confirmOutline` 等未覆盖 timeout（`api/index.ts:118-153`），而 `finalize` 内含 V4 + V6 两次 LLM 调用（`pipeline.py:923/930`） | 真实模型下 V6（12 万字长上下文）必然 >30s → 前端弹"请求失败"，后端已成功 → 用户重复提交得 400 |
| 设置页缺项 | ⚠️ | `SettingsView.vue` 只有 key/base_url/默认模型/单价/词库；无 M-05~M-11 阈值、无装配预算、无审校轮数（`grep` 0 命中） | 规格 03 §2.4"config 可调"、settings.py 注释"M0 设置页可调"未落地到 UI |
| README 偏差项核对（不作为缺陷） | ✅ | `README.md:86-96` | 偏差 3（状态机补迁移）、4（generating→mech_check 停靠）、5（FTS book_id）、6（rebuild 保留历史）、7（前 3 章 W3）、8（pending_finalize 不可区分）均与代码一致，未报为缺陷 |

---

## 阻断级缺口（按严重度排序）

> 判定标准：会导致规格要求的功能"实际用不了"或"用了就丢数据/卡死"。

1. **正文生成失败即永久卡死（无 error 态、无重试路径）** —— `pipeline.py:345-410`（`generate_prose` 不捕获 LLM 异常）、`statemachine.py:20-40`（`generating` 无 `retry`/`rewrite` 出边）、`api.py:686-692`（`/retry` 前端 0 调用）。实测：LLM 抛错后状态停在 `generating`，`retry` 事件 `StateError`。**用户影响**：一次网络抖动 = 该章报废，只能手工改 DB。
2. **「从版本重写」会清空正文且旧 draft 无快照可回** —— `pipeline.py:857-872`（先写空 body 再迁移状态）、`chapters.py:54-55`（正文单文件，路径不含 version）、`pipeline.py:895-901`（版本列表只列定稿快照）。实测：`human_review` + 无快照 + `base_version=null` → 2568 字正文变 0 字节且不可恢复；`mech_check`/`pending_finalize` 下调用则"先毁数据后报错"。
3. **人工关「手改正文」完全缺失** —— 无任何写正文的 API/UI（`chapters.write_prose` 5 处调用全在 pipeline 内部；`api.py` 无 prose 端点；`WorkbenchView.vue` 正文只读 `<pre>`）。规格 §2.5 人工关口三选项缺一，用户遇小问题只能整章重生成或豁免（绕过质量门）。
4. **rubric <70 不触发 fix，五维分数不参与裁决** —— `pipeline.py:746-772` 仅按模型自报 `verdict` 分支（lead 独立实测 + 本次复核）。规格 02 P-V1 改造点 5 失效：模型写 pass 即通关，质量门形同虚设。
5. **崩溃不保留半成品草稿** —— `pipeline.py:407`（唯一落盘点）、`api.py:569-571`（流式片段只推前端）。规格 §5 明确要求 `draft.vN`；2500 字生成中途崩溃 = 全部丢失。
6. **无「章内全自动」编排入口** —— `pipeline.py` 无串联函数，`WorkbenchView.vue:536/564/620` 全靠人点。规格 01 §4 的"循环内无人工动作"不成立，且漏点一步即停在中间态（与缺口 1 叠加）。
7. **定稿后正文仍可被 `/fix` 静默改写（人工关红线被绕过）** —— `pipeline.py:575-594` + `api.py:619-635` 均无状态守卫。实测 finalized 章被改写后 status 仍 final、快照仍旧、状态机仍 finalized → 正文/快照/导出三方不一致且无版本记录。
8. **V6 卷末审计不入 `review_records`，且失败会误报定稿失败** —— `pipeline.py:1020-1069`（只写 md，无 `add_review`）、`pipeline.py:930`（未包 try，审计异常冒泡但定稿已提交）。审计历史不可查；用户看到"定稿失败"实际已定稿。
9. **成本按次整数分四舍五入 → 系统性低估，>1 元标黄难触发** —— `settings.py:133-141`（每次调用各自 round 到整数分）。实测 4 次调用合计 2 分；单章多轮修复时误差累积。规格 §4 验收线失真。
10. **本卷累计成本未实现** —— 规格 §4 三项展示缺一（`db.py:192-205` 只按章/全书，前端无卷维度）。

### 次级缺口（不阻断但明确违反规格/浪费用户时间）
- 装配预览逐槽 `est_tokens` 恒为 0（`assembler.py:49/61`，字段从未赋值）；估算值未入 `generation_log`（规格 §2.2"记入 log"）。
- 与上版本 diff 完全缺失（规格 §2.6）：无 `get_snapshot` 的 API 暴露（`pipeline.py:886-892` 无路由）。
- 截断顺序多砍 2 槽、永不截断多护 2 槽（`assembler.py:30-32` vs 规格 §2.2 原文）。
- 场景模式（`scene_mode`）无字数重跑、无截断重跑（`pipeline.py:384-386`/`:413-446`）；W2/W4/W5 被当整章调用却不注入 `〖字数区间〗`（`pipeline.py:375-376`）。
- G6 `plant` 跳过 FO 编号校验（`pipeline.py:266-267`），实测任意编号被接受。
- 「改细纲重写」仅 `human_review` 可达（`statemachine.py:35`），且回 draft 后文件 `confirmed` 仍为 True → `db._build_status` 重建会跳回旧进度（`db.py:296-302`）。
- 记忆卡回写失败无补做入口（`pipeline.py:923` 唯一调用点），状态变 `error` 后 `/retry` 只回到 finalized、上下文永久缺该章。
- 孤儿接口 `/retry`、`/config`；死代码 `characters.read_all`、`characters.relationships`、`bible.update_section`、`chapters.count_words`。
- `finalize` 前端 30s 默认超时 vs 后端含 2 次 LLM（`client.ts:8` + `api/index.ts:152-153`）。
- 设置页无 M-05~M-11 阈值/装配预算/审校轮数编辑（`SettingsView.vue`）。
- P-V1 五维分数无跨章列表（M3 验收"分数列表可见"未满足）。

---

*审计边界*：未运行 `run.py`（避免污染真实 `books/`、`data/`）；所有实跑均在 `%TEMP%` 下的临时 `AINOVEL_HOME` 中进行。未改动仓库任何源码/规格/测试；除本文件外仓库唯一变化是 `.audit/` 目录。
