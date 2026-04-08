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

### 2.0 第零原则：渐进式披露——围绕大模型的可导航性设计

**这是整个系统最根本的设计原则。** 一切其他设计都从这里推导。

知识库的组织方式不应以传统检索系统为中心，而应以"可由大模型（和人类）逐层触达和理解"为目标设计。这意味着知识库在结构上是一棵**树**（树状层级用于高效定位，O(log n)），同时叠加了一张**图**（`[[wiki-links]]` 构成的网状引用，用于发现关联）。

```
导航路径（树）：

index.md （根 — 列出 Hub + 一句话描述，控制在 ~1000 tokens）
  → Hub 页面  （某个主题的概览 + 指向具体页面的链接）
    → Canonical / Note / Synthesis  （具体内容）
      → Sources  （原始依据）

关联发现（图）：

任何页面 --[[wiki-link]]--> 任何其他页面
```

**为什么是树+图？**

- 树解决"我知道我要找什么"——LLM 从 index.md 出发，两跳到达任何内容
- 图解决"我不知道这两个东西有关系"——LLM 沿 `[[链接]]` 发现跨主题关联
- 树+图 = 人脑组织知识的方式（层级分类 + 联想关联）

**为什么这是"根本奥义"？**

好的 LLM 可读性和好的人类可读性本质上是同一件事。一个组织良好的知识库，不论是人还是 LLM 来阅读，导航路径都应该相似——从概览到具体，从入口到深处。如果知识库组织得连 LLM 都无法高效导航，人类也一定无法维护。

**倒金字塔原则**：每个页面的前 1-2 句必须是自包含的摘要，回答"这个页面讲什么"。LLM 可以通过只读多个页面的开头段落来快速判断相关性，只深读真正需要的页面。

对应代码实现：
- `vault.py` 中的 `INITIAL_SCHEMA`：写入了渐进式披露原则和树状结构规范
- `vault.py` 中的 `INITIAL_INDEX`：模板从平列表改为 Hub 导航入口
- `agent.py` 中的 `SYSTEM_PROMPT`：导航流程为 index → Hub → 具体页面；写作规范要求倒金字塔结构；当主题积累 3+ 页面时创建 Hub

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

### 3.1 核心范式：电脑整理 + 手机记录

知识管理有两种截然不同的使用场景，需要不同的设备和交互方式：

```
 🖥️ 电脑 = 书房                          📱 手机 = 随身便签
 ──────────────────                     ──────────────────
 深度整理、综合分析、                      碎片想法、随手记录、
 阅读导入、结构重组                        听到的观点、突发灵感

 CLI / Web UI / Obsidian                Telegram / 微信（未来）
 长会话、复杂操作                          短消息、快进快出
 看到全貌                                只需要"记下来"
```

这两个场景必须打通——在手机上随手记的东西，回到电脑上能看到已经被 Agent 整理好了。这不是"多端同步"（Obsidian Sync 解决的问题），而是**多入口共享同一个 Agent 和同一个知识库**。

### 3.2 四层架构（借鉴 Hermes Agent 网关模式）

```
┌──────────────────────────────────────────────────────────┐
│                    入口层 (Platform Adapters)              │
│                                                          │
│  ┌───────┐  ┌──────────┐  ┌────────┐  ┌──────────────┐  │
│  │  CLI  │  │ Telegram  │  │ Web UI │  │ 未来: 微信   │  │
│  │       │  │  Bot      │  │        │  │ Discord 等   │  │
│  └───┬───┘  └────┬─────┘  └───┬────┘  └──────┬───────┘  │
│      │           │            │              │           │
│      └───────────┴─────┬──────┴──────────────┘           │
│                        ▼                                 │
│              统一消息接口 Message                          │
│              { source, userId, text, attachments }       │
└────────────────────────┬─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│                   Agent 核心层                             │
│                   (与平台完全无关)                          │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │  KnowledgeAgent                                  │    │
│  │                                                  │    │
│  │  意图理解 → 操作规划 → 权限检查 → 执行 → 反馈     │    │
│  │                                                  │    │
│  │  工具: 9 种知识操作原语                            │    │
│  │  (READ/WRITE/LINK/SEARCH/INGEST/                 │    │
│  │   ORGANIZE/FETCH/ANALYZE/RENDER)                 │    │
│  └──────────────────────────────────────────────────┘    │
└────────────────────────┬─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│                   知识库层 (Vault)                         │
│                                                          │
│  📁 vault/                                               │
│  ├── 📁 sources/      ← 原始素材（不可变）                 │
│  ├── 📁 wiki/         ← Agent 维护的结构化知识             │
│  │   ├── index.md     ← 知识索引                          │
│  │   ├── log.md       ← 操作日志                          │
│  │   ├── entities/    ← 实体页面                          │
│  │   ├── concepts/    ← 概念页面                          │
│  │   ├── journals/    ← 日记/日志                         │
│  │   └── synthesis/   ← 综合分析                          │
│  ├── 📁 .schema/      ← 知识库的"宪法"                    │
│  │   └── schema.md    ← 结构约定、工作流定义                │
│  └── 📁 .meta/        ← 衍生数据（可重建）                 │
│      ├── graph.db     ← 知识图谱 (SQLite)                 │
│      ├── search.idx   ← 搜索索引                          │
│      └── state.json   ← Agent 状态                        │
└──────────────────────────────────────────────────────────┘
```

关键设计原则（借鉴 Hermes Agent）：

1. **适配器只做翻译**：每个平台入口只负责消息格式转换（平台格式 ↔ 统一 Message），不包含任何业务逻辑。加一个新平台 = 写一个薄适配器。
2. **Agent 核心与平台无关**：`KnowledgeAgent` 不知道消息来自 CLI 还是 Telegram。它只处理 `Message`，返回 `Response`。
3. **所有入口共享同一个 Vault**：手机上记的东西，电脑上立刻能看到（因为操作的是同一套文件）。

### 3.3 两种部署模式

```
模式 A: 纯本地（MVP 默认）
─────────────────────────
  用户的电脑上运行 CLI / Web UI
  知识库就是本地文件夹
  没有手机入口（要加需要自己部署网关）

  适合: 先在电脑上验证核心价值


模式 B: 本地 + 网关（加手机后）
────────────────────────────
  用户的电脑: CLI / Web UI，直接操作本地 vault
  远端网关: 一个轻量服务，连接 Telegram Bot
  两者操作同一个 vault（通过 Git 同步或共享存储）

  ┌──────────────┐          ┌──────────────────┐
  │  用户电脑      │          │  网关服务          │
  │              │   sync   │  (VPS / 云函数)   │
  │  CLI ──► vault ◄──────► │  Telegram ──► vault│
  │  Obsidian ──┘│          │                  │
  └──────────────┘          └──────────────────┘

  适合: 需要手机随时记录的用户
```

网关服务可以很轻（$5/月 VPS 或按用量计费的云函数）。Hermes Agent 已经证明了这个模式的可行性。

vault 同步有几种方案：
- **Git 推拉**：网关每次操作后 commit + push，本地定期 pull。简单但有延迟。
- **共享文件系统**：vault 放在云存储（如 S3 + 本地挂载）。实时但需要基础设施。
- **中心化 API**：Agent 核心跑在服务端，CLI/Telegram 都是客户端。最简洁但失去本地优先。

MVP 建议从"纯本地"起步，在验证核心价值后加入 Telegram 网关。

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

### 6.1 两种设备，两种节奏

核心洞察：知识管理不是单一场景，而是**两种节奏的交替**。产品必须同时服务好这两种节奏。

#### 📱 手机节奏：快进快出，随手记录

手机上的交互应该像发微信一样轻——打开 Telegram，说一句话，关掉。Agent 在后台默默处理。

```
[Telegram]

用户: 刚听播客，提到 Transformer 的 KV Cache 优化有种叫
     PagedAttention 的技术，来自 vLLM 项目

Agent: ✓ 收到。已记录到今日日记。
       发现知识库中已有「Transformer 架构」和「推理优化」页面，
       我会把 PagedAttention/vLLM 整合进去。

(30 分钟后)

用户: 午饭时同事说 DeepSeek 的 MLA 比 MHA 省 70% KV Cache

Agent: ✓ 收到。这和你刚才记的 PagedAttention 相关——
       两者都是 KV Cache 优化方向，一个优化存储布局，
       一个优化注意力计算本身。我会在概念页里标注这个对比。
```

手机上 Agent 的行为特征：
- **极简反馈**：确认收到 + 一句话说明会怎么处理，不长篇大论
- **自动归类**：用户不需要指定"这条记到哪里"，Agent 自行判断
- **延迟整合**：不需要实时完成所有交叉引用更新，可以攒一批一起处理
- **支持碎片输入**：一条消息可以是一句话、一个链接、一张图片、一段语音

#### 🖥️ 电脑节奏：深度整理，全局操控

电脑上的交互是深度工作模式——用户有时间和屏幕空间来做复杂操作。

```
[CLI]

用户: 整理一下我这周所有关于 LLM 推理优化的笔记

Agent: 我找到了本周 8 条相关记录（5 条来自 Telegram 随手记，
       2 条来自导入的文章，1 条来自昨天的对话）。
       
       我计划：
       1. 将 5 条碎片笔记合并整理
       2. 创建综合页「LLM 推理优化全景」
       3. 更新 3 个已有概念页的交叉引用
       4. 标注发现的 1 处矛盾（关于 MQA vs GQA 的效率对比）
       
       执行？

用户: 执行

Agent: ✓ 创建了 wiki/synthesis/llm-inference-optimization.md
       ✓ 更新了 wiki/concepts/kv-cache.md（+PagedAttention, +MLA）
       ✓ 更新了 wiki/concepts/attention-mechanisms.md（+MLA vs MHA）
       ✓ 更新了 wiki/entities/deepseek.md（+MLA 技术）
       ⚠ 矛盾标注: 你 4/3 记录说 MQA 比 GQA 快 2x，
          但 4/5 导入的论文说只快 1.3x。需要你判断。
       ✓ 更新了 index.md
```

电脑上 Agent 的行为特征：
- **展示操作计划**：复杂操作先展示计划，确认后执行
- **详细反馈**：每一步做了什么，改了哪些页面
- **矛盾标注**：发现数据不一致时提请人类判断
- **支持复杂操作**：导入文章、结构重组、批量整理、生成可视化

#### 两种节奏的协作

关键不是"多端同步"，而是**手机喂料，电脑加工**：

```
  📱 手机                              🖥️ 电脑
  ──────                              ──────
  碎片输入 ──► Agent 暂存日记 ──────────► 整理成结构化知识
  链接分享 ──► Agent 抓取暂存 ──────────► 深度导入知识库
  语音备忘 ──► Agent 转录暂存 ──────────► 关联已有笔记
                                       浏览 Wiki + Obsidian
                                       综合查询和分析
                                       结构重组
```

Agent 会维护一个"收件箱"——`wiki/journals/inbox.md`，手机端的碎片输入先落到这里。用户回到电脑时，Agent 可以建议："你有 6 条待整理的笔记，要我帮你处理吗？"

### 6.2 其他交互模式

**自动模式**：Agent 在后台自主维护知识库
- 定期执行"lint"——发现矛盾、孤立页、缺口
- 自动更新交叉引用
- 建议新的研究方向

**投射模式**（受 Fridman 启发）：从知识库抽取迷你版，投射到特定场景
```
用户: 我要出去跑步了，帮我准备一个关于"分布式共识算法"的迷你知识库，
      用语音模式和我聊
Agent: ✓ 已从知识库中抽取了以下内容生成迷你知识库：
       - Paxos 和 Raft 的核心区别
       - CAP 定理及其实际影响
       - 3 个你上周标注为"需要深入"的问题
       正在加载到语音模式...
```

Fridman 的原话："I have the system generate a temporary focused mini-knowledge-base for a particular topic that I then load into an LLM for voice-mode interaction on a long 7-10 mile run. So it becomes an interactive podcast while I run."

知识库是母体，各种交互形态是投射。同一份知识可以投射为：
- 语音对话（跑步/通勤时，通过 Telegram 语音或 LLM Voice Mode）
- 交互式 HTML 仪表板（深度分析时）
- 幻灯片（分享/演讲时）
- 迷你知识库（聚焦学习时）

### 6.3 与 OpenClaw/Claude Code 的交互对比

| | OpenClaw / Claude Code | NoteWeaver |
|---|---|---|
| 输入 | 自然语言 → 转化为代码/命令 | 自然语言 → 转化为知识操作 |
| 输出 | 代码变更、终端输出 | 知识页面更新、结构变化、分析报告 |
| 反馈 | "执行了 `git commit`" | "更新了《量子计算》页面的第 3 节" |
| 设备 | 只有电脑（终端） | 电脑整理 + 手机记录 |
| 风险 | 可能执行危险命令 | 最多写错一篇笔记（可回滚） |

---

## 七、技术栈选型（逐项论证）

### 7.1 运行时：Bun

| 选项 | 优势 | 劣势 |
|------|------|------|
| **Bun ✓** | 冷启动 8-15ms（CLI 体感好）；内置打包、测试；Karpathy 推荐的 qmd 就是 Bun 生态；Claude Code 也用 Bun | 98% Node 兼容但非 100%；生态比 Node 年轻 |
| Node.js | 15 年生产验证；最大生态 | 冷启动 40-120ms；需要额外的打包/测试工具链 |

**结论**：Bun。2026 年 Bun 已经是 greenfield 项目的合理默认选择。CLI 场景对冷启动敏感（用户每次执行命令都能感知到），Bun 的 8-15ms 对比 Node 的 40-120ms 差距显著。qmd 也是 Bun 生态，未来集成更自然。

### 7.2 语言：TypeScript

| 选项 | 优势 | 劣势 |
|------|------|------|
| **TypeScript ✓** | LLM 生态最丰富（Vercel AI SDK、Mastra 等都是 TS-first）；开发速度快；团队招聘容易 | IO 密集型没问题但 CPU 密集型弱于 Rust |
| Rust | 极致性能；Tauri backend 天然适配 | 开发慢 2-3x；LLM 生态弱；对这个场景杀鸡用牛刀 |
| Python | LLM/ML 生态最强 | 不适合做 CLI/桌面应用；类型系统弱 |

**结论**：TypeScript。我们的核心瓶颈是 LLM API 调用（IO 密集），不是 CPU 计算。知识库规模在万级页面以下，TS 性能完全够用。关键是开发速度和 LLM 工具链生态——这两项 TS 都是最优的。

### 7.3 LLM 集成层：Vercel AI SDK（直接使用，不套框架）

| 选项 | 优势 | 劣势 |
|------|------|------|
| **Vercel AI SDK ✓** | 多模型统一接口（OpenAI/Anthropic/Google 等）；原生 tool calling + streaming；Zod schema 生成结构化输出 | 需要自己构建 Agent loop |
| Mastra | 基于 Vercel AI SDK；内置 Agent/Workflow/Memory/RAG | 太重——它的 Memory 系统、RAG 管线、Workflow 编排，都是我们刻意不要的。我们的"记忆"就是 Wiki 本身 |
| LangChain.js | 最大生态 | Python 移植味重；抽象层太厚；过度工程化 |
| 直接调 HTTP API | 最轻量 | 每换一个模型就要重写适配层 |

**结论**：Vercel AI SDK，但**不用** Mastra 封装。理由：

1. Mastra 的核心卖点（Memory、RAG、Workflow 编排）恰好是我们不需要的。我们的 Agent 记忆就是 Wiki 文件本身，我们的检索是 index.md 驱动而非 RAG，我们的操作是 9 种固定原语而非通用 Workflow。用 Mastra 相当于带着一堆不用的抽象，反而增加理解和调试成本。

2. Vercel AI SDK 的 `generateText()` / `streamText()` + tool calling 已经足够构建我们的 Agent loop。我们自己实现的 loop 大约 100-200 行代码，换来的是完全控制权。

Agent loop 伪代码：
```typescript
async function agentLoop(userMessage: string, vault: Vault) {
  const result = await streamText({
    model: selectedModel,
    system: buildSystemPrompt(vault.schema, vault.index),
    messages: conversationHistory,
    tools: knowledgeOperations, // 9 种操作，Zod schema 定义
    maxSteps: 20,
    onStepFinish: (step) => ui.showProgress(step),
  });
  // Vercel AI SDK 自动处理 tool calling loop：
  // model 选择 tool → 执行 → 结果返回 model → 继续推理
}
```

### 7.4 搜索引擎：分层策略

| 规模 | 方案 | 理由 |
|------|------|------|
| 小（<500 页） | **index.md + LLM 导航** | Karpathy 在 ~100 篇/~400K 词时就靠 index 文件。LLM 读 index 后定位相关页面再深入读取，zero infra |
| 中（500-5000 页） | **MiniSearch（内嵌）** | 轻量、零依赖、embeddable。657K 周下载量，API 简洁。在这个规模内性能足够 |
| 大（5000+ 页） | **qmd（外部集成）** | Karpathy 直接推荐。BM25 + 向量 + LLM reranking 三合一。MCP server 支持 Agent 直接调用。Bun 生态 |

**结论**：MVP 阶段只用 index.md + LLM 导航。这是 Karpathy 验证过的方案，在百级规模下足够好。当用户知识库增长后，先内嵌 MiniSearch（无外部依赖），再推荐集成 qmd（需要单独安装）。

不在 MVP 引入 FlexSearch 的原因：虽然它在 100K+ 文档下性能更好，但 API 更复杂，而且我们在那个规模下会直接推 qmd（更适合 Markdown + Agent 场景）。

### 7.5 Web 内容拉取：Readdown（而非 Readability + Turndown）

| 选项 | 优势 | 劣势 |
|------|------|------|
| Readability + Turndown | 久经验证；Obsidian Web Clipper 就用这个 | 两个包组合；未专门为 LLM 优化 |
| Defuddle | 更宽容的内容提取；更好的脚注/代码块支持 | 需要搭配 Turndown 做 Markdown 转换 |
| **Readdown ✓** | 单包搞定（提取+转MD）；**内置 token 估算**（为 LLM 设计）；2026.3 benchmark 胜出；更好的结构保留 | 最新（2026.3），经验证少 |

**结论**：Readdown。它是 2026 年 3 月发布的，专门为"网页 → Markdown → 喂给 LLM"这条链路设计。单包替代 Readability + Turndown 两包组合，内置 token 估算，benchmark 在 4/5 测试页面上胜出。虽然最新，但对我们的场景是最优匹配。

### 7.6 版本控制：simple-git → 后续考虑无依赖方案

| 选项 | 优势 | 劣势 |
|------|------|------|
| **simple-git ✓ (MVP)** | 封装好的 API；性能好（调用系统 git） | 要求用户安装 git |
| isomorphic-git | 纯 JS，无外部依赖 | 大仓库性能差；API 更底层 |
| 自建简易版本控制 | 零依赖；针对 MD 文件优化 | 工作量大；失去 git 生态互操作 |

**结论**：MVP 阶段用 simple-git（要求系统装 git，对开发者 CLI 阶段可接受）。桌面应用阶段需要重新评估：要么 bundle git binary，要么用 isomorphic-git，要么退化为"每次操作保存快照"的简易版本控制。

对 C 端用户来说，要求安装 git 是不可接受的。这是架构决策中需要留出的扩展点。

### 7.7 CLI 框架：Commander.js + Ink（双模式）

我们的 CLI 有两种交互模式，需要不同的工具：

| 模式 | 例子 | 工具 |
|------|------|------|
| 命令模式 | `noteweaver init`, `noteweaver ingest <url>`, `noteweaver lint` | **Commander.js** — 经典 CLI 命令解析 |
| 对话模式 | `noteweaver chat` → 进入交互式 REPL | **Ink**（React for CLI）— Claude Code 同款技术 |

**结论**：Commander.js 处理命令路由，Ink 渲染交互式对话界面。Ink 让我们可以做到：
- 实时进度展示（"正在更新 3 个页面..."）
- 彩色结构化输出
- 分栏显示（操作计划 | 执行进度）
- 未来升级到桌面 UI 时，核心逻辑不变，只换渲染层

不选 blessed/blessed-contrib 的原因：太底层，要自己管理状态。Ink 用 React 范式，和未来的 Web UI 共享心智模型。

### 7.8 桌面应用：Web-first → Tauri（非 MVP）

| 阶段 | 方案 | 理由 |
|------|------|------|
| MVP | **本地 Web 服务器 + 浏览器** | 零额外依赖；快速迭代；开发者用 CLI，普通用户访问 localhost |
| 产品阶段 | **Tauri** | 3-10MB 包体 vs Electron 80-150MB；20-80MB 内存 vs Electron 100-300MB；200ms 启动 vs 1-2s；更好的安全模型 |

**结论**：不在 MVP 做桌面应用。先做 CLI + 本地 Web UI。验证核心价值后再包 Tauri。

选 Tauri 而非 Electron 的理由：知识管理工具应该轻量、安全。3MB 的安装包 vs 120MB 对 C 端分发来说差距巨大。Tauri 的安全模型（粒度权限控制）也更符合我们"操作即白名单"的设计哲学。

Tauri 的 Rust backend 不会造成语言分裂——我们的核心逻辑全在 TypeScript（运行在 Bun sidecar 或内嵌 WebView 中），Rust 只做薄壳。

### 7.9 Web UI：React + TailwindCSS

这个选择比较直接，没有特别的争议：

- **React**：最大生态；Ink（CLI）和 Web 共享 React 心智模型；Tauri 原生支持
- **TailwindCSS**：快速迭代 UI；和 React 配合成熟
- 后续可考虑 **Shadcn/UI** 做组件库，避免从零构建

### 7.10 总览（最终选择：Python）

经过讨论，最终选择 Python 而非 TypeScript。核心原因：
- MVP 阶段验证核心价值最重要，Python 写 CLI + LLM 调用最快出活
- Karpathy/Fridman 的生态就是 Python
- Fine-tuning 方向只有 Python 能走
- C 端分发是后面的问题

```
┌──────────────────────────────────────────────────────────────┐
│                    技术栈总览                                  │
├──────────────┬───────────────────────────────────────────────┤
│ 语言          │ Python 3.11+                                 │
│ LLM 集成      │ openai SDK（直接 tool calling，不套框架）      │
│ Agent Loop   │ 自建（~120 行），基于 OpenAI tool calling      │
│ 知识库存储     │ Markdown 文件 + Git（gitpython）              │
│ 搜索（MVP）   │ index.md + LLM 导航 + 朴素全文搜索            │
│ 搜索（增长后） │ qmd（Karpathy 推荐） / SQLite FTS5           │
│ Web 拉取      │ readability-lxml + markdownify               │
│ CLI 交互      │ rich + prompt-toolkit                        │
│ Web UI（未来） │ 待定                                         │
│ 桌面（未来）   │ 待定                                         │
│ 打包          │ pyproject.toml + hatchling                   │
└──────────────┴───────────────────────────────────────────────┘
```

---

## 八、MVP 范围

第一个可用版本应该足够小但足够有说服力：

### MVP v1：电脑端核心（验证知识编译模式）

1. **知识库初始化**：创建 vault 目录结构
2. **对话式知识管理**：通过 CLI 对话创建、查找、更新笔记
3. **自动交叉引用**：Agent 在写入时自动维护链接
4. **Web 内容导入**：给一个 URL，Agent 拉取、清洗、整合到知识库
5. **知识库健康检查**：Karpathy 所说的 "linting"
6. **产出回填**：查询的输出可以 "filing back into the wiki"
7. **Git 版本控制**：所有变更自动 commit

### MVP v2：加手机入口（验证双设备范式）

8. **Telegram Bot 网关**：手机随时发消息给 Agent
9. **收件箱机制**：碎片输入先落到 `inbox.md`，攒批处理
10. **智能归类**：Agent 自动判断碎片笔记属于哪个主题
11. **轻量确认**：手机端极简反馈，不做复杂交互

为什么 Telegram 是 v2 而不是更后面：
- "随时记录"是知识管理的刚需，没有手机入口，用户在通勤/聊天/听播客时的想法就丢了
- Telegram Bot API 开发成本很低（比做 Web UI 低得多）
- 适配器层和 Agent 核心解耦，加 Telegram 不影响核心逻辑

### 路线图（后续）

- 交互式 HTML/JS 可视化输出（Fridman 模式）
- 迷你知识库投射 + 语音模式（Fridman 的跑步模式）
- Web UI（浏览知识库 + 对话）
- 桌面应用 (Tauri)
- 本地模型支持
- 更多平台入口（微信、Discord 等）
- 多人协作
- 端到端加密

---

## 九、产品关键决策

以下是必须正面面对的产品级矛盾和选择。这些比技术选型更根本——技术可以换，产品方向错了就是白做。

### 决策 1：我们到底是什么？——定位的根本问题

当前设计文档有一个未言明的张力：**我们同时想做三件不同的事**。

| 定位 | 描述 | 对标 | 用户 |
|------|------|------|------|
| A. Karpathy 的产品化 | 把"hacky collection of scripts"变成一个产品 | 无直接竞品 | 会用 LLM 的知识工作者 |
| B. 更好的 Obsidian | AI-native 的笔记管理工具 | Obsidian (400万用户, $25M ARR) | 广谱笔记用户 |
| C. 安全的消费级 Agent | 受限领域的安全 Agent，证明 Agent 不必须是 Shell-based | NotebookLM, 各种 AI 助手 | 普通人 |

这三个定位导向截然不同的 MVP：

**如果是 A**：MVP 就是 CLI，目标用户是已经在用 Claude Code/Codex 的人，但嫌自己搭 wiki 工作流太麻烦。Karpathy 原文的最后一句话就是在呼唤这个。切入快，市场小但精准。

**如果是 B**：MVP 必须有 GUI，必须比 Obsidian 在某个维度上好 10 倍——大概率是"AI 帮你自动整理"这个点。市场大，但 Obsidian 是 400 万用户的成熟产品，正面竞争极难。

**如果是 C**：核心创新不在知识管理本身，而在"受限 Agent"这个范式。知识管理只是第一个场景。这是最有想象力的方向，但也最不具体。

**建议**：**先做 A，证明 A 后自然长成 C，B 不是我们该追的方向。**

理由：
- Karpathy/Fridman 验证了需求存在，而且他们自己就是用户
- 从 A 起步，CLI 阶段可以快速验证"知识编译"模式的产品化是否成立
- 如果 A 成功了，底层的"9 种操作原语"框架就被验证了，自然可以延伸到 C
- B 是一个 UI 密集的产品竞争，在 Agent 核心价值被验证之前不应该投入

### 决策 2：LLM 成本——大象屋里的房间

一次 ingest（导入一篇文章到知识库）按 Karpathy 的描述，可能触及 10-15 个 wiki 页面。粗略估算：

```
一次 ingest 操作的 LLM 成本估算:

1. 读取原文:               ~3000 tokens input
2. 讨论要点 + 生成摘要:      ~2000 tokens output
3. 读 index.md:            ~2000 tokens input
4. 读取 5 个相关页面:        ~5000 tokens input
5. 更新 5 个页面 + 创建摘要:  ~3000 tokens output
6. 更新 index + log:        ~1000 tokens output

总计: ~10K input + ~6K output

用 Claude Sonnet:  ~$0.03 + ~$0.09 = ~$0.12/次 ingest
用 GPT-4o:         ~$0.025 + ~$0.06 = ~$0.085/次 ingest
用 GPT-4o-mini:    ~$0.0015 + ~$0.0036 = ~$0.005/次 ingest
```

如果用户每天导入 5 篇文章 + 10 次查询 + 1 次 lint：

| 模型 | 日成本 | 月成本 |
|------|--------|--------|
| Claude Sonnet | ~$2.5 | ~$75 |
| GPT-4o | ~$1.7 | ~$50 |
| GPT-4o-mini | ~$0.1 | ~$3 |

**这是一个尖锐的产品问题。** 对标 Obsidian Sync 每月 $5，如果我们用 Sonnet 级别模型，一个中度活跃用户每月 LLM 成本就 $50-75。

**可选策略**：

| 策略 | 方案 | 权衡 |
|------|------|------|
| **分层模型** | 轻活（更新 index、链接维护）用 mini 模型，重活（综合分析、写作）用强模型 | 质量可能不一致 |
| **批处理** | 累积多个变更，合并为一次 LLM 调用处理 | 失去实时反馈感 |
| **本地模型** | Ollama 跑轻量任务（分类、标签、简单搜索） | 增加部署复杂度；模型质量有上限 |
| **用户自带 API Key** | 让用户用自己的 key，我们不承担 LLM 成本 | 对 C 端用户门槛高 |
| **订阅制包含额度** | 月费包含一定量 LLM 调用，超出按量 | 定价模型复杂 |

**建议**：MVP 阶段用"用户自带 API Key"（和 Cursor/Claude Code 一样），验证产品价值。分层模型策略从第一天就设计进架构（轻任务走 mini，重任务走强模型）。C 端产品阶段再考虑订阅制。

### 决策 3："所有数据都是 Markdown"——真的吗？

当前设计说"所有数据就是本地 Markdown 文件"。这个承诺有几个层面的张力：

**优势确实很大**：
- 数据主权（用户可以用任何编辑器打开）
- 与 Obsidian 共存（用户可以同时用 Obsidian 浏览同一个 vault）
- Git 友好
- LLM 友好

**但现实中的摩擦**：

1. **元数据存在哪里？** 交叉引用图谱、搜索索引、操作日志——如果只用 Markdown 文件，LLM 每次操作都要解析大量文本来重建状态。Karpathy 在小规模下说"够了"，但这不 scale。

2. **并发编辑**：如果用户在 Obsidian 里改了一个文件，我们的 Agent 同时也在改——怎么处理？Markdown 文件不支持锁。

3. **结构化数据表达力**：Fridman 要"sort/filter data"的交互式可视化——Markdown 表达结构化数据很费劲。YAML frontmatter 是一种折中，但本质上是在纯文本里硬塞结构化数据。

**建议**：坚持 "Markdown 为用户可见的主格式"，但允许 `.meta/` 目录存放衍生的结构化数据（SQLite 索引、图谱缓存等）。对用户来说，他们的知识就是 `.md` 文件；`.meta/` 是可删除可重建的加速缓存。

```
vault/
├── sources/          ← Markdown (用户可见，不可变)
├── wiki/             ← Markdown (用户可见，Agent 维护)
├── .schema/          ← Markdown (用户可见，协同编辑)
└── .meta/            ← 衍生数据 (用户不用关心，可重建)
    ├── graph.db      ← 知识图谱 (SQLite)
    ├── search.idx    ← 搜索索引
    └── state.json    ← Agent 状态
```

### 决策 4：与 Obsidian 的关系——共存还是替代？

这个问题影响到产品的核心定位。

**方案 A：Obsidian 插件**
- 最快触达 Obsidian 的 400 万用户
- 不需要自建 UI
- 但受限于 Obsidian 的插件 API，被平台捏住
- Obsidian 自己很可能在做 AI 功能

**方案 B：独立产品，兼容 Obsidian vault 格式**
- 用户可以同时用 Obsidian 浏览同一个 vault（因为就是 Markdown 文件夹）
- 我们做 Agent，Obsidian 做 IDE——正如 Karpathy 所说"Obsidian is the IDE; the LLM is the programmer"
- 不受 Obsidian API 限制
- 但用户需要同时管理两个工具

**方案 C：完全独立，自建 UI**
- 最大自由度
- 但要和 Obsidian 在 UI 层竞争——这不是我们的核心优势

**建议**：**方案 B**——独立产品，但刻意兼容 Obsidian vault 格式。

理由：
- 我们的核心价值是 Agent（"the programmer"），不是 IDE。用户可以用 Obsidian（或 VS Code, Typora, 任何 MD 编辑器）做 IDE。
- 这让我们聚焦在最有差异化的部分——Agent 智能——而不是 UI 的无底洞
- 初期用户很可能已经在用 Obsidian。"用 NoteWeaver 管理你的 Obsidian vault"是一个强有力的 pitch
- 长期不排除自建 UI，但 UI 不是 MVP 的胜负手

兼容意味着：使用 `[[wiki-link]]` 语法、支持 YAML frontmatter、尊重 `.obsidian/` 目录（不动它）、支持标准的 Markdown 文件夹结构。

### 决策 5：Agent 主动性的光谱

这是一个需要仔细调参的设计：

```
完全被动 ◄─────────────────────────────────────► 完全自主
   │                                                │
   │  "用户不说，什么都不做"                         │  "Agent 自己决定一切"
   │                                                │
   │  问题：和 ChatGPT 没区别                        │  问题：用户失去控制感
   │                                                │
   ▼                                                ▼
Siri                                            OpenClaw
NotebookLM                                      自动驾驶
```

我们需要找到一个具体的位置。我建议把 Agent 行为分为三个层级：

| 层级 | 行为 | 何时执行 | 需要确认？ |
|------|------|----------|-----------|
| **被动响应** | 回答查询、执行明确指令 | 用户主动要求时 | 不需要 |
| **主动建议** | 发现矛盾、建议补充、推荐关联 | Agent 在操作过程中发现时 | 展示建议，用户选择是否采纳 |
| **后台维护** | 更新交叉引用、维护 index、清理格式 | 每次操作后自动执行 | 不需要（但记录在 log 中） |

**关键原则**：Agent 永远不会在用户不知情的情况下修改知识的内容。它可以静默维护**结构**（链接、索引、格式），但**内容变更**必须透明。

具体例子：
- ✅ 自动做：用户创建新页面后，自动更新 index.md、添加反向链接——这是结构维护
- ✅ 建议做：ingest 时发现与已有页面矛盾——展示矛盾，让用户决定如何处理
- ❌ 不自动做：自行从网上拉取信息补充知识库——这是内容变更，必须用户触发或确认
- ❌ 不自动做：重组文档结构（合并、拆分页面）——这是重大结构变更，需要用户确认

### 决策 6：MVP 的成功标准是什么？

当前 MVP 列表是功能列表，但缺少一个回答："怎么知道 MVP 成功了？"

**建议的成功标准**：

> 给 Agent 10 篇相关主题的文章（如 "LLM Agent 安全性"），让它构建一个知识库。然后问它一个需要综合 3 篇以上文章才能回答的问题。如果 Agent 能基于它自己编译的 wiki 给出一个有结构、有引用、有洞察的答案——而且这个答案比直接把 10 篇文章扔给 ChatGPT 的回答质量更好——那 MVP 就成功了。

这个标准直接检验了核心假设：**编译型知识库 > 实时 RAG**。

更具体的可量化指标：
1. **编译质量**：10 篇文章 ingest 后，wiki 中产生了多少有意义的交叉引用和概念页面？
2. **回答质量**：综合性问题的回答，是否引用了正确的来源？是否发现了跨文章的关联？
3. **复合效应**：第 10 篇文章 ingest 时的 wiki 更新，是否比第 1 篇时触及了更多已有页面？（证明知识在"复合"）
4. **回答对比**：同一个问题，wiki 模式 vs 直接 RAG（把原文全扔给 LLM），哪个回答更好？

---

## 十、知识对象模型

### 已采纳（基于外部设计评审反馈）

经过评审，我们引入了**按知识职责区分**（而非仅按内容类别区分）的对象模型。每个 wiki 页面通过 frontmatter 的 `type` 字段标记其角色：

| 类型 | 职责 | 核心规则 |
|------|------|----------|
| `hub` | 导航入口——组织和指向相关页面 | 保持简洁，链接为主，不做深度论证 |
| `canonical` | 结论文档——某主题当前最成熟的表述 | **必须有 sources 字段**（系统强制），同一主题不应有多个 canonical |
| `journal` | 时间流记录——快速捕获，日志 | 保留原始表达，不过度编辑 |
| `synthesis` | 综合分析——跨文章比较，源文档摘要 | 始终引用来源 |
| `note` | 工作中间态——尚未成熟的内容 | 可自由修改、合并、提升为 canonical |
| `archive` | 退场——被替代或过时的页面 | 由 archive_page 工具创建，保留不删除 |

### Hub vs Canonical 的区分

这是模型中最重要的区分。Hub 说"关于 X，去读这些页面"，Canonical 说"这是 X 的权威解释"。
如果一个页面既在做导航又在做深度论证，说明它需要拆分。

### 硬约束下沉到代码层

以下规则不只存在于 system prompt 中，而是在 `write_page` 执行时由 `frontmatter.py` 强制校验：
- 所有 wiki 页面必须有包含 `title` 和 `type` 的合法 frontmatter
- Canonical 页面必须有非空的 `sources` 字段
- `type` 必须是已定义的合法类型之一
- 系统文件（index.md, log.md）豁免校验

### Archive 替代删除

知识库中永远不应该物理删除页面。`archive_page` 工具将页面移动到 `wiki/archive/`，更新其 frontmatter type 为 `archive`，并记录归档原因和日期。

### 关于 Skill 层的取舍

评审建议将低层工具（read_page, write_page）替换为高层语义技能（CaptureToJournal, PromoteToCanonical）。我们选择**不采纳这个建议**，理由：

1. LLM 擅长理解"用 write_page 写一个 journal 条目"——智能在模型里，不需要硬编码到工具名里
2. Claude Code/Codex 也用低层工具（read_file, write_file），效果很好
3. 原子工具的组合性更强。`CaptureToJournal` 固化了 capture 的含义，减少了灵活性
4. 我们通过**在低层工具中嵌入硬约束**（frontmatter 校验）来获得 Skill 层的安全性，同时保留灵活性

### 关于 plan → apply → evaluate → commit 流程

评审建议所有重要修改后必须经过 evaluate 阶段。我们部分采纳——**分级执行**：

| 操作类型 | 评估级别 |
|---|---|
| Journal 条目、快速捕获 | 无（直接写入） |
| 更新已有页面 | 轻量（frontmatter 校验，系统自动） |
| 新建/修改 Hub 或 Canonical | 完整评估（由系统 prompt 引导 Agent 自查） |
| 结构重组（归档、合并） | 完整评估 + 用户确认 |

---

## 十一、开放问题（剩余）

1. **RENDER 的安全边界**：Fridman 的 "generate dynamic html (with js)" 很有吸引力，但生成的 JS 代码如何安全执行？需要 iframe sandbox 或类似机制。

2. **语音模式的实现路径**：Fridman 的跑步模式是杀手级场景。是集成现有 TTS/STT API，还是依赖 LLM 供应商的原生语音模式（如 ChatGPT 的 Advanced Voice）？

3. **Karpathy 提到的 "synthetic data generation + finetuning"**：让 LLM 把知识"知道"在权重里而非上下文窗口里。这是长期方向，但对成本和离线使用有巨大影响。

4. **命名**：暂用 NoteWeaver，但需要一个更好的名字。

---

## 十一、下一步

1. **确认产品定位**：A（Karpathy 的产品化）→ 决定了 MVP 形态
2. 搭建项目骨架
3. 实现 vault 初始化 + 基本 CRUD 操作
4. 实现 Agent loop（LLM + 9 种知识操作）
5. 实现 Web ingest
6. **执行成功标准测试**：10 篇文章 → 编译 wiki → 综合查询 → 对比 RAG
7. 迭代
