# AI小说生成项目全维度调研报告

**功能设计、提示词工程、流程架构与世界观构建体系深度研究**

**报告日期：2026年10月24日**

## 摘要

本报告对AI小说生成项目进行了全维度调研，覆盖GitHub上5个活跃开源项目（InkOS 10.5k stars、AI_NovelGenerator 6.3k stars、webnovel-writer 5315 stars、oh-story-claudecode 3212 stars、AI-Novel-Writing-Assistant 1799 stars）、3种主流提示词工程设计模式（DOME动态分层大纲、RecurrentGPT循环记忆、多线程状态追踪）、7模块端到端系统架构，以及世界观构建、角色设计、武力等级体系、情节结构四大核心要素的AI适配范式。报告提出了六层提示词模板体系（世界观宪法→角色状态→大纲骨架→章节生成→审计校验→修订润色），给出了15项核心功能在5个项目上的覆盖度矩阵，并针对四种创作场景提供了选型建议。当前行业仍存在多平台分发、读者反馈闭环、多人协同、版权检测和TTS集成五个功能空白。

## 1. 开源项目全景与竞品格局

截至2026年10月，GitHub上已形成一批活跃的AI小说生成开源项目，按星标数排序，排名前五的项目分别为InkOS（10.5k stars）、AI_NovelGenerator（6.3k stars）<sup>[GitHub](https://github.com/YILING0013/AI_NovelGenerator)</sup>、webnovel-writer（5,315 stars）、oh-story-claudecode（3,212 stars）<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> 和 AI-Novel-Writing-Assistant（1,799 stars）<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup>。此外，ai-novel-lab以完整100章43万字成品《大厂重生》验证了端到端可行性<sup>[GitHub](https://github.com/xindoo/ai-novel-lab)</sup>，novel-studio则以Go语言本地优先引擎的差异化路线切入<sup>[GitHub](https://github.com/Xiaoyangy/novel-studio)</sup>。

InkOS是目前生态最完整的项目，2.0版本采用pi-agent harness统一架构，内置19个专业Skills，提供Studio Web工作台、TUI终端全屏交互和CLI命令行三种使用形态<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。其核心差异化能力包括：剧情分支推演（forecast create/show/select）、Play互动世界（自然语言定义世界契约与角色agent）、封面生成、去AI味（inkos-story-deslop Skill）、同人/仿写/续写四种模式，以及多Provider模型路由（OpenAI/Anthropic/Gemini/DeepSeek/Moonshot）<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。InkOS的架构最为成熟，但学习曲线也最陡峭。

AI_NovelGenerator以Python技术栈为核心，提供可视化工作流编辑、角色/势力关系图谱、Volume→Arc→Scene→Chapter四层分层规划面板，以及MongoDB事务存储<sup>[GitHub](https://github.com/YILING0013/AI_NovelGenerator)</sup>。该项目2026年3月启动重构，原作者声明精力有限，目前处于维护模式与重构并行状态<sup>[GitHub](https://github.com/YILING0013/AI_NovelGenerator/blob/main/README.md)</sup>。webnovel-writer专为中文网文场景设计，基于Claude Code构建，支持200万字量级连载，内置37个中文网文题材模板，实现了番茄小说自动发布（Playwright+Chromium），是唯一实现平台自动发布的开源项目<sup>[GitHub](https://github.com/981029l/webnovel-writer)</sup>。

oh-story-claudecode以"网文全流程Skill包"定位切入，13个skill覆盖从扫榜选材、拆解爆款到搭大纲写正文、去AI味、生成封面图的完整链路<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup>。其独特优势在于多平台适配（Claude Code、Google Antigravity、OpenCode、ZCode、OpenClaw、Codex CLI、Reasonix），以及拆文分析能力——可深度拆解对标书的文风、节奏、情绪模块、写作手法<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup>。AI-Novel-Writing-Assistant（Biz Novel Studio）则面向新手用户，提供Windows桌面版（install/portable双形态），V2自动导演模式支持从一句灵感到整本规划的全自动创作流程，并独创"写法引擎"——可保存、编辑、绑定的长期表达规则资产<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup>。

**表1：五大开源AI小说生成项目核心对比**

| 维度 | InkOS | AI_NovelGenerator | webnovel-writer | oh-story-claudecode | AI-Novel-Writing-Assistant |
|------|-------|-------------------|-----------------|---------------------|---------------------------|
| 星标数 | 10.5k | 6.3k<sup>[GitHub](https://github.com/YILING0013/AI_NovelGenerator)</sup> | 5,315 | 3,212<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | 1,799<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| 技术栈 | TypeScript | Python<sup>[GitHub](https://github.com/YILING0013/AI_NovelGenerator)</sup> | Claude Code插件 | Claude Code Skill包<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | TypeScript+LangGraph+Qdrant<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| 交互形态 | Studio Web+TUI+CLI<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | Next.js桌面应用 | Web前端+CLI | 多平台CLI+Dashboard<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | Windows桌面版+Web版<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| Agent数量 | 10+ Agent管线<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 多阶段流水线 | 6 Skills+8 Agents<sup>[GitHub](https://github.com/981029l/webnovel-writer)</sup> | 7专业Agent<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | 自动导演+多模型路由 |
| 记忆机制 | SQLite FTS5/BM25+Zod schema<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 向量检索+状态文件<sup>[CSDN](https://blog.csdn.net/gitblog_00279/article/details/163272948?index=prev&type=card)</sup> | RAG+实体图谱+重排序<sup>[CSDN](https://gitcode.csdn.net/69bbb80954b52172bc6286d9.html)</sup> | 知识库按需加载+作者记忆<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | Qdrant+Embedding可选RAG<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| 特色能力 | 剧情推演/互动世界/去AI味<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 四层可视化规划<sup>[GitHub](https://github.com/YILING0013/AI_NovelGenerator)</sup> | 番茄自动发布/37题材模板<sup>[GitHub](https://github.com/981029l/webnovel-writer)</sup> | 扫榜拆文/多平台适配<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | 写法引擎/漫画短剧衍生<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| 活跃状态 | 活跃（2.0迭代中） | 维护/重构中<sup>[GitHub](https://github.com/YILING0013/AI_NovelGenerator/blob/main/README.md)</sup> | 活跃（v6维护/v8开发） | 活跃（高频更新）<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode/releases)</sup> | 活跃（v0.5.19发布）<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant/releases)</sup> |

## 2. 端到端系统架构

### 2.1 三种架构范式

当前AI长篇小说生成系统可归纳为三种架构范式。第一种是分层Prompt Chaining，以Google DeepMind的Dramatron为代表，采用log line→角色描述→情节要点→地点描述→对话的自顶向下逐层生成方式，强调人机协同而非全自动<sup>[arXiv](https://arxiv.org/abs/2209.14958)</sup>。第二种是多Agent协作框架，以StoryWriter、InkOS和webnovel-writer为代表，通过专职Agent分工与结构化状态传递实现端到端生成<sup>[ACM 数字图书馆](https://dl.acm.org/doi/10.1145/3746252.3761616),[yjpoo.com](https://www.yjpoo.com/site/7682.html),[CSDN](https://gitcode.csdn.net/69bbb80954b52172bc6286d9.html)</sup>。第三种是工程化工作台，以AI_NovelGenerator和NovelGen为代表，通过GUI或CLI驱动四步至五步流水线，配合外部记忆系统完成长篇创作<sup>[CSDN](https://blog.csdn.net/zsh_1314520/article/details/160830617),[GitHub](https://github.com/THU-KEG/StoryWriter)</sup>。此外，WriteHERE提出了异构递归规划DAG架构，将任务动态分解为检索、推理、写作三类子任务并智能调度，代表了第四种方向<sup>[arXiv](https://arxiv.org/abs/2503.08275)</sup>。

### 2.2 七模块全流程架构

一个完整的AI小说生成系统包含七个核心模块，按执行顺序依次为：初始设定与世界构建、大纲与情节规划、章节内容生成、一致性维护与记忆管理、审计与质量控制、修订与润色、状态同步与归档。

**模块一：初始设定与世界构建。** 用户输入种子创意（log line/brief/题材关键词），系统生成世界观硬规则、角色档案、势力关系、力量体系结构化设定文档<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。InkOS由Architect Agent基于brief生成story_bible.md、book_rules.md和author_intent.md三份核心文档<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。AI_NovelGenerator在Step1生成Novel_architecture.txt与初始角色状态表<sup>[CSDN](https://blog.csdn.net/zsh_1314520/article/details/160830617)</sup>。StoryWriter的Outline Agent生成基于事件的摘要，包含事件、角色及事件间关系<sup>[GitHub](https://github.com/THU-KEG/StoryWriter)</sup>。设定文档作为后续所有生成环节的"宪法"约束注入上下文，确保全书世界观一致性。

**模块二：大纲与情节规划。** 系统接收世界观设定、目标字数/章节数、爽点密度约束，生成卷-章两级或事件级大纲<sup>[aminer.org](https://www.aminer.org/pub/6858b4ff163c01c8505cf781)</sup>。StoryWriter的Planning Agent采用非线性叙事策略（Nonlinear Narrative），由SubTasker分解子事件、Weaver将事件分配到各章<sup>[aminer.org](https://www.aminer.org/pub/6858b4ff163c01c8505cf781)</sup>。InkOS的Planner Agent读取author_intent与记忆检索，产出每章的must-keep与must-avoid约束<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。AI_NovelGenerator在Step2生成Novel_directory.txt，包含每章标题与核心提示<sup>[CSDN](https://blog.csdn.net/gitblog_00279/article/details/163272948?index=prev&type=card)</sup>。NovelGen在章节数达到13章以上时自动分段生成plot，并支持Refine迭代优化<sup>[GitHub](https://github.com/THU-KEG/StoryWriter)</sup>。WriteHERE则将规划任务递归分解为子任务DAG，由TypedPlan生成带类型标注的子任务图<sup>[arXiv](https://arxiv.org/html/2503.08275v3)</sup>。

**模块三：章节内容生成。** 这是系统的核心产出环节，输入为当前章大纲、相关前文上下文、角色状态、世界观约束和风格规则<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。InkOS的Writer Agent基于编排后的精简上下文生成正文，内置约25条通用创作规则与题材专属规则，实现字数治理与对话引导<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。StoryWriter的Writing Agent通过ReIO机制动态压缩故事历史，Coordinator监督全局叙事结构，FinalWriter生成最终文本<sup>[aminer.org](https://www.aminer.org/pub/6858b4ff163c01c8505cf781)</sup>。AI_NovelGenerator在Step3生成outline_X.txt与chapter_X.txt，通过向量检索召回相关前文确保连贯性<sup>[CSDN](https://blog.csdn.net/gitblog_00279/article/details/163272948?index=prev&type=card)</sup>。NovelGen采用滑动散文窗口（Sliding Prose Window，约1200字符）确保章节间过渡连贯<sup>[GitHub](https://github.com/THU-KEG/StoryWriter)</sup>。WriteHERE的Composition原子任务由Writing Executor执行，组装Task Goal、全局Outline、依赖输出与已写内容作为完整prompt<sup>[arXiv](https://arxiv.org/html/2503.08275v3)</sup>。

**模块四：一致性维护与记忆管理。** 这是长篇生成的核心难点。InkOS采用三层权威记忆架构——story/state/*.json（Zod schema校验的结构化状态）、story/*.md（人类可读投影）、story/memory.db（SQLite FTS5/BM25时序记忆库），维护Facts、Open Threads、Character Status、Narrative Arc和伏笔（status: open/progressing/deferred/resolved）五类状态<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。AI_NovelGenerator通过character_state.txt、global_summary.txt和plot_arcs.txt三份文件随定稿逐章更新，向量库存储每章定稿供后续检索<sup>[CSDN](https://blog.csdn.net/gitblog_00279/article/details/163272948?index=prev&type=card)</sup>。NovelGen采用分层故事状态设计，包含Facts+Open Threads、Character Status Memory、Narrative Arc Management（Current/Closed Arcs）、Sliding Prose Window和Style & Expression Cooldown五层记忆<sup>[GitHub](https://github.com/THU-KEG/StoryWriter)</sup>。CoLong Idea Studio提出Memory-First架构，按基础重要度、时间衰减、使用强化、章节距离、任务需求五个维度动态重排序记忆<sup>[CSDN](https://blog.csdn.net/xyc_yyds/article/details/159289864)</sup>。

**模块五：审计与质量控制。** InkOS的Auditor执行37维连续性检查，覆盖角色记忆、物资连续性、伏笔回收、大纲偏离、叙事节奏、情感弧线和AI痕迹检测，Reviser修复关键问题（默认最多自动修订1次，可通过writing.reviewRetries调整）<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。webnovel-writer实现5维并行审查——爽点（High-point）、一致性（Consistency）、节奏（Pacing）、角色出戏（OOC）和连贯性（Continuity），其中爽点审查支持6种执行模式<sup>[GitHub](https://github.com/981029l/webnovel-writer)</sup>。oh-story-claudecode的consistency-checker执行S1-S4分级报告，按严重程度阻断或提醒<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup>。AI-Novel-Writing-Assistant引入"质量债务"机制，非阻塞问题以提醒形式记录，阻塞问题强制修复<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup>。

**模块六：修订与润色。** InkOS提供revise --mode anti-detect专门反检测改写，以及style analyze/import注入文风指纹的能力<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。oh-story-claudecode的story-deslop Skill实现三遍去AI法——检测清除AI写作痕迹，blocking仅限确定性句式与标点问题，内置禁用词表与改写范例库<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup>。AI-Novel-Writing-Assistant的"写法引擎"将表达规则分为剧情推进、人物表达、语言质感、节奏密度四组高级规则，叠加反AI规则全局基线<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup>。

**模块七：状态同步与归档。** InkOS的安全章节工作区实现正文/状态/伏笔/快照先校验再原子提交，export支持txt/md/epub三种格式<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。AI_NovelGenerator在Step4定稿时更新global_summary、character_state、plot_arcs和向量库，支持多格式导出及网文平台排版适配<sup>[CSDN](https://blog.csdn.net/zsh_1314520/article/details/160830617)</sup>。webnovel-writer通过Git章节标签（如ch0045）实现按章回滚，state.json保持5KB以内精简，write-gate三阶段校验（prewrite/precommit/postcommit）确保状态完整性<sup>[GitHub](https://github.com/lingfengQAQ/webnovel-writer/blob/master/docs/guides/commands.md)</sup>。

### 2.3 衔接机制与架构盲区

**表2：模块间衔接机制汇总**

| 衔接点 | 机制 | 代表项目 |
|--------|------|----------|
| 设定→大纲 | 设定文档作为prompt前置约束 | Dramatron, InkOS<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>, AI_NovelGenerator<sup>[CSDN](https://blog.csdn.net/gitblog_00279/article/details/163272948?index=prev&type=card)</sup> |
| 大纲→章节 | 结构化JSON/Markdown大纲强制消费 | StoryWriter<sup>[aminer.org](https://www.aminer.org/pub/6858b4ff163c01c8505cf781)</sup>, AI_NovelGenerator<sup>[CSDN](https://blog.csdn.net/gitblog_00279/article/details/163272948?index=prev&type=card)</sup> |
| 前文→当前章 | RAG向量检索/滑动窗口摘要/状态快照 | AI_NovelGenerator<sup>[CSDN](https://blog.csdn.net/gitblog_00279/article/details/163272948?index=prev&type=card)</sup>, InkOS<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>, webnovel-writer<sup>[CSDN](https://gitcode.csdn.net/69bbb80954b52172bc6286d9.html)</sup> |
| 章节→审计 | 草稿+结构化状态+控制文档打包送审 | InkOS<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>, webnovel-writer<sup>[CSDN](https://gitcode.csdn.net/69bbb80954b52172bc6286d9.html)</sup> |
| 审计→修订 | 问题清单驱动定向修复，循环至通过 | InkOS<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>, oh-story-claudecode<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> |
| 定稿→状态更新 | JSON delta immutable写入+schema校验 | InkOS<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>, AI_NovelGenerator<sup>[CSDN](https://blog.csdn.net/gitblog_00279/article/details/163272948?index=prev&type=card)</sup> |

当前架构存在三个尚未被可靠实现的环节：全自动武力等级体系生成与数值平衡（现有系统均将力量体系作为世界观设定的子项一次性生成，无专门的数值平衡验证模块）；读者反馈闭环自动调优（无任何项目实现从评论区/弹幕/订阅数据自动反向调整生成参数）；多人协同创作（所有项目均为单人本地工作流，无实时多人编辑/评审/权限管理）<sup>[GitHub](https://github.com/Narcooo/inkos),[GitHub](https://github.com/981029l/webnovel-writer)</sup>。

## 3. 提示词工程设计模式

### 3.1 动态分层大纲与记忆增强模式（DOME）

DOME框架由Wang等人在arXiv论文（2412.13575v1）中提出<sup>[arXiv](https://arxiv.org/html/2412.13575v1)</sup>，是当前学术界最完整的长篇小说生成提示词框架。该框架融合"计划-写作"（Plan-and-Write）范式与小说写作理论（如英雄之旅），包含两个核心Prompt模块。

**Dynamic Hierarchical Outline（DHO）** 首先基于写作理论生成粗纲（Rough Outline），确保情节完整性与结构合理性。随后在写作过程中，根据已生成内容和粗纲动态扩展细纲（Detailed Outline），以适应生成的不确定性<sup>[arXiv](https://arxiv.org/html/2412.13575v1)</sup>。这一机制解决了固定大纲导致的两大问题：剧情僵化（AI严格按预设大纲执行，无法适应生成过程中的自然演化）和剧情断裂（大纲覆盖不足时AI自行编造，与前文矛盾）。DHO的核心Prompt设计思路是：先要求LLM输出符合"起承转合"或"三幕结构"的全书骨架，再在每章写作前根据当前进度和前文摘要动态生成该章的细化指令。

**Memory-Enhancement Module（MEM）** 利用时序知识图谱（Temporal Knowledge Graph, TKG）存储生成内容的四元组（Subject, Action, Object, Chapter）<sup>[arXiv](https://arxiv.org/html/2412.13575v1)</sup>。在后续章节生成时，通过实体检索加LLM语义过滤，为当前写作提供精确、简洁的上下文，实验数据显示可减少87.61%的情节冲突<sup>[arXiv](https://arxiv.org/html/2412.13575v1)</sup>。MEM的Prompt设计要点是：将知识图谱的查询结果格式化为自然语言摘要注入上下文，而非直接注入原始四元组，以减少LLM的理解负担。

DOME框架的局限性在于计算成本高——约200次API调用生成7000词，耗时约4小时；Prompt设计依赖人工经验，自动化Prompt适配效果尚待验证<sup>[arXiv](https://arxiv.org/html/2412.13575v1)</sup>。

### 3.2 自然语言循环记忆模式（RecurrentGPT）

RecurrentGPT由Zhou等人在arXiv论文（2305.13304）中提出<sup>[arXiv](https://arxiv.org/abs/2305.13304)</sup>，核心思想是用自然语言模拟RNN/LSTM的递归计算图，突破Transformer固定上下文窗口限制。其Prompt结构包含四个自然语言组件。

**Input/Output组件**：每步生成一段正文（Content，200-400词）加下一步计划（Plan，3-5句）。**Long-term Memory组件**：所有历史段落的摘要存入VectorDB，通过语义检索召回相关片段注入当前Prompt。**Short-term Memory组件**：近期关键信息的自然语言摘要（10-20句），每步由LLM重写更新——丢弃无关信息、加入新信息——保持长度可控以适配上下文窗口。**Recurrent Prompt Template组件**：将上述三个组件组装为完整prompt，指令LLM生成新内容、新计划及更新后的短期记忆，形成生成闭环<sup>[arXiv](https://arxiv.org/abs/2305.13304)</sup>。

这一模式的核心优势是支持任意长度文本的生成，且天然适合人机协作——用户可以在每一步编辑记忆与计划，干预生成方向<sup>[arXiv](https://arxiv.org/abs/2305.13304)</sup>。开源实现jackaduma/Recurrent-LLM提供了可复用的代码框架<sup>[GitHub](https://github.com/jackaduma/Recurrent-LLM)</sup>。局限性在于仅测试至约5000词规模，超长文本评估缺失；强依赖高性能基座模型（ChatGPT/GPT-4），小模型效果显著下降。

### 3.3 多线程状态追踪与因果规划模式

这一模式来自KazKozDev/NovelGenerator项目<sup>[GitHub](https://github.com/KazKozDev/NovelGenerator)</sup>，面向工程落地的端到端小说生成管线，强调结构化状态管理而非单一Prompt技巧<sup>[GitHub](https://github.com/topics/ai-novel-generator)</sup>。

**Multi-threaded Narratives** 并行追踪多条角色视角线与时间线，维护各角色"当前所知"（Character Knowledge State）的独立快照，确保信息不对称的真实性——角色A不知道角色B的秘密，对话和行为必须体现这种信息差。**Emotional Arc Tracking** 将心理变化严格锚定于故事事件，避免情感突变——角色的情绪转变必须由具体事件驱动，而非AI随意赋予。**Causal Planning与Persistent Memory** 实现章节级因果链规划加持久化故事记忆库，保证跨章逻辑一致性<sup>[GitHub](https://github.com/topics/ai-novel-generator)</sup>。

该模式的工程实现要点是：每个角色维护独立的状态文件，章节生成时仅注入"当前在场角色"的状态与视角；情感弧线通过"事件→情绪反应→行为变化"的三元组编码；因果链通过"伏笔（cause）→发展（development）→回收（effect）"的生命周期管理实现<sup>[GitHub](https://github.com/KazKozDev/NovelGenerator)</sup>。

### 3.4 综合提示词模板体系

基于上述三种模式的实践经验，一个完整的AI小说生成项目需要构建以下六层提示词体系。

**第一层：世界观宪法Prompt。** 将世界观设定编码为结构化文档（YAML或JSON），作为所有后续生成的System Prompt第一部分强制加载<sup>[腾讯网](https://mp.weixin.qq.com/s?__biz=MzYzNjk3MjAwMw==&idx=1&mid=2247483856&sn=b00916c7ad57134e7d0332d13925e145),[腾讯网](https://mp.weixin.qq.com/s?__biz=Mzg2NTA3MTM0Mg==&idx=1&mid=2247484357&sn=f4fd2f99b24f1b6faf13ee830c84084f)</sup>。核心字段包括：时代背景、地理环境、文明形态、力量体系（来源/等级/代价）、社会结构（权力/阶层/矛盾）、历史脉络（百年内三大事件）、隐藏规则（至少两条暗线）、日常细节（三个具体生活场景），每个维度限制150-300字并要求具象细节<sup>[腾讯网](https://mp.weixin.qq.com/s?__biz=MzYzNjk3MjAwMw==&idx=1&mid=2247483856&sn=b00916c7ad57134e7d0332d13925e145)</sup>。

**第二层：角色状态Prompt。** 采用"矛盾特征矩阵"加RCP三层协议编码角色<sup>[CSDN](https://ask.csdn.net/questions/9376140)</sup>。Anchor Layer用JSON Schema定义不可变参数（核心动机、禁忌阈值、行为底线）；State Layer维护可演进变量（trust_score、情感状态、知识边界）；Transition Layer声明状态变更的前置条件与副作用<sup>[CSDN](https://ask.csdn.net/questions/9376140)</sup>。每次章节生成时，注入当前在场角色的Anchor+State快照。

**第三层：大纲骨架Prompt。** 采用三层大纲结构——全书骨架（定位/主线/结局）→卷级大纲（目标/转折/冲突）→章级大纲（剧情/角色/场景/冲突/钩子五要素）<sup>[CSDN](https://blog.csdn.net/2600_94959791/article/details/157341583)</sup>。每章大纲以JSON格式输出，包含核心事件、角色行动路线、伏笔埋设/揭晓节点、情绪走向和章末钩子设计<sup>[CSDN](https://blog.csdn.net/gitblog_00279/article/details/163272948?index=prev&type=card)</sup>。

**第四层：章节生成Prompt。** 组装当前章大纲、相关前文摘要（RAG检索结果）、角色状态快照、世界观约束和风格规则为完整上下文<sup>[GitHub](https://github.com/Narcooo/inkos),[CSDN](https://blog.csdn.net/gitblog_00279/article/details/163272948?index=prev&type=card)</sup>。正文生成指令包含八个约束维度：本章位置、核心事件、角色状态、情感弧线、节奏设计、必含元素、章末钩子和风格约束<sup>[CSDN](https://blog.csdn.net/2600_94959791/article/details/157341583)</sup>。单次生成控制在1500-3000字，禁止一次性生成数万字<sup>[tencent.com](https://cloud.tencent.com/developer/article/2693970)</sup>。

**第五层：审计校验Prompt。** 将章节草稿与结构化状态、控制文档、前文上下文打包送入审计Agent，执行多维度连续性检查<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。审计Prompt需明确列出检查清单：角色记忆是否一致、物资连续性是否成立、伏笔是否按时回收、是否偏离大纲、叙事节奏是否合理、情感弧线是否连贯、是否存在AI痕迹（如"然而""不禁""目光如炬"等高频AI用语）<sup>[GitHub](https://github.com/Narcooo/inkos),[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup>。

**第六层：修订润色Prompt。** 基于审计问题清单进行针对性修复，同时注入文风指纹（通过style analyze/import从目标作家的作品中提取）和禁词表<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。去AI味Prompt需包含：禁用句式清单（如"在这个XX的世界里""他不知道的是"）、禁用标点模式（如连续感叹号、连续问号）、对话去模板化指令（禁止每句对话都带"说道""问道"后缀）<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode),[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup>。


## 4. 世界观与背景设定体系

AI小说的世界观构建必须从散文式描述转向结构化"六模块Bible"编码<sup>[腾讯网](https://mp.weixin.qq.com/s?__biz=MzYzNjk3MjAwMw==&idx=1&mid=2247483856&sn=b00916c7ad57134e7d0332d13925e145)</sup>。传统的自然语言设定文档对LLM而言信息密度低、约束力弱，导致生成过程中AI随意添加与原始设定矛盾的内容。解决方案是将世界观拆解为机器可读的离散字段，每个字段有明确的边界和约束。

**六模块结构化Bible** 包含六个维度：基础设定（时代/地理/文明）、力量体系（来源/等级/代价）、社会结构（权力/阶层/矛盾）、历史脉络（百年内三大事件）、隐藏规则（至少两条暗线）、日常细节（三个具体生活场景）<sup>[腾讯网](https://mp.weixin.qq.com/s?__biz=MzYzNjk3MjAwMw==&idx=1&mid=2247483856&sn=b00916c7ad57134e7d0332d13925e145)</sup>。每个维度限制150-300字，要求具象细节而非抽象描述。例如"魔法来源于星辰之力"不如"魔法来源于星辰之力，仅限日落后使用，每次施法消耗施术者一年寿命"具有约束力。

马良写作工具提供本地六模块工作表，将作者输入整理为标准化提示词与创作笔记，未填项明确标为"待补充"以防止AI编造<sup>[腾讯网](https://mp.weixin.qq.com/s?__biz=MzYzNjk3MjAwMw==&idx=1&mid=2247483856&sn=b00916c7ad57134e7d0332d13925e145)</sup>。这一设计的核心洞察是：世界观的价值不在于"解释一切"，而在于"资源—权力—代价—行动"的闭环能够驱动人物选择<sup>[maliangwriter.com](https://maliangwriter.com/tools/worldbuilding-generator/)</sup>。例如：灵石是修炼资源（资源）→高阶修士掌控灵石矿脉（权力）→突破境界需消耗大量灵石（代价）→主角必须冒险进入危险矿区（行动）。这种闭环设计使得世界观不是静态背景板，而是情节的发动机。

在AI编码层面，世界观设定应保存为独立的YAML或JSON文件（如Novel_architecture.txt），每次章节生成前强制加载<sup>[腾讯网](https://mp.weixin.qq.com/s?__biz=Mzg2NTA3MTM0Mg==&idx=1&mid=2247484357&sn=f4fd2f99b24f1b6faf13ee830c84084f),[CSDN](https://blog.csdn.net/gitblog_00279/article/details/163272948?index=prev&type=card)</sup>。使用JSON Schema约束输出格式，例如`{"worldview": "...", "volumes": [...]}`，确保后续模块可解析<sup>[博客园](https://www.cnblogs.com/aresen/p/22076474)</sup>。InkOS将世界观存储在book_rules.md中，作为所有Agent的共享约束文件<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。AI_NovelGenerator则在Step1生成架构文档后，将其作为后续三个步骤的强制前置输入<sup>[CSDN](https://blog.csdn.net/zsh_1314520/article/details/160830617)</sup>。

隐藏规则的设计是高水平世界观的标志性特征。至少需要两条暗线规则——读者在前期不知道、但在后期揭晓后能回溯验证的规则<sup>[腾讯网](https://mp.weixin.qq.com/s?__biz=MzYzNjk3MjAwMw==&idx=1&mid=2247483856&sn=b00916c7ad57134e7d0332d13925e145)</sup>。例如："所有修炼到第九境的修士都会失踪"这条规则，前期表现为江湖传说，后期揭示为世界本身的筛选机制。这类规则对AI来说尤其困难，因为AI倾向于在早期就暴露所有信息。解决方法是在设定中显式标注"隐藏等级"和"揭晓时机"，并在审计环节检查是否过早泄露。

## 5. 角色设计系统

LLM在角色塑造上存在一个根本性矛盾：模型追求输出的一致性，导致角色趋于扁平化和同质化<sup>[CSDN](https://ask.csdn.net/questions/9376140)</sup>。所有角色说话像同一个人，做出选择时倾向于理性最优解，缺乏人类特有的矛盾感和非理性冲动。解决这一问题需要一套系统化的角色编码方法。

**活人档案/深度人设卡** 是角色设计的基础模板，必须包含以下字段：表层信息（姓名/外貌/身份）、声音特征（句式偏好/口头禅/信息透露习惯）、核心三要素（表面欲望/真实需求/致命缺陷）、过往创伤、标志行为、关系网络和行为底线<sup>[CSDN](https://ask.csdn.net/questions/9376140)</sup>。定调公式为"一个___的人，因为___，所以___"——例如"一个表面冷漠的人，因为幼年丧母，所以拒绝与任何人建立深层关系"。这个公式将角色从形容词堆砌转化为因果链条。

**矛盾特征** 是对抗LLM扁平化的核心武器。每个角色必须显式声明至少一组对立特质，如"善良但记仇""理性决策者在情感高潮处失控""勇敢到近乎自毁"<sup>[CSDN](https://ask.csdn.net/questions/9376140)</sup>。矛盾特征的Prompt编码方式是：在角色状态文件中用contradictions字段列出，并在章节生成Prompt中强制要求"本章至少展现一次角色内在矛盾"。

**RCP三层协议** 将角色状态管理分为三层<sup>[CSDN](https://ask.csdn.net/questions/9376140)</sup>。Anchor Layer用JSON Schema定义不可变参数——核心动机、禁忌阈值、行为底线，这些在全书中不可被违反。State Layer维护可演进变量——trust_score（对特定人物的信任度）、emotional_state（当前情绪状态）、knowledge_boundary（角色已知信息边界），这些随剧情推进而更新。Transition Layer声明状态变更的前置条件与副作用——例如"trust_score从负转正需要经历一次生死共患事件，且副作用是角色会暂时放松警惕"<sup>[CSDN](https://ask.csdn.net/questions/9376140)</sup>。

**对话差异化策略** 是角色区分度的最终体现。在对话生成Prompt中，为每个角色指定句式偏好（如A角色偏短句/B角色喜用反问）、口头禅（如C角色在紧张时摸鼻子）、信息透露习惯（如D角色从不说完整句，总是欲言又止）<sup>[CSDN](https://ask.csdn.net/questions/9376140)</sup>。AI_NovelGenerator通过character_state.txt在每章生成前加载当前在场角色的状态快照，确保对话风格与角色当前情绪状态一致<sup>[CSDN](https://blog.csdn.net/gitblog_00279/article/details/163272948?index=prev&type=card)</sup>。

## 6. 武力等级与力量体系

武力等级体系是玄幻、修仙、科幻类型小说的核心架构要素，也是AI生成中最容易出现"战力崩坏"的环节<sup>[php.cn](https://www.php.cn/faq/2912004.html)</sup>。崩坏的根源在于：AI倾向于用"他变得更强了"这类抽象表述描述实力变化，缺乏量化锚点和感官细节。

**境界词典** 是力量体系的基础数据结构。以条目形式定义每一级的名称、突破条件、对应能力、感官信号和瓶颈事件<sup>[php.cn](https://www.php.cn/faq/2912004.html)</sup>。感官信号是区分优秀与平庸设定的核心要素——例如突破时"喉间铁锈味""丹田青玉虚影""耳畔龙吟"等具象描写，远比"实力大增""气势暴涨"更有画面感<sup>[php.cn](https://www.php.cn/faq/2912004.html)</sup>。每级必须包含"不能做什么"的使用限制与代价字段，避免角色变成无所不能的"龙傲天"。

**数值/标签锚定** 为每个境界绑定可量化的参考指标<sup>[php.cn](https://www.php.cn/faq/2912004.html)</sup>。具体方式有三种：物理量化（拳力多少吨/速度多少倍音速/破坏规模多大）、人群标签（该境界在势力中的地位——家族精英/宗门长老/皇朝供奉）、场景标签（该境界能进入的区域——外围/内门/秘境/神界）。长篇控制在12-18个大境界，每境分初/中/后/巅峰四阶段，总计不超过72个小等级，避免AI在生成过程中混淆<sup>[php.cn](https://www.php.cn/faq/2912004.html)</sup>。

**状态机约束** 将力量体系编码为可校验的状态转换规则<sup>[CSDN](https://ask.csdn.net/questions/9376140)</sup>。在Prompt中使用XML标签或JSON声明状态变更契约，例如`ON_EVENT(memory_flash) → IF(trust_score > 0.7) THEN state=RECALLING`，由外部状态机验证突破条件是否满足<sup>[CSDN](https://ask.csdn.net/questions/9376140)</sup>。DeepSeek等工具可将战力体系写成约束关系，主动校验突破条件与胜负逻辑是否自洽——例如"A角色是金丹期，B角色是元婴期，按规则A不可能正面击败B，除非触发特殊条件"<sup>[今日头条](https://www.toutiao.com/article/7672176561872847394/)</sup>。

升级节奏的控制是力量体系的另一个难点。AI倾向于平滑化处理实力变化，导致升级缺乏仪式感。解决方法是在设定中为每个大境界绑定"瓶颈事件"——突破不仅需要资源积累，还需要解决特定的心理障碍或完成特定的任务<sup>[php.cn](https://www.php.cn/faq/2912004.html)</sup>。这些瓶颈事件必须人工审核，确认其在叙事中的位置合理后才允许AI生成突破章节。

## 7. 情节结构与大纲设计

AI小说的情节失控源于线性叙事的不可回溯性——人类作家可以在写作过程中自然回溯修改前文，而AI只能基于当前上下文向前生成<sup>[CSDN](https://blog.csdn.net/2600_94959791/article/details/157341583)</sup>。解决方案是将线性叙事转化为可校验、可递归生成的模块化结构。

**三层大纲骨架** 是情节设计的核心框架<sup>[CSDN](https://blog.csdn.net/2600_94959791/article/details/157341583)</sup>。第一层是全书骨架，包含作品定位、主线冲突和结局方向，一旦确定不可更改。第二层是卷级大纲，每卷包含阶段性目标、核心转折点和高潮冲突，允许在写作过程中微调。第三层是章级大纲，每章包含五个要素——剧情事件、角色行动、场景描写、冲突焦点和章末钩子<sup>[CSDN](https://blog.csdn.net/2600_94959791/article/details/157341583)</sup>。AI_NovelGenerator的四层规划（Volume→Arc→Scene→Chapter）是这一思路的工程化实现<sup>[GitHub](https://github.com/YILING0013/AI_NovelGenerator)</sup>。

**4T-O渐进式方法** 提供了一套可操作的大纲生成流程<sup>[CSDN](https://blog.csdn.net/2600_94959791/article/details/157341583)</sup>。Think阶段进行构思启动，确定核心创意与世界观框架。Typesetting阶段生成25章终版大纲，每章包含标题（不超过8字）、核心事件和情绪走向。Test阶段自审最薄弱的三章，标注需要加强的环节。Output阶段才开始正文生成。这一方法的核心价值在于：大纲不是一次性产物，而是经过自审迭代后的稳定版本。

**锚点事件链** 将关键转折点编码为带标记的事件序列<sup>[CSDN](https://blog.csdn.net/2600_94959791/article/details/157341583)</sup>。使用三种标记语法：`[强制事件]` 表示该事件必须在指定章节发生、`[可选分支]` 表示可根据生成情况决定是否执行、`[状态锁]` 表示该事件的发生会改变后续可用的剧情路径。每生成2000 token强制插入一次状态锁，确保AI不会在长段落中丢失叙事方向<sup>[CSDN](https://blog.csdn.net/2600_94959791/article/details/157341583)</sup>。

**钩子强制机制** 要求每章结尾必须设计强悬念断章钩子<sup>[tencent.com](https://cloud.tencent.com/developer/article/2693970)</sup>。钩子的本质是"在读者最想继续读的时候停下来"——在战斗最关键的时刻、在秘密即将揭晓的瞬间、在角色面临重大抉择的刹那断章。Prompt中需显式包含"章末钩子"字段，并在审计环节检查钩子强度是否达标。

**章节递归法** 是一种高效的生成策略<sup>[tencent.com](https://cloud.tencent.com/developer/article/2693970)</sup>。先生成所有章节骨架（确保全局连贯），再并行扩写各章初稿（提高效率），最后贯通衔接（统一文风和处理过渡）。应急救坑机制包括：当某章质量不达标时，将高潮提前到本章前三分之二处，后三分之一用于铺垫下一章；当出现逻辑矛盾时，通过角色回忆或第三方信息补入修正。

## 8. 记忆系统与一致性维护

长篇生成的核心矛盾是LLM有限的上下文窗口与不断增长的故事长度之间的冲突<sup>[arXiv](https://arxiv.org/abs/2305.13304)</sup>。当前最强的LLM上下文窗口为128K-200K token，约等于5-8万字，而一部网文动辄百万字以上。解决方案是构建外部结构化记忆系统，而非依赖模型的内在记忆能力。

**结构化状态存储** 是记忆系统的第一层。InkOS的三层权威记忆架构是当前最成熟的实现——story/state/*.json通过Zod schema校验确保状态格式合法，story/*.md提供人类可读的投影方便调试，story/memory.db使用SQLite FTS5/BM25构建时序记忆库<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。Observer从正文中过度提取9类事实（角色/位置/资源/关系/情感/信息/伏笔/时间/物理状态），Reflector输出JSON delta经Zod校验后immutable写入，确保每次状态变更都有完整审计轨迹<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。AI_NovelGenerator通过三份核心文件（character_state.txt、global_summary.txt、plot_arcs.txt）实现类似功能，随定稿逐章更新<sup>[CSDN](https://blog.csdn.net/gitblog_00279/article/details/163272948?index=prev&type=card)</sup>。

**RAG语义检索** 是记忆系统的第二层。AI_NovelGenerator将每章定稿向量化入库，写下一章前检索最相关的前文片段（retrieval_k默认4）<sup>[CSDN](https://blog.csdn.net/gitblog_00279/article/details/163272948?index=prev&type=card)</sup>。webnovel-writer采用混合检索策略——向量检索加BM25经RRF融合后再经Reranker重排序，同时自动构建和维护实体关系图谱<sup>[CSDN](https://gitcode.csdn.net/69bbb80954b52172bc6286d9.html)</sup>。InkOS使用统一的本地检索引擎（SQLite FTS5/BM25），故事记忆、材料库和Skill参考资料共用检索投影<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。织叙工作台可选Chroma加SentenceTransformers建立向量记忆，辅助信息回查<sup>[阿里云](https://developer.aliyun.com/article/1759428)</sup>。

**动态记忆重排序** 是记忆系统的第三层。CoLong Idea Studio提出Memory-First架构，按五个维度动态计算每条记忆的优先级：基础重要度（核心设定优先级最高）、时间衰减（越近的记忆权重越高）、使用强化（被多次引用的记忆权重提升）、章节距离（与当前章节距离越近权重越高）、任务需求（当前任务类型决定哪些记忆被优先召回）<sup>[CSDN](https://blog.csdn.net/xyc_yyds/article/details/159289864)</sup>。这种设计确保有限的上下文窗口被最相关的信息填满，而非简单按时间顺序堆砌。

**状态轮/快照机制** 是跨章一致性的最后保障。每章生成后提取结构化快照——时间点、物理位置、关键物品清单、角色情绪余波——作为下一章的强制前置输入<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。NovelGen的Sliding Prose Window保持约1200字符的最近散文片段，确保章节间的过渡连贯<sup>[GitHub](https://github.com/THU-KEG/StoryWriter)</sup>。webnovel-writer的write-gate三阶段校验（prewrite/precommit/postcommit）在每次状态写入前后进行完整性检查<sup>[GitHub](https://github.com/lingfengQAQ/webnovel-writer/blob/master/docs/guides/commands.md)</sup>。

## 9. 质量审计与去AI味体系

AI生成文本的质量瓶颈不在于"写得不够好"，而在于"AI味太重"和"前后矛盾"<sup>[GitHub](https://github.com/Narcooo/inkos),[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup>。前者导致读者一眼识别出非人类创作，后者导致长篇叙事的可信度崩塌。解决这两个问题需要构建多维度的审计管线与文风指纹注入机制。

**多维审计维度** 在不同项目中有不同实现。InkOS的Auditor执行37维连续性检查，覆盖角色记忆、物资连续性、伏笔回收、大纲偏离、叙事节奏、情感弧线和AI痕迹检测七大类别<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。webnovel-writer实现5维并行审查——爽点（High-point，检查是否达到预设的爽感密度，支持6种执行模式）、一致性（Consistency，检查设定/角色/物品是否矛盾）、节奏（Pacing，检查情节推进速度是否合理）、角色出戏（OOC，检查角色行为是否符合人设）、连贯性（Continuity，检查时间线/空间逻辑）<sup>[GitHub](https://github.com/981029l/webnovel-writer)</sup>。oh-story-claudecode的consistency-checker执行S1-S4四级分级报告，S1为阻断级问题必须修复，S4为建议级可选处理<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup>。AI-Novel-Writing-Assistant引入"质量债务"机制——非阻塞问题以提醒形式记录在质量债务队列中，阻塞问题强制修复后才能继续<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup>。

**去AI味三遍法** 是oh-story-claudecode的核心方法论<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup>。第一遍检测确定性句式问题——如"然而""不禁""目光如炬""在这个XX的世界里"等高频AI用语，以及连续感叹号/问号等AI标点模式，blocking仅限这类确定性问题。第二遍检测模板化表达——如每句对话都带"说道""问道"后缀、每个场景描写都用"映入眼帘"开头。第三遍检测结构性AI痕迹——如段落开头总是转折词、每个角色内心独白都用相同的句式结构。内置禁用词表与改写范例库供参考<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup>。

**文风指纹注入** 是从目标作家作品中提取风格特征并绑定到生成过程的能力。InkOS通过style analyze命令分析目标文本的文风特征（句式长度分布、对话比例、描写密度、修辞偏好），生成风格指纹文件，再通过style import注入后续生成<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。AI-Novel-Writing-Assistant的"写法引擎"将表达规则分为四组高级规则——剧情推进（场景切换节奏、信息释放顺序）、人物表达（对话风格、肢体语言偏好）、语言质感（词汇层级、修辞密度）、节奏密度（段落长度、对话/叙述比例）<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup>。反AI规则作为全局基线叠加在写法引擎之上，确保无论使用何种文风指纹，都不会出现典型的AI表达模式。

## 10. 功能能力矩阵与项目选型建议

### 10.1 十五项核心功能覆盖度

**表3：五大项目十五项核心功能覆盖度矩阵**

| 功能 | InkOS | AI_NovelGenerator | webnovel-writer | oh-story-claudecode | AI-Novel-Writing-Assistant |
|------|-------|-------------------|-----------------|---------------------|---------------------------|
| 世界观/角色结构化创建 | 已实现<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 已实现<sup>[GitHub](https://github.com/YILING0013/AI_NovelGenerator)</sup> | 已实现<sup>[GitHub](https://github.com/981029l/webnovel-writer)</sup> | 已实现<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | 已实现<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| 分层大纲规划 | 已实现<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 已实现<sup>[GitHub](https://github.com/YILING0013/AI_NovelGenerator)</sup> | 已实现<sup>[GitHub](https://github.com/981029l/webnovel-writer)</sup> | 已实现<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | 已实现<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| 市场扫榜与竞品拆文 | 部分实现<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 未实现 | 未实现 | 已实现<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | 部分实现<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| 多Agent协作写作 | 已实现<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 已实现<sup>[GitHub](https://github.com/YILING0013/AI_NovelGenerator)</sup> | 已实现<sup>[GitHub](https://github.com/981029l/webnovel-writer)</sup> | 已实现<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | 已实现<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| 多维质量审查 | 已实现（37维）<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 部分实现<sup>[GitHub](https://github.com/YILING0013/AI_NovelGenerator)</sup> | 已实现（5维）<sup>[GitHub](https://github.com/981029l/webnovel-writer)</sup> | 已实现（S1-S4）<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | 已实现<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| RAG语义检索 | 已实现<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 部分实现<sup>[GitHub](https://github.com/YILING0013/AI_NovelGenerator)</sup> | 已实现<sup>[CSDN](https://gitcode.csdn.net/69bbb80954b52172bc6286d9.html)</sup> | 已实现<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | 已实现<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| 去AI味/文风仿写 | 已实现<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 未实现 | 部分实现 | 已实现<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | 已实现<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| 封面/多媒体衍生 | 已实现<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 未实现 | 未实现 | 已实现<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | 已实现<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| 已有作品导入续写 | 已实现<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 未实现 | 未实现 | 已实现<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | 已实现<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| 版本管理/断点恢复 | 已实现<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 已实现<sup>[GitHub](https://github.com/YILING0013/AI_NovelGenerator)</sup> | 已实现<sup>[GitHub](https://github.com/lingfengQAQ/webnovel-writer/blob/master/docs/guides/commands.md)</sup> | 已实现<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | 已实现<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| 多端交互界面 | 已实现（三端）<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 已实现（桌面）<sup>[GitHub](https://github.com/YILING0013/AI_NovelGenerator)</sup> | 已实现（Web+CLI）<sup>[GitHub](https://github.com/981029l/webnovel-writer)</sup> | 已实现（多平台）<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | 已实现（桌面+Web）<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| 跨会话作者记忆 | 部分实现<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 未实现 | 部分实现<sup>[GitHub](https://github.com/lingfengQAQ/webnovel-writer/blob/master/docs/guides/commands.md)</sup> | 已实现<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup> | 部分实现<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |
| 平台发布集成 | 未实现 | 未实现 | 已实现<sup>[GitHub](https://github.com/981029l/webnovel-writer)</sup> | 未实现 | 未实现 |
| 交互式叙事/开放世界 | 已实现<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 未实现 | 未实现 | 未实现 | 未实现 |
| Token用量监控 | 部分实现<sup>[GitHub](https://github.com/Narcooo/inkos)</sup> | 未实现 | 未实现 | 未实现 | 已实现<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup> |

从覆盖度来看，InkOS在15项功能中实现了12项完整覆盖、3项部分覆盖，是功能最全面的项目<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。oh-story-claudecode实现了13项完整覆盖，在网文全流程覆盖上表现突出<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup>。AI-Novel-Writing-Assistant实现了11项完整覆盖、4项部分覆盖，在新手友好度和成本管理上独树一帜<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup>。

### 10.2 场景化选型建议

**场景一：网文从零创作（新手）。** 推荐AI-Novel-Writing-Assistant，其V2自动导演模式支持从一句灵感到整本规划的全自动流程，桌面版降低了技术门槛，Token预算控制（单章10万上限）避免成本失控<sup>[GitHub](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)</sup>。

**场景二：网文连载辅助（有经验的作者）。** 推荐webnovel-writer，其专为连载场景设计的Story System全链路、37个中文网文题材模板和番茄小说自动发布能力，最贴合连载作者的实际工作流<sup>[GitHub](https://github.com/981029l/webnovel-writer),[GitHub](https://github.com/lingfengQAQ/webnovel-writer/blob/master/docs/guides/commands.md)</sup>。

**场景三：高质量长篇创作（技术团队）。** 推荐InkOS，其10-Agent管线、37维连续性审计、剧情分支推演和互动世界能力，代表了当前AI小说生成的技术上限<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。但需要TypeScript技术栈能力和较高的学习投入。

**场景四：商业化网文全流程（工作室/团队）。** 推荐oh-story-claudecode，其扫榜→拆文→写作→去AI味→封面的完整链路，以及多平台CLI适配能力，最适合批量化、流程化的网文生产<sup>[GitHub](https://github.com/zenstory-ai/oh-story-claudecode)</sup>。

### 10.3 行业尚未解决的五个功能空白

通过对五个项目的全面比对，以下五个功能方向在整个开源生态中均缺乏可靠实现：

1. **多平台一键分发**：仅webnovel-writer实现了番茄小说自动发布<sup>[GitHub](https://github.com/981029l/webnovel-writer)</sup>，InkOS的Roadmap明确列出"平台格式导出"为未完成项<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>，其余项目均未涉及起点、晋江、纵横等其他主流平台的发布集成。
2. **读者反馈闭环驱动创作**：无任何项目实现从评论区、弹幕、订阅数据自动回流指导剧情调整的功能，InkOS的radar scan仅为平台趋势扫描，非实时读者反馈<sup>[GitHub](https://github.com/Narcooo/inkos)</sup>。
3. **多人协同创作**：所有项目均为单人本地工作流，无实时多人编辑、评审、权限管理机制，不支持编辑团队同时操作同一项目。
4. **版权/原创性自动检测**：去AI味仅针对文风痕迹，无抄袭或相似度比对功能，不能验证生成内容是否与已有作品高度雷同。
5. **语音朗读/TTS集成**：无任何项目内置文本转语音或有声书导出能力，无法直接生成有声读物产品。

## 核心参考文献

[Narcooo/inkos: Story Creation AI Agent for novel, scripts ...](https://github.com/Narcooo/inkos)

[zenstory-ai/oh-story-claudecode: Claude Code / Codex ...](https://github.com/zenstory-ai/oh-story-claudecode)

[YILING0013/AI_NovelGenerator: 使用ai生成多章节的长篇小说，自动 ...](https://github.com/YILING0013/AI_NovelGenerator)

[ExplosiveCoderflome/AI-Novel-Writing-Assistant: 面向长篇 ...](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant)

[981029l/webnovel-writer](https://github.com/981029l/webnovel-writer)

[StoryWriter - THU-KEG](https://github.com/THU-KEG/StoryWriter)

[webnovel-writer/docs/guides/commands.md at master](https://github.com/lingfengQAQ/webnovel-writer/blob/master/docs/guides/commands.md)

[Generating Long-form Story Using Dynamic Hierarchical ...](https://arxiv.org/html/2412.13575v1)

[KazKozDev/NovelGenerator: Local AI novel generator for ...](https://github.com/KazKozDev/NovelGenerator)

[Heterogeneous Recursive Planning for Adaptive Long ...](https://arxiv.org/html/2503.08275v3)

[lingfengQAQ/webnovel-writer: 基于Claude Code 的长篇网 ...](https://github.com/lingfengQAQ/webnovel-writer)

[RecurrentGPT: Interactive Generation of (Arbitrarily) Long ...](https://arxiv.org/abs/2305.13304)

[ai-novel-generator](https://github.com/topics/ai-novel-generator)

[Beyond Outlining: Heterogeneous Recursive Planning for ...](https://arxiv.org/abs/2503.08275)

[xindoo/ai-novel-lab: AI小说生成实验室](https://github.com/xindoo/ai-novel-lab)

[Xiaoyangy/novel-studio: 开源、本地优先的AI 长篇小说创作 ...](https://github.com/Xiaoyangy/novel-studio)

[datacrystals/AIStoryWriter: LLM story writer with a focus on ...](https://github.com/datacrystals/AIStoryWriter)

[Junxian Ma Narcooo](https://github.com/Narcooo)

[InkOS download](https://sourceforge.net/projects/inkos.mirror/)

[AI_NovelGenerator/README.md at main - yiling0013](https://github.com/YILING0013/AI_NovelGenerator/blob/main/README.md)

[Activity · YILING0013/AI_NovelGenerator](https://github.com/YILING0013/AI_NovelGenerator/activity)

[Activity · lingfengQAQ/webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer/activity)

[Releases · zenstory-ai/oh-story-claudecode](https://github.com/zenstory-ai/oh-story-claudecode/releases)

[oh-story-claudecode/skills/story-setup/SKILL.md at main](https://github.com/zenstory-ai/oh-story-claudecode/blob/main/skills/story-setup/SKILL.md)

[本版本更新说明](https://github.com/ExplosiveCoderflome/AI-Novel-Writing-Assistant/releases)

[capability-inventory.md - Xiaoyangy/novel-studio](https://github.com/Xiaoyangy/novel-studio/blob/main/docs/capability-inventory.md)

[Narrative-of-Thought: Improving Temporal Reasoning ...](https://arxiv.org/html/2410.05558v1)

[jackaduma/Recurrent-LLM: The open-source ...](https://github.com/jackaduma/Recurrent-LLM)

[\[2209.14958\] Co-Writing Screenplays and Theatre Scripts ...](https://arxiv.org/abs/2209.14958)

[A Multi-Agent Framework for Long Story Generation](https://dl.acm.org/doi/10.1145/3746252.3761616)

[A Multi-Agent Framework for Long Story Generation](https://arxiv.org/abs/2506.16445)

[principia-ai/WriteHERE: An Open-Source AI Writing Project.](https://github.com/principia-ai/WriteHERE)

[AI长篇小说生成完整指南：用AI_NovelGenerator写出设定不崩的多章节作品-CSDN博客](https://blog.csdn.net/gitblog_00279/article/details/163272948?index=prev&type=card)

[StoryWriter: A Multi-Agent Framework for Long Story Generation - AMiner](https://www.aminer.org/pub/6858b4ff163c01c8505cf781)

[【GitHub项目推荐--Webnovel Writer：基于Claude Code的长篇网文AI创作系统】⭐_github_旅之灵夫-AtomGit开源社区](https://gitcode.csdn.net/69bbb80954b52172bc6286d9.html)

[AI_NovelGenerator全指南：自动化长篇小说AI生成工具，彻底解决网文创作的人设崩塌与剧情断层难题_ai-novel-generator-CSDN博客](https://blog.csdn.net/zsh_1314520/article/details/160830617)

[AI网文提示词为何常导致角色设定崩坏？_编程语言-CSDN问答](https://ask.csdn.net/questions/9376140)

[AI网文写作全景调研（二·上）：开源项目技术路线拆解——Agent与平台路线-CSDN博客](https://blog.csdn.net/loopne/article/details/163742580)

[从静态检索到动态记忆：面向长篇 AI 写作的一种 Memory-First 架构思路_memory.first-CSDN博客](https://blog.csdn.net/xyc_yyds/article/details/159289864)

[InkOS：一个开源的故事创作AI Agent系统_映技派](https://www.yjpoo.com/site/7682.html)

[WriteHERE - 开源的AI长文写作框架，单次生成超长文本 | AI工具集](https://ai-bot.cn/writehere/)

[AI 长篇小说创作管理开源软件：织叙工作台的设定数据与创作工作流设计-阿里云开发者社区](https://developer.aliyun.com/article/1759428)

[AI写小说全套提示词，从构思到完稿这一篇就够了](https://mp.weixin.qq.com/s?__biz=MzYzNjk3MjAwMw==&idx=1&mid=2247483856&sn=b00916c7ad57134e7d0332d13925e145)

[完整Prompt模板包：7篇文章浓缩成一套可复用的AI写作系统](https://mp.weixin.qq.com/s?__biz=Mzg2NTA3MTM0Mg==&idx=1&mid=2247484357&sn=f4fd2f99b24f1b6faf13ee830c84084f)

[从0到1搭建网文AI生成系统：工程化落地完整实践 - 钱栈up - 博客园](https://www.cnblogs.com/aresen/p/22076474)

[世界观生成器｜六模块设定模板与提示词整理 · 马良写作](https://maliangwriter.com/tools/worldbuilding-generator/)

[彩云小梦怎么为 AI写作添加炫酷的修仙境界与等级体系？-人工智能-PHP中文网](https://www.php.cn/faq/2912004.html)

[2026玄幻网文AI写小说工具推荐：战力体系与长篇升级测评 - 今日头条](https://www.toutiao.com/article/7672176561872847394/)

[AI写小说提示词模板：从原理到高效创作的工程实践-CSDN博客](https://blog.csdn.net/2600_94959791/article/details/157341583)

[小白也能出书：用“章节递归法”Prompt 7天生成12万字初稿-腾讯云开发者社区-腾讯云](https://cloud.tencent.com/developer/article/2693970)

[文生文Prompt指南_提示词工程实践_最佳实践_MaaS模型即服务-华为云](https://support.huaweicloud.com/intl/zh-cn/bestpractice-maas/bestpractice_maas_0018.html)
