# tools/ — Tool Schemas, Handlers, and Dispatch

The tools package defines the 10 primitive vault operations plus `create_job` and `spawn_subagent`, along with dispatch logic and safety policy. External code uses `from noteweaver.tools.definitions import TOOL_SCHEMAS, dispatch_tool`.

## Module Responsibilities

| Module | Lines | Role |
|--------|-------|------|
| `schemas.py` | ~350 | 12 tool schemas (`TOOL_SCHEMAS`), `OBSERVATION_SCHEMAS`, `CHAT_TOOL_SCHEMAS`, `SUBMIT_PLAN_SCHEMA`. |
| `handlers_read.py` | ~290 | 6 read handlers: `handle_read_page`, `handle_search`, `handle_get_backlinks`, `handle_list_pages`, `handle_fetch_url`, `handle_audit_vault`. Also `resolve_path_or_title` and `extract_section` helpers. |
| `handlers_write.py` | ~170 | 5 write handlers: `handle_write_page`, `handle_append_section`, `handle_update_frontmatter`, `handle_add_related_link`, `handle_create_job`. |
| `legacy.py` | ~575 | 5 deprecated handlers: `handle_survey_topic`, `handle_capture`, `handle_ingest`, `handle_organize`, `handle_restructure`. Kept for backward compat. |
| `dispatch.py` | ~75 | `TOOL_HANDLERS` registry + `dispatch_tool()`. |
| `definitions.py` | ~60 | Re-export layer — all names from the old monolithic file still importable here. |
| `policy.py` | ~375 | `check_pre_dispatch()`, `PolicyContext`, `TOOL_TIERS`, safety gates. |

## Adding a New Tool (checklist)

1. `schemas.py` → add schema to `TOOL_SCHEMAS`
2. `handlers_read.py` or `handlers_write.py` → add handler function
3. If read tool, add name to `_OBSERVATION_TOOL_NAMES` in `constants.py`
4. `dispatch.py` → add to `TOOL_HANDLERS` dict
5. `policy.py` → add risk tier to `TOOL_TIERS`
6. `tests/test_tools.py` → add handler test
7. If write tool, add policy test in `tests/test_attended_policy.py`

## Test Mapping

- `test_tools.py` — handler execution (dispatch_tool for each tool)
- `test_fine_grained_tools.py` — append_section, update_frontmatter, add_related_link
- `test_attended_policy.py` — attended-mode gates: read-before-write, dedup
- `test_policy.py` — policy context tracking, tier classification
- `test_content_gates.py` — content-layer write restrictions
