# Harness Refactor Plan

基于我们的讨论，这份计划解决 NoteWeaver 当前架构的根本问题：

> **prompt 把 wiki 定位成了"工具的操作对象"，而不是"agent 的认知基础"。
> 所有后续的 plan 模式、高级语义工具、自动修复，都是在弥补这个定位错误。**

改动分为四个阶段，每个阶段有独立价值，可以单独合并。

---

## 诊断

当前架构存在一个"补丁叠补丁"的结构：

```
agent 对 wiki 没有认识
  → 补丁 1: survey_topic（把 search + list + backlinks 打包成一个"规划工作流"）
  → 补丁 2: plan 模式（不让 chat agent 直接写，用自然语言计划间接控制）
  → 补丁 3: capture/organize/restructure（把 workflow 逻辑塞进写工具）
  → 补丁 4: _ensure_progressive_disclosure（写完后自动修复连通性）
```

每一层补丁都是因为上一层没解决根本问题。根本问题有三个：

1. **Prompt 的世界观错了**。wiki 被描述为"需要时查一下的仓库"，不是"你对世界的理解"。
   agent 完全可以用参数知识回答问题，不查 wiki。

2. **Operating knowledge 对 agent 不可见**。命名规范、目录路由、frontmatter schema、
   连通性规则——散落在 Python 代码里，不在 agent 的 context 中。
   `.schema/schema.md` 有很好的内容，但不注入 system prompt，也没人提醒 agent 去读。

3. **执行阶段的 agent 几乎是盲飞**。只有 plan summary + vault 概览，
   没有对话历史，没有 schema，没有它之前读过的页面内容。

---

## 阶段 1: 重写 System Prompt — 重新定义 agent 与 wiki 的关系

**目标**: 让 agent 在所有阶段都把 wiki 当作"自己的世界"，而不是"可选参考"。

### 1.1 重写 PROMPT_IDENTITY

当前 prompt 的问题：

```
### 1. Conversation (default)
Respond naturally — discuss, reason, debate, suggest. Draw on the
knowledge base when relevant (search or read pages).
```

"when relevant" 让 agent 认为 wiki 是可选的。应该改为：

```
### Your World

The wiki is your primary knowledge about the user's domain. When discussing
any topic, FIRST check whether the wiki already has relevant pages. If it does,
base your response on wiki content and cite pages with [[wiki-links]].
Your own parametric knowledge fills gaps — the wiki is the source of truth
for this user's knowledge base.

You see a vault map in every conversation (under "Current Vault Contents").
This is an overview — use read tools to go deeper before acting.
```

核心变化：
- wiki 从"可选参考"变成"首要知识源"
- agent 应该默认先查 wiki 再回答
- vault map 是地图，不是全部——强调要深入

### 1.2 重写 Knowledge Capture 部分

当前的 Knowledge Capture 模式直接跳到 "survey → plan → submit_plan" 工作流。
应该改为强调认知前提：

```
### Writing to the Wiki

Before any write operation, you must understand the local context:
1. What pages already exist in this area? (search, list_pages)
2. What do those pages contain? (read_page)
3. Where does new content fit in the existing structure?
4. What connections need to be maintained?

Do not write based on vault map alone — read the actual pages.
```

不再硬编码 survey_topic → submit_plan 的流程。agent 用什么工具去了解
wiki 应该是它自己的判断，不是 prompt 规定的工作流。

### 1.3 将核心 schema 知识注入 system prompt

当前 `.schema/schema.md` 不在 system prompt 中（注释说"省 ~3000 tokens"）。
但这 3000 tokens 包含了 agent 正确操作的关键知识：

- 目录结构约定（哪种 type 去哪个目录）
- frontmatter 完整 schema（哪些字段 required）
- 文件命名规范（lowercase-hyphenated）
- 连通性规则（每个页面必须可达）
- 写作风格（inverted pyramid、## Related）

方案：提取 schema.md 中最关键的操作性规则（约 800-1000 tokens），
作为 `## Operating Rules` 注入 system prompt。完整 schema 仍然按需读取。

关键内容：
- 目录布局 + type → directory 映射
- frontmatter required fields（带 canonical 的 sources 要求）
- 文件命名：lowercase-hyphenated，中文标题用拼音或语义英文
- 连通性：新页面必须链接到 hub 或被 hub 链接
- 写作：inverted pyramid，## Related 结尾

### 1.4 重写 EXECUTE_PLAN_PROMPT

当前执行 prompt 只有 6 条规则 + plan 描述。应该加入：
- 与 chat prompt 相同的 Operating Rules
- vault map（已有，但需要强调其含义）
- 明确指令：在 write_page 之前用 read_page 了解目标页面当前内容

**涉及文件**: `agent.py`（PROMPT_IDENTITY, PROMPT_TOOLS, EXECUTE_PLAN_PROMPT,
_build_system_prompt）

**测试影响**: `test_prompt_engine.py` 需要更新断言
（检查 prompt 包含的关键词和结构）

---

## 阶段 2: 工具拆解 — 从 workflow 工具变成原语工具

**目标**: 工具只描述"how to act"，不嵌入 workflow、intent classification、
或自动决策逻辑。

**前提**: 阶段 1 完成后，agent 有足够的 context 来自己组合原语。

### 2.1 写操作工具拆解

| 当前工具 | 问题 | 拆解为 |
|----------|------|--------|
| `capture(content, title, tags, target, type)` | 内含 slug 生成、目录路由、frontmatter 拼装、section 插入 | `write_page` + `append_section` |
| `organize(target, action=classify\|update_metadata\|archive\|link)` | 4 个不相关操作伪装成 1 个工具 | `update_frontmatter` + `add_link` + `move_page` |
| `restructure(scope, action=merge_tags\|deduplicate\|rebuild_hubs\|audit)` | 4 个不相关操作 | `merge_tags` + `audit`（deduplicate/rebuild_hubs 降级为 CLI 命令） |
| `ingest(source, source_type=url\|file\|directory)` | 3 种不同数据流 | `save_source`（file/URL → sources/），目录导入留在 CLI |

新的写工具集：

```
write_page(path, content)       — 创建或覆盖完整页面（已有，保留）
append_section(path, heading, content) — 往已有页面追加 section
update_frontmatter(path, fields)       — 更新 frontmatter 字段
add_link(source_path, target_title)    — 添加 [[wiki-link]] 到 Related
move_page(from_path, to_path)          — 移动/重命名/归档
save_source(path, content)             — 写入 sources/（create-only）
merge_tags(old_tag, new_tag)           — vault 范围 tag 重命名
audit()                                — 运行 vault 健康检查
```

每个工具做一件事，参数清晰无歧义，没有 action 路由。

### 2.2 读操作工具调整

| 工具 | 处理 |
|------|------|
| `read_page` | 保留，无变化 |
| `search` | 保留，无变化 |
| `list_pages` | 保留，无变化 |
| `get_backlinks` | 保留，无变化 |
| `fetch_url` | 保留，无变化 |
| `survey_topic` | **去掉** — 它做的事 agent 可以通过 search + list_pages + get_backlinks 自己组合 |

### 2.3 submit_plan 变化

`submit_plan` 保留，但简化：
- 去掉 `intent` 参数（这是 intent classification，不该在工具层）
- 去掉 `change_type` 参数（由 policy 层根据 targets 自动判断）
- 保留 `summary`、`targets`、`rationale`、`open_questions`

agent 只需要说"我想做什么、涉及哪些页面、为什么"。
系统自动判断 incremental vs structural。

**涉及文件**: `tools/definitions.py`（schema + handlers）,
`tools/policy.py`（TOOL_TIERS 更新）, `agent.py`（_ensure_progressive_disclosure
中对 capture/organize 的引用需要更新）

**测试影响**: `test_tools.py` 需要大幅重写,
`test_attended_policy.py` / `test_policy.py` 需要更新工具名,
`test_fine_grained_tools.py` 中的 append_section 等测试可以复用

---

## 阶段 3: Plan 模式从"强制流程"变为"可选保障"

**目标**: plan 模式不再是所有写操作的前置要求，而是对大型/结构性变更的
可选安全机制。

### 3.1 chat 阶段允许直接写（小范围变更）

当前：chat 阶段只有 CHAT_TOOL_SCHEMAS（读 + submit_plan），
所有写必须通过 plan → execute 间接完成。

改为：chat 阶段可以使用部分低风险写工具：
- `append_section`（追加到已有页面）
- `update_frontmatter`（更新元数据）
- `add_link`（添加链接）

这些操作是增量性的、可逆的、不改变 wiki 结构的。
它们仍然受 policy.py 的 read-before-write 规则保护。

高风险操作仍然需要 plan：
- `write_page`（创建新页面或覆盖）
- `move_page`（移动/归档）
- `merge_tags`（vault 范围变更）

### 3.2 简化 execute_plan

执行阶段的核心问题是 context 不足。改进：

1. 在 plan 对象中保存 chat 阶段 agent 已读取的页面内容摘要
  （不是全文——太大；摘要足以让执行 agent 理解上下文）
2. 将 Operating Rules 注入执行 prompt（阶段 1 的成果）
3. 可选：如果 plan 是 incremental 且只涉及 1-2 个页面，
   跳过独立的 execute_plan LLM 调用，直接在 chat loop 中执行

### 3.3 _ensure_progressive_disclosure 的定位变化

从"写完后的自动修复"变为"写完后的验证 + 提醒"：
- 仍然检查新页面是否可达
- 不再自动创建 hub / 自动添加链接
- 如果发现孤儿页面，生成一条警告返回给 agent，让 agent 自己决定怎么修复

这样 agent 有机会学习连通性规则，而不是永远依赖自动修复。

**涉及文件**: `agent.py`（chat loop, execute_plan,
_ensure_progressive_disclosure）, `tools/definitions.py`
（CHAT_TOOL_SCHEMAS 扩展）

**测试影响**: `test_integration.py` 需要更新 mock 的 tool_calls 行为

---

## 阶段 4: 清理和一致性

### 4.1 统一 .schema/schema.md 与 prompt

阶段 1 将核心规则注入了 prompt。同步更新 schema.md：
- 去掉 schema.md 中引用旧工具名的 Workflows 部分
   （当前引用了 `save_source`、`list_page_summaries`、`archive_page`
   等已不存在的工具名）
- 确保 schema.md 和 prompt 中的 Operating Rules 保持一致
- schema.md 保留完整细节；prompt 中是压缩版

### 4.2 Policy 层更新

- TOOL_TIERS 对齐新工具名
- classify_change_type 简化（不再需要 intent 参数）
- read-before-write 规则适用于所有写工具（不只是 write_page 和 organize）
- 去掉对 organize(action=...) 的特殊处理

### 4.3 清理遗留代码

- 去掉 `_execute_legacy_plan` 和 `pending-organize.json` 支持
- 去掉 `_save_pending_plan` / `_load_pending_plan` / `_clear_pending_plan`
- 去掉 `format_organize_plan` 对 `list[dict]` 的处理
- 去掉 `handle_capture` 中的 slug 生成逻辑（现在 agent 自己决定路径）
- 去掉 `ORGANIZE_SESSION_PROMPT`（generate_organize_plan 流程简化）

### 4.4 AGENTS.md / CLAUDE.md 更新

反映新的工具集和架构。

---

## 执行顺序和依赖关系

```
阶段 1 (Prompt)
  ↓
阶段 2 (工具拆解)  ←  依赖阶段 1（agent 需要 context 来使用原语）
  ↓
阶段 3 (Plan 模式简化)  ←  依赖阶段 2（需要知道哪些工具低风险）
  ↓
阶段 4 (清理)  ←  依赖前三个阶段
```

每个阶段结束后运行完整测试套件。阶段之间可以有独立的 PR。

---

## 风险和缓解

### 阶段 1 风险低
只改 prompt 文本和 schema 注入。不改工具、不改逻辑。
如果 prompt 改坏了，agent 行为会变差但不会 break。
通过 `test_prompt_engine.py` + 手动 chat 测试验证。

### 阶段 2 风险中等
工具 schema 变化意味着所有 handler 重写。
现有测试需要大幅更新。
缓解：保持 `dispatch_tool()` 接口不变，逐个迁移 handler。
新旧工具可以短暂共存（在同一个 TOOL_SCHEMAS 中）。

### 阶段 3 风险中等
改变了 chat 阶段的工具可用性——agent 可能会在不该写的时候写。
缓解：policy.py 的 read-before-write 规则是硬保障。
先在 attended 模式下试行，unattended 保持只读。

### 阶段 4 风险低
纯清理，去掉已废弃的代码路径。通过测试覆盖验证。

---

## 不做什么

- **不做 Skill/Protocol substrate**。目前没有足够的使用 pattern 来证明需要
  独立 substrate。如果需要，先作为 wiki 的特殊页面类型存在。
- **不做 Memory 统一**。`.schema/memory.md`、`preferences.md`、
  `session-memory.md` 的整合是有价值的，但不在这次范围内。
- **不做自动 promotion 机制**。journal → wiki 的 promotion 继续由
  digest/用户手动触发，不引入自动 promotion。
- **不改 Vault 底层**。`vault.py` 的 write_file / save_source / git
  batching 保持不变。
