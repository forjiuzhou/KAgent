# AGENTS.md

## Cursor Cloud specific instructions

### Overview

NoteWeaver is a Python CLI application (`nw`) for AI-powered knowledge management. All code lives on the `cursor/c-bcfb` branch (the `main` branch only contains a placeholder README).

### Running the application

- Offline commands (no API key needed): `nw init`, `nw status`, `nw rebuild-index`, `nw help`
- LLM-powered commands (require `OPENAI_API_KEY`): `nw chat`, `nw lint`, `nw ingest <url>`
- Set vault path via `NW_VAULT` env var, or run from inside a vault directory
- See `README.md` for all CLI commands and environment variables

### Testing

- Run all tests: `python3 -m pytest tests/ -v`
- 69 tests covering vault operations, frontmatter validation, git integration, and tool dispatch
- Tests are fast (~0.3s) and require no external services or API keys

### Linting

- No project-level linter is configured (no ruff/flake8/mypy/pylint in `pyproject.toml`)

### Key gotchas

- `$HOME/.local/bin` must be on `PATH` for the `nw` CLI entry point to work (pip user installs go there)
- `nw lint` uses the LLM agent loop — it requires `OPENAI_API_KEY` even though it sounds like a static check
- The vault auto-initializes a Git repo on `nw init`; tests create temp vaults with Git, so `git` must be installed
