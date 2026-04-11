"""Frontmatter validation and extraction for wiki pages.

Hard constraints that are enforced at write time, not just in the prompt.
This ensures knowledge base invariants hold even if the LLM ignores instructions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

VALID_TYPES = {"source", "journal", "hub", "canonical", "archive", "note", "synthesis", "preference"}

EXEMPT_PATHS = {"wiki/index.md", "wiki/log.md"}

FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]


@dataclass
class PageSummary:
    """Lightweight representation of a page — frontmatter only, no body."""
    path: str
    title: str
    type: str
    summary: str
    tags: list[str]
    sources: list[str]
    related: list[str]


def extract_frontmatter(content: str) -> dict | None:
    """Extract YAML frontmatter from markdown content. Returns None if absent."""
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return None
    try:
        return yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None


def page_summary_from_file(rel_path: str, content: str) -> PageSummary | None:
    """Extract a PageSummary from file content. Returns None if no frontmatter."""
    fm = extract_frontmatter(content)
    if fm is None:
        return None
    raw_tags = fm.get("tags", []) or []
    raw_sources = fm.get("sources", []) or []
    raw_related = fm.get("related", []) or []
    return PageSummary(
        path=rel_path,
        title=str(fm.get("title", "")),
        type=str(fm.get("type", "")),
        summary=str(fm.get("summary", "")),
        tags=[str(t) for t in raw_tags] if isinstance(raw_tags, list) else [],
        sources=[str(s) for s in raw_sources] if isinstance(raw_sources, list) else [],
        related=[str(r) for r in raw_related] if isinstance(raw_related, list) else [],
    )


def validate_frontmatter(path: str, content: str) -> ValidationResult:
    """Validate frontmatter for a wiki page.

    Hard constraints:
    - All wiki pages (except index.md, log.md) must have frontmatter
    - Frontmatter must include 'title' and 'type'
    - 'type' must be one of the valid types
    - Canonical pages must have 'sources' field
    - 'tags' must be a list if present
    """
    if path in EXEMPT_PATHS:
        return ValidationResult(valid=True, errors=[])

    if not path.startswith("wiki/"):
        return ValidationResult(valid=True, errors=[])

    errors = []

    fm = extract_frontmatter(content)
    if fm is None:
        errors.append("Missing YAML frontmatter (---\\n...\\n---)")
        return ValidationResult(valid=False, errors=errors)

    if "title" not in fm:
        errors.append("Frontmatter missing required field: 'title'")

    page_type = fm.get("type")
    if page_type is None:
        errors.append(f"Frontmatter missing required field: 'type' (valid: {', '.join(sorted(VALID_TYPES))})")
    elif page_type not in VALID_TYPES:
        errors.append(f"Invalid type '{page_type}'. Must be one of: {', '.join(sorted(VALID_TYPES))}")

    if page_type == "canonical":
        sources = fm.get("sources")
        if not sources:
            errors.append("Canonical pages must have a non-empty 'sources' field for traceability")

    tags = fm.get("tags")
    if tags is not None and not isinstance(tags, list):
        errors.append("'tags' must be a list, e.g. tags: [topic-a, topic-b]")

    return ValidationResult(valid=len(errors) == 0, errors=errors)
