"""Runtime policy layer for tool dispatch.

Enforces capability-based access control:
- Read operations: always allowed, no restrictions
- Low-risk writes (append_to_section, update_frontmatter, add_related_link,
  append_log): auto-execute
- Medium writes (append_section, write_page to existing file): auto-execute
  with logging
- High-risk writes (write_page to new file): require prior find_existing_page
  call in the same session

This replaces intent classification with capability tiering — more stable
and easier to reason about than trying to classify Mode 1/2/3 accurately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RiskTier(Enum):
    READ = "read"
    LOW_WRITE = "low_write"
    MEDIUM_WRITE = "medium_write"
    HIGH_WRITE = "high_write"


TOOL_TIERS: dict[str, RiskTier] = {
    # Read — always allowed
    "read_page": RiskTier.READ,
    "list_page_summaries": RiskTier.READ,
    "search_vault": RiskTier.READ,
    "vault_stats": RiskTier.READ,
    "get_backlinks": RiskTier.READ,
    "find_existing_page": RiskTier.READ,
    "read_transcript": RiskTier.READ,
    "fetch_url": RiskTier.READ,
    # Low-risk writes — fine-grained, append-only, or metadata-only
    "append_to_section": RiskTier.LOW_WRITE,
    "update_frontmatter": RiskTier.LOW_WRITE,
    "add_related_link": RiskTier.LOW_WRITE,
    "append_log": RiskTier.LOW_WRITE,
    # Medium writes — create sections or archive
    "append_section": RiskTier.MEDIUM_WRITE,
    "archive_page": RiskTier.MEDIUM_WRITE,
    "save_source": RiskTier.MEDIUM_WRITE,
    "import_files": RiskTier.MEDIUM_WRITE,
    # Medium-high writes — promotion (has built-in dedup)
    "promote_insight": RiskTier.MEDIUM_WRITE,
    # High-risk writes — full page creation/overwrite
    "write_page": RiskTier.HIGH_WRITE,
}


@dataclass
class PolicyContext:
    """Tracks policy-relevant state within a single chat session.

    The agent loop updates this after each tool call so the policy layer
    can enforce sequencing constraints (e.g. dedup check before create).
    """

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


@dataclass
class PolicyVerdict:
    """Result of a policy check."""

    allowed: bool
    warning: str | None = None


def check_pre_dispatch(
    name: str,
    args: dict,
    ctx: PolicyContext,
) -> PolicyVerdict:
    """Check whether a tool call should proceed, based on session context.

    Returns a verdict. If not allowed, the warning message is returned
    to the LLM as the tool result instead of executing the tool.
    """
    tier = TOOL_TIERS.get(name, RiskTier.MEDIUM_WRITE)

    if tier == RiskTier.READ:
        return PolicyVerdict(allowed=True)

    if tier == RiskTier.HIGH_WRITE and name == "write_page":
        path = args.get("path", "")
        return _check_write_page(path, args, ctx)

    return PolicyVerdict(allowed=True)


def _check_write_page(
    path: str,
    args: dict,
    ctx: PolicyContext,
) -> PolicyVerdict:
    """Enforce dedup-before-create for write_page.

    System files (index.md, log.md) are exempt.
    Overwrites of pages that were read in the same turn are exempt
    (the agent already knows the page exists).
    """
    exempt_paths = {"wiki/index.md", "wiki/log.md"}
    if path in exempt_paths:
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
