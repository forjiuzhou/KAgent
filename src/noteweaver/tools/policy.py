"""Runtime policy layer for tool dispatch.

Two orthogonal dimensions of control:

1. **Write-target classification** — what is being modified?
   - RUNTIME:   .meta/* (transcripts, session memory) — always OK
   - STRUCTURE: wiki/index.md, wiki/log.md, backlinks, ## Related — auto OK
   - JOURNAL:   wiki/journals/* — low barrier
   - CONTENT:   wiki/concepts/*, wiki/synthesis/*, .schema/preferences.md — guarded
   - SOURCE:    sources/* — explicit import only (existing create-only enforcement)

2. **Attended vs unattended** — is the user present?
   - attended (nw chat, nw ingest run by user in terminal): content writes allowed
   - unattended (gateway cron digest): content writes blocked, only proposals

Combined rule:
  attended   + structure → allow
  attended   + content   → allow (with dedup / read-before-write checks)
  unattended + structure → allow
  unattended + content   → BLOCK, return proposal guidance to LLM
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ======================================================================
# Write-target classification
# ======================================================================

class WriteTarget(Enum):
    RUNTIME = "runtime"
    STRUCTURE = "structure"
    JOURNAL = "journal"
    CONTENT = "content"
    SOURCE = "source"


_STRUCTURE_PATHS = frozenset({"wiki/index.md", "wiki/log.md"})

_STRUCTURE_TOOLS = frozenset({"append_log", "add_related_link"})


def classify_write_target(tool_name: str, path: str) -> WriteTarget:
    """Classify what a write operation is targeting."""
    if path.startswith(".meta/"):
        return WriteTarget.RUNTIME

    if path.startswith("sources/"):
        return WriteTarget.SOURCE

    if path.startswith("wiki/journals/"):
        return WriteTarget.JOURNAL

    if path in _STRUCTURE_PATHS:
        return WriteTarget.STRUCTURE

    if tool_name in _STRUCTURE_TOOLS:
        return WriteTarget.STRUCTURE

    if path.startswith(".schema/"):
        return WriteTarget.CONTENT

    if path.startswith("wiki/"):
        return WriteTarget.CONTENT

    return WriteTarget.CONTENT


# ======================================================================
# Risk tier (retained for backward compat, now secondary to target)
# ======================================================================

class RiskTier(Enum):
    READ = "read"
    LOW_WRITE = "low_write"
    MEDIUM_WRITE = "medium_write"
    HIGH_WRITE = "high_write"


TOOL_TIERS: dict[str, RiskTier] = {
    "read_page": RiskTier.READ,
    "list_page_summaries": RiskTier.READ,
    "search_vault": RiskTier.READ,
    "vault_stats": RiskTier.READ,
    "get_backlinks": RiskTier.READ,
    "find_existing_page": RiskTier.READ,
    "read_transcript": RiskTier.READ,
    "fetch_url": RiskTier.READ,
    "append_to_section": RiskTier.LOW_WRITE,
    "update_frontmatter": RiskTier.LOW_WRITE,
    "add_related_link": RiskTier.LOW_WRITE,
    "append_log": RiskTier.LOW_WRITE,
    "append_section": RiskTier.MEDIUM_WRITE,
    "archive_page": RiskTier.MEDIUM_WRITE,
    "save_source": RiskTier.MEDIUM_WRITE,
    "import_files": RiskTier.MEDIUM_WRITE,
    "promote_insight": RiskTier.MEDIUM_WRITE,
    "write_page": RiskTier.HIGH_WRITE,
}


# ======================================================================
# Policy context
# ======================================================================

@dataclass
class PolicyContext:
    """Tracks policy-relevant state within a single chat session."""

    attended: bool = True
    dedup_checked_titles: set[str] = field(default_factory=set)
    pages_read: list[str] = field(default_factory=list)
    pages_written: list[str] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    navigation_done: bool = False

    def record_tool_call(self, name: str, args: dict) -> None:
        """Record a tool invocation for policy tracking."""
        self.tools_called.append(name)

        if name == "find_existing_page":
            title = args.get("title", "")
            if title:
                self.dedup_checked_titles.add(title.lower())

        if name in ("list_page_summaries", "read_page", "search_vault"):
            self.navigation_done = True

        path = args.get("path", "")
        if name == "read_page" and path:
            if path not in self.pages_read:
                self.pages_read.append(path)
        if name in ("write_page", "append_section", "append_to_section",
                     "update_frontmatter", "add_related_link"):
            if path and path not in self.pages_written:
                self.pages_written.append(path)


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

    # Reads always pass
    if tier == RiskTier.READ:
        return PolicyVerdict(allowed=True)

    path = args.get("path", "") or ""
    target = classify_write_target(name, path)

    # Runtime and structure: always OK regardless of attended
    if target in (WriteTarget.RUNTIME, WriteTarget.STRUCTURE):
        return PolicyVerdict(allowed=True)

    # Journal: always OK (low-cost buffer)
    if target == WriteTarget.JOURNAL:
        return PolicyVerdict(allowed=True)

    # Content or source in unattended mode: block
    if not ctx.attended and target in (WriteTarget.CONTENT, WriteTarget.SOURCE):
        return PolicyVerdict(allowed=False, warning=_UNATTENDED_CONTENT_MSG)

    # Content in attended mode: apply per-tool checks
    if name == "write_page":
        return _check_write_page(path, args, ctx)

    return PolicyVerdict(allowed=True)


def _check_write_page(
    path: str,
    args: dict,
    ctx: PolicyContext,
) -> PolicyVerdict:
    """Enforce dedup-before-create for write_page in attended mode."""
    if path in _STRUCTURE_PATHS:
        return PolicyVerdict(allowed=True)

    if path in ctx.pages_read or path in ctx.pages_written:
        return PolicyVerdict(allowed=True)

    if not ctx.dedup_checked_titles:
        return PolicyVerdict(
            allowed=False,
            warning=(
                "Policy: call find_existing_page before creating a new page "
                "to avoid duplicates. If you've already confirmed this is new "
                "content, call find_existing_page(title) first, then retry "
                "write_page."
            ),
        )

    return PolicyVerdict(allowed=True)
