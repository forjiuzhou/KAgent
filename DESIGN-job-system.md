# Job System — 后台任务 Loop 执行模型

> **状态**：设计草案 v2（2026-04-15）
>
> 本文档记录 NoteWeaver Job 系统的设计。v2 基于与 Ralph Wiggum technique
> 的对比讨论大幅简化：砍掉 Generator/Evaluator 分离、专用工具、结构化数据模型，
> 回归"合同文件 → loop → 干净 agent"的极简架构。

---

## 一、问题

现有系统只有两种半成品执行能力：

1. **`chat()` 的工具循环** — 单次会话内最多 `AGENT_MAX_STEPS=25` 步的推理执行，
   不是"围绕验收标准反复推进直到完成"。用户离开后一切停止。

2. **Gateway cron** — `set_attended(False)` 后只能写 journal/promote candidates，
   `policy.py` 里 `unattended + CONTENT` 直接返回 `allowed=False`。

这意味着以下场景无法闭环：

- 批量导入 20 篇论文（sources → wiki 概念页 → hub → 交叉链接）
- 大规模整理杂乱 vault（frontmatter 补全、分类调整、孤儿页处理）
- 针对某个领域的 deep research（fetch → 综合 → 产出 synthesis 页）

这些任务的共同特点：需要多轮迭代、需要质量判断、用户不在也要能继续、
需要一个"完成"的定义。

---

## 二、设计哲学

### 2.1 Ralph Wiggum Technique 的启发

Ralph 的核心是一个 bash while loop：

```bash
while :; do cat PROMPT.md | claude-code; done
```

关键洞察：
- **每轮全新 context** — 避免上下文污染，天然去偏
- **合同（specs）和进度（fix_plan.md）分离** — 合同不变，进度由 agent 自己维护
- **Backpressure 来自外部** — 编译器、测试套件提供确定性反馈，不是 agent 自评
- **信任 eventual consistency** — 出了问题跑更多 loop，或者 git reset

### 2.2 我们的场景差异

知识库没有编译器。"摘要写得好不好"没有 `cargo build` 能告诉你。
但我们有 `audit_vault()` — 能程序化检查 orphan pages、broken links、
missing frontmatter、missing summaries 等结构性问题。这是我们的"编译器"。

另一个差异：Ralph 用在 greenfield 项目，"出了问题 git reset"可以接受。
我们的 vault 里有用户的内容。但合同是用户显式确认过的，
最坏情况也只是 git 回撤，这是用户应当承受的风险。

### 2.3 我们的选择

| 维度 | Ralph | NoteWeaver Job |
|------|-------|----------------|
| Loop 结构 | bash while loop | Gateway cron 轮询 |
| 合同 | PROMPT.md（静态） | `.meta/jobs/xxx/contract.md`（Markdown，静态） |
| 进度 | fix_plan.md（agent 自己写） | `.meta/jobs/xxx/progress.md`（worker 自己写） |
| Backpressure | 编译器 + 测试 | `audit_vault()` + worker 自评 |
| 质量评审 | 无（靠编译器） | 不单独设 evaluator，worker 在干净 context 下自审 |
| Write scope | 无限制 | 无限制（合同已用户确认，出问题 git reset） |
| 粒度 | "one item per loop" | 由 prompt 约束，worker 自己判断 |

---

## 三、架构

### 3.1 整体流程

```
┌─────────────┐
│  Chat Agent  │  用户 + agent 在 chat() 中协商合同
│  (attended)  │  → 用 write_page 写合同到 .meta/jobs/xxx/contract.md
│              │  → 用户确认后 agent 改 status 为 ready
└──────┬──────┘
       │
       ▼  Gateway cron 发现 ready 的合同文件
┌──────────────────────────────────────────────────────┐
│  Loop（每轮，只在主 agent 空闲时执行）                    │
│                                                      │
│  1. harness 跑 audit_vault() → 结构化报告              │
│  2. harness 拿上一轮 git diff --name-only              │
│  3. harness 读合同 + 进度文件                           │
│  4. 拼成 prompt → 喂给全新的 worker agent               │
│     worker = KnowledgeAgent(fresh, job_system_prompt)  │
│     worker.chat(拼好的 prompt)                         │
│  5. worker 干活（用工具读写 vault）                      │
│  6. worker 更新进度文件                                 │
│  7. git commit                                        │
│  8. harness 跑 audit 检查硬指标                         │
│  9. 判断是否完成:                                       │
│     - worker 宣布完成 + audit 硬指标通过 → COMPLETED     │
│     - 达到 max_iterations → FAILED                     │
│     - 连续无进展 → 通知用户                              │
│     - 否则 → 下一轮                                     │
└──────────────────────────────────────────────────────┘
```

### 3.2 关键设计决策

1. **每轮全新 agent 实例** — worker 是 `KnowledgeAgent(fresh)`，
   走 `chat()` 路径。每轮 context 干净，不累积对话历史。
   和 Ralph 的 "每轮全新 context window" 一致。

2. **合同和进度分离** — 合同文件（contract.md）基本不变，
   进度文件（progress.md）由 worker 每轮更新。
   和 Ralph 的 PROMPT.md / fix_plan.md 分离一致。

3. **Audit 是我们的"编译器"** — harness 轮间跑 `audit_vault()`，
   结果注入下一轮 worker 的 context。audit 同时封装为 read tool，
   worker 也可以在执行中随时自检。

4. **没有独立的 Evaluator** — 不搞 Generator/Evaluator 分离。
   每轮 worker 在干净 context 下工作，它读到上轮写的差内容时，
   没有"这是我写的我要维护它"的偏见。程序化 audit 检查结构，
   worker 的干净 context 自审检查质量。如果后续发现质量不够，
   加 evaluator 只是在 loop 里多一步调用，不需要改架构。

5. **不做 Write scope 限制** — 合同是用户显式确认过的，
   后台 worker 跑 attended mode，有完整写权限。
   出了问题 git reset。

6. **只在主 agent 空闲时执行** — 用户来了 job 让路，
   避免并发写入冲突。Job 设计成每轮可恢复（每轮结束 git commit），
   中断了下轮继续。

7. **Worker 可以 spawn subagent** — 现有 `spawn_subagent` 机制
   天然可用。Worker 主 context 做调度和决策，重活交给 subagent。
   限制嵌套深度为 2 层（worker → subagent），不允许 sub-sub-subagent。

8. **Worker 用独立的 system prompt** — 加载 schema.md + preferences.md，
   但 protocols.md 替换为 job-specific 版本。保留 Observation Protocols
   和 Structure Protocols，把 Change Protocols 替换为后台执行约束。
   代码层面在 `_build_job_system_prompt()` 中实现。

### 3.3 Acceptance Criteria 的两层验证

合同协商阶段就把每条 criteria 的验证方式定下来：

```markdown
## Acceptance Criteria
- [ ] 每篇论文有对应 wiki/concepts/ 页面 [audit: concepts count >= sources count]
- [ ] frontmatter 完整 [audit: missing_frontmatter = 0]
- [ ] 每页至少 2 个 wiki-link [audit: avg_links_per_page >= 2]
- [ ] 无孤儿页 [audit: orphan_pages = 0]
- [ ] 摘要准确反映原文核心观点 [worker]
- [ ] 分类合理，hub 结构清晰 [worker]
```

- `[audit: ...]` — harness 用 `audit_vault()` 程序化验证，零成本确定性判断
- `[worker]` — 由 worker 在执行过程中自主判断

停止条件三层保险：
1. Worker 在进度文件里宣布完成
2. Harness 检查所有 `[audit]` 标记的硬指标是否通过
3. Max iterations 兜底（默认 30）

### 3.4 Worker 每轮看到什么

Worker 的 user message 由 harness 拼装，包含：

1. **合同内容** — 从 contract.md 读取，包含 goal + criteria
2. **上一轮进度** — 从 progress.md 读取最近几轮的记录
3. **上一轮变更文件列表** — harness 从 `git diff --name-only HEAD~1` 获取
4. **最新 audit 结果** — harness 跑 `audit_vault()` 格式化后注入

Worker 的 system prompt 包含：
- schema.md（vault 结构定义）
- preferences.md（用户偏好）
- Job-specific protocols（替换 Change Protocols 的后台执行版本）

Prompt 里指导 worker：
- 进度文件里每个更改的文件都写一个说明
- 如果对上轮变更有疑惑，可以用工具查看详细 diff
- 在写之前先读（保留 Observation Protocols）
- 不要假设某个东西没有实现（Ralph 的教训：search before create）

### 3.5 通知策略

只通知终态 + 关键事件，正常推进不打扰用户：

| 事件 | 是否通知 |
|------|---------|
| 正常完成一轮 | 否 |
| Job 完成（COMPLETED） | 是 |
| 达到 max_iterations（FAILED） | 是 |
| 连续几轮无实质进展 | 是 |
| Audit 发现严重退化（如大量新 broken links） | 是 |
| Worker 在进度里报告困难 | 是 |

### 3.6 主 Agent 的可见性

`_build_messages_for_query()` 自动注入 active jobs 摘要：

```
## Active Jobs

- [bulk-import-20260415-a3f2] 批量导入论文 (running, 第3轮/30)
  最近进度: "处理了 5 篇，创建 attention.md, transformer.md 等。"
  audit: 15/20 frontmatter 完整，2 orphan pages
```

用户随时可以问"进展怎样"，主 agent 不用主动读文件就有上下文。

---

## 四、文件结构

### 4.1 合同和进度

```
.meta/jobs/
  bulk-import-20260415-a3f2/     # 一个 job 一个子目录
    contract.md                   # 合同（基本不变）
    progress.md                   # 进度（worker 每轮更新）
```

Job ID 自动生成（描述性前缀 + 日期 + 短随机后缀），告知用户后用户可以用 ID 查找。

### 4.2 合同文件模板

```markdown
# Job: 批量导入论文

## Status
draft

## Goal
为 sources/ 下 20 篇论文创建概念页，按主题建立 hub，交叉链接。
产出物涵盖 concept 页、hub 页、交叉 wiki-link、index.md 更新。

## Acceptance Criteria
- [ ] 每篇论文有对应 wiki/concepts/ 页面 [audit: concepts count matches]
- [ ] 每个页面 frontmatter 完整（title, type, tags, summary） [audit: missing_frontmatter = 0]
- [ ] 每页至少 2 个有意义的 [[wiki-link]] [audit: avg_links_per_page >= 2]
- [ ] 无孤儿页（所有新页面至少被一个 hub 引用） [audit: orphan_pages = 0]
- [ ] 相关主题的 hub 页面已创建或更新 [audit: hub_coverage]
- [ ] wiki/index.md 已更新 [worker]
- [ ] 摘要准确反映原文核心观点 [worker]
- [ ] 分类合理，hub 结构清晰 [worker]

## Max Iterations
30

## Created
2026-04-15
```

### 4.3 进度文件模板

```markdown
# Progress: 批量导入论文

## Iteration 1 (2026-04-15 03:00)

### 本轮工作
- 处理了 5 篇论文: paper-a.pdf, paper-b.pdf, ...
- 创建页面: wiki/concepts/attention-mechanism.md, wiki/concepts/transformer-architecture.md, ...
- 创建 hub: wiki/concepts/deep-learning-hub.md
- 更新 wiki/index.md 添加 Deep Learning hub

### 文件变更说明
- wiki/concepts/attention-mechanism.md: 新建，基于 paper-a.pdf，覆盖 self-attention 和 multi-head attention
- wiki/concepts/transformer-architecture.md: 新建，基于 paper-b.pdf，覆盖 encoder-decoder 结构
- wiki/concepts/deep-learning-hub.md: 新建 hub，组织上述页面
- wiki/index.md: 添加 Deep Learning hub 链接

### Audit 结果（harness 注入）
missing_frontmatter: 0, orphan_pages: 0, broken_links: 0

### 自评
前 5 篇处理顺利。分类暂时全放 Deep Learning 下，后续如果出现
NLP 或 CV 相关论文可能需要拆分 hub。

## Iteration 2 (2026-04-15 03:15)
...
```

---

## 五、和现有代码的关系

### 5.1 新增

| 文件 | 内容 |
|------|------|
| `job.py`（新建） | 轻量 helper：读写合同/进度 markdown、解析 status、从 audit 结果验证硬指标 |
| `tools/schemas.py` | 新增 `audit_vault` read tool schema |
| `tools/handlers_read.py` | 新增 `handle_audit_vault` handler |
| `tools/dispatch.py` | 注册 audit_vault tool |
| `tools/policy.py` | audit_vault 的 tier（OBSERVATION） |
| `agent/core.py` | 新增 `_build_job_system_prompt()`；`_build_messages_for_query()` 注入 active jobs |
| `gateway.py` | `_run_cron()` 新增 job loop 调度 |
| `constants.py` | 新增 job 相关常量（默认 max_iterations 等） |

### 5.2 不改动

| 文件 | 原因 |
|------|------|
| `plan.py` | Plan 继续服务 session-organize，短期共存 |
| `session.py` | session finalization 逻辑不变 |
| `skills/` | skill 保留，后续可作为合同模板来源 |
| `vault/` | vault 层不变，job 通过现有 tool 层操作 vault |
| `tools/policy.py` 的核心逻辑 | 不需要 write scope 机制，worker 跑 attended mode |

### 5.3 现有测试不应该 break

所有改动都是新增路径。唯一碰现有文件的是：
- `tools/schemas.py` 新增一个 read tool（不影响现有 schema）
- `agent/core.py` 新增方法 + `_build_messages_for_query()` 多注入一段
  （只在有 active jobs 时才注入，没有 jobs 时行为不变）
- `gateway.py` cron 新增一段 job 轮询（和现有 digest/lint 并列，不互相影响）

---

## 六、和 v1 设计的对比

### 砍掉的

| v1 设计 | v2 为什么砍 |
|---------|-----------|
| Generator / Evaluator 分离 | 每轮干净 context + audit 程序检查已够用，不需要独立 evaluator。后续可加 |
| Evaluator prompt 协商机制 | 过度工程。合同里直接写 criteria 就行 |
| JobStatus enum / StepRecord / WriteScope dataclass | 全用 Markdown，人和 LLM 都能读写 |
| `create_job` / `start_job` 专用 tools | 用现有 `write_page` 写合同文件，prompt 约束格式 |
| `step_job()` 新方法 | Worker 走现有 `chat()` 路径 |
| PolicyContext.job_write_scope + 合同授权逻辑 | 不做 write scope 限制，合同已用户确认 |
| JobStore（save/load/list_by_status/next_ready） | 轻量 helper 读 markdown 文件即可 |

### 保留的

| 设计要素 | 保留理由 |
|---------|---------|
| 合同文件 + 用户显式确认 | 用户必须知道后台会做什么 |
| 程序化 audit 做 backpressure | 知识库没有编译器，audit 是我们的确定性反馈源 |
| 每轮 git commit | 每轮的产出是可恢复的中间结果 |
| 只在主 agent 空闲时执行 | 避免并发写入 |
| System prompt 注入 active jobs | 用户随时能问进展 |
| spawn_subagent 能力 | worker 可以分出子任务做重活 |

### 后续如果需要可以加的

| 能力 | 触发条件 | 改动量 |
|------|---------|--------|
| 独立 Evaluator | 发现 worker 自审质量不够 | loop 里多一步 sub-agent 调用 |
| Write scope 硬限制 | 发现 worker 越界写入 | PolicyContext 加字段 + check_pre_dispatch 几行 |
| 多 job 并行 | vault 支持多写入者 | gateway 调度逻辑 |
| Job 类型模板 | 协商成本太高 | 预置 contract 模板 |

---

## 七、用户交互流程（完整示例）

```
用户: 帮我把 sources/ 下的 20 篇论文整理进知识库

Agent: 我看了 sources/，有 20 个文件。我建议创建一个后台任务：

  目标：为每篇论文创建概念页，按主题建立 hub，交叉链接
  验收标准：
    1. 每篇论文有对应 wiki/concepts/ 页面 [audit]
    2. 每个页面 frontmatter 完整 [audit]
    3. 每页至少 2 个有意义的 wiki-link [audit]
    4. 无孤儿页 [audit]
    5. 相关 hub 已创建 [audit]
    6. 摘要准确反映原文核心观点 [worker]
    7. 分类合理，hub 结构清晰 [worker]
  最多 30 轮

  需要调整什么标准？

用户: 可以，开始吧

Agent: [用 write_page 写合同文件 status: draft]
       [展示合同内容]
       [用 write_page 把 status 改为 ready]
       任务已创建 [bulk-import-20260415-a3f2]，我会在后台持续推进。
       你可以随时问我进展。

--- 用户离线 ---

Gateway cron 发现 ready job，主 agent 空闲
  → 读合同 + 空进度 + 空 diff + audit 结果
  → spawn fresh worker agent
  → worker.chat(拼好的 prompt)
  → worker 处理 5 篇论文，创建页面，建 hub
  → worker 更新 progress.md
  → git commit
  → harness 跑 audit：5/20 完成，frontmatter ✓，0 orphan
  → 硬指标未全部满足 → 继续

下一轮 cron（主 agent 仍空闲）
  → 读合同 + 上轮进度 + git diff file list + 新 audit
  → spawn fresh worker
  → worker 看到上轮进度和 audit，继续处理下一批
  → ...

...（多轮后）...

最终轮 cron
  → worker 补全最后几篇，修复 audit 报告的问题
  → worker 在 progress.md 里写 "所有标准已满足，建议标记完成"
  → harness 跑 audit：20/20 完成，0 orphan，0 broken links
  → worker 宣布完成 ✓ + audit 硬指标全过 ✓
  → 把 contract.md 的 status 改为 completed
  → 通知用户

--- 用户回来 ---

用户: 进展怎样了？

Agent:（从 system prompt 注入看到 job 状态）
      批量导入任务已完成！20 篇论文全部整理进知识库：
      - 创建了 20 个概念页
      - 建立了 4 个主题 hub
      - 交叉链接 47 个
      所有验收标准满足。你可以查看进度详情。
```

---

## 八、实现计划

### 第一步：Audit 工具化

- `tools/schemas.py` 新增 `audit_vault` read tool
- `tools/handlers_read.py` 新增 handler（调 `vault.audit_vault()`，格式化返回）
- `tools/dispatch.py` 注册
- `tools/policy.py` 加 tier（OBSERVATION）
- `constants.py` 加 `OBSERVATION_TOOL_NAMES`
- 测试

### 第二步：Job 文件读写 + 状态判断

- 新建 `job.py`：读写合同/进度 markdown 的 helper 函数
- 扫描 `.meta/jobs/` 找 active jobs
- 从 contract.md 解析 status、goal、criteria
- 从 audit 结果验证 `[audit: ...]` 标记的硬指标
- 生成 job ID
- 测试

### 第三步：Worker System Prompt

- `agent/core.py` 新增 `_build_job_system_prompt()`
- 加载 schema.md + preferences.md
- 替换 protocols.md → job-specific protocols
- 测试

### 第四步：Harness Loop + Gateway 集成

- 构建 worker prompt（合同 + 进度 + diff + audit）
- 在 `gateway._run_cron()` 里新增 job 轮询
- 只在主 agent 空闲时执行（无 active chat 时）
- 每轮：spawn worker → 等完成 → git commit → 跑 audit → 判断停止条件
- 完成/失败/关键事件时通知用户
- `_build_messages_for_query()` 注入 active jobs 摘要
- 测试

---

## 九、开放问题

1. **Job 类型是否需要预置模板？** MVP 不需要。任何 goal + criteria 组合都是一个 job。
   后续可以基于 ImportSources/OrganizeWiki skill 的经验做模板。

2. **"无进展"怎么判定？** 比较连续两轮的 git diff 和 audit 数字。
   如果连续 3 轮 diff 为空或 audit 指标无改善，视为卡住，通知用户。

3. **进程崩溃后 RUNNING 的 job？** 自动恢复。每轮结束有 git commit，
   下次 cron 轮询发现 status 还是 running 就继续下一轮。

4. **多 job 排队？** MVP 同一时刻最多一个 running job。
   多个 ready jobs 按创建时间排序执行。

5. **Plan 怎么处置？** 短期共存。中期 session-organize 可改造成
   `max_iterations=1` 的轻量 job。长期 Plan 退场。
