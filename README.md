# NoteWeaver

> 一个安全、自主的个人知识管理 Agent。以 Markdown 为核心，让 AI 帮你构建和维护结构化的知识网络。

## 理念

在 "哑巴 RAG 聊天" 和 "拥有 Shell 权限的全自主 Agent" 之间，存在一个**安全、专用、面向消费者**的甜点。

NoteWeaver 不是通用 Agent——它只做一件事：**帮你管理知识**。

- **知识编译器**，不是知识检索器：持续构建结构化 Wiki，而非每次查询临时拼凑
- **领域专用操作**，不是 Shell：8 种知识操作原语构成全部能力边界，安全由设计保证
- **本地优先**：所有数据都是本地 Markdown 文件 + Git，数据主权完全在你手中
- **零配置**：打开就能用，不需要理解配置文件或权限模型

## 知识操作原语

Agent 只能执行这 9 种操作，不多不少：

| 操作 | 说明 |
|------|------|
| `READ` | 读取笔记/文档 |
| `WRITE` | 创建或更新笔记 |
| `LINK` | 建立/管理文档间关联 |
| `SEARCH` | 在知识库中搜索 |
| `INGEST` | 从外部源导入内容 |
| `ORGANIZE` | 重组织结构（移动、合并、拆分） |
| `FETCH` | 从互联网拉取信息 |
| `ANALYZE` | 分析知识库状态 |
| `RENDER` | 生成多模态输出（交互式 HTML/JS、幻灯片、图表、迷你知识库） |

## 知识库结构

```
vault/
├── sources/        ← 原始素材（不可变）
├── wiki/           ← Agent 维护的结构化知识
│   ├── index.md    ← 知识索引
│   ├── log.md      ← 操作日志
│   ├── entities/   ← 实体页面
│   ├── concepts/   ← 概念页面
│   ├── journals/   ← 日记/日志
│   └── synthesis/  ← 综合分析
├── .schema/        ← 知识库的"宪法"
└── .meta/          ← 元数据
```

## 灵感来源

- [Karpathy 的 LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) — 持续积累的知识编译模式
- [Lex Fridman 对 Agent 安全性的思考](https://x.com/lexfridman/status/2039841897066414291) — 安全是 Agent 大规模采用的关键瓶颈
- Claude Code 的权限管线 — 简化为领域专用的操作白名单
- OpenClaw 的安全模型 — 层次化安全的思想，但大幅简化

## 状态

🚧 早期设计阶段。详见 [DESIGN.md](./DESIGN.md) 了解完整的设计讨论。

## License

MIT
