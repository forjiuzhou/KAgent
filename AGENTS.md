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

LLM-powered commands (need API key): `nw chat`, `nw lint`, `nw digest`, `nw ingest <url>`, `nw import-sources`, `nw gateway`

## Architecture Map

NoteWeaver is a single Python package at `src/noteweaver/` (~5700 LOC). No frameworks — just OpenAI/Anthropic SDKs, tool calling, and file I/O.

**Primary interface: Gateway** (long-running chat agent via Telegram/IM). CLI is a secondary interface for power users. All features must work through `agent.chat()` — gateway just passes user messages to it.

### Data flow

```
User input (Telegram / CLI)
  → KnowledgeAgent.chat()             ← continuous conversation
    → _build_messages_for_query()     ← context assembly (schema always included)
    → LLMProvider.chat_completion()   ← ALL tools (read + write)
    → dispatch_tool()                 ← execute any tool
    → if LLM emits <<skill:name>>:
        skill.prepare() → execute()   ← multi-step workflow via tools
    → loop up to 25 steps
  → Agent proposes in natural language → user approves → agent writes
  → save_transcript() + save_trace() + save_session_memory()
```

### Module map

| Module | LOC | Role | Key types/functions |
|--------|-----|------|---------------------|
| `agent.py` | ~1200 | Agent loop, context assembly, system prompt, run_skill() | `KnowledgeAgent`, `chat()`, `run_skill()`, `SYSTEM_PROMPT` |
| `session.py` | ~280 | Shared session logic (agent construction, finalization, journal, digest prompts) — used by both cli.py and gateway.py | `make_agent()`, `finalize_session()`, `build_digest_prompt()` |
| `plan.py` | ~200 | Plan data model (legacy, kept for backward compat) | `Plan`, `PlanStatus`, `PlanStore` |
| `tools/definitions.py` | ~1100 | 9 tool schemas + legacy handlers + dispatch | `TOOL_SCHEMAS`, `TOOL_HANDLERS`, `dispatch_tool()` |
| `skills/__init__.py` | ~40 | Skill registry | `get_skill()`, `list_skills()`, `SKILL_REGISTRY` |
| `skills/base.py` | ~100 | Skill ABC + context/result types | `Skill`, `SkillContext`, `SkillResult` |
| `skills/import_sources.py` | ~130 | Bulk-import source files into wiki | `ImportSources` |
| `skills/organize_wiki.py` | ~120 | Audit + remediate wiki health | `OrganizeWiki` |
| `vault.py` | 882 | On-disk vault: reads, writes, git batching, FTS, stats | `Vault`, `write_file()`, `read_file()`, `init()`, `rebuild_search_index()` |
| `cli.py` | ~500 | CLI commands, interactive plan approval, UI | `cmd_chat()`, `cmd_trace()`, `_approve_and_execute()`, `main()` |
| `tools/policy.py` | ~300 | Pre-dispatch safety gates (read-before-write, etc.) | `check_pre_dispatch()`, `PolicyContext` |
| `trace.py` | 334 | Structured trace for agent observability | `TraceCollector`, `record_tool_call()`, `render_human()` |
| `gateway.py` | ~200 | Long-running gateway (Telegram + cron digest/lint) | `run_gateway()`, `Gateway` |
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
| What tools the agent has | `tools/definitions.py` — `TOOL_SCHEMAS` (all tools, single set) |
| Add a multi-step workflow | `skills/` — subclass `Skill`, register in `__init__.py` |
| How the agent converses and writes | `agent.py` — `SYSTEM_PROMPT`, `chat()` |
| What schema rules the agent knows | `agent.py` — `.schema/schema.md` injection |
| What the agent is allowed to do | `tools/policy.py` — `check_pre_dispatch()` |
| What context the LLM sees | `agent.py` — `_build_messages_for_query()` |
| How long conversations are compressed | `agent.py` — `_update_session_summary()` + `_apply_tool_result_tiers()` |
| How files are read/written on disk | `vault.py` — `read_file()`, `write_file()` |
| How git commits happen | `vault.py` — `_end_operation()`, `_git_commit()` |
| How search works | `search.py` — `SearchIndex` (SQLite FTS5) |
| The CLI command routing | `cli.py` — `main()` at the bottom |
| How sessions are saved on exit | `session.py` — `finalize_session()` (shared by CLI + Gateway) |
| How agents are constructed | `session.py` — `make_agent()` (shared by CLI + Gateway) |
| Agent run traces (observability) | `trace.py` — `TraceCollector`; CLI: `nw trace` |
| How Anthropic messages are shaped | `adapters/anthropic_provider.py` — `_to_anthropic_messages()` |
| Gateway cron logic | `gateway.py` — `_periodic_tasks()` |

### Key design decisions to know

1. **Continuous conversation flow.** All tools (read + write) available during chat. Agent proposes changes in natural language, writes after user approval. No separate Plan/execute phases.

2. **Schema always in context.** `PROMPT_SCHEMA_CORE` (~800 tokens) is always in the system prompt. Agent always knows wiki rules without needing to read schema.md.

3. **Three layers: tools → skills → chat.** 9 primitive tools (5 read + 4 write) are the atomic operations. Skills (`skills/`) are multi-step workflows triggered by the LLM when it recognises skill-level intent — the LLM emits `<<skill:name>>` markers, the chat loop intercepts and executes. Gateway is the primary interface; CLI wraps `agent.chat()` or `agent.run_skill()`. Legacy handlers (capture, organize, etc.) are deprecated in favor of skills.

4. **Transcript is append-only.** `self.messages` is never mutated. Context compression happens only in the query view (`_build_messages_for_query()`).

5. **Tool results are tiered.** Recent tool results stay full, older ones get previewed, stale ones become placeholders. See `_apply_tool_result_tiers()`.

6. **Git batching.** All writes within a single chat turn are batched into one git commit via `_operation_depth` tracking in `Vault`.

7. **Policy enforces safety rules in code, not in the prompt.** Read-before-write, synthesis link requirements, unattended mode restrictions — all in `policy.py`.

## Test Map

Tests are in `tests/`. All use pytest, create temp vaults with `auto_git=False`, and mock the LLM provider. No API keys needed.

| Test file | Tests | What it covers |
|-----------|-------|----------------|
| `test_skills.py` | Skill registry, prepare/execute/dry-run, agent.run_skill() |
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
| `test_session.py` | Shared session logic (make_agent, finalize, digest prompts) |

**Pattern for adding a new tool:**
1. Add schema to `TOOL_SCHEMAS` and handler to `TOOL_HANDLERS` in `tools/definitions.py`
2. If it's a read tool, also add to `_OBSERVATION_TOOL_NAMES`
3. Add policy tier in `TOOL_TIERS` in `tools/policy.py`
4. Add tests in `test_tools.py` (or a new file for complex tools)
5. If the tool writes, add policy tests in `test_attended_policy.py`

**Pattern for adding a new skill:**
1. New file in `skills/` subclassing `Skill` — implement `name`, `description`, `prepare()`, `execute()`
2. Register in `skills/__init__.py` → `SKILL_REGISTRY`
3. Add tests in `test_skills.py`
4. Add CLI command in `cli.py` if it should be user-invocable

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
- The agent has 9 tools (5 read + 4 write) — `tools/definitions.py` is the source of truth
- All tools available during chat (`CHAT_TOOL_SCHEMAS` == `TOOL_SCHEMAS` in V2)
- Legacy tools (capture, organize, restructure, ingest, survey_topic) still dispatchable but not in schemas — deprecated in favor of skills
- Skills (`skills/`) are the proper way to do multi-step workflows; use `agent.run_skill()` or CLI `nw import-sources` / `nw lint`
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
