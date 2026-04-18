"""Write tool handlers: write_page, append_section, update_frontmatter, add_related_link, create_job."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import yaml

from noteweaver.constants import (
    INDEX_TOKEN_BUDGET,
    JOB_DEFAULT_MAX_ITERATIONS,
    is_job_progress_path,
)
from noteweaver.frontmatter import validate_frontmatter, extract_frontmatter
from noteweaver.tools.handlers_read import resolve_path_or_title

if TYPE_CHECKING:
    from noteweaver.vault import Vault


def handle_write_page(vault: Vault, path: str, content: str) -> str:
    try:
        if is_job_progress_path(path):
            vault.write_file(path, content)
            return f"OK: written to {path} ({len(content)} chars)"
        if not path.startswith("wiki/"):
            return (
                f"Error: write_page can only write to wiki/ or "
                f".meta/jobs/<job_id>/progress.md. Rejected path: {path}"
            )
        validation = validate_frontmatter(path, content)
        if not validation.valid:
            return "Error: frontmatter validation failed:\n" + "\n".join(
                f"  - {e}" for e in validation.errors
            )
        vault.write_file(path, content)
        result = f"OK: written to {path} ({len(content)} chars)"
        if path == "wiki/index.md" and len(content) > INDEX_TOKEN_BUDGET:
            result += (
                f"\n\nWarning: index.md is {len(content)} chars "
                f"(target: <{INDEX_TOKEN_BUDGET})."
            )
        return result
    except PermissionError as e:
        return f"Error: {e}"


def handle_append_section(
    vault: Vault, path: str, heading: str, content: str,
) -> str:
    """Append a new section to an existing wiki page."""
    try:
        resolved = resolve_path_or_title(vault, path)
        existing = vault.read_file(resolved)
    except FileNotFoundError:
        return f"Error: page not found: {path}"
    except PermissionError as e:
        return f"Error: {e}"

    if not resolved.startswith("wiki/") and not is_job_progress_path(resolved):
        return (
            f"Error: can only append to wiki/ pages or "
            f".meta/jobs/<job_id>/progress.md. Path: {resolved}"
        )

    section_text = f"\n## {heading}\n\n{content}\n"

    related_pattern = re.compile(r"(\n## Related\b)", re.IGNORECASE)
    match = related_pattern.search(existing)
    if match:
        insert_pos = match.start()
        new_content = existing[:insert_pos] + section_text + existing[insert_pos:]
    else:
        new_content = existing.rstrip() + "\n" + section_text

    vault.write_file(resolved, new_content)
    return f"OK: appended section '{heading}' to {resolved}"


def handle_update_frontmatter(
    vault: Vault, path: str, fields: dict,
) -> str:
    """Update specific frontmatter fields on an existing wiki page."""
    try:
        existing = vault.read_file(path)
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    if not path.startswith("wiki/"):
        return f"Error: can only edit wiki/ pages. Path: {path}"

    from noteweaver.frontmatter import FRONTMATTER_PATTERN
    fm = extract_frontmatter(existing)
    if fm is None:
        return f"Error: no frontmatter found in {path}"

    fm.update(fields)
    fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
    body = FRONTMATTER_PATTERN.sub("", existing, count=1)
    new_content = f"---\n{fm_str}\n---\n{body}"

    validation = validate_frontmatter(path, new_content)
    if not validation.valid:
        return "Error: updated frontmatter is invalid:\n" + "\n".join(
            f"  - {e}" for e in validation.errors
        )

    vault.write_file(path, new_content)
    updated_keys = ", ".join(fields.keys())
    return f"OK: updated [{updated_keys}] in {path}"


def handle_add_related_link(
    vault: Vault, path: str, link_to: str,
) -> str:
    """Add a [[wiki-link]] to the Related section of a page."""
    if not link_to:
        return "Error: link_to is required"
    try:
        existing = vault.read_file(path)
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    if not path.startswith("wiki/"):
        return f"Error: can only edit wiki/ pages. Path: {path}"

    link = f"[[{link_to}]]"
    if link in existing:
        return f"OK: link to {link} already exists in {path}"

    related_pattern = re.compile(r"(## Related\b.*)", re.IGNORECASE | re.DOTALL)
    match = related_pattern.search(existing)
    if match:
        related_section = match.group(1)
        new_related = related_section.rstrip() + f"\n- {link}\n"
        new_content = existing[: match.start()] + new_related
    else:
        new_content = existing.rstrip() + f"\n\n## Related\n\n- {link}\n"

    vault.write_file(path, new_content)
    return f"OK: added {link} to Related section of {path}"


def handle_create_job(
    vault: Vault,
    description: str,
    goal: str,
    criteria: list[str],
    max_iterations: int | None = None,
) -> str:
    """Create a background job under ``.meta/jobs/``."""
    from datetime import datetime, timezone
    from noteweaver.job import generate_job_id
    from noteweaver.constants import JOB_DIR

    if not description or not goal or not criteria:
        return "Error: description, goal, and criteria are all required."

    job_id = generate_job_id(description)
    max_iter = max_iterations or JOB_DEFAULT_MAX_ITERATIONS

    criteria_lines = "\n".join(f"- {c}" for c in criteria)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    contract = (
        f"# Job: {description}\n\n"
        f"## Status\nready\n\n"
        f"## Goal\n{goal}\n\n"
        f"## Acceptance Criteria\n{criteria_lines}\n\n"
        f"## Max Iterations\n{max_iter}\n\n"
        f"## Created\n{created}\n"
    )

    progress = f"# Progress: {description}\n\n"

    job_dir = vault.root / JOB_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "contract.md").write_text(contract, encoding="utf-8")
    (job_dir / "progress.md").write_text(progress, encoding="utf-8")

    return (
        f"OK: job created — {job_id}\n"
        f"Contract: {JOB_DIR}/{job_id}/contract.md\n"
        f"The gateway cron will pick it up automatically."
    )
