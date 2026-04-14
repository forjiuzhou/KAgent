# agent/ — Core Agent Loop

The agent package contains the `KnowledgeAgent` class and its system prompt. External code interacts through `from noteweaver.agent import KnowledgeAgent, create_provider`.

## Module Responsibilities

| Module | Lines | Role |
|--------|-------|------|
| `core.py` | ~1690 | `KnowledgeAgent` class: chat loop, context assembly, session memory, plan/organize, journal generation, sub-agent spawning, skill execution. |
| `prompts.py` | ~120 | System prompt constants (`PROMPT_IDENTITY`, `PROMPT_TOOLS`, `PROMPT_SKILLS_HEADER`), `_format_available_skills`, `create_provider` factory. |

## Public Interface

```python
from noteweaver.agent import KnowledgeAgent, create_provider
```

## Key Methods in core.py (by responsibility)

**System prompt**: `_build_system_prompt` (assembles static prompt + `.schema/` files)

**Context assembly** (what the LLM sees each turn):
- `_build_messages_for_query` — constructs the message list sent to the LLM
- `_apply_tool_result_tiers` — full → preview → placeholder based on age
- `_update_session_summary` — structured compression of older turns

**Session memory** (persists across sessions):
- `_load_session_memory`, `save_session_memory`
- `_scan_pending_proposals`, `_extract_open_items`

**Main chat loop**: `chat()` — the core agent loop (~190 lines)

**Plan/organize** (session-level workflows):
- `_handle_submit_plan`, `execute_plan`
- `generate_organize_plan`, `execute_organize_plan`
- `_ensure_progressive_disclosure`

**Journal**: `generate_journal_summary`, `_parse_journal_sections`

**Sub-agent**: `_handle_spawn_subagent`

**Skills**: `run_skill`

## What to Change Where

| Want to change... | Look at... |
|-------------------|------------|
| Agent personality/behavior | `prompts.py` — prompt constants |
| What context the LLM sees | `core.py` → `_build_messages_for_query()` |
| How the chat loop works | `core.py` → `chat()` |
| Session memory persistence | `core.py` → `save_session_memory()` |
| Plan/organize logic | `core.py` → plan-related methods |
| Provider selection | `prompts.py` → `create_provider()` |

## Test Mapping

- `test_integration.py` — end-to-end agent flows with mocked LLM
- `test_context_management.py` — transcript, session memory, journal
- `test_prompt_engine.py` — system prompt structure, size budgets
- `test_provider.py` — provider factory, message shaping
- `test_retry_and_journal.py` — retry logic, journal extraction
