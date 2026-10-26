# 档案层 / 提示词体系 / M1 生成链 / M5 文风 —— 规格 01 + 02 落地审计

- 审计对象：`D:\1-work\ai-novel`（只读审计；唯一写入文件 = 本文件）
- 真相源：`docs/specs/01-数据与档案规格.md`（197 行）、`docs/specs/02-提示词体系规格.md`（154 行）
- 判据补充：`README.md`「与规格的对应与偏差」（1–9 条为**已定案有意决策**）、`AGENTS.md`、`docs/开发计划.md`（M5 = 后期增强，已确认延后）
- 实测环境：Windows / Python 3.12 / SQLite 3.49.1（trigram 可用）
- 实测手段：只读代码 + 在**临时 AINOVEL_HOME**（`tempfile.mkdtemp()`，拷 prompts/lint）里跑 standalone 校验脚本，未触碰仓库 `books/`、`data/`；未运行 `run.py`；临时脚本已删除
- 回归基线：`tests/test_prompts.py + test_characters.py + test_db_rebuild.py + test_style.py + test_mdtable.py + test_frontmatter.py + test_api.py` = **36 项全绿**

判定图例：✅ 实现且可用 ／ ⚠️ 部分实现 ／ ❌ 未实现（或声称做了但实际不可用） ／ 🔶 与规格有偏差（含 README 已定案偏差） ／ ➖ 不适用

---

## 0. 结论摘要

规格 01 的**文件格式与 SQLite 表结构基本落地**，规格 02 的**提示词库组织层几乎全绿**（23 条齐全、7 字段齐全、17 个温度值 100% 一致、digest 双路入 log、书级覆盖可用、悬空变量为零）。真正的缺口集中在**"档案生成链 M1 的落盘环节"和"用 UI 编辑档案的往返"**：有几处会**静默丢数据**或**静默失败**，不影响测试却会影响真实使用。

| 组 | 结论 | 一句话 |
|----|------|--------|
| A 规格01 §2 文件格式 | ⚠️ 部分 | 字段结构齐全（characters 10 节与规格逐字一致），但 Bible 七节/八列**无校验**（3 个列常量是死代码），OUTLINE 保存路径**污染 frontmatter 且丢节奏总谱** |
| B 规格01 §3 SQLite | ✅ 实现（🔶 2 处扩展） | 5 表逐列一致；chapters 多 `prev_status`/`fix_rounds` 2 列（error 恢复+轮次语义所需，README 未单列此扩展）；FTS trigram/bigram 双路都在 |
| C 规格01 §5 校验防损 | ⚠️ 部分 | 原子写/schema 拒写/定稿快照/装配预览全在；**原地 rebuild 不修复已污染的状态、不清理残留章节行** |
| D 规格02 §1 组织与版本 | ✅ 实现 | 23 文件、7 字段齐全、阶段目录正确、书级覆盖可用、SHA-256 digest 双路入 log、**温度 17/17 全对**、model_stage 映射可用 |
| E 规格02 §2 主表 23 条 | ⚠️ 部分 | ID 无缺无多，但 **T1 无任何 UI 入口**（开发计划允许"工具外"，故非缺陷），G4/G5 采纳逻辑与规格承诺不符 |
| F 规格02 §4 M1 生成链 | ⚠️ 部分 | "生成→预览→改定→采纳"**框架在**，但**无 diff**；**G4 关系网永不写回**；**G5 前端必定 404 + 采纳覆盖台账** |
| G M5 文风指纹 | ✅ 实现（超出预期） | **不是"仅提示词"**：语料/特征卡落盘、F1 调用、F2 装配注入、UI 面板、测试全都存在 |
| H 提示词渲染 | ✅ 实现（2 处轻微偏差） | 严格渲染 + 缺变量报错 + `〖〗残留` 测试都在；**悬空变量 = 0**；缺二次残留校验 |

**最重要的一句话**：规格 02 §4 结尾承诺"AI 生成的档案永远不直接静默覆盖人工内容"——**G5 大纲采纳这一条违反了它**（实测覆盖已有伏笔台账状态，见 F-2）。这是本次审计最该修的一个点。

---

## A. 规格 01 §2 文件格式逐项

### A-1 meta.md —— ✅ 实现

规格 §2.1 的 11 个字段全部落地，且是程序生成时一次写全：

| 规格字段 | 代码位置 | 判定 |
|---|---|---|
| title / author / genre / tags / target_words / chapter_word_range / platform / benchmark_book / compliance_note / status | `ainovel/books.py:49-61` | ✅ 全部生成 |
| status 枚举 `drafting\|serializing\|done` | `ainovel/books.py:23`（`META_SCHEMA["enums"]`） | ✅ 校验 |
| 自由文本正文（故事核/卖点/金手指） | `ainovel/books.py:61`（占位串）、`books.py:141`（保留旧 body） | ✅ |

schema 校验：`META_SCHEMA`（`books.py:18-24`）含 `required / non_empty / types / enums`，读写两侧都过 `FM.validate`（`books.py:134`、`books.py:139`）——**失败拒绝写回**，符合规格 §5.2。

> 小瑕疵：`tags`、`benchmark_book`、`compliance_note` 不在 `META_SCHEMA["types"]` 里，写错类型不会被拦。属可接受（规格未要求这三者强类型）。

### A-2 STORY_BIBLE.md 七个固定 H2 节 —— ⚠️ 部分实现

| 规格要求 | 代码位置 | 判定 | 说明 |
|---|---|---|---|
| 七节常量定义 | `ainovel/books.py:26` `BIBLE_SECTIONS` | ✅ | 七节名称逐字一致 |
| 建书时生成七节骨架 | `ainovel/books.py:79-80` | ✅ | |
| **程序按节解析** | `ainovel/bible.py:15-28` `_parse_sections` | ✅ | 按 `^## ` 切节，返回 `{标题: 内容}` |
| 写回保留固定节顺序 | `ainovel/bible.py:31-37` | ✅ | 已知节按 `BIBLE_SECTIONS` 排序，未知节追加尾部 |
| **力量体系表 8 列校验** | `ainovel/bible.py:10` 定义 `POWER_COLS` | ❌ | **`POWER_COLS` / `HIDDEN_COLS` / `TERM_COLS` 三个常量定义后全仓库 0 处引用**（grep 确认只有定义行）。写 Bible 时不校验列名，读力量体系时也不校验 |
| 隐藏规则 3 列校验 | `ainovel/bible.py:11` `HIDDEN_COLS` | ❌ | 同上，纯死代码 |
| 术语表 2 列校验 | `ainovel/bible.py:12` `TERM_COLS` | ❌ | 同上，纯死代码 |
| 术语一致性检查（审查用） | `ainovel/bible.py:66-70` `terms()` → `pipeline.py:669-674` | ✅ | V1 预检里比对"术语未在正文出现" |

**实测证据**（临时 home）：用只有 `{'基础设定': 'x'}` 这一节的 dict 调 `bible.write()` → **写盘成功**，`bible.read()` 只返回 1 节；再用错误列名 `| 错误列 | 另一列 |` 写力量体系 → `power_rows()` 返回 `[]`、`realm_order()` 返回 `[]`，**无任何报错**。

**影响**：规格 §2.2 表格写的"固定表格，列：大境界\|子境界\|…"在代码里只是**注释性常量**。真实后果是：手改或 AI 采纳时列名打错 → 境界倒退检测（`characters.py:139-146`，依赖 `realm_order`）**静默失效**，`build_suspect_list` 的术语比对也静默失效。不是崩溃，是"防护悄悄不生效"。

### A-3 characters/char-*.md —— ✅ H2 节一致；⚠️ 正文节不受 schema 约束

> 先纠正一处口径：任务清单写"11 个 H2 节"，但规格 01 §2.3 的代码块（`01-数据与档案规格.md:97-107`）实际只列了 **10 个 `##` 节**（表层信息/声音特征/核心三要素/内在矛盾/过往创伤/标志行为/关系网络/行为底线/弧光预设/对话试金石）。以规格原文为准，故按 **10 节**核对，不虚报"缺 1 节"。

| 规格要求 | 代码位置 | 判定 | 说明 |
|---|---|---|---|
| 目录形式（README 偏差 1 + 规格 §2.3 说明） | `ainovel/characters.py:24-34` | 🔶 定案 | 规格 §2.3 已写明偏离豆包 F1 单文件，不是缺陷 |
| frontmatter `name / role / status` | `ainovel/characters.py:17-21` `CHAR_SCHEMA` | ✅ | `required`+`types`+`enums`（role 五值） |
| status 动态段 8 个字段 | `ainovel/characters.py:15` `STATUS_FIELDS` | ✅ | realm/location/injuries/inventory/emotion/alive/known_info/updated_chapter 全在（规格 §2.3 列的也是这 8 个） |
| 未知 status 字段拦截 | `characters.py:147-152` | ✅ | 记 `unknown_field` 冲突，供人审 |
| **10 个 H2 节建卡** | `ainovel/characters.py:72-73` | ✅ | 与规格 §2.3 逐字一致（10/10） |
| H2 节是否属 schema 校验 | `characters.py:17-21` | ⚠️ | schema **只校验 frontmatter**，正文 H2 结构不校验：删掉某节、改节名都不会报错 |

> ⚠️ 与 A-2 同源的问题：characters 的"10 节固定结构"和 Bible 的"7 节固定结构"一样，都只是**建卡时的模板**，不是可校验可修复的 schema。区别是 characters 的节名没有被硬编码成常量（`characters.py:72-73` 是内联列表），所以连"节名漂移检测"的抓手都没有。这直接导致 F-1（G4 写回缺 `## 关系网络` 标题时无人报错）。

### A-4 OUTLINE.md —— ⚠️ 部分实现（保存路径有真实缺陷）

| 规格要求 | 代码位置 | 判定 | 说明 |
|---|---|---|---|
| frontmatter `logline / ending_direction / volumes` | `ainovel/outline.py:10-13` `OUTLINE_SCHEMA` | ✅ | |
| volumes 七字段（no/name/chapters/core_conflict/goal/climax/hook_out） | `outline.py:32-38` | ✅ | 渲染时全用 |
| `## 章节总表` 7 列 | `outline.py:15` `CHAPTER_COLS` + `outline.py:39` | ✅ | 列名逐字一致 |
| `## 爽点节奏总谱` | `outline.py:41` | 🐞 **缺陷**（见下） |
| 读取章节行 | `outline.py:22-25`、`chapter_row()` `outline.py:53-57`、`total_chapters()` `outline.py:60-63` | ✅ | 章号容错抽数字 |

**🐞 缺陷 A-4-a：UI 保存大纲会污染 frontmatter 且丢节奏总谱**

链路：`OverviewView.vue:189-201` `saveOutlineRows()` → `getOutlineFull()` → 改 `chapter_rows` → `putOutlineFull()`（整包回传）。后端 `api.py:276-284` 把整包 `payload` 传给 `outline.write_outline(book, payload, rows, rhythm)`，而 `write_outline` 直接 `FM.dump(fm, body)`（`outline.py:44`）——**`payload` 里的 `_body`（GET 时返回的正文全文）被当成 frontmatter 键写进 YAML**，同时 `rhythm` 参数取 `payload.pop("_rhythm","")` 的默认空串 → 节奏总谱被替换成 `（待规划）`。

实测（临时 home，走真实 TestClient）：
```
GET /api/books/t/outline 返回键: ['_body', 'chapter_rows', 'ending_direction', 'logline', 'volumes']
PUT /api/books/t/outline → 200
保存后 OUTLINE.md frontmatter 出现:  _body: '## 章节总表 ...  ## 爽点节奏总谱 ...'
保存后正文区节奏总谱:  （待规划）          ← 原文「1-2 小打脸 12 卷终 —— 原始节奏总谱文字」在frontmatter里，正文已是占位串
frontmatter 是否被塞入 _body: True
```
即：**每点一次"保存大纲"，OUTLINE.md 就多一份 `_body` 巨型 YAML 键，且用户的节奏总谱从正文区消失**。`outline.read_outline()` 只取 `fm | {"_body": body, "chapter_rows": rows}`（`outline.py:25`），`_body` 键被自己的 `body` 覆盖，所以**读回来功能上还能用**（不会立刻崩），但文件已被污染、节奏总谱事实丢失。这是"文件是真相源"原则下的实打实数据损坏。

同类问题：`api.py:211-217` `put_meta` 对 `_body` 做了 pop 保护 ✅；`api.py:252-259` `put_character` 也 pop 了 `_body` ✅ —— **唯独 outline 漏了**。（角色那条另有 `_file` 泄漏，见 A-5。）

### A-5 PLOT_POINTS.md 七列表 + 三条程序校验 —— ✅ 实现

| 规格要求 | 代码位置 | 判定 | 说明 |
|---|---|---|---|
| 7 列 | `ainovel/outline.py:70` `PP_COLS` | ✅ | 编号/内容/埋设章/推进章/预计回收章/状态/备注 逐字一致 |
| 状态枚举 | `outline.py:71` `PP_STATUSES` | ✅ | planted/building/paid |
| **校验1：paid 后不得回 building（防复活）** | `outline.py:86-101` `validate_transitions()` | ✅ | 按 `PP_STATUSES` 序号比大小，回退即报错 |
| **校验2：planted 超 30 章未进 building → 预警** | `outline.py:104-116` `overdue_warnings()` | ✅ | grace=30，`apply_foreshadow_ops` 回报（`outline.py:171`） |
| **校验3：只允许 planted→building→paid 单向** | `outline.py:86-101` | ✅ | 同上 |
| 非法伏笔操作（引用不存在编号 / 重复 plant / 非 building 就 pay） | `outline.py:133-167` | ✅ | 四类错误全覆盖，出错整体不写盘（`outline.py:168-170`） |
| 测试覆盖 | `tests/test_mdtable.py:24-61` | ✅ | 迁移/超期/plant/pay 都有断言 |

> ⚠️ 观察（非缺陷，但值得记）：`overdue_warnings` 的返回值只在 `pipeline.writeback_memory` 里传给 `result["warnings"]`（`pipeline.py:996`），而**前端没有任何地方展示 warnings**（`web/src/**` grep `warnings|超期|预警` = 0 命中）。也就是说"超 30 章预警"程序算出来了、也返回了 API，**但用户在 UI 上看不到**。这条归入 H/功能缺口表。

### A-6 LOCATIONS.md 五列表 —— ✅ 实现

`ainovel/outline.py:179` `LOC_COLS = ["名称","特征","方位/距离","首现章","关联剧情"]` —— 与规格 §2.6（`01-数据与档案规格.md:134`）的 5 列表头**逐字一致 ✅**。（任务清单写"四列"，规格原文实为 5 列，此处以规格为准。）写入 `upsert_location()`（`outline.py:188-196`）带去重；读取 `read_locations()`（`outline.py:182-185`）供装配器挑地名（`assembler.py:107`）。

> 缺口：`api.py:303-305` 只有 `GET /api/books/{book}/locations`，**没有 PUT/POST**，前端也**没有地点编辑入口**（`web/src` grep 地点/location = 0 处编辑 UI）。规格 01 没要求地点必须在 UI 可编辑，但规格 04 的"档案编辑页"若要求六类档案可编辑，这里只有 5 类——留给 UI 审计员交叉确认，本报告只登记后端事实。

### A-7 001_细纲.md（章合同）confirmed 锁 —— ✅ 实现（API 层）

| 规格要求 | 代码位置 | 判定 |
|---|---|---|
| frontmatter schema | `ainovel/chapters.py:10-25` `OUTLINE_CONTRACT_SCHEMA` | ✅ 全部字段 + stage/emotion_task 枚举 |
| `hook.type` 必填校验 | `chapters.py:81-82` | ✅ |
| `chapter_no` 与文件名一致 | `chapters.py:87-88` | ✅ |
| `confirmed` 字段 | `chapters.py:90`（write 时 setdefault False）、`chapters.py:97`（confirm 置 True） | ✅ |
| **confirmed 锁语义** | `api.py:513-516`（状态非 draft/awaiting/error → 400 拒绝） | ✅ |

实测：状态 `outline_locked` + `confirmed: true` 时 `PUT /chapters/1/outline` → **400 "细纲已锁定"** ✅。

> ⚠️ 但库层**没有锁**：直接调 `chapters.write_outline_contract()` 对已 confirmed 的章节仍能改写成功（实测 `True`）。这是"API 层守门、库层不设防"，对当前单进程应用可接受，但任何脚本/未来批处理绕过 API 就会破坏人工确认关（AGENTS.md 第 3 条"人工关不许自动化"的红线在库层无护栏）。

### A-8 001_正文.md frontmatter 6 字段 —— ✅ 实现

`ainovel/chapters.py:27-32` `PROSE_SCHEMA`：required `chapter_no/version/status/word_count`，types 含 `finalized_at`，enums `status: [draft, final]` —— 规格 §2.8 的 6 个字段（chapter_no/version/status/word_count/finalized_at）全部覆盖 ✅。写入侧还有 `chapter_no` 与文件名一致性校验（`chapters.py:111-112`）。

### A-9 001_记忆卡.md —— ✅ 实现

`ainovel/chapters.py:34-43` `MEMORY_SCHEMA` 与规格 02 P-V4 的 JSON schema（`02 §3 P-V4`，8 个顶层键）**逐键对应**：`chapter_no / char_updates / new_chars / new_locations / foreshadow_ops / new_settings_pending / event_summary / time_elapsed` ✅。写盘前 `FM.validate`（`pipeline.py:956`），校验失败回喂重试（`pipeline.py:950-961`，3 次）。

---

## B. 规格 01 §3 SQLite —— ✅ 实现

### B-1 五张表逐列核对

| 表 | 规格列 | 代码（`ainovel/db.py`） | 判定 |
|---|---|---|---|
| **books** | id, name, path, created_at | `db.py:55-60` | ✅ 一致 |
| **chapters** | id, book_id, no, title, status, current_version, updated_at | `db.py:61-72` | 🔶 一致 + **2 列扩展**：`prev_status`（error→retry 恢复原状态，`db.py:67`，README 偏差 4 的支撑）、`fix_rounds`（修复轮次，`db.py:68`，规格 03 §2.5 两轮上限的支撑）。README「与规格的对应与偏差」**没有单列这两列**，属未登记的 schema 扩展（但语义上必需，合理） |
| **generation_log** | id, book_id, chapter_no, stage, prompt_id, prompt_version, prompt_digest, model, input_tokens, output_tokens, cost_cents, status, error, created_at | `db.py:73-88` | ✅ 14 列逐字一致；status 有 CHECK('ok','error') |
| **review_records** | id, book_id, chapter_no, version, kind, scores_json, issues_json, verdict, created_at | `db.py:89-99` | ✅ 9 列一致；kind 有 CHECK('mech','llm','polish','audit') |
| **snapshots** | id, book_id, chapter_no, version, label, content, created_at | `db.py:100-109` | ✅ 7 列一致 + `UNIQUE(book_id, chapter_no, version)` |

### B-2 chapters_fts 虚表 + trigram/bigram 降级 —— ✅ 实现

| 规格要求 | 代码位置 | 判定 |
|---|---|---|
| FTS5 虚表（chapter_no, body） | `ainovel/fts.py:26-37` `ensure_table` | ✅ 一致 + **`book_id UNINDEXED` 扩展**（README 偏差 5 已定案，多书共存必需） |
| trigram tokenizer 优先 | `fts.py:12-23` `_try_trigram()`（建探针表试） | ✅ |
| 不可用自动降级 bigram | `fts.py:32-36`（无 tokenize 参数）+ `fts.py:44-53`（入库前 `_bigram` 切分） | ✅ |
| 2 字关键词 Python 兜底 | `fts.py:71-106`（`short_kws` 走 `in` 子串） | ✅ README 偏差 5 已定案 |
| 检索 top8 / ≤150 字 | `fts.py:66`、`fts.py:113` | ✅ 规格 03 归管线审计 |
| 实测 | `tests/test_db_rebuild.py:35-45` | ✅ trigram 分支断言 2 字名可召回 |

> 说明：FTS 降级是**进程级缓存**（`_TRIGRAM_OK` 全局，`fts.py:9`），测试里 `conftest.py:30` 会重置——运行时行为正确。

---

## C. 规格 01 §5 校验与防损五条 —— ⚠️ 部分实现

| # | 规格条款 | 代码位置 | 判定 | 说明 |
|---|---|---|---|---|
| 1 | 所有文件写回 = 临时文件 → 原子替换 | `ainovel/atomic.py:9-23`（mkstemp + `os.replace`，`newline=""`） | ✅ | 全仓库 **0 处** 绕过 `atomic` 的直接 `open(...,"w")`（grep 确认唯一命中在 atomic 自身）；26 处 `atomic.write_text` 调用点覆盖 books/bible/characters/outline/chapters/conflicts/pipeline/settings/style/summaries |
| 2 | frontmatter schema 失败 → 拒绝写回并报错 | `frontmatter.py:51-84` `validate()` + 各模块 write 前调用（`books.py:139`、`characters.py:54`、`chapters.py:89/113/134`、`outline.py:29`） | ✅ | 有 `required/non_empty/types/enums` 四类；bool-as-int 有专门处理（`frontmatter.py:73-75`）。**但见 A-2：Bible 无 schema、characters 只校验 fm 不校验正文节** |
| 3 | 每次定稿自动写快照；保留全部历史版本 | `pipeline.py:917-919`（`db.add_snapshot`）+ `db.py:175-189`（同版本 `DO NOTHING` 不覆盖） | ✅ | 定稿 → 快照 → `list_versions` 可查 |
| 4 | SQLite 可从文件全量重建（rebuild），重建后校验 generation_log 之外的表一致 | `db.py:212-257` `rebuild()` | ⚠️ **有缺口** | 见 C-1 |
| 5 | 装配器预览必须可见（UI 折叠面板） | `pipeline.py:284-301` + `assembler.py:55-67` + `WorkbenchView.vue:509-520` | ✅ | 13 槽位 + token 估算 + `saved_to` 落盘；API `GET /chapters/{no}/preview`；`tests/test_api.py:55-56` 断言 13 槽 |

### 🐞 C-1：`rebuild()` 不能修复"已存在但被污染"的 chapters 行

`db.py:246-252` 的 UPSERT **只更新 `title / current_version / updated_at`，不更新 `status`**：

```sql
ON CONFLICT(book_id, no) DO UPDATE SET
  title=excluded.title, current_version=excluded.current_version,
  updated_at=excluded.updated_at          -- ← 没有 status=excluded.status
```

而 `_derive_status()`（`db.py:291-309`）算出的状态只在 **INSERT 分支**生效。

实测（临时 home）：
```
文件证据：细纲 confirmed + 正文 status:final  →  正确状态应为 finalized
人为把 DB status 改成 human_review
db.rebuild()
→ 状态仍是 human_review        ← 期望 finalized
```

同时 **`rebuild` 不清理"文件已删但 DB 行还在"的章节**：造第 2 章 DB 行、无任何文件 → `rebuild()` 后第 2 章行**仍残留 1 行**（`db.py:224-228` 只清"书目录不存在"的 books，不清"章节文件不存在"的 chapters）。

**为什么测试没抓到**：`tests/test_db_rebuild.py:6-32` 走的是**真正删库文件**路径（DB 全新 → 全部走 INSERT 分支），因此状态正确。我另跑了一次"真删 .db 文件 → rebuild"验证：`status = finalized` ✅。所以：
- **真·删库重建**（README/规格的本意）→ ✅ 可用
- **原地 rebuild 修状态**（用户/开发者更可能的用法，API `POST /api/db/rebuild` 直接暴露）→ ❌ 状态与残留行都不会被修正

规格 §5.4 原文是"SQLite 可从文件全量重建（rebuild 命令），重建后校验 generation_log 之外的表一致"——"全量重建"语义下 UPSERT 不刷新 status 属于**实现不完整**，且 `POST /api/db/rebuild` 是 UI 设置页的可见按钮（`SettingsView.vue:106`），用户点它会得到"报告成功但状态没修好"的结果。

**建议**：`DO UPDATE SET` 里补 `status=excluded.status`；并在 rebuild 里按 `_scan_chapters` 产出的章节集合删除多余行。

---

## D. 规格 02 §1 组织与版本 —— ✅ 实现

### D-1 23 个文件齐全 + 阶段目录正确

`prompts/` 下 **23 个 .md**（实测 `Get-ChildItem -Recurse -Filter *.md | Count = 23`）：

| 目录 | 文件（逐字核对 frontmatter 的 `stage` == 目录名） |
|---|---|
| `system/` | S0, S1, S2, F2 → `stage: system` ✅ |
| `generate/` | G1~G6, T1 → `stage: generate` ✅ |
| `write/` | W1~W5 → `stage: write` ✅ |
| `review/` | V1~V6 → `stage: review` ✅ |
| `style/` | F1 → `stage: style` ✅ |

代码侧目录白名单：`prompts.py:22` `LAYERS = ["system","generate","write","review","style"]` —— 5 层齐全 ✅。

> 注：`F2` 是"常驻注入段"，放在 `system/` 而非 `style/`；规格 02 §2 主表把 F2 的模型档标为"常驻"而非 `style`，代码放 `system/` 是**合理落位**，不算缺陷（`stage: system` 与目录一致，加载路径正确）。

### D-2 frontmatter 七字段 —— ✅ 实现

规格 §1（`02-提示词体系规格.md:8`）要求 `id / stage / model_stage / temperature / version / inputs / output` 共 **7 个**字段。

- 必填校验：`prompts.py:24` `REQUIRED_FM = ["id","stage","model_stage","temperature","version"]`，缺则 `PromptError`（`prompts.py:53-55`）
- `inputs`/`output` 不参与必填校验，但 **23 条全都写了**（实测逐条扫描，0 缺）
- 另有额外 `name` 字段（23 条都有），供 UI 展示（`prompts.py:114`）

> ⚠️ 轻微：`inputs`/`output` 是规格 7 字段之一，却**不在 `REQUIRED_FM`** 里 —— 删掉某条的 `inputs` 不会被发现。`tests/test_prompts.py:16-20` 只断言了 `model_stage / temperature / digest`，没断 `inputs/output/stage/id`。

### D-3 书级覆盖 books/<书名>/prompts/ 同名优先 —— ✅ 实现（但目录约定与规格字面不同）

代码（`prompts.py:72-81`）：遍历 `books/<书>/prompts/<layer>/*.md`。
规格 §1 原文：「**书级可覆盖：`books/<书名>/prompts/` 下同名文件优先**」——字面读是**扁平放置**。

实测两种布局（临时 home）：
```
扁平放置 books/t/prompts/V5.md        → source=global  生效=False   ← 按规格字面做的，不生效
分层放置 books/t/prompts/review/V5.md → source=book    生效=True    ← 必须按层再分一级
```

**判定：功能 ✅ 实现、文档 ⚠️ 不足**。规格只说"prompts/ 下同名文件"，实际要求 `prompts/<layer>/` 镜像全局结构。这是**规格表述不精确**而非代码错误（代码的 glob 也只能这么写才能支持 5 层同名不同 ID 的场景），但用户照规格字面建目录会以为覆盖生效、实际静默退回全局。建议在规格 02 §1 补一句"目录结构与全局 prompts/ 一致（`prompts/<层>/<ID>.md`）"。

> 另注：`tests/test_prompts.py:40-50` 用的就是分层写法，所以测试是绿的、而规格字面是错的——典型"测试跟着代码走"。

### D-4 prompt_digest = SHA-256 且入 generation_log —— ✅ 实现

| 环节 | 代码位置 | 判定 |
|---|---|---|
| 计算 | `prompts.py:56-57` `hashlib.sha256(text.encode("utf-8")).hexdigest()`（**含 frontmatter 的全文**） | ✅ 64 hex（测试断言 `len==64`，`test_prompts.py:20`） |
| 入 log | `pipeline.py:58-64`（成功）与 `pipeline.py:67-72`（失败）**两条路径都写** `prompt_digest=p.digest` | ✅ |
| 表列 | `db.py:80` `prompt_digest TEXT` | ✅ |
| 可复现性 | 同一细纲 + 同一 digest → 同输入 | ✅ 机制成立 |

> 说明：`digest` 覆盖**含 frontmatter** 的全文件内容（不只是 body）。改 `version` 或 `inputs` 也会改 digest —— 这比规格"文件内容 SHA-256"更严格，属合理实现。

### D-5 温度总表 14 个值逐条对照 —— ✅ 100% 一致

规格 02 §1：`G1=0.8, G2=0.6, G3=0.8, G4=0.7, G5=0.6, G6=0.5, W1=0.75, W2-W5=0.7, V1=0.2, V2=0.4, V3=0.3, V4=0.1, V5=0.4, V6=0.2`

| # | ID | 规格温度 | 实际 frontmatter | 判定 | 证据 |
|---|----|---------|-----------------|------|------|
| 1 | G1 | 0.8 | 0.8 | ✅ | `prompts/generate/G1.md:6` |
| 2 | G2 | 0.6 | 0.6 | ✅ | `prompts/generate/G2.md:6` |
| 3 | G3 | 0.8 | 0.8 | ✅ | `prompts/generate/G3.md:6` |
| 4 | G4 | 0.7 | 0.7 | ✅ | `prompts/generate/G4.md:6` |
| 5 | G5 | 0.6 | 0.6 | ✅ | `prompts/generate/G5.md:6` |
| 6 | G6 | 0.5 | 0.5 | ✅ | `prompts/generate/G6.md:6` |
| 7 | W1 | 0.75 | 0.75 | ✅ | `prompts/write/W1.md:6` |
| 8 | W2 | 0.7 | 0.7 | ✅ | `prompts/write/W2.md:6` |
| 9 | W3 | 0.7 | 0.7 | ✅ | `prompts/write/W3.md:6` |
| 10 | W4 | 0.7 | 0.7 | ✅ | `prompts/write/W4.md:6` |
| 11 | W5 | 0.7 | 0.7 | ✅ | `prompts/write/W5.md:6` |
| 12 | V1 | 0.2 | 0.2 | ✅ | `prompts/review/V1.md:6` |
| 13 | V2 | 0.4 | 0.4 | ✅ | `prompts/review/V2.md:6` |
| 14 | V3 | 0.3 | 0.3 | ✅ | `prompts/review/V3.md:6` |
| 15 | V4 | 0.1 | 0.1 | ✅ | `prompts/review/V4.md:6` |
| 16 | V5 | 0.4 | 0.4 | ✅ | `prompts/review/V5.md:6` |
| 17 | V6 | 0.2 | 0.2 | ✅ | `prompts/review/V6.md:6` |

**差异数：0 / 17**（规格列 14 个档位值，展开 W2-W5 后共 17 个 ID，全部一致）。规格未给温度的四条：S0/S1/S2/F2 均 `1.0`、F1 `0.6`、T1 `0.8` —— 规格无规定，不构成偏差。

**温度真的被用上了**：`pipeline.py:51` `temperature = float(p.fm.get("temperature", 0.7))` → 传进 `cli.chat`（`pipeline.py:55-56`）→ `llm.py:59` 塞进请求体 ✅。不是装饰性字段。

### D-6 model_stage → 模型映射 —— ✅ 实现

| 规格 stage | 默认模型（规格） | 代码落点 | 判定 |
|---|---|---|---|
| plan | DeepSeek | `books.py:66-72` config 默认 `"plan": "deepseek-chat"` | ✅ |
| prose | Gemini | `"prose": "gemini-2.5-pro"` | ✅ |
| polish | Gemini | `"polish": "gemini-2.5-pro"` | ✅ |
| review | DeepSeek | `"review": "deepseek-chat"` | ✅ |
| longctx | Gemini | `"longctx": "gemini-2.5-pro"` | ✅ |

映射逻辑：`pipeline.py:47-50`
```python
model_stage = p.fm.get("model_stage")
model = (settings.load().get("default_model") or ""
         or cfg.get("model_stages", {}).get(model_stage, "deepseek-chat"))
```
`default_model` 一键覆盖全档（README 第 32 行有说明）→ 留空则按书 config 的 `model_stages` 分档 ✅。unknown model_stage 兜底 `deepseek-chat`（静默兜底，规格未要求报错，可接受）。

23 条提示词的 `model_stage` 与主表"模型档"列**全部对得上**（实测逐条：G1-G6/T1/F1=plan、W1-W5=prose、V1/V3/V4/V5=review、V2=polish、V6=longctx、S0/S1/S2/F2=plan 占位）✅。

---

## E. 规格 02 §2 主表 23 条 ID —— ⚠️ 部分实现（ID 无缺无多，可用性有缺口）

### E-1 23 条 ID 一一对应 —— ✅ 无缺无多

规格主表：S0 S1 S2 / G1 G2 G3 G4 G5 G6 / W1 W2 W3 W4 W5 / V1 V2 V3 V4 V5 V6 / F1 F2 / T1 = **23 条**
实际文件：**23 条，集合完全相同**（`tests/test_prompts.py:6-14` 也把 23 个 ID 写死并断言 `ALL_IDS <= set(lib)` 且 `len(lib) >= 23`）✅

### E-2 各条的实际可达性（这是真差异所在）

| ID | 规格里程碑 | 代码调用点 | 用户可达路径 | 判定 |
|---|---|---|---|---|
| S0/S1/S2 | M2 常驻 | `assembler.py:150/151/153`、`assembler.py:292-296` | 每次生成自动注入 | ✅ |
| G1/G2 | M1 | `api.py:341`（通用 `/ai/generate`） | 总览页按钮 `OverviewView.vue:340`（G_LABELS） | ✅ |
| G3 | M1 | 同上 | 同上 | ✅ |
| **G4** | M1 | 同上（能生成） | 按钮存在，**但采纳写回有缺陷** | ⚠️ 见 F-1 |
| **G5** | M1 | 同上 | 按钮存在，**但前端变量不全** | ❌ 见 F-3 |
| G6 | M2 | `pipeline.py:162` | 工作台"生成细纲" | ✅ |
| W1 | M2 | `pipeline.py:388`、`pipeline.py:441` | 工作台生成（自动） | ✅ |
| W2/W4/W5 | M3 | `pipeline.py:388` + `_scene_special_vars` | 工作台下拉（`WorkbenchView.vue:40-42`） | ✅ |
| W3 | M3 | `pipeline.py:367`（前 3 章自动） | 自动 | ✅（README 偏差 7） |
| V1 | M2 | `pipeline.py:707` | 管线自动 | ✅ |
| V2/V3 | M3 | `pipeline.py:797`、`pipeline.py:813` | 工作台润色按钮（V3 在 V2 内串联） | ✅ |
| V4 | M2 | `pipeline.py:951` | 定稿自动触发 | ✅ |
| V5 | M2 | `pipeline.py:585` | 机检/审校自动 + 手动 fix | ✅ |
| V6 | M3 | `pipeline.py:1053` | 卷末自动 + 总览页手动按钮（`OverviewView.vue:426`） | ✅ |
| F1 | M5 | `api.py:754` | 总览页"分析生成特征卡" | ✅ |
| F2 | M5 | `assembler.py:306` | 启用后自动注入 | ✅ |
| **T1** | M4 | **无任何调用点** | **前端无按钮、无变量构造** | ❌ 见 E-3 |

### ❌ E-3：T1 选题六问 —— 后端能调，但 UI 完全没有入口

- 后端：`POST /api/books/{book}/ai/generate`（`api.py:338-348`）**不限定 prompt_id**，传 `T1` + 三个变量可以跑通（实测：补齐 `题材方向/市场观察/对标书` 后进入 502"未配置 API key"——说明已过渲染阶段、卡在真实调用，**链路是通的**）。
- 前端：`OverviewView.vue:50-63` 的 `G_TARGETS` / `G_LABELS` **只有 G1~G5**，没有 T1 按钮；`aiArchive()` 的变量构造分支（`OverviewView.vue:116-144`）也**没有 T1 分支**。
- 规格 02 §2 主表把 T1 标 `M4`；开发计划 §3 M4 写的是"第一本书立项：都市题材选题（**工具外用 T1 对话**）"、§4 收割表标"立项对话（**工具外**或简单页）"。

**判定**：开发计划明确允许"工具外"，所以这**不是缺陷**，而是"T1 目前是纯资产（提示词文件），工具链里无入口"。但 README 第 84 行声称"规格 02 提示词组织/版本/digest/温度总表/词库同源"已按规格实现——**没提 T1 无入口**。如实登记为：**T1 = 提示词已交付、无 UI 入口（开发计划允许工具外使用）**。

---

## F. 规格 02 §4 档案生成链 M1 —— ⚠️ 部分实现（有 3 个实质缺陷）

### F-0 "生成 → UI diff 预览 → 人工改定 → 采纳落盘"框架 —— ⚠️ 无 diff

| 环节 | 代码位置 | 判定 |
|---|---|---|
| 生成 | `api.py:338-348` `POST /ai/generate` → `pipeline._call` | ✅ 记 log、返回 text/digest/tokens |
| UI 预览 | `OverviewView.vue:476-488`（`el-dialog` + `el-input type=textarea` 可编辑） | ✅ 可改 |
| 人工改定 | 同上（textarea 双向绑定 `aiText`） | ✅ |
| 采纳落盘 | `api.py:355-458`（三个 adopt 端点） | ⚠️ 见下各条 |
| **diff 预览** | **无**。`web/package.json` 无 diff 库；`web/src` grep `diff` = 0 命中；后端也不产 diff | ❌ |

UI 自己在 `OverviewView.vue:344` 写着"流程：生成 → **diff 预览** → 人工改定 → 采纳落盘"——但实际弹窗只是**纯文本 textarea**，用户看不到"新内容 vs 现有档案"的差异，只能靠肉眼比对整段。规格 02 §4 末句"生成 → UI diff 预览 → 人工改定 → 采纳落盘"里的 diff 是**明确要求**，README 第 84 行也声称规格 02 已实现。

**影响**：G1/G2 采纳是覆盖式更新（`api.py:367-369` 读现有节 + `update`），用户无法在采纳前看到"哪一节将被改掉"。这是 M1 的可用性缺口，不是崩溃。

### 🐞 F-1：G4 关系网采纳 —— 对已有角色完全不写回，"合并写回关系网络节"未实现

规格 02 §4：**"G4 关系网：【全集】提示词5 原文；结果合并写回各角色的『关系网络』节"**

代码 `api.py:436-458` `adopt_characters`：
```python
if name in characters.list_characters(book):
    continue                      # ← 已是角色就整段跳过，关系表被丢弃
...
characters.create(book, name, role=role)
d = characters.read(book, name)
fm = {k: v for k, v in d.items() if not k.startswith("_")}
characters.write(book, fm, body + "\n")   # ← 直接把整张卡当正文写，不做节合并
```
注释 `api.py:452` 还写着"关系网络矩阵（G4）合并到各角色'关系网络'节"——**代码与注释不符**。

实测（临时 home，走真实 API）：
```
已有 林寒 / 老周 两张卡 → POST /adopt/characters 传 G4 两卡输出
→ 返回 {'ok': True, 'created': []}          ← created 空，即什么都没做
→ "老周隐瞒身世" 是否出现在林寒卡里: False    ← 关系表丢弃
→ characters.relationships("t","林寒") → []   ← 关系网络节仍为空
```
对**新**角色：`created: ['沈九']`，但正文写成了
```
### 沈九
| 对象 | 关系 | 不稳定因素 |
|---|---|---|
| 林寒 | 宿敌 | 旧仇 |
```
——**直接放在文档顶层，没有 `## 关系网络` 节标题**，因此 `characters.relationships()` 的正则（`characters.py:176`，要求 `^##\s*关系网络`）匹配不到 → 返回 `[]`。

**结论**：G4 的输出**在任何情况下都不会进入"关系网络"节**：
- 已有角色 → 被 `continue` 整条丢弃
- 新角色 → 写进正文但没有节标题，`relationships()` 读不出

而 `characters.relationships()` 本身（`characters.py:173-180`）也**无任何调用点**（grep 全仓库仅定义行）——是死代码，装配器的 `char_card_text`（`assembler.py:127-131`）只拼 `## 全文`，不区分节。

**连带影响**：装配器槽 6 `char_cards` 会把整张角色卡（含所有 H2 节全文）塞进上下文（`assembler.py:131`）——如果 G4 修好了、关系表写进卡里，会**导致上下文膨胀**（角色卡永不截断，`assembler.py:30`）。修 G4 时要同步考虑这点。

### 🐞 F-2：G5 采纳会覆盖已有伏笔台账（违反"AI 永远不静默覆盖人工内容"）

规格 02 §4：**"伏笔总表生成后由程序转写入 PLOT_POINTS.md（编号自动分配 FO-xxx）"**
规格 02 §4 末句：**"AI 生成的档案永远不直接静默覆盖人工内容。"**

代码 `api.py:430-431`：
```python
if fo_rows:
    outline.write_plot_points(book, fo_rows)   # ← 整表替换，不合并、不比状态
```
`write_plot_points`（`outline.py:80-83`）是**全量覆写**。

实测（临时 home，真实 API）：
```
采纳前台账: FO-001 = building（推进章 7）、FO-002 = paid
POST /adopt/outline（G5 输出只含 1 条 F1）
返回: {'foreshadows': 1, 'id_map': {'F1': 'FO-001'}}   ← F1 被重新编号成 FO-001
采纳后台账: [('FO-001', 'planted', '')]                ← 状态被重置为 planted！FO-002 消失！
```

三重数据损失：
1. **已有 FO 的 building/paid 状态被重置为 planted**（推进章清空）——直接破坏"paid 不得回 building"的防复活语义（这里是程序自己干的）
2. **未在 G5 输出里出现的 FO 记录被删除**（FO-002 消失）
3. **编号复用**：`FO-{i:03d}` 从 1 重新编（`api.py:420`），旧 FO-001 的内容被新内容替换

此外 `adopt_outline` 还会用 `books.read_meta(book)["_body"]` 覆盖 `logline`（`api.py:427-428`）——实测 `logline` 被写成 `（待填：一句话故事核、核心卖点、金手指概述）` 这个建书占位串，**用户已填的 logline 丢失**。

**这是本次审计最严重的缺陷**：规格明确要求"AI 永不静默覆盖人工内容"，而 G5 采纳（M1 里程碑核心链路）实际是粗暴全量覆盖。触发条件很常见：用户先手工建台账跑了几章，再回头用 G5 重新生成大纲 → 台账进度全丢。

### 🐞 F-3：G5 在前端直接不可用（变量缺 2 个 → 渲染即报错）

`OverviewView.vue:114-144` `aiArchive()` 对 G5 只传 3 个变量：
```ts
vars['目标总章数'], vars['单章字数区间'], vars['logline']
```
但 `prompts/generate/G5.md` 需要 5 个：`〖世界观摘要〗 〖角色卡摘要〗 〖logline〗 〖目标总章数〗 〖单章字数区间〗`（`G5.md:15-18`）。

实测渲染：
```
G5 render FAILED -> PromptError: G5: 未提供变量 ['世界观摘要', '角色卡摘要']
```
→ `prompts.render` 抛错（`prompts.py:107-108`）→ `api.py:345-346` 捕获 `PromptError` 返回 **HTTP 404** → 用户点"G5 全书大纲"按钮，**必定拿到 404 报错**，永远用不了。

对比：`scripts/e2e_test.py:102-107` 传了全部 5 个变量（所以 e2e 能过），而**前端漏了**——典型"脚本绿、UI 红"。同时 `G3/G4` 分支把 `世界观摘要` 硬编码成字符串 `'见圣经'`（`OverviewView.vue:138`），是**无意义占位**（模型收到"见圣经"三个字当世界观摘要），不算报错但质量受损。

### F-4 各 G 链的变量完整性核对（附证据）

| ID | prompt 需要的变量 | 代码/前端传参位置 | 判定 |
|---|---|---|---|
| G1 | 题材/故事核/对标书/既有设定摘要 | `OverviewView.vue:121-133`（全部构造） | ✅ |
| G2 | 题材/基础设定/既有力量体系 | `OverviewView.vue:121-133` | ✅ |
| G3 | 世界观摘要/角色需求清单 | `OverviewView.vue:134-144`（世界观摘要=占位串"见圣经"） | ⚠️ 质量 |
| G4 | 角色卡合集 | `OverviewView.vue:137` | ✅ 变量够 |
| **G5** | 世界观摘要/角色卡摘要/logline/目标总章数/单章字数区间 | `OverviewView.vue:116-120` **只传 3 个** | ❌ 必定 404 |
| G6 | 10 个变量 | `pipeline.py:144-155`（逐个构造） | ✅ |
| F1 | 语料样本 | `api.py:754` | ✅ |
| T1 | 题材方向/市场观察/对标书 | **无调用点** | ❌ 无入口（开发计划允许） |

### F-5 G2 的"单节采纳" —— ✅ 实现且合理

`api.py:355-370` `adopt_bible`：
- 解析 H2 节 → **剥离所有含"自检"的节**（`api.py:360`，对应 G1/G2 提示词里的 `## 自检（入库前程序剥离）`）
- G2 输出没有 H2 标题时，检测到 `大境界` 就整体收进 `力量体系` 节（`api.py:362-364`）——**专门为 G2 的表格形态做了兜底**，是认真实现
- `sections.update(incoming)`（`api.py:367-368`）→ **只更新出现的节，其余保留** ✅ 符合"不静默覆盖人工内容"

实测 e2e 脚本 `scripts/e2e_test.py:81-90` 也验证了这条链路 ✅。

---

## G. M5 文风指纹 —— ✅ 实现（**远不止"仅提示词"，超出任务预期**）

任务描述预判"M5 只有提示词没有引擎可能是合理状态"。**实际不是**——M5 有完整引擎。

| 规格 02 要求的环节 | 代码位置 | 判定 |
|---|---|---|
| `books/<书名>/style/` 目录 | `ainovel/style.py:11-12` `style_dir()`；规格 01 §1 也画了这个目录 | ✅ |
| 语料导入（txt → 语料.txt） | `style.py:23-30` `read_corpus` / `write_corpus`（`atomic` 原子写） | ✅ |
| 特征卡落盘 | `style.py:33-40` `read_card` / `write_card` → `style/特征卡.md` | ✅ |
| F1 文风分析调用 | `api.py:745-758` `POST /style/analyze`：语料 <500 字拒绝（`api.py:751-752`）、截 60000 字、`pipeline._call(... "F1", {"语料样本": ...})` **计入 generation_log** | ✅ |
| F2 注入装配器 | `assembler.py:290-309` `render_system()`：S0+S1+S2 + **`if style_mod.enabled(book)` 时 render F2**（`assembler.py:298-306`） | ✅ |
| 启用开关 | `style.py:43-52` `enabled()`（要求卡存在 + config `style_enabled`）、`api.py:765-772`（无卡则拒绝启用） | ✅ |
| UI 面板 | `OverviewView.vue:357-380`：语料 textarea / 保存 / "分析生成特征卡(F1)" / 开关 / 特征卡 textarea / 保存 | ✅ 完整 |
| 装配器槽位注入 | 走 **system 段**（不是独立槽位）——开发计划 §3 M5 写"新增槽位 4.5"，实际实现是 system message 追加 | 🔶 实现方式偏差，效果等价 |
| 测试 | `tests/test_style.py:6-37`（语料→F1→卡→开关→注入断言） | ✅ |
| MockLLM 支持 F1 | `mockllm.py:60`（识别"分析以下语料"→返回四部分卡） | ✅ |

**额外发现（规格 02 没要求，代码自己加的）**：
1. `pipeline.py:304-322` `_style_excerpt()` —— 从语料**随机取 600 字真实原文**做 few-shot 范文，注入 W1 的 `〖范文样本〗`（`pipeline.py:369-371`、`prompts/write/W1.md:21-22`）。这是"模仿目标文风的真实段落"的强化实现。
2. `pipeline.py:325-342` `_style_points()` —— 从特征卡抽 `## 句法特征` + `## 禁忌清单` 两节，注入 V1 的 `〖文风要点〗`（`pipeline.py:701`），让审校按**目标文风**而非通用标准评分（`prompts/review/V1.md:28` 明确写"作者目标文风的特征不算 AI 味"）。
3. `pipeline.py:540-545` `_lint_whitelist()` —— 书级 `lint_whitelist` 豁免：目标文风作者的招牌词与通用禁词冲突时豁免（用于 V2 禁词表 `pipeline.py:789-790` 与 mechcheck `pipeline.py:550`）。

**这三处是超出规格的实现**，说明 M5 不是"仅提示词"而是**按"后期增强"标准做完了**。开发计划 §3 M5 标注"（后期增强，已确认延后）"，但代码实际已实现主体 —— 属**"延后决策但提前做了"**，不是缺陷。

唯一名不副实的地方：开发计划说"注入装配器（**新增槽位 4.5**）"，实际是拼进 system message（`assembler.py:290-309`）。槽位数仍是 13（`test_api.py:56` 断言 13），所以**规格 03 的 13 槽位没有被破坏** —— 这个选择反而更安全（不用改槽位表）。🔶 记为"实现方式与开发计划措辞不同，结果等价且不破坏规格 03"。

---

## H. 提示词渲染与变量调用点 —— ✅ 实现（2 处轻微）

### H-1 〖变量〗严格渲染 + 残留报错 —— ✅ 实现

| 规格要求 | 代码位置 | 判定 |
|---|---|---|
| 变量语法 `〖变量名〗` | `prompts.py:26` `_VAR_RE = re.compile(r"〖([^〗]+)〗")` | ✅ |
| 未提供的变量 → 报错（不静默发给模型） | `prompts.py:99-108`：收集 `missing` 并 `raise PromptError(f"{prompt_id}: 未提供变量 {sorted(missing)}")` | ✅ |
| 测试 | `tests/test_prompts.py:29-37`（缺变量抛错 + 填充后无 `〖`） | ✅ |

实测确认有效：G5/T1 缺变量时确实抛 `PromptError`（见 F-3），说明**守门生效**（这也是 G5 前端必定失败的原因——守门是正确的，错的是一方没传）。

### H-2 悬空变量核对（"提示词里要求〖X〗但代码从不传"）—— **0 个悬空**

逐条把 23 个 prompt 的 `〖变量〗` 与代码调用点对照：

| prompt | 变量（去重） | 传参代码位置 | 判定 |
|---|---|---|---|
| S0 | （无） | `assembler.py:150` | ✅ |
| S1 | 禁词表、对话后缀禁式 | `assembler.py:279-287` `_s1_vars()` ← `mechcheck.load_banned_words()` / `load_dialog_patterns()` | ✅ 词库同源 |
| S2 | （无） | `assembler.py:153` | ✅ |
| F2 | 文风特征卡 | `assembler.py:306` `{"文风特征卡": style_card}` | ✅ |
| F1 | 语料样本 | `api.py:754` | ✅ |
| G1 | 题材、故事核、对标书、既有设定摘要 | `OverviewView.vue:121-133` | ✅ |
| G2 | 题材、基础设定、既有力量体系 | `OverviewView.vue:121-133` | ✅ |
| G3 | 世界观摘要、角色需求清单 | `OverviewView.vue:134-144` | ✅（值是占位串，但变量传了） |
| G4 | 角色卡合集 | `OverviewView.vue:137` | ✅ |
| G5 | 世界观摘要、角色卡摘要、logline、目标总章数、单章字数区间 | `OverviewView.vue:116-120` **缺 2 个** | ❌ 见 F-3 |
| G6 | 全局大纲该章行、所属卷目标、上一章钩子、出场角色状态、活跃伏笔线程、章号、总章数、字数区间、上一章场景结构、上一章钩子类型 | `pipeline.py:144-155`（10 个逐个构造） | ✅ 全传 |
| T1 | 题材方向、市场观察、对标书 | **无调用点** | ❌ 无入口 |
| W1 | 系统段、装配上下文、章合同、范文样本、字数区间 | `pipeline.py:369-378` | ✅ 全传（`范文样本` 来自 `_style_excerpt`） |
| W2 | 系统段、章合同、本场景人物状态、本场景潜台词要求 | `pipeline.py:369-374` + `_scene_special_vars` `pipeline.py:468-472` | ✅ |
| W3 | 系统段、章号、装配上下文、章合同、字数区间 | `pipeline.py:369-380` | ✅ |
| W4 | 系统段、装配上下文、章合同、力量体系相关行、突破代价设定 | `pipeline.py:480-482` | ✅ |
| W5 | 系统段、装配上下文、章合同、对抗双方状态与底牌、已埋伏笔清单 | `pipeline.py:483` | ✅ |
| V1 | 系统段、正文、章合同、疑似冲突清单、台账摘要、字数区间、程序字数、文风要点 | `pipeline.py:691-702`（8 个逐个构造） | ✅ |
| V2 | 系统段、正文、禁词表、对话后缀禁式 | `pipeline.py:791-796` | ✅ |
| V3 | 润色后全文、改动清单、禁词表 | `pipeline.py:813-815` | ✅ |
| V4 | 正文、章号、现有角色名清单、活跃伏笔编号清单 | `pipeline.py:940-947` | ✅ |
| V5 | 正文、问题清单、字数区间 | `pipeline.py:580-584` | ✅ |
| V6 | 系统段、本卷全部正文、伏笔台账全表、主要角色弧光预设、世界观圣经 | `pipeline.py:1043-1052` | ✅ |

**悬空变量总计：0**（除 G5 前端漏传 2 个、T1 无入口这两处已在 F/E 记录）。`tests/test_prompts.py:29-37` 的严格渲染测试是这条承诺的真正保障。

**附带发现（frontmatter `inputs` 与正文变量不同步 2 处）**：
- `prompts/review/V1.md:8` `inputs` 没列 `文风要点`，但正文 `V1.md:28` 用它
- `prompts/write/W1.md:8` `inputs` 没列 `范文样本`，但正文 `W1.md:22` 用它

两者渲染都不受影响（`inputs` 只是元数据，`render` 只看正文里的 `〖〗`），但**文档与实现不同步**。

### H-3 渲染产物包含 frontmatter —— ⚠️ 轻微（可接受）

`prompts.render` 用的是 `p.full_text()`（`prompts.py:96` → `prompts.py:46-47` `FM.dump(self.fm, self.body)`），即**把 YAML frontmatter 一起渲染并作为 user message 发出**。

实测（V5 渲染结果开头）：
```
---
id: V5
name: 定向修复
stage: review
model_stage: review
temperature: 0.4
version: 2
inputs: [正文, 问题清单, 字数区间]
output: 修复后全文
---
```
影响：每次调用多消耗约 60-100 token 的元数据，且模型可能把 `id: V5 / output: 修复后全文` 当指令读（对格式类 prompt 或许是"有益的误导"）。**无功能损坏**，但属无谓成本与潜在干扰。规格未明确要求"只发 body"，故判 ⚠️ 不判 ❌。

### H-4 残留 `〖〗` 检测是"单次"而非"最终校验" —— ⚠️ 轻微

规格 02 §1 说"渲染后仍残留 〖〗 视为错误"（AGENTS.md 同样表述）。`prompts.py:106-108` 的做法是：**替换时**收集未提供的变量名，有则报错。但：
1. **变量值本身含 `〖〗` 不会被查**：实测传 `正文="含〖伪变量〗正文"` → 渲染后文本仍含 `〖伪变量〗`，**无报错**（`_VAR_RE.sub` 一次替换完就不再扫）。
2. 空名 `〖〗` 不被识别为变量（`_VAR_RE` 要求 `[^〗]+`，至少 1 字符）。

`tests/test_prompts.py:36` 只断言了 `"〖" not in text`（正常填充场景），覆盖不到上述两种。属轻微，实际触发概率低（除非 AI 生成内容里带 `〖〗`，比如正文里出现中文方括号括起来的注释）。建议：渲染后加一次 `if _VAR_RE.search(rendered): raise`。

---

## 23 条提示词清单表（含证据与状态）

| # | ID | 名称 | 目录 | stage | model_stage | temp | ver | 变量数 | 调用点 | 状态 |
|---|----|------|------|-------|-------------|------|-----|--------|--------|------|
| 1 | S0 | 系统角色定位 | system | system | plan | 1.0 | 2 | 0 | `assembler.py:150,292` | ✅ |
| 2 | S1 | 全局写作规则 | system | system | plan | 1.0 | 4 | 2 | `assembler.py:151,293` | ✅ 词库同源 |
| 3 | S2 | 都市题材适配 | system | system | plan | 1.0 | 2 | 0 | `assembler.py:153,295` | ✅ |
| 4 | F2 | 文风特征卡注入段 | system | system | plan | 1.0 | 2 | 1 | `assembler.py:306` | ✅ M5 |
| 5 | G1 | 世界观圣经生成 | generate | generate | plan | 0.8 | 2 | 4 | `OverviewView.vue:340`→`api.py:341` | ✅ |
| 6 | G2 | 力量体系设计 | generate | generate | plan | 0.6 | 2 | 3 | 同上 | ✅ |
| 7 | G3 | 角色卡生成 | generate | generate | plan | 0.8 | 2 | 2 | 同上 | ✅ |
| 8 | G4 | 关系网络生成 | generate | generate | plan | 0.7 | 2 | 1 | 同上 | ⚠️ 采纳不写回（F-1） |
| 9 | G5 | 全书大纲生成 | generate | generate | plan | 0.6 | 2 | 5 | 同上但**前端缺 2 变量** | ❌ 必定 404（F-3） |
| 10 | G6 | 单章细纲（章合同） | generate | generate | plan | 0.5 | 2 | 10 | `pipeline.py:162` | ✅ |
| 11 | T1 | 选题六问 | generate | generate | plan | 0.8 | 2 | 3 | **无调用点** | ❌ 无 UI 入口（E-3） |
| 12 | W1 | 章节正文生成 | write | write | prose | 0.75 | 4 | 5 | `pipeline.py:388,441` | ✅ |
| 13 | W2 | 对话专场 | write | write | prose | 0.7 | 2 | 4 | `pipeline.py:388`+`468` | ✅ |
| 14 | W3 | 黄金三章专项 | write | write | prose | 0.7 | 2 | 5 | `pipeline.py:367` | ✅ 前 3 章自动 |
| 15 | W4 | 突破场景 | write | write | prose | 0.7 | 2 | 5 | `pipeline.py:388`+`480` | ✅ |
| 16 | W5 | 高潮场景精细 | write | write | prose | 0.7 | 2 | 5 | `pipeline.py:388`+`483` | ✅ |
| 17 | V1 | 章内审校 | review | review | review | 0.2 | 2 | 8 | `pipeline.py:707,727` | ✅ |
| 18 | V2 | 整章润色去AI味 | review | review | polish | 0.4 | 3 | 4 | `pipeline.py:797` | ✅ |
| 19 | V3 | AI味自检复核 | review | review | review | 0.3 | 2 | 3 | `pipeline.py:813` | ✅ |
| 20 | V4 | 记忆卡抽取 | review | review | review | 0.1 | 2 | 4 | `pipeline.py:951` | ✅ |
| 21 | V5 | 定向修复 | review | review | review | 0.4 | 2 | 3 | `pipeline.py:585` | ✅ |
| 22 | V6 | 卷末跨章审计 | review | review | longctx | 0.2 | 2 | 5 | `pipeline.py:1053` | ✅ |
| 23 | F1 | 文风分析→特征卡 | style | style | plan | 0.6 | 2 | 1 | `api.py:754` | ✅ M5 |

> `ver` 列即 frontmatter `version`；W1=4、S1=4、V2=3 是因原文档融合迭代过（README 偏差 1 说明）。

---

## 死代码 / 定义了没接线（任务第 3 点要求）

| 符号 | 位置 | 引用数 | 判定 |
|---|---|---|---|
| `POWER_COLS` | `bible.py:10` | **1（仅定义）** | ❌ 死代码 —— 力量体系 8 列从不校验 |
| `HIDDEN_COLS` | `bible.py:11` | **1（仅定义）** | ❌ 死代码 |
| `TERM_COLS` | `bible.py:12` | **1（仅定义）** | ❌ 死代码 |
| `update_section()` | `bible.py:40-43` | **1（仅定义）** | ❌ 死代码 —— 单节更新有实现但无人用（`adopt_bible` 自己走 read+update+write） |
| `characters.relationships()` | `characters.py:173-180` | **1（仅定义）** | ❌ 死代码 —— 关系网络读取有实现但无人用（也读不出，见 F-1） |
| `mdtable.split_pipes()` | `mdtable.py:45-46` | **1（仅定义）** | ❌ 死代码 |
| `_realm_index()` 的兜底 | `characters.py:166-170` | 用于 realm_regress | ✅ 活代码 |
| `db._derive_status()` | `db.py:291-309` | 3（定义 + `_scan_chapters` + docstring） | ⚠️ 只在 INSERT 分支生效（见 C-1） |
| `GET /api/books/{book}/locations` | `api.py:303-305` | 前端 0 调用 | ⚠️ 孤儿接口（无 UI 消费） |
| `GET /api/prompts`、`GET /api/prompts/{id}` | `api.py:312-326` | 前端 0 调用 | ⚠️ 孤儿接口（提示词库无 UI 浏览页） |
| `GET/PUT /api/books/{book}/config` | `api.py:192-202` | 前端 0 调用 | ⚠️ 孤儿接口 —— **`model_stages` / `chapter_word_range` / `review_fix_max_rounds` / `assembly_budget_overrides` / `style_enabled` / `lint_whitelist` 在 UI 里都无法编辑**，只能手改 `config.json` |
| `GET/PUT /api/books/{book}/meta` | `api.py:205-217` | 前端 0 调用（`putMeta` 不在 `api/index.ts`） | ⚠️ 孤儿接口 —— 规格 01 §2.1 的 meta 字段在 UI 里**全部不可编辑**（只能建书时填） |
| `POST /api/books/{book}/chapters/{no}/retry` | `api.py:686-692` | 前端 0 调用 | ⚠️ 孤儿接口（error 状态无 UI 重试按钮） |

### 附带：`put_character` 会把内部字段 `_file` 写进 frontmatter

`api.py:252-259` 只 pop 了 `_body`，没 pop `_file`；而 `characters.read()` 返回 `fm | {"_body":..., "_file":...}`（`characters.py:46`），前端 `OverviewView.vue:224-227` 是 `const fm = {...c}; delete fm._body`（**没删 `_file`**）。

实测（临时 home，复刻前端往返）：
```
GET /api/books/t/characters/林寒 返回键: ['_body', '_file', 'name', 'role', 'status']
PUT → 200
char-林寒.md frontmatter 末尾出现:
  _file: C:\Users\...\Temp\tmpXXXX\books\t\characters\char-林寒.md
```
影响：① `_file` 泄漏到档案文件（含**本机绝对路径**，违反"可 git、可手修"的初衷，换机器后是死路径）；② 该字段不在 `CHAR_SCHEMA` 里所以不报错，会一直累积。属真实缺陷（**P2**，不影响功能正确性但污染档案）。

---

## 影响用户的功能缺口（按严重程度排序）

### 🔴 P0 —— 会丢数据 / 功能必定不可用

1. **G5 大纲采纳覆盖已有伏笔台账**（`api.py:430-431` + `outline.py:80-83`）
   - 实证：`FO-001(building,推进章7) + FO-002(paid)` → 采纳后只剩 `FO-001(planted)`，FO-002 消失、状态重置、编号复用
   - 同时 `logline` 被建书占位串覆盖（`api.py:427-428`）
   - **直接违反规格 02 §4 末句"AI 生成的档案永远不直接静默覆盖人工内容"**
   - 修法：`write_plot_points` 改为按编号**合并**（已有编号保留状态、只补空字段；新编号追加），或采纳前要求用户确认"将重置 N 条台账"

2. **G5 在前端必定 404**（`OverviewView.vue:116-120` 少传 `世界观摘要`/`角色卡摘要`）
   - 实证：`PromptError: G5: 未提供变量 ['世界观摘要','角色卡摘要']` → `api.py:345-346` → HTTP 404
   - 用户点"G5 全书大纲"→ 必失败，M1 四条生成链缺一条
   - 修法：补两个变量（可从 `getBible` 的基础设定 + `listCharactersFull` 拼摘要）

3. **OUTLINE 保存污染 frontmatter + 丢节奏总谱**（`api.py:276-284` + `outline.py:28-44`）
   - 实证：保存后 frontmatter 出现巨型 `_body` 键，正文区`## 爽点节奏总谱`变成`（待规划）`
   - 修法：`api.py` 里 `payload.pop("_body", None)`（照 `put_meta` 的做法），并把原节奏总谱经 `_rhythm` 传回

### 🟠 P1 —— 规格承诺的功能实际没生效

4. **G4 关系网永不写回"关系网络"节**（`api.py:449-457` + `characters.py:173-180`）
   - 已有角色被 `continue` 跳过；新角色写进正文但无 `## 关系网络` 标题 → `relationships()` 返回 `[]`
   - 注释 `api.py:452` 声称"合并到各角色关系网络节"与实际不符
   - 连带：`relationships()` 是死代码

5. **Bible 七节 / 力量体系 8 列 / 隐藏规则 3 列 / 术语表 2 列全无校验**（`bible.py:10-12` 常量零引用）
   - 后果：列名写错 → 境界倒退检测（`characters.py:139-146`）与术语比对（`pipeline.py:669-674`）**静默失效**，无任何报错

6. **`rebuild()` 不刷新已存行的 status、不清理残留章节行**（`db.py:246-252`）
   - 实证：污染成 `human_review` 后 rebuild 仍是 `human_review`（文件证据是 final）；无文件的第 2 章行残留
   - 真·删库重建没问题（已验证 `finalized`），但 UI 暴露的 `POST /api/db/rebuild` 按钮给出的是"报告成功、状态没修"

7. **M1 无 diff 预览**（`web/src` grep diff = 0）
   - 规格 02 §4 明确要求"UI diff 预览"，实际只有纯文本 textarea；G1/G2 覆盖式采纳时用户看不到"哪节将被改"

### 🟡 P2 —— 数据卫生 / 可用性

8. `put_character` 把 `_file`（含本机绝对路径）写进角色卡 frontmatter（`api.py:252-259` + `OverviewView.vue:224-227`）
9. 悬空预警不展示：`apply_foreshadow_ops` 的 `overdue_warnings` 算出来、API 返回，但前端 0 处展示（`pipeline.py:996`、`web/src` 无 warnings）
10. 渲染把 frontmatter 一并发给模型（`prompts.py:46-47,96`），每次多 60-100 token 元数据
11. 残留 `〖〗` 无最终校验：变量值里含 `〖〗` 不会被发现（`prompts.py:106-108`）
12. `inputs`/`output` 不在 `REQUIRED_FM`（`prompts.py:24`），缺失不会被发现；`V1` 的 `inputs` 漏 `文风要点`、`W1` 漏 `范文样本`
13. 书级覆盖的目录约定（`prompts/<layer>/<ID>.md`）与规格 02 §1 字面（`prompts/` 下同名）不符，照规格做会静默失效
14. 大量孤儿接口：`config`/`meta`/`locations`/`prompts`/`retry` 均无 UI 消费 → 规格 01 的 meta 字段、书级 `model_stages`、字数区间、`review_fix_max_rounds`、`lint_whitelist` 在 UI 上**全都改不了**（只能手改文件）

### ⚪ 非缺陷（README 已定案偏差，明确不报）

- characters 由单文件改目录（规格 01 §2.3 已写明）
- FTS 加 `book_id` 列（README 偏差 5）
- trigram 不可用降级 bigram + 2 字词 Python 兜底（README 偏差 5）
- rebuild 保留 generation_log/snapshots/review_records（README 偏差 6）
- 前 3 章走 W3（README 偏差 7）
- 23 条提示词已按 `docs/` 原文档融合（README 偏差 1）
- M5 "已确认延后"但实际已实现（提前做了，属加分）
- T1 无 UI 入口（开发计划 §3 M4 明确"工具外用 T1 对话"）

---

## 附：本次审计的实测清单（可复现）

所有实测都在 `tempfile.mkdtemp()` 的 `AINOVEL_HOME` 里跑（拷 prompts/lint），未触碰仓库 `books/`、`data/`，未运行 `run.py`：

| 实测 | 结论 |
|---|---|
| `pytest tests/test_prompts.py test_characters.py test_db_rebuild.py test_style.py test_mdtable.py test_frontmatter.py test_api.py` | **36 passed** |
| `bible.write({'基础设定':'x'})` | 接受（缺 6 节不报错） |
| 力量体系错误列名 → `power_rows()` / `realm_order()` | 均返回 `[]`，无报错 |
| `prompts.render("G5", 前端所传 3 变量)` | `PromptError: 未提供变量 ['世界观摘要','角色卡摘要']` |
| `prompts.render("T1", {})` | `PromptError: 未提供变量 [...]`（且前端无 T1 按钮） |
| G4 采纳（已有 2 角色） | `created: []`，关系表丢弃，`relationships()` → `[]` |
| G4 采纳（新角色） | 卡正文无 `## 关系网络` 标题 → `relationships()` → `[]` |
| G5 采纳（已有 FO-001 building + FO-002 paid） | 只剩 `FO-001(planted)`，`logline` 被占位串覆盖 |
| UI 保存 OUTLINE 往返 | frontmatter 出现 `_body`，正文节奏总谱变 `（待规划）` |
| 角色卡 UI 往返 | frontmatter 被写入 `_file: C:\Users\...\char-林寒.md` |
| 污染 DB status 后 `db.rebuild()` | status 不变（仍 `human_review`）；无文件章节行残留 |
| **真删 `.db` 文件**后 `db.rebuild()` | `finalized` ✅（真·删库路径正常） |
| 书级覆盖扁平布局 `prompts/V5.md` | `source=global`（不生效） |
| 书级覆盖分层布局 `prompts/review/V5.md` | `source=book`（生效） |
| 渲染产物开头 | 含完整 YAML frontmatter |
| 渲染时变量值含 `〖伪变量〗` | 无报错，原样留在结果里 |
| 细纲 `confirmed` 锁（outline_locked 下 PUT） | 400 拒绝 ✅；但库层直调可绕过 |
| 温度 17 项逐条对照 | **0 差异** |
| frontmatter `stage` vs 目录 / `id` vs 文件名 | 23 条全部一致 |
| `prompts/*.md` 文件数 | **23** |
