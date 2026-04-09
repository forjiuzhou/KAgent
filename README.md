# NoteWeaver

> A knowledge harness for LLMs. 不是 chatbot，不是笔记应用——而是让 LLM 能够持续构建、维护和使用结构化知识库的运行时。

## 理念

ChatGPT 们只在 prompt 上下功夫：`用户 → prompt → 模型 → 回复 → 消失`。没有持久状态，没有知识积累。

Claude Code 强大不是因为模型强，而是因为 **harness 强**——文件系统作为外部状态，tool calling 循环，权限管线，上下文管理。模型是引擎，harness 是整辆车。

NoteWeaver 是知识工作领域的 harness。Claude Code 把代码世界变成了 agent 可维护的外部状态，我们把知识世界做同样的事。

- **知识编译器**，不是知识检索器：持续构建结构化 Wiki，而非每次查询临时拼凑
- **领域专用操作**，不是 Shell：10 种知识操作工具构成全部能力边界，安全由设计保证
- **本地优先**：所有数据都是本地 Markdown 文件 + Git，数据主权完全在你手中
- **电脑整理 + 手机记录**：电脑上深度整理，Telegram 上随手记录，Agent 打通两端
- **零配置**：打开就能用，不需要理解配置文件或权限模型

## 快速开始

```bash
# 基础安装（OpenAI provider）
pip install -e .

# 如需使用 Anthropic / Claude，安装带可选依赖
pip install -e ".[anthropic]"

nw init                      # 创建知识库（自动初始化 Git）
```

### 配置 LLM Provider

```bash
# OpenAI（默认）
export OPENAI_API_KEY=sk-...
nw chat

# Anthropic
export ANTHROPIC_API_KEY=sk-ant-...
nw chat

# 本地 Anthropic 代理（如 claude-proxy）
export ANTHROPIC_AUTH_TOKEN=your-token
export ANTHROPIC_BASE_URL=http://127.0.0.1:8082
nw chat
```

Provider 自动检测：设置了 `ANTHROPIC_API_KEY` 或 `ANTHROPIC_AUTH_TOKEN` 即自动切换到 Anthropic。也可用 `NW_PROVIDER=anthropic` 显式指定。

### 所有命令

```bash
nw chat                      # 交互式对话（会话自动沉淀到 journal）
nw ingest <url>              # 导入网页文章
nw import <path>             # 导入已有 Markdown 文件
nw lint                      # 知识库健康检查
nw rebuild-index             # 从文件元数据重建索引
nw status                    # 查看知识库状态 + 量化健康指标
nw help                      # 查看所有命令和环境变量
```

## 核心设计原则

**文档是主体，模型是维护者和执行者。** 知识库（结构化 Markdown 文件）是持久的一等资产；LLM 是可替换的工具。操作手册住在知识库自身（`.schema/schema.md`），不在代码里——你可以拿着 vault 去任何 LLM 工具使用。

**三级渐进式披露**控制 token：scan（~30/页）→ shallow（~150/页）→ deep（~2000/页）。

**三种导航**：树（index → Hub → Page）、标签（frontmatter tags）、链接（[[wiki-links]]）。

## 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.11+ | Karpathy/Fridman 生态；LLM/ML 最强；验证最快 |
| LLM 集成 | openai + anthropic SDK | Provider 抽象，直接 tool calling，不套框架 |
| 搜索 | index.md + 朴素全文搜索 → qmd | 小规模零 infra，大规模接入 Karpathy 推荐的 qmd |
| Web 拉取 | readability-lxml + markdownify | 网页 → 清洗 → Markdown |
| CLI | rich + prompt-toolkit | 富文本输出 + 交互式输入 |
| 版本控制 | gitpython | 自动 commit 所有变更 |

## 灵感来源

- [Karpathy 的 LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — 持续积累的知识编译模式
- [Lex Fridman 对 Agent 安全性的思考](https://x.com/lexfridman/status/2039841897066414291) — 安全是 Agent 大规模采用的关键瓶颈
- Claude Code 的权限管线 — 简化为领域专用的操作白名单
- OpenClaw 的安全模型 — 层次化安全的思想，但大幅简化

## 状态

🚧 早期设计阶段。详见 [DESIGN.md](./DESIGN.md) 了解完整的设计讨论。

## License

MIT
