# Job System — 目标驱动的后台任务执行模型

> **状态**：设计草案（2026-04-14）
>
> 本文档记录 NoteWeaver Job 系统的设计讨论和决策。这是一个**新的执行模型**，
> 与现有 `chat()` 平级，允许 agent 在用户不在场时持续推进长期任务。

---

## 一、问题

现有系统只有两种半成品执行能力：

1. **`chat()` 的工具循环** — 单次会话内最多 `AGENT_MAX_STEPS=25` 步的推理执行，
   不是"围绕验收标准反复推进直到完成"。用户离开后一切停止。

2. **Gateway cron** — `set_attended(False)` 后只能写 journal/promote candidates，
   `policy.py` 里 `unattended + CONTENT` 直接返回 `allowed=False`。

这意味着以下场景无法闭环：

- 批量导入 20 篇论文（sources → wiki 概念页 → 交叉链接）
- 大规模整理杂乱 vault（frontmatter 补全、分类调整、孤儿页处理）
- 针对某个领域的 deep research（fetch → 综合 → 产出 synthesis 页）

这些任务的共同特点：需要多轮迭代、需要质量判断、用户不在也要能继续、
需要一个"完成"的定义。

---

## 二、核心概念：Job

### 2.1 Job 是什么

Job 是一个**用户和 agent 协商产生的、可后台多轮执行的长期任务**。

它和现有概念的关系：

| 概念 | 本质 | 和 Job 的关系 |
|------|------|---------------|
| Tool | 原子操作 | Job 的每一轮 step 内部会调用 tools |
| Skill | 单次多步工作流 | Skill 可以作为某类 Job 的模板/初稿来源 |
| Plan | 一次性变更提案 | Plan 是 organize-only 的提案对象，语义太窄（见下文） |
| Job | 可迭代的后台任务 | 新的一等公民 |

### 2.2 为什么不扩展 Plan

Plan 在代码里的实际角色非常窄：

- **只在一个场景下被创建**：`finalize_session()` → `generate_organize_plan()` → `_handle_submit_plan()`
- **`submit_plan` 是一个 ghost tool**：参数定义是空的（`"properties": {}, "required": []`），
  不暴露给正常 chat，只在 `generate_organize_plan()` 内部调用
- **PlanStatus.PARTIALLY_EXECUTED 从未被任何代码设置过**
- **所有注释都写着 "organize-only"**

| 维度 | Plan 的实际语义 | Job 需要的语义 |
|------|----------------|---------------|
| 谁创建 | 系统自动（session 结束时） | 用户和 agent 协商后显式创建 |
| 生命周期 | pending → approve → execute once → done | draft → ready → running ↔ evaluating → completed |
| 执行模型 | 一次性：一个 LLM 调用跑完 | 多轮迭代：generator/evaluator 循环 |
| 进度 | 没有进度概念 | iteration_count, progress, last_evaluation |
| 验收 | 用户说"好" = 通过 | 结构性检查 + evaluator agent 判断 |

硬扩展 Plan 会导致老字段（`targets`, `rationale`, `change_type`, `target_mtimes`）
只对 organize 有意义，新字段只对 job 有意义，每段代码都要分支判断。
不如从头建一个干净的模型。

**Plan 的处置路径**：
1. 短期：Plan 和 Job 共存
2. 中期：session-organize 改造成轻量级 Job（`max_iterations=1`，无 evaluator）
3. 长期：Plan 退场，统一到 Job

---

## 三、架构：Generator / Evaluator 循环

### 3.1 灵感来源

- **Anthropic "Harness design for long-running apps"**（2026-03-24）：
  三层架构（Planner → Generator → Evaluator），GAN-inspired 的生成/评判对立。
  核心发现：模型对自己输出的评价虚高（self-evaluation bias），
  分离 generator 和 evaluator 的上下文是最有效的去偏手段。

- **Karpathy autoresearch**：完全无人值守的循环（假设 → 实验 → 分析 → 下一个假设），
  每轮迭代产出真实有价值的中间结果。

### 3.2 三个角色

```
┌─────────────┐
│   Planner   │  用户 + agent 在 chat() 中交互协商
│  (attended)  │  → 产出 Job（goal + criteria + evaluator prompt）
└──────┬──────┘
       │ Job 持久化，用户显式确认启动
       ▼
┌─────────────┐     feedback     ┌──────────────┐
│  Generator  │ ◄──────────────► │  Evaluator    │
│ (step_job)  │                  │ (sub-agent)   │
│  有执行上下文 │  ──vault 状态──►  │  干净上下文    │
│  可写 vault  │                  │  只读 vault    │
└─────────────┘                  └──────────────┘
       │
       ▼ 循环直到 evaluator 判定通过或达到 max_iterations
```

关键设计决策：

1. **Planner 不是新抽象** — 就是正常 `chat()` 对话。Agent 和用户讨论目标、验收标准、
   写入范围。不需要特殊机制。

2. **Generator 不知道自己是 generator** — 它看到的 system prompt 是面向目标的
   （"你正在执行一个后台任务，目标是...，上次反馈是..."），不是"你是三件套里的 generator"。

3. **Evaluator 不知道自己是 evaluator** — 它看到的是"你是一个质量审查员，以下是验收标准..."。
   通过 sub-agent 机制启动（干净的 KnowledgeAgent 实例），**只给读工具**。

4. **Evaluator 由 harness 强制调用，不是 generator 自愿触发** — 如果让 generator 自己
   决定什么时候叫 evaluator，等于让被考核者决定什么时候叫监考官。

5. **每个 step 是一个 git commit** — 已提交的工作即使中断也是有效的。
   借鉴 autoresearch 的原则：每轮迭代的产出物都是自洽的中间结果。

### 3.3 对 LLM 的可见性

**Agent 只需要看到 Job 这一个概念**，不需要看到 planner/generator/evaluator 三个角色。

> "不然 agent 会开始'操作流程本身'，而不是完成任务。"

| 对 LLM 可见 | 对 LLM 不可见 |
|-------------|--------------|
| Job 对象（goal, criteria, progress） | Generator / evaluator 的区分 |
| `create_job` tool | Evaluator 的具体 prompt |
| `start_job` tool | 程序验证谓词 |
| System prompt 中自动注入的 active jobs 状态 | 调度策略 |

### 3.4 验收：程序验证前置，LLM 评判后置

不是两种检查并列，而是漏斗：

```
step 完成一轮执行
    │
    ▼
[程序验证] ← 不调 LLM，零成本
    │
    ├── 结构性标准未满足 → 直接反馈给 generator，不叫 evaluator
    │   （"还有 3 个文件没导入"、"2 个页面缺 frontmatter"）
    │
    └── 结构性标准全部通过 → 再叫 evaluator
        │
        ▼
    [Evaluator sub-agent] ← 调 LLM，有成本
        判断质量性标准
        （分类合理性、摘要质量、链接语义关联度）
```

这和 Anthropic 的经验一致：他们的 evaluator 通过 Playwright 先跑功能测试（程序验证），
只有功能通过了才做设计/质量评分。

### 3.5 Evaluator prompt 的来源

Planner 阶段（chat 中协商），evaluator prompt 应该由谁写？

- **选项 A**：Generator agent 自己写。风险：rubric 对自己宽容。
- **选项 B**：让用户写。风险：用户不知道怎么写 LLM prompt。
- **选项 C**：Planner 阶段 spawn "rubric 制定" sub-agent，为 evaluator 生成严格的
  评判 prompt。Generator 只知道 acceptance_criteria 的自然语言描述，不知道 evaluator
  具体怎么打分。避免 "teaching to the test"。

**MVP 用选项 A**。因为 context separation 本身就是最大的去偏利器
（Anthropic blog 验证），prompt 调优是第二步。
后续可以升级到选项 C。

---

## 四、数据模型

### 4.1 Job

```python
class JobStatus(Enum):
    DRAFT = "draft"              # 协商中，还没确认
    READY = "ready"              # 用户确认，等待启动
    RUNNING = "running"          # 正在执行某一轮
    EVALUATING = "evaluating"    # 等待 evaluator 判定
    PAUSED = "paused"            # 用户主动暂停
    BLOCKED = "blocked"          # 需要人类介入
    COMPLETED = "completed"      # evaluator 判定全部通过
    FAILED = "failed"            # 超过 max_iterations 或不可恢复错误
    CANCELLED = "cancelled"      # 用户取消

@dataclass
class Job:
    id: str
    status: JobStatus
    created_at: str
    updated_at: str

    # --- 合同 ---
    goal: str
    acceptance_criteria: list[str]
    evaluator_prompt: str
    write_scope: WriteScope
    max_iterations: int

    # --- 进度 ---
    iteration_count: int = 0
    progress: list[StepRecord] = field(default_factory=list)
    last_evaluation: str | None = None
    blocked_reason: str | None = None

    # --- 运行时 ---
    generator_context: str | None = None  # 上一轮 generator 的压缩状态

@dataclass
class WriteScope:
    allowed_path_prefixes: list[str]   # e.g. ["wiki/concepts/", "sources/"]
    allowed_tools: list[str]           # e.g. ["write_page", "append_section", ...]
    max_pages: int = 50

@dataclass
class StepRecord:
    iteration: int
    started_at: str
    completed_at: str
    actions_taken: list[str]           # tool call 摘要
    structural_check: dict             # 程序验证结果
    evaluation: str | None             # evaluator 反馈（如果触发了）
    evaluation_passed: bool | None
```

### 4.2 持久化

```
.meta/jobs/
  job-20260414-a3f2.json     # Job 对象
  job-20260414-a3f2/         # Job 运行时数据
    step-001.json            # 每轮 step 的详细记录
    step-002.json
    ...
```

### 4.3 JobStore

和 PlanStore 同构但独立：`save()`, `load()`, `list_by_status()`, `next_ready()`,
`update_status()`, `append_step()`。

`next_ready()` 保证同一时刻只有一个 RUNNING job（vault git batching 约束）。

---

## 五、执行入口

### 5.1 新增 Tools（对 LLM 可见）

```python
# create_job: 把协商好的合同固化
{
    "name": "create_job",
    "parameters": {
        "goal": str,
        "acceptance_criteria": list[str],
        "evaluator_prompt": str,
        "write_scope": {
            "allowed_path_prefixes": list[str],
            "allowed_tools": list[str],
            "max_pages": int
        },
        "max_iterations": int     # 默认 10
    }
}

# start_job: 用户确认后显式启动
{
    "name": "start_job",
    "parameters": {
        "job_id": str
    }
}
```

### 5.2 新增执行方法（对 LLM 不可见）

```python
class KnowledgeAgent:
    def chat(self, user_message: str) -> Generator[str, None, None]:
        """交互式对话。用户在场。（现有）"""
        ...

    def step_job(self, job_id: str) -> StepResult:
        """推进一个 job 一步。由 gateway cron 调用。（新增）"""
        # 1. 加载 Job
        # 2. 构建 generator prompt（目标 + 上次反馈 + vault 状态）
        # 3. 跑一轮 LLM + tool 循环（面向目标的 context，不是对话历史）
        # 4. 程序验证
        # 5. 如果结构性标准全部通过 → spawn evaluator sub-agent
        # 6. 根据评判结果更新 job progress
        # 7. 持久化 → 等待下一次调度
        ...
```

`step_job()` 和 `chat()` 的关键区别：

- `chat()` 的 context 是对话历史（append-only transcript + session summary）
- `step_job()` 的 context 是面向目标的（goal + criteria + last feedback + vault state），
  每轮构建全新的消息序列，不累积对话历史。类似现有 `execute_plan()` 的做法。

### 5.3 System prompt 注入

`_build_messages_for_query()` 新增一段，自动注入 active jobs 状态：

```
## Active Jobs

- [job-20260414-a3f2] 批量导入论文 (7/20 完成, 第3轮)
  最近评价: "前7篇结构完整，分类合理。第5篇摘要过于简短。"
- [job-20260414-b7e1] 整理 vault 结构 (等待启动)
```

和现有 vault context、audit report 自动注入同一模式。

---

## 六、Policy 层改动

### 6.1 PolicyContext 扩展

```python
@dataclass
class PolicyContext:
    attended: bool = True
    # --- 新增 ---
    active_job_id: str | None = None
    job_write_scope: WriteScope | None = None
    # --- 现有 ---
    topics_surveyed: set[str] = ...
    pages_read: list[str] = ...
    ...
```

### 6.2 check_pre_dispatch 改动

在现有的 unattended content block 里加一条：

```python
if not ctx.attended and target in (WriteTarget.CONTENT, WriteTarget.SOURCE):
    if ctx.active_job_id and ctx.job_write_scope:
        if _within_job_scope(ctx.job_write_scope, path, name):
            return PolicyVerdict(
                allowed=True,
                warning=f"Job {ctx.active_job_id} authorized write to {path}"
            )
    return PolicyVerdict(allowed=False, warning=_UNATTENDED_CONTENT_MSG)
```

这是最关键的拔高点：从"无人值守全禁写"到"按合同授权写"。
侵入性很低，只影响 `policy.py` 一个文件里的几行。

---

## 七、Gateway 调度

`gateway._run_cron()` 新增 job 轮询，和现有 digest/lint 并列：

```python
# --- Job execution ---
if now - last_job_check >= job_poll_interval:
    job = self.agent.job_store.next_ready()
    if job:
        async with self._lock:
            try:
                self.agent.step_job(job.id)
            except Exception as e:
                log.error("Job step failed: %s", e)
            # 如果 job 完成或 blocked，通知用户
            updated = self.agent.job_store.load(job.id)
            if updated.status in (JobStatus.COMPLETED, JobStatus.BLOCKED):
                self._pending_notifications.append(...)
    last_job_check = now
```

### 执行队列

MVP：同一时刻只有一个 RUNNING job。`next_ready()` 在有 RUNNING 时返回 None。

后续扩展：优先级、并行限制、依赖关系。

---

## 八、用户交互流程（完整示例）

```
用户: 帮我把 sources/ 下的 20 篇论文整理进知识库

Agent: 我看到 sources/ 下有 20 个文件。我建议创建一个后台任务：

  目标：为每篇论文创建概念页，按主题建立 hub，交叉链接
  验收标准：
    1. 每篇论文有对应 wiki/concepts/ 页面
    2. 每个页面 frontmatter 完整（title, type, tags, summary）
    3. 每页至少 2 个 [[wiki-link]]
    4. 无孤儿页（所有新页面至少被一个 hub 引用）
    5. 摘要准确反映原文核心观点
  写入范围：wiki/concepts/*, sources/*
  最多 10 轮

  需要调整什么标准？

用户: 可以，开始吧

Agent: [调用 create_job tool] → Job draft 创建
       [调用 start_job tool] → Job 状态变为 ready
       任务已创建 [job-20260414-a3f2]，我会在后台持续推进。
       你可以随时问我进展。

--- 用户离线 ---

Gateway cron 发现 ready job
  → step_job(): generator 导入前 5 篇，创建页面
  → 程序验证：5/20 文件完成，frontmatter 全部完整
  → 不叫 evaluator（结构性标准还没到"全部完成"的阶段）
  → 持久化进度

下一轮 cron
  → step_job(): generator 导入第 6-10 篇
  → 程序验证：10/20 完成
  → 持久化进度

...

最终轮 cron
  → step_job(): generator 完成最后几篇，补全链接
  → 程序验证：20/20 完成，frontmatter 100%，孤儿页 0
  → 结构性标准全部通过 → 叫 evaluator
  → evaluator（干净上下文）逐条评判：
    "标准 1-4 通过。标准 5：第 12 篇摘要过于笼统，建议补充具体数据。"
  → generator 根据反馈修正第 12 篇
  → 再次触发 evaluator → 全部通过
  → Job 状态 → COMPLETED

--- 用户回来 ---

用户: 进展怎样了？

Agent:（从 system prompt 中自动看到 job 状态）
      批量导入任务已完成！20 篇论文全部整理进知识库：
      - 创建了 20 个概念页
      - 建立了 4 个主题 hub
      - 交叉链接 47 个
      评审通过，所有验收标准满足。
```

---

## 九、实现计划

### 第一步：数据模型 + 持久化

- 新建 `job.py`：`JobStatus`, `Job`, `WriteScope`, `StepRecord`, `JobStore`
- 存储在 `.meta/jobs/`
- 测试：序列化、状态机、`next_ready()` 排队语义

### 第二步：Tools

- `schemas.py` 新增 `create_job` 和 `start_job` schema
- `handlers_write.py` 新增 handler
- `dispatch.py` 注册
- `policy.py` 新增 tier（`create_job` → LOW_WRITE, `start_job` → LOW_WRITE）
- 测试：tool 调用创建/启动 job

### 第三步：执行引擎

- `agent/core.py` 新增 `step_job()` 方法
- 构建面向目标的 generator prompt
- 程序验证框架（可插拔的 verifier 函数）
- Evaluator sub-agent：受限的 KnowledgeAgent（只读工具 + 评判 prompt）
- PolicyContext 扩展：`active_job_id`, `job_write_scope`
- `check_pre_dispatch` 新增"按合同授权"逻辑
- 测试：step_job 的完整循环、policy 的 job scope 授权

### 第四步：Gateway 集成

- `gateway._run_cron()` 新增 job 轮询
- job 完成/阻塞时推送通知
- `_build_messages_for_query()` 注入 active jobs 状态
- 测试：gateway job 调度

### 第五步：第一个 Job 类型模板

- `bulk_import`：基于现有 `ImportSources` skill 的经验
- 预置的 acceptance_criteria 模板
- 预置的 evaluator_prompt 模板
- 预置的程序验证谓词（文件数、frontmatter、孤儿页）

---

## 十、和现有代码的关系

### 改动文件清单

| 文件 | 改动性质 |
|------|---------|
| `job.py`（新建） | 数据模型 + 持久化 |
| `tools/schemas.py` | 新增 2 个 tool schema |
| `tools/handlers_write.py` | 新增 2 个 handler |
| `tools/dispatch.py` | 注册新 tool |
| `tools/policy.py` | PolicyContext 扩展 + 合同授权逻辑 |
| `agent/core.py` | 新增 `step_job()` + system prompt 注入 |
| `gateway.py` | cron 新增 job 轮询 |
| `constants.py` | 新增 job 相关常量 |

### 不改动的文件

| 文件 | 原因 |
|------|------|
| `plan.py` | Plan 继续服务 session-organize，短期共存 |
| `session.py` | session finalization 逻辑不变 |
| `skills/` | skill 保留，后续可作为 job 模板来源 |
| `vault/` | vault 层不变，job 通过现有 tool 层操作 vault |

### 现有 648 测试不应该 break

所有改动都是新增路径。PolicyContext 的新字段有默认值（`None`），
`check_pre_dispatch` 的新逻辑只在 `active_job_id is not None` 时触发，
不影响现有 attended/unattended 行为。

---

## 十一、Skill 的角色

Skill 不是长期执行容器，但可以作为 Job 的模板来源：

- `ImportSources` skill → `bulk_import` job 类型的默认合同草案
- `OrganizeWiki` skill → `organize_corpus` job 类型的默认合同草案

当 agent 识别到"批量导入"场景时，不用从零协商 criteria，
而是加载对应 skill 的模板作为起点，再和用户微调。

---

## 十二、Skill 的触发机制（现状澄清）

代码里 skill 的触发有两条路径：

**路径 1：Prompt-level routing**（LLM 自发）

LLM 在 system prompt 里看到 `<available_skills>` XML 块（name + description + location）。
prompt 指示它：如果某个 skill 适用，用 `read_page` 去读 SKILL.md，然后按说明执行。
不是特殊 token，不是 parser 拦截，就是 prompt 引导。

**路径 2：代码直接调用**

CLI/gateway 直接调 `agent.run_skill("organize_wiki")`，
进入 skill 的 `prepare() → execute()` 生命周期。

注意：`gateway.py` 第 107 行关于 `<<skill:import_sources>>` 的注释是**过时的**，
代码中没有任何 `<<skill:>>` 标记检测逻辑。这个注释应该清理。

---

## 十三、开放问题

1. **Job 类型是否需要显式枚举？** MVP 可以不枚举，任何 goal + criteria 组合都是一个 job。
   但预置模板（bulk_import, organize_corpus）能降低协商成本。

2. **Evaluator 应该跑几轮？** 如果 evaluator 说"第 5 篇摘要不好"，generator 修完后
   是否需要 evaluator 再验一次？建议：改了就再验，但单个 step 内最多 evaluator 2 次，
   避免无限乒乓。

3. **Job 失败的恢复策略？** 进程崩溃后，RUNNING 的 job 应该自动恢复还是等用户确认？
   建议 MVP：自动恢复（幂等性由每 step 一个 git commit 保证）。

4. **多 job 并行？** MVP 不支持。vault git batching 假设单一写入者。
   后续可以考虑按 write_scope 不重叠来判断是否可并行。

5. **Deep research 是否在 MVP scope 内？** 建议不在。它需要 `fetch_url`（外部网络），
   验收标准难以客观化，write_scope 难以预先界定。等 bulk_import 和 organize_corpus
   跑通后再做。
