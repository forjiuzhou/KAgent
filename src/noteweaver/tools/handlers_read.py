"""Read tool handlers: read_page, search, get_backlinks, list_pages, fetch_url."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from noteweaver.constants import (
    FETCH_URL_TIMEOUT,
    FETCH_URL_MAX_CHARS,
)
from noteweaver.frontmatter import extract_frontmatter

if TYPE_CHECKING:
    from noteweaver.vault import Vault


def resolve_path_or_title(vault: Vault, path_or_title: str) -> str:
    """Resolve a path-or-title argument to an actual file path."""
    if "/" in path_or_title or path_or_title.endswith(".md"):
        return path_or_title
    resolved = vault.resolve_title(path_or_title)
    if resolved is None:
        raise FileNotFoundError(
            f"No page with title '{path_or_title}'. "
            "Use search or list_pages to find pages."
        )
    return resolved


def extract_section(content: str, heading: str) -> str | None:
    """Extract a section by heading from markdown content."""
    lines = content.split("\n")
    pattern = re.compile(
        r"^(#{1,6})\s+" + re.escape(heading) + r"\s*$", re.IGNORECASE,
    )
    start_idx = None
    start_level = None
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if m:
            start_idx = i
            start_level = len(m.group(1))
            break
    if start_idx is None:
        return None
    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        m = re.match(r"^(#{1,6})\s", lines[i])
        if m and len(m.group(1)) <= start_level:
            end_idx = i
            break
    return "\n".join(lines[start_idx:end_idx]).strip()


def handle_read_page(
    vault: Vault, path: str, section: str = "", max_chars: int = 0,
) -> str:
    try:
        resolved = resolve_path_or_title(vault, path)
        if max_chars and max_chars > 0 and not section:
            content = vault.read_file_partial(resolved, max_chars)
            if len(content) >= max_chars:
                content += "\n\n... (truncated, use read_page without max_chars for full)"
            return content
        content = vault.read_file(resolved)
        if section:
            extracted = extract_section(content, section)
            if extracted is None:
                headings = [
                    line.strip() for line in content.split("\n")
                    if re.match(r"^#{1,6}\s", line)
                ]
                return (
                    f"Error: section '{section}' not found in {resolved}. "
                    f"Available sections: {', '.join(headings[:15])}"
                )
            if max_chars and max_chars > 0 and len(extracted) > max_chars:
                extracted = extracted[:max_chars] + "\n\n... (truncated)"
            return extracted
        return content
    except FileNotFoundError as e:
        return f"Error: {e}"
    except PermissionError as e:
        return f"Error: {e}"


def handle_search(vault: Vault, query: str, scope: str = "all") -> str:
    results_wiki = []
    results_sources = []

    if scope in ("wiki", "all"):
        results_wiki = vault.search_content(query, "wiki")
    if scope in ("sources", "all"):
        results_sources = vault.search_content(query, "sources")

    all_results = results_wiki + results_sources
    if not all_results:
        return f"No results found for '{query}' in {scope}."

    frontmatters_wiki = {
        p["path"]: p for p in vault.read_frontmatters("wiki")
    }
    frontmatters_src = {}
    if scope in ("sources", "all"):
        frontmatters_src = {
            p["path"]: p for p in vault.read_frontmatters("sources")
        }
    all_fm = {**frontmatters_wiki, **frontmatters_src}

    title_lower = query.lower()
    title_matches = []
    for p in frontmatters_wiki.values():
        p_title = str(p.get("title", "")).lower()
        if title_lower in p_title or p_title in title_lower:
            if p["path"] not in {r["path"] for r in all_results}:
                title_matches.append(p)

    lines = []
    for r in all_results[:10]:
        path = r["path"]
        fm = all_fm.get(path, {})
        area = "wiki" if path.startswith("wiki/") else "sources"
        type_str = f" [{fm.get('type', '')}]" if fm.get("type") else ""
        title_str = fm.get("title", "")
        header = f"\n**{title_str}**{type_str} ({path})" if title_str else f"\n**{path}**"
        lines.append(header)
        if fm.get("summary"):
            lines.append(f"  Summary: {fm['summary']}")
        if fm.get("tags"):
            lines.append(f"  Tags: {', '.join(fm['tags'])}")
        bl_count = vault.backlinks.reference_count(title_str) if title_str else 0
        if bl_count > 0:
            lines.append(f"  Backlinks: {bl_count}")
        try:
            content = vault.read_file(path)
            page_fm = extract_frontmatter(content)
            if page_fm and page_fm.get("updated"):
                lines.append(f"  Updated: {page_fm['updated']}")
        except (FileNotFoundError, PermissionError):
            pass
        lines.append(f"  Area: {area}")
        for line_no, line_text in r["matches"][:3]:
            lines.append(f"  L{line_no}: {line_text}")

    if title_matches:
        lines.append("\n**Title matches:**")
        for p in title_matches[:5]:
            summary = f" — {p['summary']}" if p.get("summary") else ""
            lines.append(
                f"  - [{p.get('type', '?')}] **{p.get('title', '?')}** "
                f"({p['path']}){summary}"
            )

    return "\n".join(lines)


def handle_get_backlinks(vault: Vault, title: str) -> str:
    sources = vault.backlinks.backlinks_for(title)
    if not sources:
        return f"No pages link to '{title}'."
    lines = [f"**{len(sources)} page(s) link to '{title}':**"]
    for s in sources[:20]:
        lines.append(f"  - {s}")
    return "\n".join(lines)


def handle_list_pages(
    vault: Vault, directory: str = "wiki", include_raw: bool = False,
) -> str:
    if include_raw:
        files = vault.list_all_files(directory)
        if not files:
            base = vault._resolve(directory)
            if not base.is_dir():
                return f"Error: directory not found: {directory}/"
            return f"No files found in {directory}/"

        def _fmt_size(n: int) -> str:
            if n < 1024:
                return f"{n}B"
            if n < 1024 * 1024:
                return f"{n / 1024:.1f}KB"
            return f"{n / (1024 * 1024):.1f}MB"

        lines = [f"Files in {directory}/ ({len(files)} total):"]
        by_suffix: dict[str, int] = {}
        for f in files:
            by_suffix[f["suffix"]] = by_suffix.get(f["suffix"], 0) + 1
            lines.append(f"  {f['path']}  ({_fmt_size(f['size_bytes'])})")
        lines.append("")
        type_summary = ", ".join(
            f"{ext or '(no ext)'}: {cnt}" for ext, cnt in sorted(by_suffix.items())
        )
        lines.append(f"File types: {type_summary}")
        return "\n".join(lines)

    results = vault.read_frontmatters(directory)
    if not results:
        return f"No files found in {directory}/"

    structured = [r for r in results if r.get("has_frontmatter", True)]
    unstructured = [r for r in results if not r.get("has_frontmatter", True)]

    lines = [f"Page cards for {directory}/ ({len(results)} pages):"]
    lines.append("")
    for r in structured:
        tags_str = f"  tags: {', '.join(r['tags'])}" if r['tags'] else ""
        summary_str = f"  summary: {r['summary']}" if r['summary'] else ""
        updated_str = f"  updated: {r['updated']}" if r.get('updated') else ""
        lines.append(f"- [{r['type']}] **{r['title']}** → {r['path']}")
        if summary_str:
            lines.append(f"  {summary_str.strip()}")
        detail_parts = []
        if tags_str:
            detail_parts.append(tags_str.strip())
        if updated_str:
            detail_parts.append(updated_str.strip())
        if detail_parts:
            lines.append(f"  {' | '.join(detail_parts)}")

    if unstructured:
        lines.append("")
        lines.append(f"Also found {len(unstructured)} file(s) without frontmatter:")
        for r in unstructured:
            lines.append(f"  - **{r['title']}** ({r['path']})")
        lines.append("")
        lines.append(
            "These files lack structured metadata. "
            "Use read_page(path) to read them."
        )

    return "\n".join(lines)


def handle_audit_vault(vault: Vault) -> str:
    """Run audit_vault and format the result as a readable report."""
    from noteweaver.vault.audit import audit_vault

    report = audit_vault(vault)
    lines = [f"## Vault Audit\n\n{report.get('summary', 'No summary')}\n"]

    sections = [
        ("missing_frontmatter", "Missing Frontmatter", lambda items: [f"- {p}" for p in items]),
        ("orphan_pages", "Orphan Pages", lambda items: [f"- {p}" for p in items]),
        ("broken_links", "Broken Links", lambda items: [
            f"- {bl['page']}: [[{bl['link_title']}]]" for bl in items
        ]),
        ("missing_summaries", "Missing Summaries", lambda items: [f"- {p}" for p in items]),
        ("hub_candidates", "Hub Candidates", lambda items: [
            f"- tag '{hc['tag']}' ({hc['page_count']} pages)" for hc in items
        ]),
        ("stale_imports", "Stale Imports", lambda items: [
            f"- {si['path']} ({si['days_since_update']}d)" for si in items
        ]),
        ("missing_connections", "Missing Connections", lambda items: [
            f"- {mc['page_a']} ↔ {mc['page_b']} (shared: {', '.join(mc['shared_tags'][:3])})"
            for mc in items
        ]),
        ("similar_tags", "Similar Tags", lambda items: [
            f"- '{st['tag_a']}' ≈ '{st['tag_b']}' ({st['reason']})" for st in items
        ]),
    ]
    for key, title, formatter in sections:
        items = report.get(key, [])
        if items:
            lines.append(f"### {title} ({len(items)})")
            lines.extend(formatter(items[:20]))
            if len(items) > 20:
                lines.append(f"... and {len(items) - 20} more")
            lines.append("")

    return "\n".join(lines)


def handle_fetch_url(vault: Vault, url: str) -> str:
    try:
        import httpx
        from readability import Document
        from markdownify import markdownify

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; NoteWeaver/0.1; "
                "+https://github.com/forjiuzhou/KAgent)"
            ),
        }
        resp = httpx.get(
            url, follow_redirects=True, timeout=FETCH_URL_TIMEOUT, headers=headers
        )
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "text/" not in content_type:
            return f"Error: URL returned non-text content ({content_type})"

        doc = Document(resp.text)
        title = doc.title() or "Untitled"
        html_content = doc.summary()
        md_content = markdownify(html_content, heading_style="ATX", strip=["img"])

        max_chars = FETCH_URL_MAX_CHARS
        truncated = ""
        if len(md_content) > max_chars:
            md_content = md_content[:max_chars]
            truncated = f"\n\n(Content truncated at {max_chars} characters)"

        header = f"# {title}\n\nSource: {url}\n\n---\n\n"
        return header + md_content.strip() + truncated
    except Exception as e:
        return f"Error fetching {url}: {type(e).__name__}: {e}"
