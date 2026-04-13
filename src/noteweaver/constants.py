"""Centralized constants for NoteWeaver.

Single source of truth for values that were previously duplicated across
gateway.py, cli.py, frontmatter.py, policy.py, vault.py, and agent.py.

If you need to change approval keywords, wiki structure paths, page types,
or agent budget numbers — this is the only file you touch.
"""

from __future__ import annotations

# ======================================================================
# User interaction keywords
# ======================================================================

APPROVE_KEYWORDS = frozenset({
    "y", "yes", "ok",
    "好", "好的", "可以", "是", "执行", "确认",
})

REJECT_KEYWORDS = frozenset({
    "n", "no",
    "否",
})

EXIT_KEYWORDS = frozenset({
    "exit", "quit", "/exit", "/quit",
})

# ======================================================================
# Wiki structure
# ======================================================================

WIKI_DIRS = ["concepts", "journals", "synthesis", "archive"]

STRUCTURE_PATHS = frozenset({"wiki/index.md", "wiki/log.md"})
"""Pages that are structural (index, log) — exempt from frontmatter
validation, skip ``updated`` touch, and excluded from vault context scans."""

VALID_PAGE_TYPES = frozenset({
    "source", "journal", "hub", "canonical",
    "archive", "note", "synthesis", "preference",
})

PREFERENCES_PATH = ".schema/preferences.md"

# ======================================================================
# Filesystem skip patterns
# ======================================================================

SKIP_DIRS = frozenset({".git", ".meta", ".DS_Store", "__pycache__", "node_modules"})
SKIP_FILES = frozenset({".DS_Store", "Thumbs.db", ".gitignore"})

# ======================================================================
# Agent context budget
# ======================================================================

CHARS_PER_TOKEN = 4
MAX_CONTEXT_CHARS = 48_000

TOOL_RESULT_MAX_CHARS = 8_000
TOOL_RESULT_PREVIEW_CHARS = 500
RECENT_TURNS_FULL = 1
RECENT_TURNS_PREVIEW = 2

RECENT_MESSAGES_KEEP = 6
SUMMARY_KEY_POINTS_MAX = 20
MEMORY_FILE_MAX_CHARS = 3_000

AGENT_MAX_STEPS = 25

# ======================================================================
# LLM provider defaults
# ======================================================================

ANTHROPIC_MAX_TOKENS = 4_096
ANTHROPIC_SIMPLE_MAX_TOKENS = 2_048

# ======================================================================
# Retry / network
# ======================================================================

MAX_RETRIES = 4
INITIAL_BACKOFF = 1.0
BACKOFF_MULTIPLIER = 2.0
MAX_BACKOFF = 32.0

RETRYABLE_STATUS_CODES = frozenset({
    408,  # Request Timeout
    429,  # Rate Limited
    500,  # Internal Server Error
    502,  # Bad Gateway
    503,  # Service Unavailable
    504,  # Gateway Timeout
    529,  # Anthropic overloaded
})

# ======================================================================
# Tool definitions
# ======================================================================

OBSERVATION_TOOL_NAMES = frozenset({
    "read_page", "search", "get_backlinks", "list_pages", "fetch_url",
})

WRITE_TOOL_NAMES = frozenset({
    "write_page", "append_section", "update_frontmatter", "add_related_link",
    "capture", "ingest", "organize", "restructure",
})

# ======================================================================
# Policy
# ======================================================================

MIN_SYNTHESIS_LINKS = 2

# ======================================================================
# Search defaults
# ======================================================================

SEARCH_RESULT_LIMIT = 30
FETCH_URL_TIMEOUT = 30
FETCH_URL_MAX_CHARS = 15_000
INDEX_TOKEN_BUDGET = 4_000

# ======================================================================
# Session
# ======================================================================

MIN_EXCHANGES_FOR_JOURNAL = 3

# ======================================================================
# Gateway
# ======================================================================

GATEWAY_SAVE_INTERVAL = 10
GATEWAY_CRON_POLL_SECONDS = 300
