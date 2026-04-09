"""Tool definitions for LLM function calling.

Each tool is defined as an OpenAI-compatible function schema plus a handler
that operates on the Vault. The agent can ONLY use these tools — no shell,
no code execution, no arbitrary file access. Security by design.
"""

from __future__ import annotations

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
                "Read a file from the vault. By default reads the full content. "
                "Use max_chars to read only the beginning (frontmatter + first "
                "paragraph) for a quick relevance check before committing to "
                "a full read. Path is relative to vault root."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from vault root, e.g. 'wiki/index.md'",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "Optional. Max characters to read. Use ~500 for a quick relevance check (frontmatter + summary). Omit for full content.",
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
                "Returns ranked results with snippets. Searches across "
                "title, summary, tags, and body. Use when looking for "
                "content by keyword."
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
                "notes or documents."
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

def handle_read_page(vault: Vault, path: str, max_chars: int = 0) -> str:
    try:
        if max_chars and max_chars > 0:
            content = vault.read_file_partial(path, max_chars)
            if len(content) >= max_chars:
                content += "\n\n... (truncated, use read_page without max_chars for full content)"
            return content
        return vault.read_file(path)
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except PermissionError as e:
        return f"Error: {e}"


def handle_list_page_summaries(vault: Vault, directory: str = "wiki") -> str:
    results = vault.read_frontmatters(directory)
    if not results:
        return f"No pages with frontmatter in {directory}/"
    lines = []
    for r in results:
        tags_str = f"  tags: {', '.join(r['tags'])}" if r['tags'] else ""
        summary_str = f"\n    {r['summary']}" if r['summary'] else ""
        lines.append(f"- [{r['type']}] **{r['title']}** ({r['path']}){tags_str}{summary_str}")
    return "\n".join(lines)


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
    for r in results[:10]:
        lines.append(f"\n**{r['path']}**")
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

    # Remove original file (the delete is captured by the next git commit,
    # whether that's from an operation context or append_log below)
    original = vault._resolve(path)
    if original.is_file():
        original.unlink()

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


def handle_vault_stats(vault: Vault) -> str:
    metrics = vault.health_metrics()
    if metrics["total_pages"] == 0:
        return "Vault is empty (no wiki pages yet)."
    lines = ["Vault Health Metrics:"]
    for k, v in metrics.items():
        label = k.replace("_", " ").title()
        lines.append(f"  {label}: {v}")
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
    "vault_stats": handle_vault_stats,
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
