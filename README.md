# AI 网文创作工作台

依据 `docs/specs/` 四份规格实现的本地单机工具：文件档案 + SQLite 索引/状态机 + 23 条提示词体系 + 单章生成闭环管线 + Web UI。

## 快速开始

```bash
pip install -r requirements.txt
python run.py            # 打开 http://127.0.0.1:8765
```

首次使用：进入 **设置** 页填 API key 与 base_url（OpenAI 兼容协议，官方/第三方中转站通用）→ 可选填「默认模型」一键覆盖全部档位 → 点"测试连接"。

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
python -m pytest tests/          # 74 项全绿（管线全链路用 MockLLM，不耗 token）
python scripts/smoke.py          # 端到端冒烟（跑在临时目录，不污染仓库）
```

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
  llm/mockllm       DeepSeek(OpenAI兼容)/Gemini(SSE流式) 客户端 + 测试 Mock
  pipeline          管线编排（细纲→正文→机检→审校→定稿→回写→卷末审计）
  conflicts         人工裁决队列
  api               全部 HTTP 路由 + SSE
prompts/            23 条提示词（文件化，可整体替换、书级覆盖）
lint/               禁词/敏感词/对话后缀禁式（每行一个，# 注释）
web/                静态 UI（免构建）
books/<书名>/       数据：档案 + 章节 + config.json（单一真相源）
data/               SQLite、设置、裁决队列、装配预览（可随时重建）
tests/              pytest（74 项）
```

## 与规格的对应与偏差（重要）

**已按规格实现**：规格01 全部文件格式/表结构/状态机/防损规则；规格02 提示词组织/版本/digest/温度总表/词库同源；规格03 管线各环节/机械检查/异常处理/成本/断点恢复/rubric锚点；规格04 页面清单（书架/书总览/档案编辑/章节工作台/裁决/设置/导出）。

**偏差与修正（详见交付报告）**：

1. ~~【豆包】【全集】原文档不在仓库~~ **已修正（2026-10-25）**：两份原文档一直在 `docs/` 根目录（初版实现时误判缺失，因只检查了 `docs/specs/`）。**23 条提示词已全部按原文替换/融合**：S0/S1/S2=豆包系统层原文；G1=T2、G2=T3、G3=T4、G4=【全集】提示词5、G5=T5、G6=T6+T6-B、T1=T1+T1-B；W1=T7、W2=T7-B、W3=T7-C、W4=T7-D、W5=【全集】提示词9；V1=T9-B七项+提示词10+rubric；V2=T8-A七步+T8-B对照表；V3=T8-C清单；V4=T9-C；V5=新写（依据§7.4，规格02 明示为唯一新写）；V6=T9-D；F1=T10-A、F2=T10-B；lint 禁词表=S1 词表+T8-B 左列
2. ~~上游《开发计划.md》缺失~~ **已修正（2026-10-25）**：文档在 `docs/开发计划.md`。13 槽位已按其 §2.3 原文表对齐（系统角色/全局硬规则/题材适配/Bible条目/力量体系行/角色状态卡/卷节奏/活跃伏笔/前情摘要/前文检索/上章末尾/细纲合同/输出要求）；角色卡按规格03 保持永不截断（开发计划的 ≤150 字×≤4 人预算被规格03 覆盖）
3. 状态机补了图上未画但语义必需的迁移：`mech_check 修复一轮仍阻断→human_review`、`llm_review pass→pending_finalize`、`outline_draft 重新生成细纲`
4. 章节中段瞬态崩溃后的恢复停靠点：`generating 中断→mech_check`（重跑机检即可续）、文件损坏→human_review
5. FTS 表加 `book_id` 列（多本书共存必需）；trigram 模式下 2 字关键词（角色名）走 Python 子串兜底
6. rebuild 保留 generation_log/snapshots/review_records（运行历史不在文件里，DB 文件丢失则该历史不可恢复）
7. 前 3 章直接走 W3 黄金三章专项提示词（规格02 P-W1 改造点3 的等价实现）
8. 卷首新建章节的 `pending_finalize` 与 `mech_check` 在 rebuild 时不可区分 → 统一停靠 mech_check

## M0 检查单（规格04 §4）状态

| 项 | 状态 |
|----|------|
| DeepSeek 连通 | ✅ 代码就绪（OpenAI 兼容流式），设置页填 key 后"测试连接"验证 |
| Gemini 连通 | ✅ 代码就绪（原生 SSE 流式，base URL 可改走代理） |
| FTS5 trigram 中文检索 | ✅ 已实测（SQLite 3.49.1 trigram 可用；bigram 降级路径也有实现与提示） |
| Windows 打包 exe | ⚠️ 提供 `ainovel.spec` + 打包说明；PyInstaller 构建未在本机实测（见下） |

打包：`pip install pyinstaller && pyinstaller ainovel.spec`，然后把 `prompts/ lint/ web/` 三个目录拷到 `dist/ai-novel/` 下（数据目录 books/data 自动在 exe 旁创建）。

## lint 词库说明

`lint/*.txt` 为**种子词表**（原文档 S1/T8-B 不在仓库）。设置页可在线编辑，与 S1 提示词同源注入——改完词库后新生成的正文即受新词表约束。
