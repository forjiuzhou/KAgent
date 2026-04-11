"""Tool definitions for LLM function calling.

Each tool is defined as an OpenAI-compatible function schema plus a handler
that operates on the Vault. The agent can ONLY use these tools — no shell,
no code execution, no arbitrary file access. Security by design.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

from noteweaver.vault import Vault
from noteweaver.frontmatter import validate_frontmatter

# ======================================================================
# Tool schemas (OpenAI function calling format)
# ======================================================================

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "read_page",
            "description": (
                "Read a file from the vault. Accepts either a file path "
                "(e.g. 'wiki/concepts/attention.md') or a page title "
                "(e.g. 'Attention Mechanism'). Use max_chars to read only "
                "the beginning for a quick relevance check."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path (e.g. 'wiki/concepts/attention.md') or page title (e.g. 'Attention Mechanism')",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Optional. Max characters to read. Use ~500 for a quick relevance check. Omit for full content.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_page_summaries",
            "description": (
                "List all pages in a directory with their frontmatter metadata "
                "(title, type, summary, tags). Does NOT read page bodies — very "
                "cheap in tokens. Use this to scan what exists, find pages by "
                "tag, or assess relevance before reading full pages."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Directory to scan, relative to vault root. Default: 'wiki'",
                        "default": "wiki",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_page",
            "description": (
                "Create or overwrite a file in the wiki area. "
                "Cannot write to sources/ (immutable). "
                "Always include YAML frontmatter with title, type, created/updated dates. "
                "Use [[wiki-link]] syntax for internal links."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from vault root, e.g. 'wiki/concepts/attention.md'",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full markdown content of the page including YAML frontmatter",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_vault",
            "description": (
                "Full-text search across wiki pages using FTS5 index. "
                "Returns ranked results with snippets, backlink counts, "
                "and tags. Searches across title, summary, tags, and body. "
                "Use when looking for content by keyword. For structured "
                "navigation, prefer list_page_summaries → read_page."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (natural language or keywords)",
                    },
                    "directory": {
                        "type": "string",
                        "description": "Directory to search in, relative to vault root. Default: 'wiki'",
                        "default": "wiki",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_log",
            "description": (
                "Append an entry to wiki/log.md to record what you did. "
                "Call this after every significant operation (ingest, query, lint, etc.)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entry_type": {
                        "type": "string",
                        "description": "Type of operation, e.g. 'ingest', 'query', 'lint', 'organize'",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title, e.g. the article name or query",
                    },
                    "details": {
                        "type": "string",
                        "description": "Optional details about what was done",
                    },
                },
                "required": ["entry_type", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "archive_page",
            "description": (
                "Move a wiki page to the archive. Use this instead of deleting pages. "
                "Archived pages are preserved but removed from main navigation. "
                "The page's frontmatter type is changed to 'archive' and it is "
                "moved to wiki/archive/. You should also remove it from index.md."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the page to archive, e.g. 'wiki/concepts/old-topic.md'",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why this page is being archived",
                    },
                },
                "required": ["path", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_source",
            "description": (
                "Save raw content to sources/ for permanent archival. "
                "Sources are immutable — once saved, they cannot be modified or "
                "overwritten. Use this to preserve the original text of fetched "
                "web pages, imported documents, etc. as evidence."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path under sources/, e.g. 'sources/articles/my-article.md'",
                    },
                    "content": {
                        "type": "string",
                        "description": "Raw content to save",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "import_files",
            "description": (
                "Import markdown files from a local directory into the vault. "
                "Scans the directory for .md files, auto-classifies them by "
                "frontmatter, adds frontmatter to files that lack it, and "
                "rebuilds the index. Use when the user wants to import existing "
                "notes or documents. After importing, call scan_imports to "
                "prepare for organization."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Absolute path to the directory to import from, e.g. '/home/user/notes'",
                    },
                },
                "required": ["directory"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scan_imports",
            "description": (
                "Scan all files tagged [imported] and prepare a structured "
                "summary for organization planning. Returns file digests "
                "(frontmatter, headings, content preview) plus vault context "
                "(existing tags, hubs, pages). Use this after import_files "
                "to get the information needed for your organization plan. "
                "After reviewing the scan results, call apply_organize_plan "
                "with your decisions."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_organize_plan",
            "description": (
                "Apply an organization plan to imported files. Takes a JSON "
                "array where each element specifies: path, type, title, "
                "summary, tags, move_to, related, hub, duplicate_of, and "
                "confidence. Executes all updates (frontmatter, moves, "
                "links, hub creation) in a single batch operation. Items "
                "with confidence=low or duplicate_of set are flagged for "
                "user review instead of being auto-processed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "string",
                        "description": (
                            "JSON array of file plans. Each element: "
                            "{path, type, title, summary, tags, move_to, "
                            "related, hub, duplicate_of, confidence}"
                        ),
                    },
                },
                "required": ["plan"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vault_stats",
            "description": (
                "Get quantitative health metrics for the knowledge base. "
                "Returns page counts, orphan rate, hub coverage, canonical "
                "source ratio, and pages missing summaries. Use to assess "
                "the state of the vault."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_backlinks",
            "description": (
                "Find all pages that link to a given page title via [[wiki-links]]. "
                "Use this to understand how a concept is connected, find orphan pages, "
                "or trace how knowledge flows through the vault."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "The page title to find backlinks for, e.g. 'Attention Mechanism'",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_section",
            "description": (
                "Append a new section to an existing wiki page. Inserts before "
                "the ## Related section if one exists, otherwise at the end. "
                "Much cheaper than write_page for adding content — does not "
                "require sending the full page back."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the existing wiki page, e.g. 'wiki/concepts/attention.md'",
                    },
                    "heading": {
                        "type": "string",
                        "description": "Section heading (without ##), e.g. 'Multi-Head Attention'",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown content for the new section (without the heading)",
                    },
                },
                "required": ["path", "heading", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_to_section",
            "description": (
                "Append content to an existing section in a wiki page. Finds "
                "the section by heading and appends at the end of that section "
                "(before the next heading of same or higher level). Use this to "
                "add bullet points or paragraphs to an existing section."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the wiki page",
                    },
                    "heading": {
                        "type": "string",
                        "description": "Existing section heading to append to (without ##)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to append to that section",
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
                "Update specific frontmatter fields of a wiki page without "
                "touching the body. Much cheaper than write_page for metadata "
                "updates like adding tags, changing type, or updating dates."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the wiki page",
                    },
                    "fields": {
                        "type": "object",
                        "description": (
                            "Fields to update, e.g. {\"tags\": [\"ai\", \"nlp\"], "
                            "\"summary\": \"Updated summary\"}"
                        ),
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
                "Add a [[wiki-link]] to the ## Related section of a page. "
                "Creates the ## Related section if it doesn't exist. "
                "Skips if the link already exists. Very cheap operation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the wiki page",
                    },
                    "title": {
                        "type": "string",
                        "description": "Title to link to, e.g. 'Attention Mechanism'",
                    },
                },
                "required": ["path", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_existing_page",
            "description": (
                "Search for an existing page by title or topic before creating "
                "a new one. Returns matching pages with summaries so you can "
                "decide whether to update an existing page or create new. "
                "ALWAYS call this before write_page to avoid duplicates."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title or topic to search for",
                    },
                    "type": {
                        "type": "string",
                        "description": "Optional page type filter (hub/canonical/note/synthesis)",
                    },
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_transcript",
            "description": (
                "Read a saved conversation transcript from .meta/transcripts/. "
                "Use this during digest to access full conversation details "
                "when a journal entry references something worth deeper review. "
                "Returns the conversation as structured text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Transcript filename, e.g. '2025-04-09_153000.json'",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Optional. Max characters to return. Default: full transcript.",
                    },
                },
                "required": ["filename"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "promote_insight",
            "description": (
                "Promote an insight from a journal entry to a wiki page. "
                "Searches for an existing page on the topic first. If found, "
                "appends the insight as a new section. If not found, creates "
                "a new page of the specified type (note/canonical/synthesis). "
                "This is the standard journal→wiki promotion path — prefer "
                "this over manual write_page for digest results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title or topic of the insight",
                    },
                    "content": {
                        "type": "string",
                        "description": "The insight content (markdown)",
                    },
                    "source_journal": {
                        "type": "string",
                        "description": "Path to the journal entry this came from, e.g. 'wiki/journals/2025-04-09.md'",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Tags for the insight",
                    },
                    "target_type": {
                        "type": "string",
                        "enum": ["note", "canonical", "synthesis"],
                        "description": "Page type to create. Defaults to 'note'.",
                    },
                },
                "required": ["title", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "merge_tags",
            "description": (
                "Merge one tag into another across all wiki pages. "
                "Every page with old_tag gets it replaced by new_tag. "
                "Use this to clean up tag fragmentation, e.g. merge "
                "'ml' into 'machine-learning'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "old_tag": {
                        "type": "string",
                        "description": "The tag to replace, e.g. 'ml'",
                    },
                    "new_tag": {
                        "type": "string",
                        "description": "The tag to merge into, e.g. 'machine-learning'",
                    },
                },
                "required": ["old_tag", "new_tag"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Fetch a web page and extract its main content as markdown. "
                "Use this to import web articles into the knowledge base. "
                "The extracted content is returned — you should then save it "
                "to sources/ and integrate key information into wiki pages."
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
]


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
            "Use find_existing_page or list_page_summaries to search."
        )
    return resolved


def handle_read_page(vault: Vault, path: str, max_chars: int = 0) -> str:
    try:
        resolved = _resolve_path_or_title(vault, path)
        if max_chars and max_chars > 0:
            content = vault.read_file_partial(resolved, max_chars)
            if len(content) >= max_chars:
                content += "\n\n... (truncated, use read_page without max_chars for full content)"
            return content
        return vault.read_file(resolved)
    except FileNotFoundError as e:
        return f"Error: {e}"
    except PermissionError as e:
        return f"Error: {e}"


def handle_list_page_summaries(vault: Vault, directory: str = "wiki") -> str:
    results = vault.read_frontmatters(directory)
    if not results:
        return f"No pages with frontmatter in {directory}/"
    lines = []
    imported_count = 0
    for r in results:
        tags_str = f"  tags: {', '.join(r['tags'])}" if r['tags'] else ""
        summary_str = f"\n    {r['summary']}" if r['summary'] else ""
        lines.append(f"- [{r['type']}] **{r['title']}** ({r['path']}){tags_str}{summary_str}")
        if "imported" in (r.get("tags") or []):
            imported_count += 1
    result = "\n".join(lines)
    if imported_count:
        result += (
            f"\n\nNote: {imported_count} file(s) still tagged [imported] — "
            "consider running scan_imports + apply_organize_plan to classify them."
        )
    return result


INDEX_TOKEN_BUDGET = 4000  # ~1000 tokens ≈ ~4000 chars

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
                f"\n\nWarning: index.md is {len(content)} chars (target: <{INDEX_TOKEN_BUDGET}). "
                "Consider creating Hubs to move detail out of the root index."
            )
        return result
    except PermissionError as e:
        return f"Error: {e}"


def handle_search_vault(vault: Vault, query: str, directory: str = "wiki") -> str:
    results = vault.search_content(query, directory)
    if not results:
        return f"No results found for '{query}' in {directory}/"
    lines = []

    # Enrich results with metadata from frontmatters and backlinks
    frontmatters = {p["path"]: p for p in vault.read_frontmatters(directory)}

    for r in results[:10]:
        path = r["path"]
        fm = frontmatters.get(path, {})
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
        for line_no, line_text in r["matches"]:
            lines.append(f"  L{line_no}: {line_text}")
    return "\n".join(lines)



def handle_append_log(
    vault: Vault, entry_type: str, title: str, details: str = ""
) -> str:
    vault.append_log(entry_type, title, details)
    return f"OK: logged [{entry_type}] {title}"


def handle_archive_page(vault: Vault, path: str, reason: str = "") -> str:
    try:
        content = vault.read_file(path)
    except FileNotFoundError:
        return f"Error: file not found: {path}"

    filename = path.rsplit("/", 1)[-1]
    archive_path = f"wiki/archive/{filename}"

    # Update frontmatter to mark as archived
    from noteweaver.frontmatter import extract_frontmatter, FRONTMATTER_PATTERN
    import re
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
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

    vault.append_log("archive", path, reason)
    return f"OK: archived {path} -> {archive_path}"


def handle_save_source(vault: Vault, path: str, content: str) -> str:
    try:
        vault.save_source(path, content)
        return f"OK: source saved to {path} ({len(content)} chars, immutable)"
    except PermissionError as e:
        return f"Error: {e}"


def handle_import_files(vault: Vault, directory: str) -> str:
    return vault.import_directory(directory)


def handle_scan_imports(vault: Vault) -> str:
    return vault.scan_imports()


def handle_apply_organize_plan(vault: Vault, plan: str) -> str:
    return vault.apply_organize_plan(plan)


def handle_vault_stats(vault: Vault) -> str:
    metrics = vault.health_metrics()
    if metrics["total_pages"] == 0:
        return "Vault is empty (no wiki pages yet)."
    lines = ["Vault Health Metrics:"]
    for k, v in metrics.items():
        label = k.replace("_", " ").title()
        lines.append(f"  {label}: {v}")
    return "\n".join(lines)


def handle_get_backlinks(vault: Vault, title: str) -> str:
    sources = vault.backlinks.backlinks_for(title)
    if not sources:
        return f"No pages link to '{title}'."
    count = len(sources)
    lines = [f"**{count} page(s) link to '{title}':**"]
    for s in sources[:20]:
        lines.append(f"  - {s}")
    return "\n".join(lines)


def handle_append_section(vault: Vault, path: str, heading: str, content: str) -> str:
    """Append a new section to a wiki page, before ## Related if it exists."""
    try:
        existing = vault.read_file(path)
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    if not path.startswith("wiki/"):
        return f"Error: can only edit wiki/ pages. Path: {path}"

    section_text = f"\n## {heading}\n\n{content}\n"

    # Insert before ## Related if present
    related_pattern = re.compile(r"(\n## Related\b)", re.IGNORECASE)
    match = related_pattern.search(existing)
    if match:
        insert_pos = match.start()
        new_content = existing[:insert_pos] + section_text + existing[insert_pos:]
    else:
        new_content = existing.rstrip() + "\n" + section_text

    vault.write_file(path, new_content)
    return f"OK: appended section '## {heading}' to {path}"


def handle_append_to_section(vault: Vault, path: str, heading: str, content: str) -> str:
    """Append content to an existing section identified by heading."""
    try:
        existing = vault.read_file(path)
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    if not path.startswith("wiki/"):
        return f"Error: can only edit wiki/ pages. Path: {path}"

    lines = existing.split("\n")
    heading_pattern = re.compile(r"^(#{1,6})\s+" + re.escape(heading) + r"\s*$", re.IGNORECASE)
    target_idx = None
    target_level = None

    for i, line in enumerate(lines):
        m = heading_pattern.match(line)
        if m:
            target_idx = i
            target_level = len(m.group(1))
            break

    if target_idx is None:
        return f"Error: section '{heading}' not found in {path}"

    # Find end of section (next heading of same or higher level, or EOF)
    insert_idx = len(lines)
    for i in range(target_idx + 1, len(lines)):
        m = re.match(r"^(#{1,6})\s", lines[i])
        if m and len(m.group(1)) <= target_level:
            insert_idx = i
            break

    lines.insert(insert_idx, content + "\n")
    new_content = "\n".join(lines)
    vault.write_file(path, new_content)
    return f"OK: appended to section '{heading}' in {path}"


def handle_update_frontmatter(vault: Vault, path: str, fields: dict) -> str:
    """Update specific frontmatter fields without touching the body."""
    try:
        existing = vault.read_file(path)
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    if not path.startswith("wiki/"):
        return f"Error: can only edit wiki/ pages. Path: {path}"

    from noteweaver.frontmatter import extract_frontmatter, FRONTMATTER_PATTERN

    fm = extract_frontmatter(existing)
    if fm is None:
        return f"Error: no frontmatter found in {path}"

    fm.update(fields)

    # Validate the updated frontmatter
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
    return f"OK: updated frontmatter fields [{updated_keys}] in {path}"


def handle_add_related_link(vault: Vault, path: str, title: str) -> str:
    """Add a [[wiki-link]] to the ## Related section."""
    try:
        existing = vault.read_file(path)
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    if not path.startswith("wiki/"):
        return f"Error: can only edit wiki/ pages. Path: {path}"

    link = f"[[{title}]]"
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


def handle_find_existing_page(vault: Vault, title: str, type: str = "") -> str:
    """Search for existing pages by title/topic to prevent duplicates."""
    candidates = []

    # 1. FTS search
    fts_results = vault.search.search(title, limit=10)
    for r in fts_results:
        candidates.append({
            "path": r["path"],
            "title": str(r.get("title", "")),
            "type": str(r.get("type", "")),
            "summary": str(r.get("summary", "")),
            "match": "fts",
        })

    # 2. Title similarity via frontmatters
    all_pages = vault.read_frontmatters("wiki")
    title_lower = str(title).lower()
    for p in all_pages:
        if p["path"] in {c["path"] for c in candidates}:
            continue
        if title_lower in str(p["title"]).lower() or str(p["title"]).lower() in title_lower:
            candidates.append({**p, "match": "title"})

    # 3. Backlinks — pages that link to this title
    backlink_sources = vault.backlinks.backlinks_for(title)
    for bl_path in backlink_sources[:5]:
        if bl_path not in {c["path"] for c in candidates}:
            for p in all_pages:
                if p["path"] == bl_path:
                    candidates.append({**p, "match": "backlink"})
                    break

    # Filter by type if specified
    if type:
        candidates = [c for c in candidates if c.get("type") == type or not c.get("type")]

    if not candidates:
        return f"No existing pages found for '{title}'. Safe to create new."

    lines = [f"Found {len(candidates)} potential match(es) for '{title}':"]
    for c in candidates[:10]:
        summary = f" — {c['summary']}" if c.get("summary") else ""
        match_type = f" [{c.get('match', '')}]" if c.get("match") else ""
        lines.append(f"  - [{c.get('type', '?')}] **{c.get('title', '?')}** ({c['path']}){summary}{match_type}")

    lines.append("")
    lines.append(
        "Consider updating an existing page (using append_section or append_to_section) "
        "instead of creating a new one."
    )
    return "\n".join(lines)


def handle_read_transcript(vault: Vault, filename: str, max_chars: int = 0) -> str:
    """Read a saved conversation transcript from .meta/transcripts/."""
    import json as _json

    safe_name = filename.replace("/", "").replace("\\", "").replace("..", "")
    path = vault.meta_dir / "transcripts" / safe_name
    if not path.is_file():
        available = []
        transcript_dir = vault.meta_dir / "transcripts"
        if transcript_dir.is_dir():
            available = sorted(f.name for f in transcript_dir.glob("*.json"))[-10:]
        hint = f" Available: {', '.join(available)}" if available else ""
        return f"Error: transcript not found: {safe_name}.{hint}"

    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (_json.JSONDecodeError, OSError) as e:
        return f"Error reading transcript: {e}"

    lines = []
    for m in data:
        role = m.get("role", "?")
        content = m.get("content", "")
        if role == "system":
            continue
        if role == "tool":
            tool_id = m.get("tool_call_id", "")
            short = (content[:300] + "...") if len(str(content)) > 300 else content
            lines.append(f"[tool result {tool_id}]: {short}")
        elif role == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                lines.append(f"[assistant calls {fn.get('name', '?')}({fn.get('arguments', '')})]")
            if content:
                lines.append(f"Assistant: {content}")
        elif content:
            lines.append(f"{role.title()}: {content}")

    result = "\n\n".join(lines)
    if max_chars and max_chars > 0 and len(result) > max_chars:
        result = result[:max_chars] + "\n\n... (truncated)"
    return result


def handle_promote_insight(
    vault: Vault,
    title: str,
    content: str,
    source_journal: str = "",
    tags: list | None = None,
    target_type: str = "note",
) -> str:
    """Promote a journal insight to a wiki page.

    Searches for an existing page first. If found, appends the insight.
    If not, creates a new page of the requested type (note/canonical/synthesis).
    This is the controlled promotion path: journal → wiki page.
    """
    _ALLOWED_TYPES = {"note", "canonical", "synthesis"}
    if target_type not in _ALLOWED_TYPES:
        return f"Error: target_type must be one of {sorted(_ALLOWED_TYPES)}, got '{target_type}'"
    from datetime import datetime, timezone

    # Step 1: check for existing page
    candidates = vault.search.search(title, limit=5)
    all_pages = vault.read_frontmatters("wiki")
    title_lower = str(title).lower()

    existing_path = None
    for c in candidates:
        if title_lower in str(c.get("title", "")).lower():
            existing_path = c["path"]
            break

    if not existing_path:
        for p in all_pages:
            if title_lower in str(p["title"]).lower() or str(p["title"]).lower() in title_lower:
                existing_path = p["path"]
                break

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    source_ref = f"\n\n*Promoted from:* `{source_journal}`" if source_journal else ""

    if existing_path:
        section_content = content + source_ref
        try:
            existing = vault.read_file(existing_path)
        except FileNotFoundError:
            existing_path = None
        else:
            heading = "Promoted Insight"
            section_text = f"\n## {heading} ({today})\n\n{section_content}\n"
            related_pattern = re.compile(r"(\n## Related\b)", re.IGNORECASE)
            match = related_pattern.search(existing)
            if match:
                insert_pos = match.start()
                new_content = existing[:insert_pos] + section_text + existing[insert_pos:]
            else:
                new_content = existing.rstrip() + "\n" + section_text

            vault.write_file(existing_path, new_content)
            return (
                f"OK: promoted insight to existing page {existing_path} "
                f"(appended section '## {heading} ({today})')"
            )

    # Step 2: create new page with the requested type
    slug = str(title).lower().replace(" ", "-").replace("/", "-")
    slug = re.sub(r"[^a-z0-9-]", "", slug)[:60]

    if target_type == "synthesis":
        path = f"wiki/synthesis/{slug}.md"
    else:
        path = f"wiki/concepts/{slug}.md"

    tag_list = tags or []
    tag_str = ", ".join(tag_list) if tag_list else ""

    sources_line = ""
    if target_type == "canonical":
        src = source_journal or "promoted-insight"
        sources_line = f"sources: [{src}]\n"

    fm = (
        f"---\ntitle: {title}\ntype: {target_type}\n"
        f"summary: Insight promoted from journal\n"
        f"{sources_line}"
        f"tags: [{tag_str}]\n"
        f"created: {today}\nupdated: {today}\n---\n\n"
    )
    body = f"# {title}\n\n{content}{source_ref}\n\n## Related\n"
    new_content = fm + body

    validation = validate_frontmatter(path, new_content)
    if not validation.valid:
        return "Error: frontmatter validation failed:\n" + "\n".join(
            f"  - {e}" for e in validation.errors
        )

    vault.write_file(path, new_content)
    return f"OK: created new {target_type} page {path} from promoted insight"


def handle_merge_tags(vault: Vault, old_tag: str, new_tag: str) -> str:
    """Merge old_tag into new_tag across all wiki pages."""
    old_normalized = vault.normalize_tag(old_tag)
    new_normalized = vault.normalize_tag(new_tag)
    if not old_normalized or not new_normalized:
        return "Error: tags cannot be empty."
    if old_normalized == new_normalized:
        return f"Tags are already the same after normalization: '{old_normalized}'"

    from noteweaver.frontmatter import extract_frontmatter, FRONTMATTER_PATTERN

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

        # Truncate extremely long pages to avoid blowing up context
        max_chars = 15000
        truncated = ""
        if len(md_content) > max_chars:
            md_content = md_content[:max_chars]
            truncated = f"\n\n(Content truncated at {max_chars} characters)"

        header = f"# {title}\n\nSource: {url}\n\n---\n\n"
        return header + md_content.strip() + truncated
    except httpx.TimeoutException:
        return f"Error: timeout fetching {url} (30s limit)"
    except httpx.HTTPStatusError as e:
        return f"Error: HTTP {e.response.status_code} fetching {url}"
    except Exception as e:
        return f"Error fetching {url}: {type(e).__name__}: {e}"


# ======================================================================
# Dispatch
# ======================================================================

TOOL_HANDLERS: dict[str, Any] = {
    "read_page": handle_read_page,
    "list_page_summaries": handle_list_page_summaries,
    "write_page": handle_write_page,
    "search_vault": handle_search_vault,
    "append_log": handle_append_log,
    "archive_page": handle_archive_page,
    "save_source": handle_save_source,
    "import_files": handle_import_files,
    "scan_imports": handle_scan_imports,
    "apply_organize_plan": handle_apply_organize_plan,
    "vault_stats": handle_vault_stats,
    "get_backlinks": handle_get_backlinks,
    "append_section": handle_append_section,
    "append_to_section": handle_append_to_section,
    "update_frontmatter": handle_update_frontmatter,
    "add_related_link": handle_add_related_link,
    "find_existing_page": handle_find_existing_page,
    "read_transcript": handle_read_transcript,
    "promote_insight": handle_promote_insight,
    "merge_tags": handle_merge_tags,
    "fetch_url": handle_fetch_url,
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
