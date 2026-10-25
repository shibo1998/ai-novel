# AGENTS.md — AI 编码助手项目约束

本地单机 AI 网文创作工作台：文件档案 + SQLite 索引/状态机 + 23 条提示词体系 + 单章生成闭环管线 + Web UI（前端 Vue3 + TypeScript + element-plus + Vite，见规格04 §0）。Windows 主环境，Python ≥3.11，Python 依赖仅 requirements.txt 里 5 个包（不要引入新 Python 依赖，除非规格明确要求且无标准库替代）；前端依赖独立在 `web/package.json`，不受该条约束。

## 规格是唯一真相源

- `docs/specs/` 四份规格（01 数据与档案 / 02 提示词体系 / 03 管线 / 04 UI 与验收）是需求权威。改动涉及**文件格式、表结构、状态机、提示词、管线流程、UI 页面**时，先读对应规格再动手。
- README「与规格的对应与偏差」列出的偏差条目是**已定案的有意决策**，不是 bug，不要"顺手修复"。改到相关代码时保持偏差行为，并同步更新该偏差记录。
- 23 条提示词中核心 13 条已按 `docs/` 原文档（【豆包】【全集】）替换/融合（S 链/W 链/V1/V2/V3，见 README 偏差 1）；其余按规格 schema 新写。继续融合原文时**不要增删或重命名提示词文件**，只改内容并递增 frontmatter `version`。

## 命令

```bash
python -m pytest tests/        # 全绿为验收线（当前基线 104 项，只增不减）
python scripts/smoke.py        # 端到端冒烟，跑在临时目录，不污染仓库
python run.py                  # 启动 Web UI（http://127.0.0.1:8765）
cd web && npm install          # 首次或依赖变更后
npm run build                  # 前端构建（产物 web/dist/，FastAPI 静态挂载）
npm run lint                   # 前端 ESLint 检查 + 自动修复
npm run format                 # 前端 Prettier 格式化
npm run typecheck              # 前端严格类型检查（vue-tsc）
npm run dev                    # 前端开发模式（/api 代理到 8765，与 python run.py 并行）
```

日常开发验证只用 pytest + smoke。**不要运行 run.py**：它读写仓库真实的 `books/`、`data/`，还会自动开浏览器。

## 架构核心（为什么这么设计）

1. **文件是真相，DB 是索引。** `books/<书名>/` 下的 Markdown + YAML frontmatter 是单一真相源；`data/` 下的 SQLite 可随时删掉重建。任何数据逻辑改动都必须保证"删库 → rebuild 完整恢复"（`tests/test_db_rebuild.py` 守这条线）。唯一例外是运行历史：generation_log / snapshots / review_records 只存 DB，丢了不可恢复。
2. **档案写入必须走 `atomic.write_text` + frontmatter 校验**，禁止直接 `open(..., "w")` 绕过（防写一半崩溃损坏档案，规格01 §5.1）。写文件一律显式 `encoding="utf-8"`、`newline=""`（Windows 防自动转 CRLF）。
3. **人工关不许自动化。** 细纲确认、章节定稿是两个人工确认点。M-04 敏感词命中必须阻断转人工、**禁止自动改写**；V5 越权护栏（未涉及句段改动率 >10% 丢弃输出）不许放宽；裁决队列非空时禁止生成下一章细纲。这些是产品红线，不是可优化项。
4. **所有 LLM 调用必须记 generation_log**（prompt_digest / tokens / cost），豁免必填理由入 log。

## 测试约定

- 新测试必须用 `conftest.py` 的 `home` fixture（拷贝 prompts/lint 到临时目录并设 `AINOVEL_HOME`），禁止读写真实 `books/`、`data/`。
- 测试一律用 `mockllm`，禁止真实网络 / LLM 调用（不耗 token，且必须可离线跑）。

## 环境怪癖（代码看不出来的）

- 路径解析顺序：`AINOVEL_HOME` → PyInstaller frozen（exe 所在目录）→ 包父目录。改路径逻辑要同时照顾三种模式。
- FTS5 trigram 不可用时自动降级 bigram；trigram 模式下 2 字关键词（角色名）走 Python 子串兜底——这是有意设计，删掉会坏中文角色名检索。
- `web/` 是 Vue3 + TS + vue-router + pinia + element-plus + vite + sass + tailwindcss 前端工程（规格04 §0）。UI 改动走 npm 流程，`npm run build` 通过才算完成；`web/dist/` 是构建产物**禁止手改**；改页面涉及接口时以 `ainovel/api.py` 契约为准。提交暂存的前端文件会触发 pre-commit 钩子（lint-staged：eslint --fix + prettier --write），提交前先 `npm run lint && npm run format`，钩子的自动修改会并入本次提交。
- `dist/`、`build/` 是 PyInstaller 产物，不要编辑；打包后需手工把 `prompts/ lint/ web/dist` 拷到 exe 旁（流程见 README）。
- 仓库已 git init。`data/settings.json` 含 API key，`.gitignore` 已挡住 `data/`、`books/`、`node_modules/`——**绝不能**把 key 写进代码、日志或任何会进入版本控制的文件。

## 提示词与词库

- 每条提示词一个文件，frontmatter 必填 `id / stage / model_stage / temperature / version`；变量统一 `〖变量名〗`，渲染后残留 `〖〗` 视为错误。**不要在 Python 代码里内联 prompt 文本。**
- `lint/*.txt` 是种子词库，与 S1 提示词同源注入。改词库只改 txt 文件或设置页，**不要在代码里硬编码词表**。

## 验收标准

改动算完成 = pytest 全绿 + smoke 通过；改 `web/` 前端时另需 `npm run lint:check`、`npm run typecheck`、`npm run build` 全过；若动了管线/状态机/文件格式，README 偏差记录与相关 docstring 的规格引用已同步更新。

## 风格

- 注释、docstring、文档一律中文；docstring 开头标注对应规格章节（如"（规格01 §5.1）"）是本仓库惯例，新代码沿用。
- 文件顶部 `from __future__ import annotations`；类型注解全量；早返回、少嵌套。
