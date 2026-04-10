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

### Core loop

```
KnowledgeAgent.chat(user_message)
  → _build_messages_for_query()     # assemble what LLM sees
  → LLMProvider.chat_completion()   # call OpenAI or Anthropic
  → for each tool_call:
      check_pre_dispatch()          # policy gate
      dispatch_tool()               # execute handler
      append result to messages
  → loop up to 25 steps
  → _end_operation()                # git commit if dirty
```

### File → Responsibility

```
agent.py              Agent loop, system prompt, context assembly, session memory
tools/definitions.py  18 tool schemas + handlers + dispatch_tool()
tools/policy.py       Pre-dispatch policy: attended/unattended, write gates
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

- **No explicit planning layer.** Routing is prompt-based ("Three Modes" in SYSTEM_PROMPT). No planner, no action selector. Policy is post-hoc.
- **Transcript is append-only.** Context compression only in the query view (`_build_messages_for_query`).
- **Tool results are tiered.** Full → preview → placeholder based on age. See `_apply_tool_result_tiers()`.
- **Git batching.** All writes in one `chat()` call → one git commit via `_operation_depth`.
- **Safety in code, not prompt.** Read-before-write, dedup, synthesis links, unattended restrictions → `policy.py`.

### Where to look

| Task | Primary file(s) |
|------|-----------------|
| Add/change a tool | `tools/definitions.py` (schema + handler), `tools/policy.py` (tier) |
| Change what context LLM sees | `agent.py` → `_build_messages_for_query()` |
| Change agent behavior/personality | `agent.py` → `SYSTEM_PROMPT` (top of file) |
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
2. Risk tier in `TOOL_TIERS` (`tools/policy.py`)
3. Tests in `test_tools.py`; policy tests in `test_attended_policy.py` if it writes

## Gotchas

- `$HOME/.local/bin` must be on PATH for `nw` CLI
- `nw lint` and `nw digest` need API keys (they run the agent loop)
- Tests use `auto_git=False` — the real vault auto-initializes a Git repo
- 18 tools exist (not 10 as some docs say) — `tools/definitions.py` is truth
- DESIGN.md is product philosophy (~1000 lines), not code architecture
