# AGENTS.md

## Instructions for AI coding agents (Cursor, Codex, Claude Code)

### Overview

NoteWeaver is a Python CLI application (`nw`) for AI-powered knowledge management. The main codebase lives on the `main` branch.

### Running the application

- Offline commands (no API key needed): `nw init`, `nw status`, `nw rebuild-index`, `nw import <path>`, `nw help`
- LLM-powered commands (require API key): `nw chat`, `nw lint`, `nw digest`, `nw ingest <url>`, `nw gateway`
- Supports both OpenAI and Anthropic providers — set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`
- Set vault path via `NW_VAULT` env var, or run from inside a vault directory
- See `README.md` for all CLI commands and environment variables

### Testing

- Run all tests: `python3 -m pytest tests/ -v`
- 315+ tests covering vault operations, frontmatter validation, git integration, tool dispatch, search, backlinks, providers, policy, context management, prompt engine, and more
- Tests are fast (~0.5s) and require no external services or API keys
- After any code change, run the full test suite to catch regressions

### Linting

- No project-level linter is configured (no ruff/flake8/mypy/pylint in `pyproject.toml`)

### Key gotchas

- `$HOME/.local/bin` must be on `PATH` for the `nw` CLI entry point to work (pip user installs go there)
- `nw lint` and `nw digest` use the LLM agent loop — they require an API key even though they sound like static checks
- The vault auto-initializes a Git repo on `nw init`; tests create temp vaults with Git, so `git` must be installed
- The agent supports 18 tools (not 10 as mentioned in some design docs) — see `src/noteweaver/tools/definitions.py` for the full list
- Feishu adapter is referenced in CLI help but not yet implemented; only Telegram is available

### Architecture quick reference

```
src/noteweaver/
├── cli.py                  # CLI entry point (Click-style commands)
├── agent.py                # KnowledgeAgent: LLM loop, system prompt, context management
├── vault.py                # Vault: file I/O, git, import, index, search infra
├── config.py               # Config loading (env vars + .meta/config.yaml)
├── frontmatter.py          # YAML frontmatter validation (hard constraints)
├── search.py               # FTS5 search index
├── backlinks.py            # [[wiki-link]] tracking
├── gateway.py              # Telegram bot gateway
├── adapters/
│   ├── provider.py         # LLMProvider ABC + CompletionResult/ToolCall
│   ├── openai_provider.py  # OpenAI API adapter
│   ├── anthropic_provider.py  # Anthropic API adapter
│   ├── retry.py            # Exponential backoff retry wrapper
│   └── telegram_adapter.py # Telegram message adapter
└── tools/
    ├── definitions.py      # 18 tool schemas (TOOL_SCHEMAS) + handlers + dispatch
    └── policy.py           # Pre-dispatch policy checks (attended/unattended, write gates)
```

### How to observe NoteWeaver in action

To verify changes end-to-end, create a temporary vault and run offline commands:

```bash
# Install in dev mode
pip install -e .

# Create a test vault
mkdir /tmp/test-vault && cd /tmp/test-vault
nw init

# Import some markdown files (no API key needed)
nw import /path/to/markdown/files

# Check vault status
nw status

# Rebuild the search index
nw rebuild-index
```

For LLM-powered commands, set an API key and use `nw chat`:

```bash
export OPENAI_API_KEY=sk-...   # or ANTHROPIC_API_KEY
export NW_VAULT=/tmp/test-vault
nw chat
```

### Design principles for contributors

- **Tools are atomic**: 18 low-level tools (read_page, write_page, etc.) — intelligence lives in the LLM, not tool names
- **Hard constraints in code**: frontmatter validation, sources/ immutability, canonical requires sources — enforced at write time, not just in prompts
- **Policy layer**: `tools/policy.py` gates writes by target classification (runtime/structure/journal/content/source) and attended/unattended mode
- **Context management**: 3-tier tool result cleanup + session summary compression — see `_build_messages_for_query()` and `_apply_tool_result_tiers()` in `agent.py`
- **Auto-continue**: the agent loop detects when models (especially GPT) stop mid-operation to ask "should I continue?" and auto-injects continuation prompts — see `_looks_like_mid_operation_pause()` in `agent.py`
