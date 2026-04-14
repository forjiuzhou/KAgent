"""Legacy handler stubs — kept for backward compatibility.

DEPRECATED: These handlers pre-date the skills layer.  New code should
use skills (src/noteweaver/skills/) for multi-step workflows:
  - import_sources skill replaces ingest(directory) / capture
  - organize_wiki skill replaces organize / restructure / audit
"""

from __future__ import annotations

import json as _json
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import yaml

from noteweaver.frontmatter import validate_frontmatter, extract_frontmatter
from noteweaver.tools.handlers_read import resolve_path_or_title, handle_fetch_url
from noteweaver.tools.handlers_write import handle_update_frontmatter, handle_add_related_link

if TYPE_CHECKING:
    from noteweaver.vault import Vault


def handle_survey_topic(vault: Vault, topic: str) -> str:
    """Topic assessment — combines search + list + title matching."""
    sections: list[str] = [f"## Topic Survey: {topic}\n"]

    fts_results = vault.search.search(topic, limit=10)
    candidates = []
    for r in fts_results:
        if r["path"].startswith("wiki/"):
            candidates.append(r)

    all_pages = vault.read_frontmatters("wiki")
    topic_lower = topic.lower()
    title_hits = []
    for p in all_pages:
        p_title = str(p.get("title", "")).lower()
        if topic_lower in p_title or p_title in topic_lower:
            if p["path"] not in {c["path"] for c in candidates}:
                title_hits.append(p)

    if candidates or title_hits:
        sections.append("### Candidate pages (could host this content)")
        suggested = None
        for c in candidates[:5]:
            fm_info = next(
                (p for p in all_pages if p["path"] == c["path"]), {}
            )
            type_str = fm_info.get("type", "")
            title_str = c.get("title", "") or fm_info.get("title", "")
            summary = fm_info.get("summary", "")
            bl = vault.backlinks.reference_count(title_str) if title_str else 0
            sections.append(
                f"- **{title_str}** [{type_str}] ({c['path']}) "
                f"— {summary} (backlinks: {bl})"
            )
            if suggested is None and type_str in ("canonical", "hub", "note"):
                suggested = {"title": title_str, "path": c["path"], "type": type_str}
        for p in title_hits[:3]:
            bl = vault.backlinks.reference_count(p.get("title", "")) if p.get("title") else 0
            sections.append(
                f"- **{p.get('title', '?')}** [{p.get('type', '')}] "
                f"({p['path']}) — {p.get('summary', '')} (backlinks: {bl})"
            )
        sections.append("")
    else:
        sections.append("### Candidate pages\nNone found — this appears to be a new topic.\n")
        suggested = None

    related_tags: set[str] = set()
    hub_matches: list[dict] = []
    for p in all_pages:
        tags = p.get("tags") or []
        title = str(p.get("title", "")).lower()
        if topic_lower in title or any(topic_lower in str(t).lower() for t in tags):
            for t in tags:
                related_tags.add(str(t))
            if p.get("type") == "hub":
                hub_matches.append(p)

    if related_tags:
        sections.append(f"### Related tags\n{', '.join(sorted(related_tags))}\n")
    if hub_matches:
        sections.append("### Related hubs")
        for h in hub_matches:
            sections.append(f"- **{h.get('title', '?')}** ({h['path']})")
        sections.append("")

    source_hits = vault.search_content(topic, "sources")
    if source_hits:
        sections.append(f"### Related sources ({len(source_hits)} found)")
        for sh in source_hits[:5]:
            sections.append(f"- {sh['path']}")
        sections.append("")

    bl_sources = vault.backlinks.backlinks_for(topic)
    if bl_sources:
        sections.append(f"### Pages linking to '{topic}'")
        for bl in bl_sources[:10]:
            sections.append(f"- {bl}")
        sections.append("")

    sections.append("### Suggestion")
    if suggested:
        sections.append(
            f"Consider updating **{suggested['title']}** ({suggested['path']}) "
            f"rather than creating a new page."
        )
    else:
        sections.append(
            f"No existing page covers this topic. Creating a new note is appropriate."
        )

    return "\n".join(sections)


def handle_capture(
    vault: Vault,
    content: str,
    title: str,
    tags: list | None = None,
    target: str = "",
    type: str = "note",
) -> str:
    """Legacy capture handler — kept for backward compatibility with tests."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tag_list = tags or []

    if target:
        try:
            resolved = resolve_path_or_title(vault, target)
            existing = vault.read_file(resolved)
        except FileNotFoundError:
            return f"Error: target page not found: {target}"

        section_text = f"\n## {title}\n\n{content}\n"

        related_pattern = re.compile(r"(\n## Related\b)", re.IGNORECASE)
        match = related_pattern.search(existing)
        if match:
            insert_pos = match.start()
            new_content = existing[:insert_pos] + section_text + existing[insert_pos:]
        else:
            new_content = existing.rstrip() + "\n" + section_text

        vault.write_file(resolved, new_content)

        if tag_list:
            fm = extract_frontmatter(new_content)
            if fm:
                existing_tags = fm.get("tags") or []
                merged = list(dict.fromkeys(existing_tags + tag_list))
                if merged != existing_tags:
                    fm["tags"] = merged
                    fm_str = yaml.dump(
                        fm, default_flow_style=False, allow_unicode=True,
                    ).strip()
                    from noteweaver.frontmatter import FRONTMATTER_PATTERN
                    body = FRONTMATTER_PATTERN.sub("", new_content, count=1)
                    vault.write_file(resolved, f"---\n{fm_str}\n---\n{body}")

        return f"OK: appended section '{title}' to {resolved}"

    _ALLOWED_TYPES = {"note", "canonical", "synthesis"}
    if type not in _ALLOWED_TYPES:
        type = "note"

    slug = str(title).lower().replace(" ", "-").replace("/", "-")
    slug = re.sub(r"[^a-z0-9\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff-]", "", slug)[:60]
    slug = re.sub(r"-{2,}", "-", slug).strip("-")

    if type == "synthesis":
        path = f"wiki/synthesis/{slug}.md"
    else:
        path = f"wiki/concepts/{slug}.md"

    tag_str = ", ".join(tag_list) if tag_list else ""
    sources_line = ""
    if type == "canonical":
        sources_line = "sources: []\n"

    fm = (
        f"---\ntitle: {title}\ntype: {type}\n"
        f"summary: \ntags: [{tag_str}]\n"
        f"{sources_line}"
        f"created: {today}\nupdated: {today}\n---\n\n"
    )
    body = f"# {title}\n\n{content}\n\n## Related\n"
    full_content = fm + body

    validation = validate_frontmatter(path, full_content)
    if not validation.valid:
        return "Error: frontmatter validation failed:\n" + "\n".join(
            f"  - {e}" for e in validation.errors
        )

    try:
        vault.write_file(path, full_content)
    except PermissionError as e:
        return f"Error: {e}"

    return f"OK: created {type} page at {path}"


def handle_ingest(
    vault: Vault,
    source: str,
    source_type: str,
    save_raw: bool = True,
    organize: bool = False,
) -> str:
    """Legacy ingest handler — kept for backward compatibility."""
    if source_type == "url":
        return _ingest_url(vault, source, save_raw)
    elif source_type == "file":
        return _ingest_file(vault, source, save_raw)
    elif source_type == "directory":
        return _ingest_directory(vault, source, organize)
    else:
        return f"Error: unknown source_type '{source_type}'. Use 'url', 'file', or 'directory'."


def _ingest_url(vault: Vault, url: str, save_raw: bool) -> str:
    fetched = handle_fetch_url(vault, url)
    if fetched.startswith("Error"):
        return fetched
    results = [f"Fetched: {url}"]
    if save_raw:
        slug = re.sub(r"[^a-z0-9-]", "", url.split("//")[-1].split("?")[0].replace("/", "-"))[:60]
        source_path = f"sources/web/{slug}.md"
        try:
            vault.save_source(source_path, fetched)
            results.append(f"Saved raw to {source_path}")
        except PermissionError:
            results.append(f"Source already exists at {source_path} (skipped)")
    results.append(f"\nContent preview ({len(fetched)} chars):\n{fetched[:2000]}")
    if len(fetched) > 2000:
        results.append("... (truncated preview)")
    results.append("\nUse write_page() or append_section() to add to the wiki.")
    return "\n".join(results)


def _ingest_file(vault: Vault, file_path: str, save_raw: bool) -> str:
    from pathlib import Path
    p = Path(file_path)
    if not p.is_absolute():
        p = vault.root / file_path
    p = p.resolve()
    if not p.is_file():
        return f"Error: file not found: {file_path}"
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Error reading {file_path}: {e}"
    results = [f"Read: {file_path} ({len(content)} chars)"]
    if save_raw:
        source_path = f"sources/files/{p.name}"
        try:
            vault.save_source(source_path, content)
            results.append(f"Saved raw to {source_path}")
        except PermissionError:
            results.append(f"Source already exists at {source_path} (skipped)")
    results.append(f"\nContent preview:\n{content[:2000]}")
    if len(content) > 2000:
        results.append("... (truncated preview)")
    results.append("\nUse write_page() or append_section() to add to the wiki.")
    return "\n".join(results)


def _ingest_directory(vault: Vault, directory: str, do_organize: bool) -> str:
    result = vault.import_directory(directory)
    return result


def handle_organize(
    vault: Vault,
    target: str,
    action: str,
    reason: str = "",
    metadata: dict | None = None,
    link_to: str = "",
) -> str:
    """Legacy organize handler — kept for backward compatibility with tests."""
    if action == "archive":
        return _organize_archive(vault, target, reason)
    elif action == "update_metadata":
        return _organize_update_metadata(vault, target, metadata or {})
    elif action == "classify":
        return _organize_classify(vault, target)
    elif action == "link":
        return _organize_link(vault, target, link_to)
    else:
        return f"Error: unknown action '{action}'"


def _organize_archive(vault: Vault, path: str, reason: str) -> str:
    try:
        content = vault.read_file(path)
    except FileNotFoundError:
        return f"Error: file not found: {path}"

    filename = path.rsplit("/", 1)[-1]
    archive_path = f"wiki/archive/{filename}"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    from noteweaver.frontmatter import FRONTMATTER_PATTERN
    fm = extract_frontmatter(content)
    if fm:
        fm["type"] = "archive"
        fm["archived"] = today
        if reason:
            fm["archive_reason"] = reason
        fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
        body = FRONTMATTER_PATTERN.sub("", content, count=1)
        new_content = f"---\n{fm_str}\n---\n{body}"
    else:
        new_content = content

    vault.write_file(archive_path, new_content)

    original = vault._resolve(path)
    if original.is_file():
        original.unlink()
    vault.search.remove(path)
    vault.backlinks.remove_page(path)

    return f"OK: archived {path} → {archive_path}" + (f" ({reason})" if reason else "")


def _organize_update_metadata(vault: Vault, path: str, fields: dict) -> str:
    return handle_update_frontmatter(vault, path, fields)


def _organize_classify(vault: Vault, target: str) -> str:
    if target == "imported":
        return vault.scan_imports()
    try:
        content = vault.read_file(target)
    except FileNotFoundError:
        return f"Error: file not found: {target}"
    fm = extract_frontmatter(content)
    headings = [
        line.strip() for line in content.split("\n")
        if re.match(r"^#{1,4}\s", line)
    ]
    info = {
        "path": target,
        "frontmatter": fm or {},
        "headings": headings,
        "length": len(content),
        "preview": content[:1000],
    }
    return _json.dumps(info, ensure_ascii=False, indent=2)


def _organize_link(vault: Vault, path: str, link_to: str) -> str:
    return handle_add_related_link(vault, path, link_to)


def handle_restructure(
    vault: Vault,
    scope: str,
    action: str,
    old_tag: str = "",
    new_tag: str = "",
) -> str:
    """Legacy restructure handler — kept for backward compatibility."""
    if action == "merge_tags":
        return _restructure_merge_tags(vault, old_tag, new_tag)
    elif action == "deduplicate":
        return _restructure_deduplicate(vault, scope)
    elif action == "rebuild_hubs":
        return _restructure_rebuild_hubs(vault, scope)
    elif action == "audit":
        return _restructure_audit(vault)
    else:
        return f"Error: unknown action '{action}'"


def _restructure_merge_tags(vault: Vault, old_tag: str, new_tag: str) -> str:
    old_normalized = vault.normalize_tag(old_tag)
    new_normalized = vault.normalize_tag(new_tag)
    if not old_normalized or not new_normalized:
        return "Error: tags cannot be empty."
    if old_normalized == new_normalized:
        return f"Tags are already the same after normalization: '{old_normalized}'"

    from noteweaver.frontmatter import FRONTMATTER_PATTERN

    updated_files = 0
    for rel_path in vault.list_files("wiki"):
        try:
            content = vault.read_file(rel_path)
        except (FileNotFoundError, PermissionError):
            continue
        fm = extract_frontmatter(content)
        if not fm or not fm.get("tags") or not isinstance(fm["tags"], list):
            continue
        normalized_tags = [vault.normalize_tag(t) for t in fm["tags"]]
        if old_normalized not in normalized_tags:
            continue
        new_tags = []
        for t in normalized_tags:
            if t == old_normalized:
                if new_normalized not in new_tags:
                    new_tags.append(new_normalized)
            else:
                if t not in new_tags:
                    new_tags.append(t)
        fm["tags"] = new_tags
        fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
        body = FRONTMATTER_PATTERN.sub("", content, count=1)
        new_content = f"---\n{fm_str}\n---\n{body}"
        vault.write_file(rel_path, new_content)
        updated_files += 1

    if updated_files == 0:
        return f"No pages found with tag '{old_normalized}'."
    return (
        f"OK: merged tag '{old_normalized}' → '{new_normalized}' "
        f"in {updated_files} file(s)."
    )


def _restructure_deduplicate(vault: Vault, scope: str) -> str:
    all_pages = vault.read_frontmatters("wiki")
    content_pages = [
        p for p in all_pages
        if p.get("type") not in ("hub", "journal", "archive")
        and p.get("path") not in ("wiki/index.md", "wiki/log.md")
    ]

    if scope.startswith("tag:"):
        tag = scope.split(":", 1)[1]
        content_pages = [
            p for p in content_pages if tag in (p.get("tags") or [])
        ]
    elif scope.startswith("topic:"):
        topic = scope.split(":", 1)[1].lower()
        content_pages = [
            p for p in content_pages
            if topic in str(p.get("title", "")).lower()
            or any(topic in str(t).lower() for t in (p.get("tags") or []))
        ]

    if len(content_pages) < 2:
        return "Not enough pages to check for duplicates."

    duplicates: list[dict] = []
    checked: set[tuple[str, str]] = set()

    for i, pa in enumerate(content_pages):
        for pb in content_pages[i + 1:]:
            pair = (pa["path"], pb["path"])
            if pair in checked:
                continue
            checked.add(pair)
            reason = vault._similar_tag_reason(
                str(pa.get("title", "")).lower(),
                str(pb.get("title", "")).lower(),
            )
            if reason:
                duplicates.append({
                    "page_a": pa["path"],
                    "title_a": pa.get("title", ""),
                    "page_b": pb["path"],
                    "title_b": pb.get("title", ""),
                    "reason": reason,
                })

    if not duplicates:
        return f"No potential duplicates found in {scope}."

    lines = [f"Found {len(duplicates)} potential duplicate pair(s):"]
    for d in duplicates[:20]:
        lines.append(
            f"  - **{d['title_a']}** ({d['page_a']}) ↔ "
            f"**{d['title_b']}** ({d['page_b']}) — {d['reason']}"
        )
    lines.append(
        "\nReview these pages and use write_page to merge their content."
    )
    return "\n".join(lines)


def _restructure_rebuild_hubs(vault: Vault, scope: str) -> str:
    all_pages = vault.read_frontmatters("wiki")
    tag_pages: dict[str, list[dict]] = {}
    existing_hubs: set[str] = set()

    for p in all_pages:
        if p.get("path") in ("wiki/index.md", "wiki/log.md"):
            continue
        if p.get("type") == "hub":
            for t in (p.get("tags") or []):
                existing_hubs.add(str(t).lower())
            continue
        if p.get("type") in ("journal", "archive"):
            continue
        for t in (p.get("tags") or []):
            if t not in ("imported", "journal", "pinned"):
                tag_pages.setdefault(str(t), []).append(p)

    candidates = []
    for tag, pages in tag_pages.items():
        if len(pages) >= 3 and tag.lower() not in existing_hubs:
            candidates.append({"tag": tag, "pages": pages})

    if not candidates:
        vault.rebuild_index()
        return "No new hubs needed. Index rebuilt."

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    created = []

    with vault.operation("Rebuild hubs"):
        for c in candidates:
            tag = c["tag"]
            hub_slug = str(tag).lower().replace(" ", "-")
            hub_slug = re.sub(r"[^a-z0-9-]", "", hub_slug)
            hub_slug = re.sub(r"-{2,}", "-", hub_slug).strip("-")[:60]
            hub_path = f"wiki/concepts/{hub_slug}.md"

            page_titles = [p.get("title", "") for p in c["pages"] if p.get("title")]
            links_block = "\n".join(f"- [[{pt}]]" for pt in page_titles[:15])
            hub_content = (
                f"---\ntitle: {tag.title()}\ntype: hub\n"
                f"summary: Hub for {tag} topics\n"
                f"tags: [{tag}]\n"
                f"created: {today}\nupdated: {today}\n---\n\n"
                f"# {tag.title()}\n\n"
                f"## Pages\n\n{links_block}\n\n## Related\n"
            )
            try:
                vault.write_file(hub_path, hub_content)
                created.append(f"{tag.title()} ({len(page_titles)} pages)")
            except Exception:
                pass

        vault.rebuild_index()

    if created:
        return f"Created {len(created)} hub(s): {', '.join(created)}. Index rebuilt."
    return "No new hubs created. Index rebuilt."


def _restructure_audit(vault: Vault) -> str:
    report = vault.audit_vault()
    vault.save_audit_report(report)

    lines = [f"**Audit Result:** {report.get('summary', 'No issues')}"]
    for key, label in [
        ("stale_imports", "Stale imports"),
        ("hub_candidates", "Hub candidates"),
        ("orphan_pages", "Orphan pages"),
        ("missing_summaries", "Missing summaries"),
        ("broken_links", "Broken links"),
        ("missing_connections", "Missing connections"),
        ("similar_tags", "Similar tag pairs"),
    ]:
        items = report.get(key, [])
        if items:
            lines.append(f"\n**{label}** ({len(items)}):")
            for item in items[:5]:
                if isinstance(item, str):
                    lines.append(f"  - {item}")
                elif isinstance(item, dict):
                    lines.append(f"  - {_json.dumps(item, ensure_ascii=False)}")
            if len(items) > 5:
                lines.append(f"  ... and {len(items) - 5} more")

    return "\n".join(lines)
