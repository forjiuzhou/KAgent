# AGENTS.md

## Cursor Cloud specific instructions

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
- 315 tests covering vault operations, frontmatter validation, git integration, tool dispatch, search, backlinks, providers, policy, context management, prompt engine, and more
- Tests are fast (~0.5s) and require no external services or API keys

### Linting

- No project-level linter is configured (no ruff/flake8/mypy/pylint in `pyproject.toml`)

### Key gotchas

- `$HOME/.local/bin` must be on `PATH` for the `nw` CLI entry point to work (pip user installs go there)
- `nw lint` and `nw digest` use the LLM agent loop — they require an API key even though they sound like static checks
- The vault auto-initializes a Git repo on `nw init`; tests create temp vaults with Git, so `git` must be installed
- The agent supports 18 tools (not 10 as mentioned in some design docs) — see `src/noteweaver/tools/definitions.py` for the full list
- Feishu adapter is referenced in CLI help but not yet implemented; only Telegram is available
