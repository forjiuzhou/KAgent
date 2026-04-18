# AGENTS.md

Instructions for coding agents (Cursor, Codex, Claude Code, etc.) working on NoteWeaver.

## Quick Reference

```bash
pip install -e .                     # install in dev mode
pip install -e ".[all]"              # all optional deps (anthropic, telegram, feishu)
python3 -m pytest tests/ -v          # run all tests (~10s, no API keys needed)
```

Offline CLI commands (no API key): `nw init`, `nw status`, `nw rebuild-index`, `nw import <path>`, `nw trace`, `nw help`

LLM-powered commands (need API key): `nw chat`, `nw lint`, `nw digest`, `nw ingest <url>`, `nw import-sources`, `nw gateway`

## Repo Layout

```
src/noteweaver/            Python package (~9300 LOC)
├── agent/                 Core agent loop, system prompt, context assembly
│   ├── core.py              KnowledgeAgent class — chat(), context, memory, planning
│   └── prompts.py           System prompt constants + provider factory
├── vault/                 On-disk knowledge vault
│   ├── core.py              Vault class — file I/O, init, tags, resolve
│   ├── seeds.py             Seed templates (INITIAL_SCHEMA etc.)
│   ├── git.py               Git init/commit, OperationContext
│   ├── indexing.py          Search index, backlinks, FTS
│   ├── audit.py             Health audit, metrics, tag similarity
│   ├── context.py           LLM-facing context builders
│   └── organize.py          Import organization, rebuild_index
├── tools/                 Tool schemas, handlers, dispatch
│   ├── schemas.py           12 tool schemas (TOOL_SCHEMAS)
│   ├── handlers_read.py     6 read tool handlers
│   ├── handlers_write.py    5 write tool handlers
│   ├── legacy.py            Deprecated legacy handlers
│   ├── dispatch.py          TOOL_HANDLERS registry + dispatch_tool()
│   ├── definitions.py       Re-export layer (backward compat)
│   └── policy.py            Pre-dispatch safety gates
├── skills/                Multi-step workflows above tools
├── adapters/              LLM providers (OpenAI, Anthropic) + IM (Telegram)
├── cli.py                 CLI commands + main()
├── session.py             Shared session logic (agent construction, finalization)
├── gateway.py             Long-running Telegram gateway + cron
├── job.py                 Background job system (contract, progress, worker prompt)
├── trace.py               Structured trace for observability
├── config.py              Config from .meta/config.yaml + env vars
├── plan.py                Plan data model (legacy)
├── search.py              SQLite FTS5 full-text search
├── backlinks.py           Wiki-link extraction + backlink index
├── frontmatter.py         YAML frontmatter validation
└── constants.py           Shared constants

tests/                     ~720 tests, all offline, ~10s
references/                Read-only reference projects (DO NOT MODIFY)
deploy/                    Docker/systemd deployment scripts
DESIGN.md                  Product philosophy (~1000 lines, not code architecture)
```

## Architecture (5-line summary)

**Primary interface: Gateway** (Telegram) — user messages go to `agent.chat()`.
CLI is secondary. All features work through `agent.chat()`.
**Three layers**: 10 primitive tools + create_job + spawn_subagent → skills (multi-step workflows) → chat.
Agent proposes changes in natural language → user approves → agent writes.
Schema from `.schema/` is always in the system prompt.

## Boundary Rules

- **`references/` is read-only.** Contains source code of successful projects for architecture reference. **Never modify files under `references/`.** Two projects:
  - `references/claude-code/` — Claude Code source snapshot (TypeScript, ~1900 files)
  - `references/openclaw/` — OpenClaw project (TypeScript, IM agent framework)

- **Progressive disclosure for docs**: only read the AGENTS.md for the boundary you are touching.
  - Touching `vault/` → read `src/noteweaver/vault/AGENTS.md`
  - Touching `agent/` → read `src/noteweaver/agent/AGENTS.md`
  - Touching `tools/` → read `src/noteweaver/tools/AGENTS.md`
  - For everything else → this file is sufficient.

## Where to Look

| Task | Primary file(s) |
|------|-----------------|
| Add/change a tool | `tools/schemas.py` + `tools/handlers_read.py` or `handlers_write.py` + `tools/dispatch.py` + `tools/policy.py` |
| Add a skill | `skills/` — new file + register in `skills/__init__.py` |
| Change what context LLM sees | `agent/core.py` → `_build_messages_for_query()` |
| Change agent behavior | `agent/prompts.py` → prompt constants |
| Change write permission rules | `tools/policy.py` → `check_pre_dispatch()` |
| Change vault file operations | `vault/core.py` |
| Change session finalization | `session.py` |
| Add a CLI command | `cli.py` → add `cmd_*()` + routing in `main()` |
| Change vault audit | `vault/audit.py` |
| Change LLM context builders | `vault/context.py` |
| Change git behavior | `vault/git.py` |
| Change job system | `job.py` (contracts, progress) + `gateway.py` (cron execution) + `tools/handlers_write.py` (`handle_create_job`) |

## Test Patterns

All tests use `auto_git=False` vaults and mocked LLM providers:

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

**Adding a new tool**: schema in `tools/schemas.py` → handler in `handlers_read/write.py` → register in `tools/dispatch.py` → tier in `tools/policy.py` → tests in `test_tools.py`

**Adding a new skill**: new file in `skills/` → register in `skills/__init__.py` → tests in `test_skills.py`

## Key Design Facts

- **Continuous conversation flow.** All tools (read + write) available during chat. No separate plan/execute phases.
- **Schema always in context.** `.schema/schema.md` injected into system prompt.
- **Transcript is append-only.** `self.messages` never mutated. Compression only in query view.
- **Tool results are tiered.** Full → preview → placeholder based on age.
- **Git batching.** All writes in one chat turn → one git commit via `_operation_depth`.
- **Safety in code, not prompt.** Read-before-write etc. enforced in `tools/policy.py`.

## Gotchas

- `$HOME/.local/bin` must be on PATH for `nw` CLI
- `nw lint` and `nw digest` need API keys (they run the agent loop)
- Tests use `auto_git=False` — real vault auto-inits Git
- 12 tools in TOOL_SCHEMAS (6 read + 4 write + create_job + spawn_subagent) — `tools/schemas.py` is truth
- Legacy tools (capture, organize, restructure, ingest, survey_topic) in `tools/legacy.py` — 5 deprecated handlers
- Skills are in `skills/` — use `agent.run_skill("name")` or CLI `nw import-sources` / `nw lint`
- DESIGN.md is product philosophy, not code architecture
