"""Tool schemas for LLM function calling (OpenAI format).

Read tools:  read_page, search, get_backlinks, list_pages, fetch_url, audit_vault
Write tools: write_page, append_section, update_frontmatter, add_related_link
Job:         create_job
Sub-agent:   spawn_subagent
"""

from noteweaver.constants import OBSERVATION_TOOL_NAMES as _OBSERVATION_TOOL_NAMES

TOOL_SCHEMAS: list[dict] = [
    # ------------------------------------------------------------------
    # Read tools
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": (
                "Read a file from the vault. Accepts a file path "
                "(e.g. 'wiki/concepts/attention.md') or a page title "
                "(e.g. 'Attention Mechanism'). Use max_chars for a quick "
                "relevance check. Use section to read only a specific section."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path or page title",
                    },
                    "section": {
                        "type": "string",
                        "description": "Optional heading to read only that section (without ##)",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Max characters to read. Use ~500 for quick check.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "Search the knowledge base by keyword or topic. Returns ranked "
                "results with title, type, summary, tags, backlink count, and "
                "updated date. Use scope to limit search area. "
                "Also checks title similarity to find near-matches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (keywords or topic)",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["wiki", "sources", "all"],
                        "description": "Where to search. Default: 'all'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_backlinks",
            "description": (
                "Find all pages that link to a given page title via "
                "[[wiki-links]]. Use to understand how a concept is connected."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The page title to find backlinks for",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_pages",
            "description": (
                "List pages in a directory with metadata (title, type, "
                "summary, tags). Set include_raw=true to also see non-markdown "
                "files and files without frontmatter."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Directory to scan, e.g. 'wiki', 'sources'. Default: 'wiki'",
                        "default": "wiki",
                    },
                    "include_raw": {
                        "type": "boolean",
                        "description": "Include non-markdown files and files without frontmatter. Default: false",
                        "default": False,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Fetch a web page and extract its content as markdown. "
                "Use this to preview or import a URL's content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch",
                    },
                },
                "required": ["url"],
            },
        },
    },
    # ------------------------------------------------------------------
    # Write tools
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "write_page",
            "description": (
                "Create or overwrite a full wiki page. You MUST read the "
                "target page first (read_page) before overwriting an existing "
                "page. Always include YAML frontmatter with title, type, "
                "created/updated dates, summary, tags. "
                "For background jobs only: you may also write "
                "`.meta/jobs/<job_id>/progress.md` (plain markdown, no frontmatter)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path in wiki/, e.g. 'wiki/concepts/attention.md', "
                            "or `.meta/jobs/<job_id>/progress.md` for job progress."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Full markdown content including YAML frontmatter for wiki pages; "
                            "plain markdown for job progress files."
                        ),
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_section",
            "description": (
                "Append a new section to an existing wiki page. The section "
                "is inserted before the ## Related section if one exists, "
                "otherwise appended at the end. Use this for incremental "
                "additions to existing pages. For background jobs, you may "
                "append to `.meta/jobs/<job_id>/progress.md`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path of the existing wiki page, or "
                            "`.meta/jobs/<job_id>/progress.md` for job progress."
                        ),
                    },
                    "heading": {
                        "type": "string",
                        "description": "Heading for the new section",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown content for the new section",
                    },
                },
                "required": ["path", "heading", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_frontmatter",
            "description": (
                "Update specific frontmatter fields on an existing wiki page. "
                "Only the fields you specify will be changed; all other fields "
                "and the page body are preserved."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the wiki page to update",
                    },
                    "fields": {
                        "type": "object",
                        "description": "Frontmatter fields to update, e.g. {\"tags\": [\"ai\"], \"summary\": \"...\"}",
                    },
                },
                "required": ["path", "fields"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_related_link",
            "description": (
                "Add a [[wiki-link]] to the Related section of a page. "
                "Creates the ## Related section if it doesn't exist. "
                "Skips if the link already exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the wiki page",
                    },
                    "link_to": {
                        "type": "string",
                        "description": "Page title to link to",
                    },
                },
                "required": ["path", "link_to"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "audit_vault",
            "description": (
                "Run a full health audit on the vault. Returns structured "
                "findings: missing frontmatter, orphan pages, broken links, "
                "missing summaries, hub candidates, stale imports, missing "
                "connections, and similar tags. Use this to understand the "
                "overall health of the knowledge base."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    # ------------------------------------------------------------------
    # Sub-agent tool
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "create_job",
            "description": (
                "Create a background job for work that requires many tool calls "
                "(batch imports, deep research, wiki-wide cleanup). The job runs "
                "asynchronously via the gateway cron loop — a dedicated worker "
                "agent processes iterations against the contract. Use this when "
                "the task is too large for a single chat turn."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": (
                            "Short human-readable description of the job "
                            "(used in the job ID slug)"
                        ),
                    },
                    "goal": {
                        "type": "string",
                        "description": "Detailed goal paragraph for the worker agent.",
                    },
                    "criteria": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Acceptance criteria. Prefix mechanical checks with "
                            "[audit: metric_name op value] for automatic verification."
                        ),
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "Max worker iterations. Default: 30.",
                    },
                },
                "required": ["description", "goal", "criteria"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_subagent",
            "description": (
                "Spawn an independent sub-agent to handle a self-contained "
                "task. The sub-agent gets its own context window and step "
                "budget — use this for heavy subtasks like processing a "
                "topic cluster during import, or fixing a batch of wiki "
                "issues. The sub-agent has access to the same vault and "
                "tools. Returns the sub-agent's final summary when done."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "A detailed task description for the sub-agent. "
                            "Include all necessary context: file paths, "
                            "current wiki structure, what to create/update, "
                            "and any constraints."
                        ),
                    },
                },
                "required": ["task"],
            },
        },
    },
]


OBSERVATION_SCHEMAS: list[dict] = [
    s for s in TOOL_SCHEMAS
    if s["function"]["name"] in _OBSERVATION_TOOL_NAMES
]

CHAT_TOOL_SCHEMAS: list[dict] = TOOL_SCHEMAS

SUBMIT_PLAN_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "submit_plan",
        "description": (
            "Propose a batch of changes as a plan (used by session-organize "
            "flow only — not available during normal chat)."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
