"""Tool dispatch: TOOL_HANDLERS registry and dispatch_tool()."""

from __future__ import annotations

import inspect
from typing import Any, TYPE_CHECKING

from noteweaver.tools.handlers_read import (
    handle_read_page,
    handle_search,
    handle_get_backlinks,
    handle_list_pages,
    handle_fetch_url,
)
from noteweaver.tools.handlers_write import (
    handle_write_page,
    handle_append_section,
    handle_update_frontmatter,
    handle_add_related_link,
)
from noteweaver.tools.legacy import (
    handle_survey_topic,
    handle_capture,
    handle_ingest,
    handle_organize,
    handle_restructure,
)

if TYPE_CHECKING:
    from noteweaver.vault import Vault

TOOL_HANDLERS: dict[str, Any] = {
    "read_page": handle_read_page,
    "search": handle_search,
    "get_backlinks": handle_get_backlinks,
    "list_pages": handle_list_pages,
    "fetch_url": handle_fetch_url,
    "write_page": handle_write_page,
    "append_section": handle_append_section,
    "update_frontmatter": handle_update_frontmatter,
    "add_related_link": handle_add_related_link,
    # spawn_subagent is intercepted by agent.chat() — stub for dispatch_tool
    "spawn_subagent": lambda vault, task="": (
        "Error: spawn_subagent must be called from agent.chat() context"
    ),
    # Legacy handlers (not in TOOL_SCHEMAS but still dispatchable for tests)
    "survey_topic": handle_survey_topic,
    "capture": handle_capture,
    "ingest": handle_ingest,
    "organize": handle_organize,
    "restructure": handle_restructure,
}


def dispatch_tool(vault: Vault, name: str, arguments: dict) -> str:
    """Execute a tool call and return the result as a string."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return f"Error: unknown tool '{name}'"

    sig = inspect.signature(handler)
    valid_params = set(sig.parameters.keys()) - {"vault"}
    filtered_args = {k: v for k, v in arguments.items() if k in valid_params}

    try:
        return handler(vault, **filtered_args)
    except TypeError as e:
        return f"Error calling {name}: {e}. Arguments received: {list(arguments.keys())}"
