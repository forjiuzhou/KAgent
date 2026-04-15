"""Tool definitions for LLM function calling — backward compatibility re-exports.

All implementation has moved to focused modules:
  schemas.py        — TOOL_SCHEMAS, OBSERVATION_SCHEMAS, SUBMIT_PLAN_SCHEMA
  handlers_read.py  — read tool handlers
  handlers_write.py — write tool handlers
  legacy.py         — deprecated legacy handlers
  dispatch.py       — TOOL_HANDLERS + dispatch_tool()

This file re-exports everything so existing imports continue to work.
"""

# Schemas
from noteweaver.tools.schemas import (  # noqa: F401
    TOOL_SCHEMAS,
    OBSERVATION_SCHEMAS,
    CHAT_TOOL_SCHEMAS,
    SUBMIT_PLAN_SCHEMA,
)

from noteweaver.constants import OBSERVATION_TOOL_NAMES as _OBSERVATION_TOOL_NAMES  # noqa: F401

# Read handlers
from noteweaver.tools.handlers_read import (  # noqa: F401
    handle_read_page,
    handle_search,
    handle_get_backlinks,
    handle_list_pages,
    handle_fetch_url,
    handle_audit_vault,
    resolve_path_or_title as _resolve_path_or_title,
    extract_section as _extract_section,
)

# Write handlers
from noteweaver.tools.handlers_write import (  # noqa: F401
    handle_write_page,
    handle_append_section,
    handle_update_frontmatter,
    handle_add_related_link,
)

# Legacy handlers
from noteweaver.tools.legacy import (  # noqa: F401
    handle_survey_topic,
    handle_capture,
    handle_ingest,
    handle_organize,
    handle_restructure,
)

# Dispatch
from noteweaver.tools.dispatch import (  # noqa: F401
    TOOL_HANDLERS,
    dispatch_tool,
)
