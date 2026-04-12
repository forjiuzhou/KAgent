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

### Core loop (Continuous conversation flow)

```
KnowledgeAgent.chat(user_message)
  → _build_messages_for_query()          # assemble what LLM sees
  → LLMProvider.chat_completion()        # ALL tools (read + write)
  → for each tool_call:
      dispatch_tool()                    # execute any tool
  → loop up to 25 steps

Agent proposes changes in natural language → user approves → agent writes.
All in one continuous conversation. No separate Plan/execute phases.
```

### File → Responsibility

```
agent.py              Agent loop, system prompt (with schema core), context assembly,
                      session memory, continuous chat with all tools
plan.py               Plan data model (legacy, kept for backward compatibility)
tools/definitions.py  9 tool schemas + legacy handlers + dispatch_tool()
                      Single TOOL_SCHEMAS set (read + write, used everywhere)
tools/policy.py       Pre-dispatch safety gates (read-before-write, etc.)
vault.py              On-disk vault: read/write files, git batching, FTS, stats
cli.py                CLI commands (chat, trace, lint, digest, ingest, etc.)
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

- **Continuous conversation flow.** All tools (read + write) available during chat. Agent proposes changes in natural language, writes after user approval. No separate Plan/execute phases.
- **Schema always in context.** `PROMPT_SCHEMA_CORE` (~800 tokens) is always in the system prompt. Agent always knows wiki rules.
- **Primitive tools.** 9 tools: 5 read (read_page, search, get_backlinks, list_pages, fetch_url) + 4 write (write_page, append_section, update_frontmatter, add_related_link). Legacy high-semantic tools kept as handlers for backward compat.
- **Transcript is append-only.** Context compression only in the query view (`_build_messages_for_query`).
- **Tool results are tiered.** Full → preview → placeholder based on age. See `_apply_tool_result_tiers()`.
- **Git batching.** All writes in one chat turn → one git commit via `_operation_depth`.
- **Safety in code, not prompt.** Read-before-write, synthesis link requirements, unattended restrictions → `policy.py`.

### Where to look

| Task | Primary file(s) |
|------|-----------------|
| Add/change a tool | `tools/definitions.py` (schema + handler), `tools/policy.py` (tier) |
| Change what context LLM sees | `agent.py` → `_build_messages_for_query()` |
| Change agent behavior/personality | `agent.py` → `SYSTEM_PROMPT` (top of file) |
| Change schema rules in prompt | `agent.py` → `PROMPT_SCHEMA_CORE` |
| Change write permission rules | `tools/policy.py` → `check_pre_dispatch()` |
| Change vault file operations | `vault.py` |
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

## Gotchas

- `$HOME/.local/bin` must be on PATH for `nw` CLI
- `nw lint` and `nw digest` need API keys (they run the agent loop)
- Tests use `auto_git=False` — the real vault auto-initializes a Git repo
- 9 tools in `TOOL_SCHEMAS` — `tools/definitions.py` is truth
- `CHAT_TOOL_SCHEMAS` == `TOOL_SCHEMAS` in V2 (all tools available during chat)
- Legacy tools (capture, organize, restructure, ingest, survey_topic) kept as handlers for backward compat
- DESIGN.md is product philosophy (~1000 lines), not code architecture
