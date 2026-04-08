"""Frontmatter validation for wiki pages.

Hard constraints that are enforced at write time, not just in the prompt.
This ensures knowledge base invariants hold even if the LLM ignores instructions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import yaml

VALID_TYPES = {"source", "journal", "hub", "canonical", "archive", "note", "synthesis"}

# Paths that are exempt from frontmatter validation (system files)
EXEMPT_PATHS = {"wiki/index.md", "wiki/log.md"}

FRONTMATTER_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]


def extract_frontmatter(content: str) -> dict | None:
    """Extract YAML frontmatter from markdown content. Returns None if absent."""
    match = FRONTMATTER_PATTERN.match(content)
    if not match:
        return None
    try:
        return yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError:
        return None


def validate_frontmatter(path: str, content: str) -> ValidationResult:
    """Validate frontmatter for a wiki page.

    Hard constraints:
    - All wiki pages (except index.md, log.md) must have frontmatter
    - Frontmatter must include 'title' and 'type'
    - 'type' must be one of the valid types
    - Canonical pages must have 'sources' field
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

    return ValidationResult(valid=len(errors) == 0, errors=errors)
