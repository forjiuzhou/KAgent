"""LLM-facing vault context builders: scan_vault_context, scan_imports."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from noteweaver.constants import STRUCTURE_PATHS

if TYPE_CHECKING:
    from noteweaver.vault.core import Vault

_UNORGANIZED_DISPLAY_LIMIT = 10
_TOTAL_SCAN_BUDGET = 80_000
_PER_FILE_MIN = 800
_PER_FILE_MAX = 5_000


def scan_vault_context(vault: Vault) -> str:
    """Build a fixed-structure world summary for the LLM system prompt.

    Returns a consistent structural overview regardless of vault size.
    The summary shows hub-level aggregates and key signals; the agent
    uses ``list_pages`` to drill into any hub or directory for page cards,
    and ``read_page`` for full content.
    """
    _SKIP_PATHS = STRUCTURE_PATHS
    all_summaries = vault.read_frontmatters("wiki")
    existing_tags: set[str] = set()
    hubs: list[dict] = []
    content_pages: list[dict] = []
    journals: list[dict] = []
    unorganized: list[dict] = []
    total = 0
    no_summary_count = 0

    hub_tag_set: set[str] = set()

    for ps in all_summaries:
        if ps.get("path") in _SKIP_PATHS:
            continue
        ptype = ps.get("type", "")
        if ptype == "archive":
            continue
        for t in (ps.get("tags") or []):
            if t != "imported":
                existing_tags.add(t)
        if ptype == "journal":
            journals.append(ps)
            continue
        total += 1
        if not ps.get("summary"):
            no_summary_count += 1
        if ptype == "hub":
            hubs.append(ps)
            for t in (ps.get("tags") or []):
                hub_tag_set.add(t)
        else:
            content_pages.append(ps)

    hub_children: dict[str, list[dict]] = {h["path"]: [] for h in hubs}
    for ps in content_pages:
        page_tags = set(ps.get("tags") or [])
        matched = False
        for hub in hubs:
            hub_tags = set(hub.get("tags") or [])
            if page_tags & hub_tags:
                hub_children[hub["path"]].append(ps)
                matched = True
                break
        if not matched:
            unorganized.append(ps)

    orphan_pages: list[dict] = []
    for ps in content_pages:
        title = ps.get("title", "")
        if title and ps.get("type") not in ("hub", "journal"):
            if vault.backlinks.reference_count(title) == 0:
                orphan_pages.append(ps)

    lines: list[str] = []

    if hubs:
        lines.append("Hubs:")
        for hub in hubs:
            children = hub_children[hub["path"]]
            summary_part = f" — {hub['summary']}" if hub.get("summary") else ""
            lines.append(
                f"  {hub['title']} ({len(children)} pages) → {hub['path']}{summary_part}"
            )

    if unorganized:
        lines.append(
            f"\nUnorganized ({len(unorganized)} page(s) not under any hub):"
        )
        shown = unorganized[:_UNORGANIZED_DISPLAY_LIMIT]
        for ps in shown:
            lines.append(
                f"  [{ps.get('type', '?')}] {ps['title']} → {ps['path']}"
            )
        remaining = len(unorganized) - len(shown)
        if remaining > 0:
            lines.append(f"  … and {remaining} more")

    if orphan_pages:
        lines.append(
            f"\nOrphan pages ({len(orphan_pages)} — no inbound links):"
        )
        shown = orphan_pages[:_UNORGANIZED_DISPLAY_LIMIT]
        for ps in shown:
            lines.append(
                f"  [{ps.get('type', '?')}] {ps['title']} → {ps['path']}"
            )
        remaining = len(orphan_pages) - len(shown)
        if remaining > 0:
            lines.append(f"  … and {remaining} more")

    if existing_tags:
        sorted_tags = sorted(existing_tags)
        if len(sorted_tags) > 20:
            lines.append(f"\nTags ({len(sorted_tags)}): {', '.join(sorted_tags[:20])}, …")
        else:
            lines.append(f"\nTags: {', '.join(sorted_tags)}")

    if journals:
        dates = sorted(
            ps.get("path", "") for ps in journals
        )
        lines.append(f"\nJournals: {len(journals)} entries")
        if len(dates) >= 2:
            first_stem = Path(dates[0]).stem
            last_stem = Path(dates[-1]).stem
            lines.append(f"  range: {first_stem} → {last_stem}")
        elif dates:
            lines.append(f"  latest: {Path(dates[0]).stem}")

    lines.append(f"\nTotal: {total} pages")

    source_files = vault.list_files("sources")
    if source_files:
        by_subdir: dict[str, list[str]] = {}
        for sf in source_files:
            parts = sf.split("/")
            subdir = parts[1] if len(parts) > 2 else "(root)"
            by_subdir.setdefault(subdir, []).append(sf)
        lines.append(f"\nSources: {len(source_files)} file(s)")
        for sd, files in sorted(by_subdir.items()):
            prefix = f"sources/{sd}" if sd != "(root)" else "sources/"
            samples = [Path(f).name for f in files[:3]]
            sample_str = ", ".join(samples)
            more = f", …" if len(files) > 3 else ""
            lines.append(f"  {prefix}: {len(files)} file(s) [{sample_str}{more}]")

    health_signals = []
    if no_summary_count:
        health_signals.append(f"{no_summary_count} pages missing summary")
    if orphan_pages:
        health_signals.append(f"{len(orphan_pages)} orphan pages")
    if unorganized:
        health_signals.append(f"{len(unorganized)} pages not under any hub")
    if health_signals:
        lines.append(f"\nHealth: {', '.join(health_signals)}")

    lines.append("")
    lines.append(
        "Use list_pages to see page cards for any hub or directory, "
        "read_page on a hub to see its child pages, "
        "or search for keyword lookup."
    )

    return "\n".join(lines)


def scan_imports(vault: Vault) -> str:
    """Scan files needing organization and vault context for LLM-driven planning."""
    from noteweaver.frontmatter import extract_frontmatter

    imported_pages: list[dict] = []
    for rel_path in vault.list_files("wiki"):
        try:
            content = vault.read_file(rel_path)
        except (FileNotFoundError, PermissionError):
            continue
        fm = extract_frontmatter(content)
        if fm:
            tags = fm.get("tags") or []
            if "imported" not in tags:
                continue
        else:
            fm = {}
        imported_pages.append({
            "path": rel_path,
            "content": content,
            "fm": fm,
        })

    if not imported_pages:
        return "No files needing organization found. Nothing to organize."

    n = len(imported_pages)
    per_file = max(
        _PER_FILE_MIN,
        min(_PER_FILE_MAX, _TOTAL_SCAN_BUDGET // n),
    )

    file_sections: list[str] = []
    for i, page in enumerate(imported_pages, 1):
        digest = build_file_digest(
            page["path"], page["content"], page["fm"], per_file,
        )
        file_sections.append(f"### File {i}: {page['path']}\n{digest}")

    vault_ctx = scan_vault_context(vault)

    output_parts = [
        f"## Imported files to organize: {n}\n",
        f"Per-file character budget: {per_file}\n",
        "## Vault context\n",
        vault_ctx,
        "\n## File details\n",
        "\n\n".join(file_sections),
        "\n## Instructions\n",
        (
            "For EACH file above, output a JSON array. Each element:\n"
            "```json\n"
            "{\n"
            '  "path": "wiki/concepts/example.md",\n'
            '  "type": "note|canonical|journal|synthesis|hub",\n'
            '  "title": "Corrected Title",\n'
            '  "summary": "One-sentence summary of the page",\n'
            '  "tags": ["tag-a", "tag-b"],\n'
            '  "move_to": "wiki/journals/example.md or null if no move needed",\n'
            '  "related": ["Existing Page Title", "Another Page"],\n'
            '  "hub": "Existing or suggested hub name, or null",\n'
            '  "duplicate_of": "path of existing page if duplicate, else null",\n'
            '  "confidence": "high|low"\n'
            "}\n"
            "```\n"
            "Rules:\n"
            "- Use existing tags when possible; create new ones sparingly.\n"
            "- Set confidence=low for items you're unsure about.\n"
            "- Set duplicate_of only when content genuinely overlaps an existing page.\n"
            "- Suggest hub when 3+ pages (including existing) share a topic.\n"
            "- Respond ONLY with the JSON array. No extra text."
        ),
    ]
    return "\n".join(output_parts)


def build_file_digest(
    rel_path: str, content: str, fm: dict, budget: int,
) -> str:
    """Build a structured digest of a file within a character budget."""
    import re as _re

    parts: list[str] = []
    used = 0

    fm_match = _re.match(r"^---\s*\n.*?\n---\s*\n", content, _re.DOTALL)
    if fm_match:
        fm_text = fm_match.group(0)
        parts.append(fm_text.strip())
        used += len(fm_text)

    headings = [
        line for line in content.split("\n")
        if _re.match(r"^#{1,4}\s", line)
    ]
    if headings:
        outline = "Headings: " + " | ".join(h.strip() for h in headings)
        if used + len(outline) < budget:
            parts.append(outline)
            used += len(outline)

    meta = f"Total length: {len(content)} chars"
    parts.append(meta)
    used += len(meta)

    body_start = fm_match.end() if fm_match else 0
    remaining = budget - used
    if remaining > 50:
        body_slice = content[body_start:body_start + remaining].strip()
        if body_slice:
            parts.append(f"Content preview:\n{body_slice}")

    return "\n".join(parts)
