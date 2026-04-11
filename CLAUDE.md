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

### Core loop (Explore → Propose → Execute)

```
KnowledgeAgent.chat(user_message)        # EXPLORE + PROPOSE
  → _build_messages_for_query()          # assemble what LLM sees
  → LLMProvider.chat_completion()        # only observation tools + submit_plan
  → for each tool_call:
      if submit_plan: create Plan object → persist to .meta/plans/
      else: dispatch_tool()              # execute read tool
  → loop up to 25 steps

Caller (CLI/Gateway):
  → incremental plans: auto-approve → execute_plan()
  → structural plans: present to user → approve → execute_plan()

KnowledgeAgent.execute_plan(plan_id)     # EXECUTE (separate LLM call)
  → load Plan from .meta/plans/
  → check staleness (target mtimes)
  → new LLM call with EXECUTE_PLAN_PROMPT + write tools
  → dispatch_tool() for each write tool call
  → _ensure_progressive_disclosure()
```

### File → Responsibility

```
agent.py              Agent loop, system prompt, context assembly, session memory,
                      execute_plan() for LLM-driven plan execution
plan.py               Plan data model (Plan, PlanStatus, PlanStore),
                      persistence in .meta/plans/, staleness check, legacy migration
tools/definitions.py  11 tool schemas + submit_plan + handlers + dispatch_tool()
                      CHAT_TOOL_SCHEMAS (read + submit_plan) vs TOOL_SCHEMAS (all)
tools/policy.py       Pre-dispatch policy, change_type classification
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

- **Explore → Propose → Execute.** Chat phase only has read tools + `submit_plan`. Plans are natural-language proposals persisted as first-class objects. Execution is a separate LLM call after user approval.
- **Plans are persistent.** Stored in `.meta/plans/<plan_id>.json` with id, status, timestamps, staleness tracking. Support incremental (auto-execute) and structural (require approval) change types.
- **System verifies change_type.** Model suggests incremental/structural, but `classify_change_type()` in policy.py can override (e.g. create intent → always structural).
- **Transcript is append-only.** Context compression only in the query view (`_build_messages_for_query`).
- **Tool results are tiered.** Full → preview → placeholder based on age. See `_apply_tool_result_tiers()`.
- **Git batching.** All writes in one `execute_plan()` call → one git commit via `_operation_depth`.
- **Safety in code, not prompt.** Read-before-write, dedup, synthesis links, unattended restrictions → `policy.py`.

### Where to look

| Task | Primary file(s) |
|------|-----------------|
| Add/change a tool | `tools/definitions.py` (schema + handler), `tools/policy.py` (tier) |
| Change what context LLM sees | `agent.py` → `_build_messages_for_query()` |
| Change agent behavior/personality | `agent.py` → `SYSTEM_PROMPT` (top of file) |
| Change execution behavior | `agent.py` → `EXECUTE_PLAN_PROMPT`, `execute_plan()` |
| Change write permission rules | `tools/policy.py` → `check_pre_dispatch()` |
| Change plan data model | `plan.py` → `Plan`, `PlanStore` |
| Change incremental/structural classification | `tools/policy.py` → `classify_change_type()` |
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
- 11 execution tools + submit_plan exist — `tools/definitions.py` is truth
- `CHAT_TOOL_SCHEMAS` (read + submit_plan) is used during chat; `TOOL_SCHEMAS` (all) during execution
- DESIGN.md is product philosophy (~1000 lines), not code architecture
