# CLAUDE.md

Instructions for Claude Code (and Codex) working on NoteWeaver.

## Build & Test

```bash
pip install -e .                     # install in dev mode
pip install -e ".[all]"              # all optional deps (anthropic, telegram, feishu)
python3 -m pytest tests/ -v          # run all tests (~0.5s, no API keys needed)
```

No linter is configured. Tests use pytest, temp directories, and mocked LLM providers — no external services required.

## Architecture

Single Python package at `src/noteweaver/` (~5700 LOC). No frameworks — just OpenAI/Anthropic SDKs, tool calling, and file I/O.

**Primary interface: Gateway** (long-running chat agent via Telegram/IM).
CLI is a secondary interface for power users.
All features must work through `agent.chat()` — gateway just passes user messages to it.

### Core loop (Continuous conversation flow)

```
KnowledgeAgent.chat(user_message)
  → _build_messages_for_query()          # assemble what LLM sees
  → LLMProvider.chat_completion()        # ALL tools (read + write)
  → for each tool_call:
      dispatch_tool()                    # execute any tool
  → if LLM output contains <<skill:name>>:
      skill.prepare() → skill.execute()  # multi-step workflow
  → loop up to 25 steps

Agent proposes changes in natural language → user approves → agent writes.
All in one continuous conversation. No separate Plan/execute phases.
Skills are triggered by the LLM when it recognises skill-level intent.
```

### File → Responsibility

```
agent.py              Agent loop, system prompt (with schema core), context assembly,
                      session memory, continuous chat with all tools, run_skill()
session.py            Shared session logic: agent construction, finalize_session,
                      journal writing, digest prompts — used by both cli.py and gateway.py
plan.py               Plan data model (legacy, kept for backward compatibility)
tools/definitions.py  9 tool schemas + legacy handlers + dispatch_tool()
                      Single TOOL_SCHEMAS set (read + write, used everywhere)
tools/policy.py       Pre-dispatch safety gates (read-before-write, etc.)
skills/              Multi-step workflows above the tool layer
  base.py            Skill ABC, SkillContext, SkillResult
  import_sources.py  Bulk-import source files into wiki (replaces legacy ingest)
  organize_wiki.py   Audit + remediate wiki health (replaces legacy organize/restructure)
  __init__.py        Skill registry: get_skill(), list_skills()
vault.py              On-disk vault: read/write files, git batching, FTS, stats
cli.py                CLI commands (chat, trace, lint, digest, ingest, import-sources, etc.)
trace.py              Structured trace collector for observability
config.py             Config from .meta/config.yaml + env vars
gateway.py            Long-running Telegram gateway + cron digest/lint
search.py             SQLite FTS5 full-text search
backlinks.py          Wiki-link extraction and backlink index
frontmatter.py        YAML frontmatter validation
adapters/provider.py  Abstract LLM provider interface
adapters/openai_provider.py    OpenAI adapter
adapters/anthropic_provider.py Anthropic Messages API adapter
adapters/retry.py     Exponential backoff for LLM calls
adapters/telegram_adapter.py   Telegram bot adapter
adapters/base.py      Abstract IM adapter interface
```

### Key design facts

- **Gateway is the primary interface.** The long-running chat agent (Telegram/IM) is the main way users interact with NoteWeaver. CLI is secondary. All features must work through `agent.chat()`.
- **Three layers: tools → skills → chat.** Tools are atomic operations (9 primitives). Skills are multi-step workflows triggered by the LLM when it recognises skill-level intent (via `<<skill:name>>` markers in the system prompt). CLI commands wrap `agent.chat()` or `agent.run_skill()`.
- **Continuous conversation flow.** All tools (read + write) available during chat. Agent proposes changes in natural language, writes after user approval. No separate Plan/execute phases.
- **Schema always in context.** Schema files from `.schema/` are injected into the system prompt. Agent always knows wiki rules.
- **Primitive tools.** 9 tools: 5 read (read_page, search, get_backlinks, list_pages, fetch_url) + 4 write (write_page, append_section, update_frontmatter, add_related_link).
- **Skills are LLM-triggered.** The system prompt tells the LLM about available skills. When the LLM determines the user's intent matches a skill, it emits `<<skill:name(args)>>` in its response. The `chat()` loop intercepts this, runs `prepare()` then `execute()`, and injects results back. Skills: `import_sources` (bulk-import from sources/), `organize_wiki` (audit + remediate). Legacy high-semantic handlers (ingest, organize, restructure) are deprecated.
- **Transcript is append-only.** Context compression only in the query view (`_build_messages_for_query`).
- **Tool results are tiered.** Full → preview → placeholder based on age. See `_apply_tool_result_tiers()`.
- **Git batching.** All writes in one chat turn → one git commit via `_operation_depth`.
- **Safety in code, not prompt.** Read-before-write, synthesis link requirements, unattended restrictions → `policy.py`.

### Where to look

| Task | Primary file(s) |
|------|-----------------|
| Add/change a tool | `tools/definitions.py` (schema + handler), `tools/policy.py` (tier) |
| Add a skill | `skills/` — new file + register in `skills/__init__.py` |
| Change what context LLM sees | `agent.py` → `_build_messages_for_query()` |
| Change agent behavior/personality | `agent.py` → `SYSTEM_PROMPT` (top of file) |
| Change schema rules in prompt | `agent.py` → `.schema/schema.md` injection |
| Change write permission rules | `tools/policy.py` → `check_pre_dispatch()` |
| Change vault file operations | `vault.py` |
| Change session finalization logic | `session.py` (shared by CLI + Gateway) |
| Add a CLI command | `cli.py` → add `cmd_*()` + routing in `main()` |
| Understand agent traces | `trace.py`, CLI: `nw trace` |
| Change LLM provider behavior | `adapters/openai_provider.py` or `adapters/anthropic_provider.py` |

## Test patterns

All tests use this fixture pattern:

```python
@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v

@pytest.fixture
def agent(vault: Vault) -> KnowledgeAgent:
    mock_provider = MagicMock()
    return KnowledgeAgent(vault=vault, provider=mock_provider)
```

Adding a new tool requires:
1. Schema in `TOOL_SCHEMAS` + handler in `TOOL_HANDLERS` (`tools/definitions.py`)
2. If it's a read tool, add to `_OBSERVATION_TOOL_NAMES` in definitions.py
3. Risk tier in `TOOL_TIERS` (`tools/policy.py`)
4. Tests in `test_tools.py`; policy tests in `test_attended_policy.py` if it writes

Adding a new skill requires:
1. New file in `skills/` subclassing `Skill` (implement `name`, `description`, `prepare`, `execute`)
2. Register in `skills/__init__.py` → `SKILL_REGISTRY`
3. Tests in `test_skills.py`
4. CLI command in `cli.py` if it should be user-invocable

## Gotchas

- `$HOME/.local/bin` must be on PATH for `nw` CLI
- `nw lint` and `nw digest` need API keys (they run the agent loop)
- Tests use `auto_git=False` — the real vault auto-initializes a Git repo
- 9 tools in `TOOL_SCHEMAS` — `tools/definitions.py` is truth
- `CHAT_TOOL_SCHEMAS` == `TOOL_SCHEMAS` in V2 (all tools available during chat)
- Legacy tools (capture, organize, restructure, ingest, survey_topic) kept as handlers for backward compat — deprecated in favor of skills
- Skills are in `skills/` — use `agent.run_skill("name")` or CLI `nw import-sources` / `nw lint`
- DESIGN.md is product philosophy (~1000 lines), not code architecture
