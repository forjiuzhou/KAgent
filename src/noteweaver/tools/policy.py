"""Runtime policy layer for tool dispatch.

V2 policy — safety gates for the direct-write chat path, plus
``classify_change_type()`` for session-organize plans.

Two orthogonal dimensions of control:

1. **Write-target classification** — what is being modified?
   - RUNTIME:   .meta/* (transcripts, session memory) — always OK
   - STRUCTURE: wiki/index.md, wiki/log.md — auto OK
   - JOURNAL:   wiki/journals/* — low barrier
   - CONTENT:   wiki/concepts/*, wiki/synthesis/*, .schema/preferences.md — guarded
   - SOURCE:    sources/* — explicit import only

2. **Attended vs unattended** — is the user present?
   - attended (nw chat, nw ingest): content writes allowed
   - unattended (gateway cron digest): content writes blocked

Content-layer gates (attended mode):
   - write_page: target page must have been read in this session (for overwrites)
   - append_section: target page must have been read in this session
   - update_frontmatter: target page must have been read in this session
   - add_related_link: target page must have been read in this session
   - .schema/preferences.md: allowed, but agent must inform user
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from noteweaver.constants import (
    STRUCTURE_PATHS as _STRUCTURE_PATHS,
    PREFERENCES_PATH as _PREFERENCES_PATH,
    MIN_SYNTHESIS_LINKS,
    is_job_progress_path,
)

if TYPE_CHECKING:
    from noteweaver.vault import Vault


# ======================================================================
# Write-target classification
# ======================================================================

class WriteTarget(Enum):
    RUNTIME = "runtime"
    STRUCTURE = "structure"
    JOURNAL = "journal"
    CONTENT = "content"
    SOURCE = "source"

_WIKI_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def classify_write_target(tool_name: str, path: str) -> WriteTarget:
    """Classify what a write operation is targeting."""
    if tool_name == "create_job":
        return WriteTarget.RUNTIME

    if path.startswith(".meta/"):
        return WriteTarget.RUNTIME

    if path.startswith("sources/"):
        return WriteTarget.SOURCE

    if path.startswith("wiki/journals/"):
        return WriteTarget.JOURNAL

    if path in _STRUCTURE_PATHS:
        return WriteTarget.STRUCTURE

    if tool_name in ("restructure",):
        return WriteTarget.STRUCTURE

    if path.startswith(".schema/"):
        return WriteTarget.CONTENT

    if path.startswith("wiki/"):
        return WriteTarget.CONTENT

    return WriteTarget.CONTENT


# ======================================================================
# Risk tier
# ======================================================================

class RiskTier(Enum):
    READ = "read"
    LOW_WRITE = "low_write"
    MEDIUM_WRITE = "medium_write"
    HIGH_WRITE = "high_write"


TOOL_TIERS: dict[str, RiskTier] = {
    # Read tools
    "read_page": RiskTier.READ,
    "search": RiskTier.READ,
    "get_backlinks": RiskTier.READ,
    "list_pages": RiskTier.READ,
    "fetch_url": RiskTier.READ,
    "audit_vault": RiskTier.READ,
    # V2 write tools
    "write_page": RiskTier.HIGH_WRITE,
    "append_section": RiskTier.MEDIUM_WRITE,
    "update_frontmatter": RiskTier.MEDIUM_WRITE,
    "add_related_link": RiskTier.LOW_WRITE,
    "create_job": RiskTier.LOW_WRITE,
    # Sub-agent tool
    "spawn_subagent": RiskTier.READ,
    # Legacy tools (kept for backward compatibility)
    "survey_topic": RiskTier.READ,
    "capture": RiskTier.MEDIUM_WRITE,
    "ingest": RiskTier.MEDIUM_WRITE,
    "organize": RiskTier.MEDIUM_WRITE,
    "restructure": RiskTier.HIGH_WRITE,
}


# ======================================================================
# Policy context
# ======================================================================

@dataclass
class PolicyContext:
    """Tracks policy-relevant state within a single chat session."""

    attended: bool = True
    topics_surveyed: set[str] = field(default_factory=set)
    pages_read: list[str] = field(default_factory=list)
    pages_written: list[str] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    navigation_done: bool = False

    def record_tool_call(self, name: str, args: dict) -> None:
        """Record a tool invocation for policy tracking."""
        self.tools_called.append(name)

        if name == "survey_topic":
            topic = args.get("topic", "")
            if topic:
                self.topics_surveyed.add(str(topic).lower())

        if name == "search":
            query = args.get("query", "")
            if query:
                self.topics_surveyed.add(str(query).lower())

        if name in ("list_pages", "read_page", "search", "survey_topic"):
            self.navigation_done = True

        path = args.get("path", "")
        if name == "read_page" and path:
            if path not in self.pages_read:
                self.pages_read.append(path)
        if name in ("write_page", "capture", "append_section",
                     "update_frontmatter", "add_related_link"):
            target = args.get("target", path)
            if target and target not in self.pages_written:
                self.pages_written.append(target)


# ======================================================================
# Verdict
# ======================================================================

@dataclass
class PolicyVerdict:
    """Result of a policy check."""
    allowed: bool
    warning: str | None = None


# ======================================================================
# Pre-dispatch check
# ======================================================================

_UNATTENDED_CONTENT_MSG = (
    "Policy: content writes are not allowed in unattended mode. "
    "Instead, write your findings as a '#### Promotion Candidates' "
    "section in today's journal (wiki/journals/). The user will review "
    "and confirm promotions in their next interactive session."
)


def check_pre_dispatch(
    name: str,
    args: dict,
    ctx: PolicyContext,
) -> PolicyVerdict:
    """Check whether a tool call should proceed."""
    tier = TOOL_TIERS.get(name, RiskTier.MEDIUM_WRITE)

    if tier == RiskTier.READ:
        return PolicyVerdict(allowed=True)

    path = args.get("path", "") or args.get("target", "") or ""
    target = classify_write_target(name, path)

    if target in (WriteTarget.RUNTIME, WriteTarget.STRUCTURE):
        return PolicyVerdict(allowed=True)

    if target == WriteTarget.JOURNAL:
        return PolicyVerdict(allowed=True)

    if not ctx.attended and target in (WriteTarget.CONTENT, WriteTarget.SOURCE):
        return PolicyVerdict(allowed=False, warning=_UNATTENDED_CONTENT_MSG)

    # --- Attended mode gates ---

    if path == _PREFERENCES_PATH:
        return PolicyVerdict(
            allowed=True,
            warning=(
                "You are modifying user preferences (.schema/preferences.md). "
                "After writing, you MUST tell the user exactly what was changed "
                "and why, so they can review or revert if needed."
            ),
        )

    # write_page requires read-before-write
    if name == "write_page":
        return _check_write_page(path, args, ctx)

    # Fine-grained write tools require read-before-write
    if name in ("append_section", "update_frontmatter", "add_related_link"):
        return _check_read_before_write(name, path, ctx)

    # Legacy: organize with update_metadata or link requires read
    if name == "organize" and args.get("action") in ("update_metadata", "link"):
        return _check_read_before_write(name, path, ctx)

    return PolicyVerdict(allowed=True)


def _check_read_before_write(
    name: str,
    path: str,
    ctx: PolicyContext,
) -> PolicyVerdict:
    """Require that the target page has been read in this session."""
    if not path:
        return PolicyVerdict(allowed=True)

    if is_job_progress_path(path):
        return PolicyVerdict(allowed=True)

    if path in ctx.pages_read or path in ctx.pages_written:
        return PolicyVerdict(allowed=True)

    return PolicyVerdict(
        allowed=False,
        warning=(
            f"Policy: read the target page before editing it. "
            f"Call read_page('{path}') first to see the current content, "
            f"then retry {name}."
        ),
    )


def _check_write_page(
    path: str,
    args: dict,
    ctx: PolicyContext,
) -> PolicyVerdict:
    """Full gate for write_page to content targets."""
    if is_job_progress_path(path):
        return PolicyVerdict(allowed=True)

    if path in _STRUCTURE_PATHS:
        return PolicyVerdict(allowed=True)

    if path in ctx.pages_read or path in ctx.pages_written:
        return PolicyVerdict(allowed=True)

    if not ctx.navigation_done:
        return PolicyVerdict(
            allowed=False,
            warning=(
                "Policy: survey the topic or search before creating a new page "
                "to avoid duplicates. Call search(query) or list_pages() "
                "first, then retry write_page."
            ),
        )

    content = args.get("content", "")
    return _check_content_quality(path, content)


def _check_content_quality(path: str, content: str) -> PolicyVerdict:
    """Type-specific quality checks on page content."""
    if not content:
        return PolicyVerdict(allowed=True)

    page_type = _extract_type(content)

    if page_type == "synthesis":
        link_count = len(_WIKI_LINK_RE.findall(content))
        if link_count < MIN_SYNTHESIS_LINKS:
            return PolicyVerdict(
                allowed=False,
                warning=(
                    f"Policy: synthesis pages must reference ≥{MIN_SYNTHESIS_LINKS} "
                    f"existing pages via [[wiki-links]] (found {link_count}). "
                    "A synthesis that doesn't connect multiple sources should "
                    "be a note instead."
                ),
            )

    return PolicyVerdict(allowed=True)


# ======================================================================
# Helpers
# ======================================================================

_FM_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _extract_type(content: str) -> str:
    """Quick extraction of the type field from frontmatter."""
    m = _FM_PATTERN.match(content)
    if not m:
        return ""
    for line in m.group(1).split("\n"):
        stripped = line.strip()
        if stripped.startswith("type:"):
            return stripped.split(":", 1)[1].strip()
    return ""


def _strip_frontmatter(content: str) -> str:
    """Return content with frontmatter removed."""
    m = _FM_PATTERN.match(content)
    if m:
        return content[m.end():]
    return content


# ======================================================================
# Change type classification (used by session-organize plans)
# ======================================================================

_STRUCTURAL_INTENTS = frozenset({"create", "restructure"})


def classify_change_type(
    intent: str,
    targets: list[str],
    model_suggestion: str,
    vault: "Vault | None" = None,
) -> str:
    """Verify and possibly override the model's change_type suggestion.

    Called by ``_handle_submit_plan()`` when ``generate_organize_plan()``
    creates a Plan object.  Not involved in the normal ``chat()`` direct-
    write path — V2 chat uses ``check_pre_dispatch()`` for safety gates.
    """
    if intent in _STRUCTURAL_INTENTS:
        return "structural"

    if len(targets) >= 3:
        return "structural"

    if intent == "append" and vault is not None:
        for t in targets:
            try:
                resolved = vault._resolve(t)
                if not resolved.is_file():
                    return "structural"
            except (ValueError, OSError):
                return "structural"

    if model_suggestion in ("incremental", "structural"):
        return model_suggestion

    return "structural"
