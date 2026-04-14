# vault/ — On-disk Knowledge Vault

The vault layer manages all file I/O, search indexing, health auditing, and git integration. External code interacts only through `from noteweaver.vault import Vault`.

## Module Responsibilities

| Module | Lines | Role |
|--------|-------|------|
| `core.py` | ~560 | Vault class: `__init__`, file read/write/list, tag normalization, title resolution, frontmatter handling. Delegates to other modules. |
| `seeds.py` | ~250 | Seed templates (`INITIAL_SCHEMA`, `INITIAL_PROTOCOLS`, `INITIAL_PREFERENCES`, `INITIAL_INDEX`, `INITIAL_LOG`). Pure constants. |
| `git.py` | ~70 | Git init/commit, `OperationContext` for batched writes. |
| `indexing.py` | ~100 | `rebuild_search_index`, `rebuild_backlinks`, `search_content`, `index_file`. |
| `audit.py` | ~370 | `audit_vault` (full health audit), `health_metrics`, `stats`, `save_audit_report`, tag similarity helpers. |
| `context.py` | ~290 | `scan_vault_context` (LLM world summary), `scan_imports`, `build_file_digest`. |
| `organize.py` | ~340 | `rebuild_index`, `apply_organize_plan`, `import_directory`. |

## Public Interface

```python
from noteweaver.vault import Vault  # only public import
```

All submodules are implementation details accessed via `Vault` method delegation.

## What to Change Where

| Want to change... | Look at... |
|-------------------|------------|
| How files are read/written | `core.py` — `read_file`, `write_file`, `save_source` |
| What a fresh vault looks like | `seeds.py` — template constants |
| Git commit behavior | `git.py` — `git_init`, `git_commit`, `OperationContext` |
| Search/FTS behavior | `indexing.py` |
| Health audit logic | `audit.py` — `audit_vault`, `health_metrics` |
| What the LLM sees as world summary | `context.py` — `scan_vault_context` |
| How imports are organized | `organize.py` — `apply_organize_plan`, `import_directory` |
| How index.md is rebuilt | `organize.py` — `rebuild_index` |

## Test Mapping

- `test_vault.py` — core file operations, init, resolve, list
- `test_audit.py` — audit logic, health metrics (largest test file: 1400 lines)
- `test_git.py` — git auto-commit
- `test_search.py` — FTS5 index
- `test_backlinks.py` — wiki-link extraction
- `test_organize_imports.py` — import/organize workflows
