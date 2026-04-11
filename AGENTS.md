# AGENTS.md

Instructions for coding agents (Cursor, Codex, Claude Code, etc.) working on the NoteWeaver codebase.

## Quick Reference

```
python3 -m pytest tests/ -v          # run all tests (~0.5s, no API keys needed)
pip install -e .                     # install in dev mode
pip install -e ".[anthropic]"        # include Anthropic support
pip install -e ".[all]"              # all optional deps
```

Offline CLI commands (no API key): `nw init`, `nw status`, `nw rebuild-index`, `nw import <path>`, `nw trace`, `nw help`

LLM-powered commands (need API key): `nw chat`, `nw lint`, `nw digest`, `nw ingest <url>`, `nw gateway`

## Architecture Map

NoteWeaver is a single Python package at `src/noteweaver/` (~5700 LOC). There are no frameworks — just OpenAI/Anthropic SDKs, tool calling, and file I/O.

### Data flow

```
User input (CLI / Telegram / Gateway)
  → KnowledgeAgent.chat()             ← EXPLORE + PROPOSE phase
    → _build_messages_for_query()     ← context assembly
    → LLMProvider.chat_completion()   ← only read tools + submit_plan
    → if submit_plan: create Plan → persist to .meta/plans/
    → else: dispatch_tool()           ← execute read tool
    → loop up to 25 steps
  → Caller checks pending plans:
    → incremental: auto-approve → execute_plan()
    → structural: present to user → approve → execute_plan()
  → KnowledgeAgent.execute_plan()     ← EXECUTE phase (separate LLM call)
    → load Plan, check staleness
    → LLMProvider.chat_completion()   ← write tools available
    → dispatch_tool() for each write
    → _ensure_progressive_disclosure()
  → save_transcript() + save_trace() + save_session_memory()
```

### Module map

| Module | LOC | Role | Key types/functions |
|--------|-----|------|---------------------|
| `agent.py` | ~1600 | Agent loop, context assembly, system prompt, execute_plan | `KnowledgeAgent`, `chat()`, `execute_plan()`, `SYSTEM_PROMPT`, `EXECUTE_PLAN_PROMPT` |
| `plan.py` | ~200 | Plan data model, persistence, staleness check | `Plan`, `PlanStatus`, `PlanStore`, `generate_plan_id()` |
| `tools/definitions.py` | ~1400 | 11 tool schemas + submit_plan + handlers + dispatch | `TOOL_SCHEMAS`, `CHAT_TOOL_SCHEMAS`, `TOOL_HANDLERS`, `dispatch_tool()` |
| `vault.py` | 882 | On-disk vault: reads, writes, git batching, FTS, stats | `Vault`, `write_file()`, `read_file()`, `init()`, `rebuild_search_index()` |
| `cli.py` | ~700 | CLI commands, session finalization, journal generation | `cmd_chat()`, `cmd_trace()`, `_finalize_session()`, `main()` |
| `tools/policy.py` | ~350 | Pre-dispatch policy, change_type classification | `check_pre_dispatch()`, `classify_change_type()`, `PolicyContext` |
| `trace.py` | 334 | Structured trace for agent observability | `TraceCollector`, `record_tool_call()`, `render_human()` |
| `gateway.py` | 243 | Long-running gateway (Telegram + cron digest/lint) | `run_gateway()` |
| `adapters/anthropic_provider.py` | 206 | Anthropic Messages API adapter | `AnthropicProvider` |
| `adapters/retry.py` | 127 | Exponential backoff for LLM calls | `with_retry()` |
| `config.py` | 127 | Config from `.meta/config.yaml` + env vars | `Config.load()` |
| `adapters/telegram_adapter.py` | 120 | Telegram bot adapter | `TelegramAdapter` |
| `search.py` | 115 | SQLite FTS5 full-text search | `SearchIndex` |
| `frontmatter.py` | 108 | YAML frontmatter validation | `validate_frontmatter()`, `extract_frontmatter()` |
| `backlinks.py` | 106 | Wiki-link extraction and backlink index | `BacklinkIndex` |
| `adapters/provider.py` | 54 | Abstract LLM provider interface | `LLMProvider`, `CompletionResult` |
| `adapters/openai_provider.py` | 54 | OpenAI SDK adapter | `OpenAIProvider` |
| `adapters/base.py` | 40 | Abstract IM adapter interface | `IMAdapter` |

### Where things happen

| "I want to change..." | Look at... |
|------------------------|------------|
| What tools the agent can use during chat | `tools/definitions.py` — `CHAT_TOOL_SCHEMAS` (read + submit_plan) |
| What tools the agent can use during execution | `tools/definitions.py` — `TOOL_SCHEMAS` (all write tools) |
| How the agent explores and proposes | `agent.py` — `SYSTEM_PROMPT`, `chat()` |
| How plans are executed | `agent.py` — `EXECUTE_PLAN_PROMPT`, `execute_plan()` |
| Plan data model and persistence | `plan.py` — `Plan`, `PlanStore` |
| Incremental vs structural classification | `tools/policy.py` — `classify_change_type()` |
| What the agent is allowed to do | `tools/policy.py` — `check_pre_dispatch()` |
| What context the LLM sees | `agent.py` — `_build_messages_for_query()` |
| How long conversations are compressed | `agent.py` — `_update_session_summary()` + `_apply_tool_result_tiers()` |
| How files are read/written on disk | `vault.py` — `read_file()`, `write_file()` |
| How git commits happen | `vault.py` — `_end_operation()`, `_git_commit()` |
| How search works | `search.py` — `SearchIndex` (SQLite FTS5) |
| The CLI command routing | `cli.py` — `main()` at the bottom |
| How sessions are saved on exit | `cli.py` — `_finalize_session()` |
| Agent run traces (observability) | `trace.py` — `TraceCollector`; CLI: `nw trace` |
| How Anthropic messages are shaped | `adapters/anthropic_provider.py` — `_to_anthropic_messages()` |
| Gateway cron logic | `gateway.py` — `_periodic_tasks()` |

### Key design decisions to know

1. **Explore → Propose → Execute.** Chat phase only uses read tools + `submit_plan`. Plans are natural-language proposals stored as first-class objects in `.meta/plans/`. Execution is a separate LLM call after user approval. The model is never "tricked" with fake tool results.

2. **Plans are classified as incremental or structural.** The model suggests a classification, but `classify_change_type()` in policy.py can override it (e.g. create intent → always structural). Incremental plans auto-execute with notification; structural plans require user approval.

3. **Transcript is append-only.** `self.messages` is never mutated. Context compression happens only in the query view (`_build_messages_for_query()`).

4. **Tool results are tiered.** Recent tool results stay full, older ones get previewed, stale ones become placeholders. See `_apply_tool_result_tiers()`.

5. **Git batching.** All writes within a single `execute_plan()` call are batched into one git commit via `_operation_depth` tracking in `Vault`.

6. **Policy enforces safety rules in code, not in the prompt.** Read-before-write, dedup checks, synthesis link requirements, unattended mode restrictions — all in `policy.py`, not in `SYSTEM_PROMPT`.

## Test Map

Tests are in `tests/`. All use pytest, create temp vaults with `auto_git=False`, and mock the LLM provider. No API keys needed.

| Test file | Tests | What it covers |
|-----------|-------|----------------|
| `test_vault.py` | Vault init, read/write, directory structure, stats, import |
| `test_tools.py` | Tool handler execution (dispatch_tool for each tool) |
| `test_fine_grained_tools.py` | append_section, append_to_section, update_frontmatter, add_related_link |
| `test_plan.py` | Plan data model, PlanStore CRUD, staleness check, classify_change_type |
| `test_policy.py` | Policy context tracking, tier classification, pre-dispatch checks |
| `test_attended_policy.py` | Attended-mode gates: read-before-write, dedup, synthesis links |
| `test_content_gates.py` | Content-layer write restrictions (unattended, source protection) |
| `test_context_management.py` | Transcript save/load, session memory, journal generation |
| `test_prompt_engine.py` | System prompt structure, size budgets, content verification |
| `test_provider.py` | Provider factory, message shaping, Anthropic conversion |
| `test_integration.py` | End-to-end agent flows with mocked LLM |
| `test_trace.py` | Trace collector, JSONL persistence, rendering, agent integration |
| `test_retry_and_journal.py` | Retry logic, journal summary extraction |
| `test_search.py` | FTS5 index build, search queries |
| `test_backlinks.py` | Wiki-link extraction, backlink graph |
| `test_frontmatter.py` | YAML frontmatter validation rules |
| `test_workset.py` | Session memory workset merging |
| `test_promote_insight.py` | Journal → wiki promotion tool |
| `test_git.py` | Git auto-commit integration |
| `test_telegram_adapter.py` | Telegram adapter basics |

**Pattern for adding a new tool:**
1. Add schema to `TOOL_SCHEMAS` and handler to `TOOL_HANDLERS` in `tools/definitions.py`
2. If it's a read tool, also add to `_OBSERVATION_TOOL_NAMES` so it appears in `CHAT_TOOL_SCHEMAS`
3. Add policy tier in `TOOL_TIERS` in `tools/policy.py`
4. Add tests in `test_tools.py` (or a new file for complex tools)
5. If the tool writes, add policy tests in `test_attended_policy.py`

**Pattern for adding a test:**
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

## Gotchas

- `$HOME/.local/bin` must be on `PATH` for the `nw` CLI entry point to work
- `nw lint` and `nw digest` use the LLM agent loop — they need an API key even though they sound like static checks
- The vault auto-initializes a Git repo on `nw init`; tests use `auto_git=False` to skip this
- The agent has 11 execution tools + `submit_plan` — `tools/definitions.py` is the source of truth
- During chat, only `CHAT_TOOL_SCHEMAS` (read + submit_plan) are available; write tools are only available during `execute_plan()`
- Feishu adapter is referenced in CLI help but not implemented; only Telegram works
- DESIGN.md is a product design document (~1000 lines), not a code architecture doc — don't rely on it for code navigation
- No project-level linter is configured (no ruff/flake8/mypy/pylint in `pyproject.toml`)

## Trace / Observability

Agent runs produce structured traces at `.meta/traces/*.trace.jsonl`. Use `nw trace` to list and view them. Each trace records:

- **context_assembly**: what the LLM saw (system prompt size, session memory, summary state, token estimates)
- **tool_call**: every tool dispatch (name, args, policy verdict, duration, result preview, errors)
- **turn_end**: step count, total duration, max-steps detection

To analyze a trace with an external coding agent:
```bash
nw trace --raw <file>   # outputs JSONL to stdout — paste into Claude/Codex conversation
```
