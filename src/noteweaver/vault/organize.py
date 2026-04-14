"""Import organization workflows: apply_organize_plan, import_directory, rebuild_index."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noteweaver.vault.core import Vault


def rebuild_index(vault: Vault) -> str:
    """Rebuild index.md from actual file frontmatter. Self-healing."""
    from noteweaver.frontmatter import page_summary_from_file, extract_frontmatter

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    hubs = []
    other_pages = []
    unstructured = []

    for rel_path in vault.list_files("wiki"):
        if rel_path in ("wiki/index.md", "wiki/log.md"):
            continue
        if "/archive/" in rel_path:
            continue
        try:
            content = vault.read_file(rel_path)
            ps = page_summary_from_file(rel_path, content)
            if ps is None:
                unstructured.append(rel_path)
                continue
            fm = extract_frontmatter(content) or {}
            entry = {
                "path": rel_path,
                "title": ps.title or rel_path,
                "type": ps.type,
                "summary": ps.summary,
                "tags": ps.tags,
                "updated": str(fm.get("updated", "")),
            }
            if ps.type == "hub":
                hubs.append(entry)
            else:
                other_pages.append(entry)
        except (FileNotFoundError, PermissionError):
            continue

    lines = [
        f"---\ntitle: Wiki Index\nupdated: {today}\n---\n",
        "# Wiki Index\n",
        "Root of the knowledge base. Start here to navigate.\n",
    ]

    pinned = [p for p in (hubs + other_pages) if "pinned" in p["tags"]]
    if pinned:
        lines.append("## Pinned\n")
        for p in pinned:
            desc = f" — {p['summary']}" if p['summary'] else ""
            lines.append(f"- [[{p['title']}]]{desc}")
        lines.append("")

    lines.append("## Topics\n")
    if hubs:
        for h in sorted(hubs, key=lambda x: x["title"]):
            desc = f" — {h['summary']}" if h['summary'] else ""
            lines.append(f"- [[{h['title']}]]{desc}")
    else:
        lines.append("(no hubs yet)")
    lines.append("")

    lines.append("## Recent\n")
    if other_pages:
        sorted_pages = sorted(
            other_pages,
            key=lambda x: x.get("updated", ""),
            reverse=True,
        )
        for p in sorted_pages[:10]:
            desc = f" — {p['summary']}" if p['summary'] else ""
            lines.append(f"- [[{p['title']}]] ({p['type']}){desc}")
    else:
        lines.append("(no pages yet)")

    if unstructured:
        lines.append("")
        lines.append(f"## Unstructured ({len(unstructured)} files)\n")
        for p in unstructured:
            lines.append(f"- `{p}`")

    content = "\n".join(lines) + "\n"
    vault.write_file("wiki/index.md", content)
    return content


def apply_organize_plan(vault: Vault, plan_json: str) -> str:
    """Apply an LLM-generated organization plan to imported files.

    Expects a JSON array of file plans. Performs: type/tag/summary
    updates, file moves, related-link insertion, and hub creation.
    Returns a structured report.
    """
    import json as _json
    from noteweaver.frontmatter import extract_frontmatter

    try:
        plan = _json.loads(plan_json)
    except _json.JSONDecodeError as e:
        return f"Error: invalid JSON — {e}"

    if not isinstance(plan, list):
        return "Error: expected a JSON array of file plans."

    results: list[str] = []
    processed = 0
    moved = 0
    links_added = 0
    needs_review: list[str] = []
    hubs_to_create: dict[str, list[str]] = {}
    hubs_created: list[str] = []

    with vault.operation("Organize imported files"):
        for item in plan:
            path = item.get("path", "")
            if not path:
                continue

            try:
                content = vault.read_file(path)
            except FileNotFoundError:
                results.append(f"  ⚠ {path}: not found, skipped")
                continue

            fm = extract_frontmatter(content)
            if not fm:
                results.append(f"  ⚠ {path}: no frontmatter, skipped")
                continue

            confidence = item.get("confidence", "high")
            duplicate_of = item.get("duplicate_of")

            if confidence == "low" or duplicate_of:
                reason = f"duplicate_of={duplicate_of}" if duplicate_of else "low confidence"
                needs_review.append(f"  - {path}: {reason}")
                if confidence == "low" and not duplicate_of:
                    pass
                else:
                    continue

            fm_updates: dict = {}
            if item.get("type") and item["type"] != fm.get("type"):
                fm_updates["type"] = item["type"]
            if item.get("title") and item["title"] != fm.get("title"):
                fm_updates["title"] = item["title"]
            if item.get("summary"):
                fm_updates["summary"] = item["summary"]
            if item.get("tags"):
                new_tags = [t for t in item["tags"] if t != "imported"]
                fm_updates["tags"] = new_tags

            if not fm_updates.get("tags"):
                old_tags = fm.get("tags") or []
                if "imported" in old_tags:
                    fm_updates["tags"] = [t for t in old_tags if t != "imported"]

            if fm_updates:
                fm.update(fm_updates)
                import yaml as _yaml
                from noteweaver.frontmatter import FRONTMATTER_PATTERN
                fm_str = _yaml.dump(
                    fm, default_flow_style=False, allow_unicode=True,
                ).strip()
                body = FRONTMATTER_PATTERN.sub("", content, count=1)
                content = f"---\n{fm_str}\n---\n{body}"
                vault.write_file(path, content)

            actual_path = path
            move_to = item.get("move_to")
            if move_to and move_to != path:
                try:
                    vault._title_check_skip.add(path)
                    vault.write_file(move_to, content)
                    vault._title_check_skip.discard(path)
                    original = vault._resolve(path)
                    if original.is_file():
                        original.unlink()
                    vault.search.remove(path)
                    vault.backlinks.remove_page(path)
                    actual_path = move_to
                    moved += 1
                except Exception as e:
                    vault._title_check_skip.discard(path)
                    results.append(f"  ⚠ move {path} → {move_to} failed: {e}")

            for related_title in (item.get("related") or []):
                try:
                    existing = vault.read_file(actual_path)
                    link = f"[[{related_title}]]"
                    if link not in existing:
                        related_pattern = re.compile(
                            r"(## Related\b.*)", re.IGNORECASE | re.DOTALL,
                        )
                        match = related_pattern.search(existing)
                        if match:
                            section = match.group(1)
                            new_section = section.rstrip() + f"\n- {link}\n"
                            new_content = existing[:match.start()] + new_section
                        else:
                            new_content = existing.rstrip() + f"\n\n## Related\n\n- {link}\n"
                        vault.write_file(actual_path, new_content)
                        links_added += 1
                except Exception:
                    pass

            hub_name = item.get("hub")
            if hub_name:
                title = item.get("title") or fm.get("title") or ""
                if title:
                    hubs_to_create.setdefault(hub_name, []).append(title)

            processed += 1
            results.append(f"  ✓ {path}" + (f" → {move_to}" if move_to and move_to != path else ""))

        for hub_name, page_titles in hubs_to_create.items():
            hub_slug = str(hub_name).lower().replace(" ", "-")
            hub_slug = re.sub(r"[^a-z0-9-]", "", hub_slug)
            hub_slug = re.sub(r"-{2,}", "-", hub_slug).strip("-")[:60]
            hub_path = f"wiki/concepts/{hub_slug}.md"

            try:
                existing_hub = vault.read_file(hub_path)
                for pt in page_titles:
                    link = f"[[{pt}]]"
                    if link not in existing_hub:
                        existing_hub = existing_hub.rstrip() + f"\n- {link}\n"
                vault.write_file(hub_path, existing_hub)
                results.append(f"  ✓ Updated hub: {hub_path} (+{len(page_titles)} links)")
            except FileNotFoundError:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                links_block = "\n".join(f"- [[{pt}]]" for pt in page_titles)
                hub_content = (
                    f"---\ntitle: {hub_name}\ntype: hub\n"
                    f"summary: Hub for {hub_name} topics\n"
                    f"tags: [{hub_slug}]\n"
                    f"created: {today}\nupdated: {today}\n---\n\n"
                    f"# {hub_name}\n\n"
                    f"Overview page for {hub_name} related content.\n\n"
                    f"## Pages\n\n{links_block}\n\n"
                    f"## Related\n"
                )
                vault.write_file(hub_path, hub_content)
                hubs_created.append(hub_name)
                results.append(f"  ✓ Created hub: {hub_path} ({len(page_titles)} pages)")

        rebuild_index(vault)
        vault.append_log(
            "organize",
            f"Organized {processed} imported files",
            f"Moved: {moved}, Links added: {links_added}, "
            f"Hubs created: {len(hubs_created)}",
        )

    report_lines = [
        f"Organized {processed}/{len(plan)} files:\n",
        "\n".join(results),
    ]
    if hubs_created:
        report_lines.append(f"\nNew hubs: {', '.join(hubs_created)}")
    if needs_review:
        report_lines.append(f"\n⚠ Needs review ({len(needs_review)} files):")
        report_lines.append("\n".join(needs_review))
    report_lines.append(
        f"\nSummary: {processed} processed, {moved} moved, "
        f"{links_added} links added, {len(hubs_created)} hubs created"
    )
    return "\n".join(report_lines)


def import_directory(vault: Vault, source_dir: str) -> str:
    """Import .md files from an external directory into the vault."""
    from noteweaver.frontmatter import extract_frontmatter

    candidate = Path(source_dir)
    if not candidate.is_absolute():
        candidate = vault.root / source_dir
    src = candidate.resolve()
    if not src.is_dir():
        return f"Error: not a directory: {source_dir}"

    md_files = sorted(src.rglob("*.md"))
    if not md_files:
        return f"No .md files found in {source_dir}"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    imported = 0
    results = []

    with vault.operation(f"Import {len(md_files)} files from {source_dir}"):
        for f in md_files:
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                results.append(f"  Error reading {f.name}: {e}")
                continue

            fm = extract_frontmatter(content)
            rel_name = f.name
            page_type = fm.get("type") if fm else None

            if page_type == "synthesis":
                dest = f"wiki/synthesis/{rel_name}"
            elif page_type == "journal":
                dest = f"wiki/journals/{rel_name}"
            elif page_type in ("hub", "canonical", "note"):
                dest = f"wiki/concepts/{rel_name}"
            else:
                title = f.stem.replace("-", " ").replace("_", " ").title()
                header = (
                    f"---\ntitle: {title}\ntype: note\n"
                    f"summary: Imported from {f.name}\n"
                    f"tags: [imported]\ncreated: {today}\nupdated: {today}\n---\n\n"
                )
                content = header + content
                dest = f"wiki/concepts/{rel_name}"

            try:
                vault.write_file(dest, content)
                imported += 1
                results.append(f"  ✓ {f.name} → {dest}")
            except Exception as e:
                results.append(f"  Error writing {f.name}: {e}")

        rebuild_index(vault)
        vault.append_log("import", f"Imported {imported} files from {source_dir}")

    summary = f"Imported {imported}/{len(md_files)} files from {source_dir}\n"
    summary += "\n".join(results[:20])
    if len(results) > 20:
        summary += f"\n  ... and {len(results) - 20} more"
    return summary
