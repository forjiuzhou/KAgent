"""Tool definitions for LLM function calling.

V2 primitive tool set — low-semantic file operations.

Read tools (always available):
  read_page, search, get_backlinks, list_pages, fetch_url

Write tools (always available during chat, policy gates enforce safety):
  write_page, append_section, update_frontmatter, add_related_link

Tool sets:
  TOOL_SCHEMAS       — all tools (single set, used in both chat and execute_plan)
  OBSERVATION_SCHEMAS — read-only subset (for reference / unattended mode)
  SUBMIT_PLAN_SCHEMA — session-organize only (used by generate_organize_plan)
"""

from __future__ import annotations

import json as _json
import re
from datetime import datetime, timezone
from typing import Any

import yaml

from noteweaver.vault import Vault
from noteweaver.frontmatter import validate_frontmatter, extract_frontmatter

# ======================================================================
# Tool schemas (OpenAI function calling format)
# ======================================================================

TOOL_SCHEMAS: list[dict] = [
    # ------------------------------------------------------------------
    # Read tools
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": (
                "Read a file from the vault. Accepts a file path "
                "(e.g. 'wiki/concepts/attention.md') or a page title "
                "(e.g. 'Attention Mechanism'). Use max_chars for a quick "
                "relevance check. Use section to read only a specific section."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path or page title",
                    },
                    "section": {
                        "type": "string",
                        "description": "Optional heading to read only that section (without ##)",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Max characters to read. Use ~500 for quick check.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": (
                "Search the knowledge base by keyword or topic. Returns ranked "
                "results with title, type, summary, tags, backlink count, and "
                "updated date. Use scope to limit search area. "
                "Also checks title similarity to find near-matches."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (keywords or topic)",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["wiki", "sources", "all"],
                        "description": "Where to search. Default: 'all'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_backlinks",
            "description": (
                "Find all pages that link to a given page title via "
                "[[wiki-links]]. Use to understand how a concept is connected."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The page title to find backlinks for",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_pages",
            "description": (
                "List pages in a directory with metadata (title, type, "
                "summary, tags). Set include_raw=true to also see non-markdown "
                "files and files without frontmatter."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Directory to scan, e.g. 'wiki', 'sources'. Default: 'wiki'",
                        "default": "wiki",
                    },
                    "include_raw": {
                        "type": "boolean",
                        "description": "Include non-markdown files and files without frontmatter. Default: false",
                        "default": False,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Fetch a web page and extract its content as markdown. "
                "Use this to preview or import a URL's content."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch",
                    },
                },
                "required": ["url"],
            },
        },
    },
    # ------------------------------------------------------------------
    # Write tools
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "write_page",
            "description": (
                "Create or overwrite a full wiki page. You MUST read the "
                "target page first (read_page) before overwriting an existing "
                "page. Always include YAML frontmatter with title, type, "
                "created/updated dates, summary, tags."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path in wiki/, e.g. 'wiki/concepts/attention.md'",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full markdown content including YAML frontmatter",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_section",
            "description": (
                "Append a new section to an existing wiki page. The section "
                "is inserted before the ## Related section if one exists, "
                "otherwise appended at the end. Use this for incremental "
                "additions to existing pages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the existing wiki page",
                    },
                    "heading": {
                        "type": "string",
                        "description": "Heading for the new section",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown content for the new section",
                    },
                },
                "required": ["path", "heading", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_frontmatter",
            "description": (
                "Update specific frontmatter fields on an existing wiki page. "
                "Only the fields you specify will be changed; all other fields "
                "and the page body are preserved."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the wiki page to update",
                    },
                    "fields": {
                        "type": "object",
                        "description": "Frontmatter fields to update, e.g. {\"tags\": [\"ai\"], \"summary\": \"...\"}",
                    },
                },
                "required": ["path", "fields"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_related_link",
            "description": (
                "Add a [[wiki-link]] to the Related section of a page. "
                "Creates the ## Related section if it doesn't exist. "
                "Skips if the link already exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the wiki page",
                    },
                    "link_to": {
                        "type": "string",
                        "description": "Page title to link to",
                    },
                },
                "required": ["path", "link_to"],
            },
        },
    },
]


# ------------------------------------------------------------------
# Tool subsets
# ------------------------------------------------------------------

_OBSERVATION_TOOL_NAMES = frozenset({
    "read_page", "search", "get_backlinks", "list_pages", "fetch_url",
})

OBSERVATION_SCHEMAS: list[dict] = [
    s for s in TOOL_SCHEMAS
    if s["function"]["name"] in _OBSERVATION_TOOL_NAMES
]

# V2: no separate CHAT_TOOL_SCHEMAS — agent always has full tool set.
# Alias kept for imports that reference the old name.
CHAT_TOOL_SCHEMAS: list[dict] = TOOL_SCHEMAS

# submit_plan is used only by generate_organize_plan() for session-
# organize proposals.  It is NOT included in the chat tool set.
SUBMIT_PLAN_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "submit_plan",
        "description": (
            "Propose a batch of changes as a plan (used by session-organize "
            "flow only — not available during normal chat)."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


# ======================================================================
# Tool handlers
# ======================================================================

def _resolve_path_or_title(vault: Vault, path_or_title: str) -> str:
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


def _extract_section(content: str, heading: str) -> str | None:
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


# ------------------------------------------------------------------
# Read tool handlers
# ------------------------------------------------------------------

def handle_read_page(
    vault: Vault, path: str, section: str = "", max_chars: int = 0,
) -> str:
    try:
        resolved = _resolve_path_or_title(vault, path)
        if max_chars and max_chars > 0 and not section:
            content = vault.read_file_partial(resolved, max_chars)
            if len(content) >= max_chars:
                content += "\n\n... (truncated, use read_page without max_chars for full)"
            return content
        content = vault.read_file(resolved)
        if section:
            extracted = _extract_section(content, section)
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
            url, follow_redirects=True, timeout=30, headers=headers
        )
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type and "text/" not in content_type:
            return f"Error: URL returned non-text content ({content_type})"

        doc = Document(resp.text)
        title = doc.title() or "Untitled"
        html_content = doc.summary()
        md_content = markdownify(html_content, heading_style="ATX", strip=["img"])

        max_chars = 15000
        truncated = ""
        if len(md_content) > max_chars:
            md_content = md_content[:max_chars]
            truncated = f"\n\n(Content truncated at {max_chars} characters)"

        header = f"# {title}\n\nSource: {url}\n\n---\n\n"
        return header + md_content.strip() + truncated
    except Exception as e:
        return f"Error fetching {url}: {type(e).__name__}: {e}"


# ------------------------------------------------------------------
# Write tool handlers
# ------------------------------------------------------------------

INDEX_TOKEN_BUDGET = 4000


def handle_write_page(vault: Vault, path: str, content: str) -> str:
    try:
        if not path.startswith("wiki/"):
            return f"Error: write_page can only write to wiki/. Rejected path: {path}"
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
        resolved = _resolve_path_or_title(vault, path)
        existing = vault.read_file(resolved)
    except FileNotFoundError:
        return f"Error: page not found: {path}"
    except PermissionError as e:
        return f"Error: {e}"

    if not resolved.startswith("wiki/"):
        return f"Error: can only write to wiki/ pages. Path: {resolved}"

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


# ======================================================================
# Legacy handler stubs (for backward compatibility with tests)
# ======================================================================

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
            resolved = _resolve_path_or_title(vault, target)
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


# ======================================================================
# Dispatch
# ======================================================================

TOOL_HANDLERS: dict[str, Any] = {
    "read_page": handle_read_page,
    "search": handle_search,
    "get_backlinks": handle_get_backlinks,
    "list_pages": handle_list_pages,
    "fetch_url": handle_fetch_url,
    "write_page": handle_write_page,
    "append_section": handle_append_section,
    "update_frontmatter": handle_update_frontmatter,
    "add_related_link": handle_add_related_link,
    # Legacy handlers (not in TOOL_SCHEMAS but still dispatchable for tests)
    "survey_topic": handle_survey_topic,
    "capture": handle_capture,
    "ingest": handle_ingest,
    "organize": handle_organize,
    "restructure": handle_restructure,
}


def dispatch_tool(vault: Vault, name: str, arguments: dict) -> str:
    """Execute a tool call and return the result as a string."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return f"Error: unknown tool '{name}'"

    import inspect
    sig = inspect.signature(handler)
    valid_params = set(sig.parameters.keys()) - {"vault"}
    filtered_args = {k: v for k, v in arguments.items() if k in valid_params}

    try:
        return handler(vault, **filtered_args)
    except TypeError as e:
        return f"Error calling {name}: {e}. Arguments received: {list(arguments.keys())}"
