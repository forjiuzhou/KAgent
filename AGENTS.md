# AGENTS.md

## Cursor Cloud specific instructions

### Overview

NoteWeaver is a Python CLI application (`nw`) for AI-powered knowledge management. All code lives on the `cursor/c-bcfb` branch (the `main` branch only contains a placeholder README).

### Running the application

- Offline commands (no API key needed): `nw init`, `nw status`, `nw rebuild-index`, `nw help`
- LLM-powered commands: `nw chat`, `nw lint`, `nw ingest <url>`
- LLM commands require one of: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `ANTHROPIC_AUTH_TOKEN`
- The provided `OPENAI_API_KEY` is a Poe proxy key (`sk-poe-` prefix) — you **must** also set `OPENAI_BASE_URL=https://api.poe.com/v1` for it to work
- Set vault path via `NW_VAULT` env var, or run from inside a vault directory
- See `README.md` for all CLI commands and environment variables

### Testing

- Run all tests: `python3 -m pytest tests/ -v`
- Tests are fast (~0.3s) and require no external services or API keys

### Linting

- No project-level linter is configured (no ruff/flake8/mypy/pylint in `pyproject.toml`)

### Key gotchas

- `$HOME/.local/bin` must be on `PATH` for the `nw` CLI entry point to work (pip user installs go there)
- `nw lint` uses the LLM agent loop — it requires an API key even though it sounds like a static check
- The vault auto-initializes a Git repo on `nw init`; tests create temp vaults with Git, so `git` must be installed
- The Poe API key requires `OPENAI_BASE_URL=https://api.poe.com/v1`; without it you get a 401 error from OpenAI's default endpoint
