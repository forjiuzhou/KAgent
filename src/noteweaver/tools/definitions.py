"""Tool definitions for LLM function calling.

Redesigned around user-level knowledge operations rather than file-level edits.

Observation tools (read-only, execute immediately):
  read_page, search, survey_topic, get_backlinks, list_pages, fetch_url

Planning tool (used during chat to submit change proposals):
  submit_plan — submit a natural-language change proposal for user approval

Action tools (used during plan execution after user approval):
  capture, ingest, organize, restructure, write_page

Automated (no longer tools — system handles internally):
  append_log → auto after plan execution
  add_related_link → auto via _ensure_progressive_disclosure

Tool sets:
  TOOL_SCHEMAS       — all tools (used during plan execution)
  CHAT_TOOL_SCHEMAS  — observation + submit_plan (used during chat)
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
    # Observation tools (read-only)
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
            "name": "survey_topic",
            "description": (
                "Assess a topic against the current knowledge base BEFORE "
                "planning any writes. Returns: candidate pages that could "
                "host the content, related tags/hubs, source material status, "
                "backlink connections, and a suggested landing page. "
                "This is your primary planning tool — call it before making "
                "any knowledge capture plan."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The topic to survey",
                    },
                },
                "required": ["topic"],
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
                "Use this to preview a URL's content during conversation. "
                "To actually import a URL into the knowledge base, use "
                "ingest(source_type='url') instead."
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
    # Action tools (trigger plan mode)
    # ------------------------------------------------------------------
    {
        "type": "function",
        "function": {
            "name": "capture",
            "description": (
                "Record knowledge into the vault. This is your primary write "
                "tool for all knowledge capture: quick ideas, conversation "
                "insights, new topics, additions to existing pages.\n\n"
                "If target is given: appends content as a new section to that page.\n"
                "If target is omitted: creates a new page (note by default). "
                "Duplicates at note level are OK — organize/restructure can "
                "merge them later.\n\n"
                "The system automatically handles: frontmatter, related links, "
                "hub linking, and operation logging."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "The knowledge content to capture (markdown)",
                    },
                    "title": {
                        "type": "string",
                        "description": "Title for the page or section heading",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags for categorization",
                    },
                    "target": {
                        "type": "string",
                        "description": "Optional: path of existing page to append to",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["note", "canonical", "synthesis"],
                        "description": "Page type when creating new. Default: 'note'",
                    },
                },
                "required": ["content", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ingest",
            "description": (
                "Bring external content into the knowledge base.\n\n"
                "source_type='url': fetches the URL, saves raw content to "
                "sources/, and creates wiki page(s) with key information.\n"
                "source_type='file': reads a single file, optionally saves "
                "to sources/, creates a wiki page.\n"
                "source_type='directory': batch imports all .md files, "
                "classifies by frontmatter, optionally triggers organization."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "URL, file path, or directory path",
                    },
                    "source_type": {
                        "type": "string",
                        "enum": ["url", "file", "directory"],
                        "description": "What kind of source this is",
                    },
                    "save_raw": {
                        "type": "boolean",
                        "description": "Save raw content to sources/. Default: true",
                    },
                    "organize": {
                        "type": "boolean",
                        "description": "Trigger organization after import (for directory). Default: false",
                    },
                },
                "required": ["source", "source_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "organize",
            "description": (
                "Organize specific pages or small groups of pages.\n\n"
                "Actions:\n"
                "- classify: update type, tags, summary for imported pages\n"
                "- update_metadata: update specific frontmatter fields\n"
                "- archive: move page to archive (requires reason)\n"
                "- link: add related links between pages"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Page path, or 'imported' for all imported pages",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["classify", "update_metadata", "archive", "link"],
                        "description": "What kind of organization to perform",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Required for archive action",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Fields to update for update_metadata action",
                    },
                    "link_to": {
                        "type": "string",
                        "description": "Page title to link to (for link action)",
                    },
                },
                "required": ["target", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restructure",
            "description": (
                "Vault-wide structural changes.\n\n"
                "Actions:\n"
                "- merge_tags: replace old_tag with new_tag across all pages\n"
                "- deduplicate: find and flag duplicate pages for review\n"
                "- rebuild_hubs: create/update hubs for topics with 3+ pages\n"
                "- audit: run full vault health check and report issues"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "description": "Scope: 'vault', 'topic:X', or 'tag:X'",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["merge_tags", "deduplicate", "rebuild_hubs", "audit"],
                        "description": "What structural change to perform",
                    },
                    "old_tag": {
                        "type": "string",
                        "description": "Tag to replace (for merge_tags)",
                    },
                    "new_tag": {
                        "type": "string",
                        "description": "Replacement tag (for merge_tags)",
                    },
                },
                "required": ["scope", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_page",
            "description": (
                "Create or overwrite a full wiki page. This is the precise "
                "control tool — use it when capture doesn't give enough "
                "control over page structure. You MUST read the target page "
                "first (read_page) before overwriting. Always include YAML "
                "frontmatter with title, type, created/updated dates."
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
]

# ------------------------------------------------------------------
# Planning tool (chat phase only)
# ------------------------------------------------------------------

SUBMIT_PLAN_SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "submit_plan",
        "description": (
            "Submit a knowledge base change proposal for user review. "
            "Call this after surveying the topic and forming a plan. "
            "Describe WHAT should change and WHY — the system handles "
            "precise implementation after user approval.\n\n"
            "change_type:\n"
            "- 'incremental': appending to existing pages, adding links, "
            "updating metadata — executes immediately, user is notified\n"
            "- 'structural': creating new pages, new hubs, archiving, "
            "restructuring — requires explicit user approval"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": (
                        "Human-readable description of the proposed changes. "
                        "Be specific about what content goes where."
                    ),
                },
                "targets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "File paths that will be affected "
                        "(e.g. 'wiki/concepts/react.md')"
                    ),
                },
                "rationale": {
                    "type": "string",
                    "description": (
                        "Why this change is appropriate — what context "
                        "supports this decision"
                    ),
                },
                "intent": {
                    "type": "string",
                    "enum": ["append", "create", "organize", "restructure"],
                    "description": (
                        "High-level intent: append (add to existing page), "
                        "create (new page), organize (metadata/links/archive), "
                        "restructure (vault-wide structural change)"
                    ),
                },
                "change_type": {
                    "type": "string",
                    "enum": ["incremental", "structural"],
                    "description": (
                        "incremental = low-risk changes to existing content "
                        "(auto-approved). structural = new pages, hubs, "
                        "restructuring (requires user approval)."
                    ),
                },
                "open_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Uncertainties that the user might want to weigh in on"
                    ),
                },
            },
            "required": ["summary", "rationale", "intent", "change_type"],
        },
    },
}

_OBSERVATION_TOOL_NAMES = frozenset({
    "read_page", "search", "survey_topic",
    "get_backlinks", "list_pages", "fetch_url",
})

OBSERVATION_SCHEMAS: list[dict] = [
    s for s in TOOL_SCHEMAS
    if s["function"]["name"] in _OBSERVATION_TOOL_NAMES
]

CHAT_TOOL_SCHEMAS: list[dict] = OBSERVATION_SCHEMAS + [SUBMIT_PLAN_SCHEMA]


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

    # Also check title similarity
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
        # Show updated date from frontmatter
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


def handle_survey_topic(vault: Vault, topic: str) -> str:
    """One-shot topic assessment for planning."""
    sections: list[str] = [f"## Topic Survey: {topic}\n"]

    # 1. FTS search
    fts_results = vault.search.search(topic, limit=10)
    candidates = []
    for r in fts_results:
        if r["path"].startswith("wiki/"):
            candidates.append(r)

    # 2. Title similarity
    all_pages = vault.read_frontmatters("wiki")
    topic_lower = topic.lower()
    title_hits = []
    for p in all_pages:
        p_title = str(p.get("title", "")).lower()
        if topic_lower in p_title or p_title in topic_lower:
            if p["path"] not in {c["path"] for c in candidates}:
                title_hits.append(p)

    # 3. Candidate pages
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

    # 4. Related tags and hubs
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

    # 5. Source material
    source_hits = vault.search_content(topic, "sources")
    if source_hits:
        sections.append(f"### Related sources ({len(source_hits)} found)")
        for sh in source_hits[:5]:
            sections.append(f"- {sh['path']}")
        sections.append("")

    # 6. Backlink connections
    bl_sources = vault.backlinks.backlinks_for(topic)
    if bl_sources:
        sections.append(f"### Pages linking to '{topic}'")
        for bl in bl_sources[:10]:
            sections.append(f"- {bl}")
        sections.append("")

    # 7. Suggestion
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

    lines = []
    imported_count = 0

    for r in structured:
        tags_str = f"  tags: {', '.join(r['tags'])}" if r['tags'] else ""
        summary_str = f"\n    {r['summary']}" if r['summary'] else ""
        lines.append(
            f"- [{r['type']}] **{r['title']}** ({r['path']}){tags_str}{summary_str}"
        )
        if "imported" in (r.get("tags") or []):
            imported_count += 1

    if unstructured:
        lines.append("")
        lines.append(f"Also found {len(unstructured)} file(s) without frontmatter:")
        for r in unstructured:
            lines.append(f"  - **{r['title']}** ({r['path']})")
        lines.append("")
        lines.append(
            "These files lack structured metadata. "
            "Use read_page(path) to read them, "
            "or ingest(source_type='directory') to import and organize."
        )

    result = "\n".join(lines)
    if imported_count:
        result += (
            f"\n\nNote: {imported_count} file(s) still tagged [imported] — "
            "consider using organize(target='imported', action='classify') "
            "to classify them."
        )
    return result


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
# Action tool handlers
# ------------------------------------------------------------------

INDEX_TOKEN_BUDGET = 4000


def handle_capture(
    vault: Vault,
    content: str,
    title: str,
    tags: list | None = None,
    target: str = "",
    type: str = "note",
) -> str:
    """Capture knowledge: create a new page or append to an existing one."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tag_list = tags or []

    if target:
        # Append to existing page as a new section
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

        # Update tags if provided
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

    # Create new page
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
    """Unified content ingestion: URL, file, or directory."""
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
    results.append(
        "\nUse capture() to record key information into wiki pages."
    )
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
    results.append(
        "\nUse capture() to record key information into wiki pages."
    )
    return "\n".join(results)


def _ingest_directory(vault: Vault, directory: str, do_organize: bool) -> str:
    result = vault.import_directory(directory)

    if do_organize and "Imported" in result:
        scan_result = vault.scan_imports()
        if "Nothing to organize" not in scan_result:
            result += (
                "\n\n--- Organization scan ---\n" + scan_result
            )

    return result


def handle_organize(
    vault: Vault,
    target: str,
    action: str,
    reason: str = "",
    metadata: dict | None = None,
    link_to: str = "",
) -> str:
    """Organize specific pages."""
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


def _organize_classify(vault: Vault, target: str) -> str:
    """Scan imported/target pages and return structured info for planning."""
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
    """Add a [[wiki-link]] to the Related section of a page."""
    if not link_to:
        return "Error: link_to is required for link action"
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


def handle_restructure(
    vault: Vault,
    scope: str,
    action: str,
    old_tag: str = "",
    new_tag: str = "",
) -> str:
    """Vault-wide structural changes."""
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
    """Find pages with similar titles or overlapping content."""
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
        "\nReview these pages and use organize(action='archive') to retire "
        "duplicates, or write_page to merge their content."
    )
    return "\n".join(lines)


def _restructure_rebuild_hubs(vault: Vault, scope: str) -> str:
    """Rebuild hub pages from tag analysis."""
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
    """Run full vault audit and return report."""
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


# ======================================================================
# Dispatch
# ======================================================================

TOOL_HANDLERS: dict[str, Any] = {
    "read_page": handle_read_page,
    "search": handle_search,
    "survey_topic": handle_survey_topic,
    "get_backlinks": handle_get_backlinks,
    "list_pages": handle_list_pages,
    "fetch_url": handle_fetch_url,
    "capture": handle_capture,
    "ingest": handle_ingest,
    "organize": handle_organize,
    "restructure": handle_restructure,
    "write_page": handle_write_page,
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
