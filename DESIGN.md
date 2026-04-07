# NoteWeaver — 个人知识管理 Agent 设计讨论

> 一个安全、自主的个人知识管理 Agent，以 Markdown 为核心，能自行整理、结构化、持久化知识，并从互联网拉取必要信息。面向 C 端用户，不依赖编程/Shell 等开发者抽象。

---

## 一、问题与动机

### 1.1 现状的痛点

| 方案 | 问题 |
|------|------|
| **手动管理（Obsidian、Notion 等）** | 整理是苦力活——交叉引用、保持一致性、维护结构，人类会因维护负担放弃 |
| **RAG 式对话（NotebookLM、ChatGPT 文件上传）** | 每次查询都从零开始发现知识，没有积累，没有复合效应 |
| **OpenClaw** | 大炮打苍蝇——权限太高、太灵活、太复杂，是给开发者用的通用 Agent |
| **Claude Code / Codex** | 以编程和 Shell 为基础抽象，C 端用户不应该面对终端 |

### 1.2 核心洞察

来自 Karpathy 的 [LLM Knowledge Bases](https://x.com/karpathy/status/2039805659525644595) 推文和后续 [gist](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)：

> "a large fraction of my recent token throughput is going less into manipulating code, and more into manipulating knowledge (stored as markdown and images)."

> "I thought I had to reach for fancy RAG, but the LLM has been pretty good about auto-maintaining index files and brief summaries of all the documents and it reads all the important related data fairly easily at this ~small scale."

> **"I think there is room here for an incredible new product instead of a hacky collection of scripts."**

Karpathy 的工作流：原始数据 → LLM 编译成 Wiki → 通过 CLI 工具做 Q&A → 产出（Markdown/Slides/图表）回填 Wiki → 知识不断复合积累。他的核心发现是：在 ~100 篇文章、~400K 词的规模下，不需要 fancy RAG，LLM 自维护的 index 文件就足够导航。

来自 Fridman 对 Karpathy 的[回复](https://x.com/lexfridman/status/2039841897066414291)——**之前搜索引擎完全没捕获到这条回复的内容**：

> "For answers, I often have it generate dynamic html (with js) that allows me to sort/filter data and to tinker with visualizations interactively."

> "I have the system generate a temporary focused mini-knowledge-base for a particular topic that I then load into an LLM for voice-mode interaction on a long 7-10 mile run. So it becomes an interactive podcast while I run."

Fridman 的用法揭示了两个关键方向：
1. **多模态输出**：不只是 Markdown，而是动态交互式 HTML/JS 可视化
2. **知识的便携投射**：从完整知识库中抽取"迷你知识库"→ 加载到特定场景（语音模式、移动端等），知识库是母体，各种交互形态是投射

来自 Fridman 另一条关于 [AI Agent 安全性的推文](https://substack.com/@lexfridman/note/c-215562154)：

> "The power of AI agents comes from: (1) intelligence of the underlying model, (2) how much access you give it to all your data, (3) how much freedom & power you give it to act on your behalf. I think for 2 & 3, security is the biggest problem."

> "The more data & control you give to the AI agent: (A) the more it can help you AND (B) the more it can hurt you."

**我们的定位**：在 "哑巴 RAG 聊天" 和 "拥有 Shell 访问权的全自主 Agent" 之间，找到一个**安全、专用、面向消费者**的甜点。正如 Karpathy 所说，这应该是一个"incredible new product"，而不是"a hacky collection of scripts"。

---

## 二、核心理念

### 2.1 "知识编译器"而非"知识检索器"

受 Karpathy 启发，我们的 Agent 不是在查询时临时拼凑答案，而是**持续构建和维护一个结构化知识库**——一个有交叉引用、有矛盾标注、有时间线的 Markdown Wiki。知识被"编译"一次，然后持续更新，而非每次"解释执行"。

### 2.2 新的抽象层：不是 Shell，而是"知识操作"

这是与 OpenClaw/Claude Code 最本质的区别。我们不给 Agent Shell 和代码执行能力，而是提供一组**领域专用的、安全的知识操作原语**：

```
[Knowledge Operations — Agent 的全部能力边界]

📖 READ      — 读取笔记/文档
✏️ WRITE     — 创建或更新笔记
🔗 LINK      — 建立/管理文档间关联
🔍 SEARCH    — 在知识库中搜索
📥 INGEST    — 从外部源导入内容（网页、PDF、图片）
🏗️ ORGANIZE  — 重组织结构（移动、合并、拆分文档）
🌐 FETCH     — 从互联网拉取信息
📊 ANALYZE   — 分析知识库状态（矛盾、孤立页、缺口）
🎨 RENDER    — 生成多模态输出（交互式 HTML/JS、幻灯片、图表、迷你知识库）
```

Agent **不可能**执行这些操作之外的任何事情。没有 `exec()`，没有 `bash`，没有任意代码。这是**设计层面的安全**，不是策略层面的限制。

注意 RENDER 操作受 Fridman 启发——他让系统"generate dynamic html (with js) that allows me to sort/filter data and to tinker with visualizations interactively"。输出不只是静态 Markdown，还可以是交互式的可视化。但 RENDER 生成的 JS 只在沙箱化的查看器中执行，不能访问文件系统或网络。

### 2.3 面向 C 端的设计原则

- **零配置启动**：打开就能用，无需理解 YAML 配置文件或权限模型
- **透明可理解**：Agent 的每一步操作都可审计，用户可以看到"Agent 正在更新 3 个相关页面的交叉引用"
- **人类保持控制**：Agent 提议，人类批准（或设置自动批准规则）
- **数据主权**：所有数据就是本地 Markdown 文件，随时可以用任何编辑器打开

---

## 三、架构设计

### 3.1 三层架构（借鉴 Karpathy）

```
┌─────────────────────────────────────────────────┐
│                  用户界面层                        │
│         Web UI / Desktop App / CLI              │
│     （对话、知识库浏览、图谱可视化）                 │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│              Agent 核心层                         │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐     │
│  │ 规划引擎  │ │ 操作执行器│ │ 知识图谱引擎  │     │
│  │(Planner) │ │(Executor)│ │(Knowledge    │     │
│  │          │ │          │ │  Graph)      │     │
│  └──────────┘ └──────────┘ └──────────────┘     │
│  ┌──────────┐ ┌──────────┐ ┌──────────────┐     │
│  │ Web 拉取  │ │ 内容解析器│ │ Schema 管理  │     │
│  │(Fetcher) │ │(Parser)  │ │(Schema Mgr)  │     │
│  └──────────┘ └──────────┘ └──────────────┘     │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│               知识库存储层                         │
│                                                  │
│  📁 vault/                                       │
│  ├── 📁 sources/      ← 原始素材（不可变）         │
│  ├── 📁 wiki/         ← Agent 维护的结构化知识     │
│  │   ├── index.md     ← 知识索引                  │
│  │   ├── log.md       ← 操作日志                  │
│  │   ├── entities/    ← 实体页面                  │
│  │   ├── concepts/    ← 概念页面                  │
│  │   ├── journals/    ← 日记/日志                 │
│  │   └── synthesis/   ← 综合分析                  │
│  ├── 📁 .schema/      ← 知识库的"宪法"            │
│  │   └── schema.md    ← 结构约定、工作流定义        │
│  └── 📁 .meta/        ← 元数据（图谱、搜索索引）    │
└──────────────────────────────────────────────────┘
```

### 3.2 Agent 执行模型

借鉴 Claude Code 的权限管线思想，但大幅简化：

```
用户意图
  │
  ▼
┌──────────────┐
│   意图理解    │  用户说 "帮我整理上周的读书笔记"
└──────┬───────┘
       │
       ▼
┌──────────────┐
│   计划生成    │  Agent 生成操作计划：
│              │  1. SEARCH: 查找上周标记为"读书"的笔记
│              │  2. READ: 读取 5 篇相关笔记
│              │  3. ANALYZE: 提取关键概念和关联
│              │  4. WRITE: 创建综合摘要页
│              │  5. LINK: 更新相关页面的交叉引用
│              │  6. WRITE: 更新 index.md
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  权限检查     │  每个操作是否在允许范围内？
│              │  WRITE 到 sources/ → 拒绝（不可变区域）
│              │  FETCH 到可疑域名 → 需要确认
│              │  其他 → 自动放行
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  执行 & 反馈  │  逐步执行，实时展示进度
│              │  "✓ 找到 5 篇相关笔记"
│              │  "✓ 创建了综合页: wiki/synthesis/读书周报-W14.md"
│              │  "✓ 更新了 3 个页面的交叉引用"
└──────────────┘
```

### 3.3 安全模型——"花园围墙"

对比三种 Agent 的安全哲学：

| | OpenClaw | Claude Code | NoteWeaver (本项目) |
|---|---|---|---|
| **基础抽象** | Shell + 任意工具 | Shell + 文件系统 | 知识操作原语 |
| **安全策略** | 6 层防御纵深 | 7 步权限管线 | 操作集合本身即安全边界 |
| **可执行操作** | 几乎无限 | 几乎无限（受限） | 9 种知识操作 |
| **最坏情况** | 系统被完全控制 | 文件被误删改 | 知识库笔记被错误修改（可回滚） |
| **目标用户** | 开发者 | 开发者 | 所有人 |

关键安全设计：

1. **操作即白名单**：Agent 只能执行预定义的 9 种知识操作，不存在"逃逸"到任意执行的路径
2. **sources/ 不可变**：原始素材只读，Agent 只能在 wiki/ 中工作
3. **Git 版本控制**：所有变更自动版本化，任何操作都可以完整回滚
4. **网络访问受限**：FETCH 操作只能访问白名单域名或需要用户确认
5. **透明审计**：所有操作记录在 log.md，用户随时可以审查

---

## 四、与参考项目的关系

### 4.1 从 Karpathy 的 LLM Knowledge Bases 借鉴

Karpathy 的完整工作流（来自原始推文）：

1. **Data ingest**: 原始文档进 `raw/`，用 Obsidian Web Clipper 裁剪网页，下载图片到本地
2. **IDE**: Obsidian 作为前端，查看原始数据、编译后的 wiki、衍生可视化。"the LLM writes and maintains all of the data of the wiki, I rarely touch it directly"
3. **Q&A**: Wiki 到 ~100 篇文章/~400K 词时，可以做复杂查询。"I thought I had to reach for fancy RAG, but the LLM has been pretty good about auto-maintaining index files"
4. **Output**: 产出 Markdown/Marp slides/matplotlib 图表，在 Obsidian 中查看。"I end up filing the outputs back into the wiki to enhance it for further queries"——探索的成果回填知识库，形成复合循环
5. **Linting**: LLM 做"健康检查"——找不一致数据、用 web 搜索补缺、发现新文章候选
6. **Extra tools**: 自己 vibe code 了搜索引擎，可以通过 web UI 直接用，也可以给 LLM 通过 CLI 作为工具调用

**直接采纳的**：
- "raw → compile → wiki → query → file back" 这个完整循环
- index 文件替代 RAG 的简洁方案
- Linting/健康检查作为一等公民操作
- 产出回填知识库的复合循环
- "You rarely ever write or edit the wiki manually, it's the domain of the LLM"

**不同的地方**：
- Karpathy 的方案是 "a hacky collection of scripts"（他自己的话），依赖 Claude Code/Codex 的通用 Agent 能力。我们要做他自己说的那个 "incredible new product"
- 他需要自己 vibe code 搜索引擎等工具——我们把这些内置
- 他用 Obsidian 作为前端——我们构建自己的 UI，更简单、更集成
- 我们加入主动性：Agent 可以自行发现知识缺口并建议补充

### 4.1.1 从 Fridman 的回复借鉴

Fridman 的用法扩展了 Karpathy 的模式：

- **多前端**: "A mix of Obsidian, Cursor (for md), and vibe-coded web terminals as front-end"——他已经在混用多种前端了，说明需要一个统一的体验
- **动态输出**: "generate dynamic html (with js) that allows me to sort/filter data and to tinker with visualizations interactively"——静态 Markdown 不够，需要交互式可视化
- **知识投射**: "generate a temporary focused mini-knowledge-base for a particular topic that I then load into an LLM for voice-mode interaction"——知识库不只是被查询，还可以被"投射"到不同场景
- **播客式学习**: "it becomes an interactive podcast while I run"——语音交互是一等公民

**直接采纳的**：
- RENDER 操作：支持生成交互式 HTML/JS 可视化（沙箱执行）
- 知识投射：从完整知识库生成面向特定场景/话题的迷你版
- 语音模式作为一等公民交互方式
- 多种输出格式（MD/HTML/Slides/Charts）

### 4.2 从 OpenClaw 借鉴

**借鉴的**：
- 多层安全模型的思想（但大幅简化）
- Schema/配置驱动 Agent 行为的模式
- Tool 分组的概念（我们的 8 种操作就是一个极简的 tool 集合）

**刻意不做的**：
- 不做通用 Agent 平台——我们是专用的知识管理 Agent
- 不做 Sandbox/Docker 隔离——我们的操作原语本身就不需要沙箱
- 不做多 Agent 编排——一个 Agent 就够了
- 不做插件/扩展系统——保持简单

### 4.3 从 Claude Code 借鉴

**借鉴的**：
- 权限管线的设计模式（简化为：操作白名单 → 区域检查 → 用户确认策略）
- 系统提示的静态/动态分离思想
- Hook 机制（用户可以设置规则，如"日记类自动归档到 journals/"）

**刻意不做的**：
- 不以终端为界面
- 不给 Shell 访问
- 不做代码执行

---

## 五、关键技术决策

### 5.1 为什么是 Markdown？

- **数据主权**：纯文本，任何编辑器都能打开，不锁定
- **LLM 友好**：LLM 天生擅长读写 Markdown
- **Git 友好**：完美的版本控制和 diff
- **生态丰富**：Obsidian、VS Code、Typora 都能打开
- **互操作性**：可以随时迁移到任何其他系统

### 5.2 存储与同步

```
本地优先 + 可选云同步

┌─────────────┐    ┌─────────────┐    ┌──────────────┐
│  本地文件系统  │◄──►│  Git 仓库    │◄──►│ 远程 Git     │
│  (Markdown)  │    │  (版本控制)  │    │ (同步/备份)  │
└─────────────┘    └─────────────┘    └──────────────┘
```

- 本地优先，离线可用
- Git 作为版本控制和同步机制
- 可选对接 GitHub/GitLab 做远程备份
- 未来可以加端到端加密

### 5.3 搜索与索引

小规模（<1000 页）：基于 index.md 的 LLM 导航就够了
中规模（1000-10000 页）：本地全文搜索（如 qmd、MiniSearch）
大规模（>10000 页）：向量索引 + BM25 混合搜索

### 5.4 LLM 集成策略

```
┌────────────────────────────────────────┐
│           LLM 路由层                    │
│                                        │
│  轻量任务 → 本地小模型 (Ollama)         │
│   - 分类、标签、简单搜索                 │
│                                        │
│  重量任务 → 云端大模型 (API)            │
│   - 综合分析、深度写作、知识整合          │
│                                        │
│  可配置：纯本地 / 纯云端 / 混合         │
└────────────────────────────────────────┘
```

---

## 六、用户交互范式

### 6.1 三种交互模式

**对话模式**：像聊天一样与知识库互动
```
用户: 我最近在研究 Rust 的所有权模型，帮我整理一下我已有的笔记
Agent: 我找到了 3 篇相关笔记。我来为你创建一个综合页面...
       [实时展示操作进度]
       完成！创建了 "Rust 所有权模型" 页面，关联了 3 篇源笔记。
       我注意到你的笔记中有一个关于生命周期的描述与官方文档不太一致，
       要我帮你从网上拉取最新的官方说明吗？
```

**自动模式**：Agent 在后台自主维护知识库
- 定期执行"lint"——发现矛盾、孤立页、缺口
- 自动更新交叉引用
- 建议新的研究方向

**快速捕获模式**：快速记录想法，Agent 稍后整理
```
用户: [快速输入] 今天和小明聊了关于分布式系统的 CAP 定理，
      他提到 Spanner 用了 TrueTime 来绕过这个限制
Agent: ✓ 已记录到今日日记。我发现你的知识库中已经有一个
       "CAP 定理" 页面，稍后我会更新它，加入 Spanner/TrueTime
       的内容。
```

**投射模式**（受 Fridman 启发）：从知识库抽取迷你版，投射到特定场景
```
用户: 我要出去跑步了，帮我准备一个关于"分布式共识算法"的迷你知识库，
      用语音模式和我聊
Agent: ✓ 已从知识库中抽取了以下内容生成迷你知识库：
       - Paxos 和 Raft 的核心区别
       - CAP 定理及其实际影响
       - 3 个你上周标注为"需要深入"的问题
       正在加载到语音模式...
       🎧 准备好了，跑步愉快！
```

Fridman 的原话："I have the system generate a temporary focused mini-knowledge-base for a particular topic that I then load into an LLM for voice-mode interaction on a long 7-10 mile run. So it becomes an interactive podcast while I run."
这揭示了一个重要范式：**知识库是母体，各种交互形态是投射**。同一份知识可以投射为：
- 语音对话（跑步/通勤时）
- 交互式 HTML 仪表板（深度分析时）
- 幻灯片（分享/演讲时）
- 迷你知识库（聚焦学习时）

### 6.2 与 OpenClaw/Claude Code 的交互对比

| | OpenClaw / Claude Code | NoteWeaver |
|---|---|---|
| 输入 | 自然语言 → 转化为代码/命令 | 自然语言 → 转化为知识操作 |
| 输出 | 代码变更、终端输出 | 知识页面更新、结构变化、分析报告 |
| 反馈 | "执行了 `git commit`" | "更新了《量子计算》页面的第 3 节" |
| 风险 | 可能执行危险命令 | 最多写错一篇笔记（可回滚） |

---

## 七、技术栈建议

```
核心引擎:     TypeScript (Bun runtime)
知识库:       Markdown 文件 + Git
搜索:         MiniSearch (本地) / qmd (高级)
LLM 集成:     Vercel AI SDK (多模型统一接口)
Web 拉取:     Mozilla Readability + Turndown (HTML→MD)
桌面应用:     Tauri (轻量跨平台)
Web UI:       React + TailwindCSS
CLI:          Commander.js (开发者可选)
```

---

## 八、MVP 范围

第一个可用版本应该足够小但足够有说服力：

### MVP 包含

1. **知识库初始化**：创建 vault 目录结构
2. **对话式知识管理**：通过对话创建、查找、更新笔记
3. **自动交叉引用**：Agent 在写入时自动维护链接
4. **Web 内容导入**：给一个 URL，Agent 拉取、清洗、整合到知识库
5. **知识库健康检查**：Karpathy 所说的 "linting"——发现矛盾、补缺数据、发现新文章候选
6. **产出回填**：查询的输出可以"filing back into the wiki"，形成复合循环
7. **CLI 界面**：先做 CLI，验证核心逻辑
8. **Git 版本控制**：所有变更自动 commit

### MVP 不包含（但路线图上很重要）

- 交互式 HTML/JS 可视化输出（Fridman 模式）—— 第二优先
- 迷你知识库投射 + 语音模式（Fridman 的跑步模式）—— 第二优先
- 桌面应用 / Web UI
- 本地模型支持（先用云端 API）
- 多人协作
- 端到端加密
- 图片/PDF 处理（先纯文本）

---

## 九、开放问题

1. **Agent 的主动性边界在哪里？** 多主动算太主动？自动重组织结构？自动从网上拉取信息？需要用户测试来确定。

2. **Schema 的演进**：Karpathy 的 gist 说 Schema 是人和 LLM "共同演进"的。对 C 端用户来说，怎么简化这个过程？也许 Agent 可以自行演进 Schema 并展示给用户确认。

3. **成本控制**：一次 ingest 可能触及 10-15 个页面，每次都调用 LLM。需要智能的缓存和批处理策略。Karpathy 提到"at this ~small scale"不需要 RAG，但规模增长后需要更高效的方案。

4. **与现有工具的关系**：Fridman 已经在混用 "Obsidian, Cursor (for md), and vibe-coded web terminals as front-end"。我们是替代它们，还是成为它们的协调者？建议：知识库就是普通 Markdown 文件夹，用户可以同时用 Obsidian 打开，但我们提供最佳的集成体验。

5. **RENDER 的安全边界**：Fridman 的 "generate dynamic html (with js)" 很有吸引力，但生成的 JS 代码如何安全执行？需要 iframe sandbox 或类似机制。

6. **语音模式的实现路径**：Fridman 的跑步模式是杀手级场景。是集成现有 TTS/STT API，还是依赖 LLM 供应商的原生语音模式？

7. **Karpathy 提到的 "synthetic data generation + finetuning"**：让 LLM 把知识"知道"在权重里而非上下文窗口里。这是长期方向，但对成本和离线使用有巨大影响。

8. **命名**：暂用 NoteWeaver，但需要一个更好的名字来传达"AI 帮你织知识网络"的概念。

---

## 十、下一步

1. 搭建项目骨架（TypeScript + Bun）
2. 实现知识库初始化和基本 CRUD 操作
3. 实现 Agent 规划-执行循环的 PoC
4. 实现 Web 内容拉取和知识整合
5. 验证核心价值：给 Agent 10 篇文章，看它能构建出怎样的知识图谱
