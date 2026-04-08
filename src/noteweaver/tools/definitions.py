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
                "Read the content of a file in the vault. "
                "Use this to read wiki pages, source documents, the index, "
                "the log, or the schema. Path is relative to vault root."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path from vault root, e.g. 'wiki/index.md' or 'wiki/concepts/attention.md'",
                    }
                },
                "required": ["path"],
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
                "Full-text search across files in a vault directory. "
                "Returns file paths and matching lines. "
                "Use this to find relevant pages before reading them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (case-insensitive substring match)",
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
            "name": "list_pages",
            "description": (
                "List all markdown files under a vault directory. "
                "Use this to see what pages exist in a given section."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "directory": {
                        "type": "string",
                        "description": "Directory to list, relative to vault root. Default: 'wiki'",
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

def handle_read_page(vault: Vault, path: str) -> str:
    try:
        return vault.read_file(path)
    except FileNotFoundError:
        return f"Error: file not found: {path}"
    except PermissionError as e:
        return f"Error: {e}"


def handle_write_page(vault: Vault, path: str, content: str) -> str:
    try:
        # Hard constraint: validate frontmatter before writing
        validation = validate_frontmatter(path, content)
        if not validation.valid:
            return "Error: frontmatter validation failed:\n" + "\n".join(
                f"  - {e}" for e in validation.errors
            )
        vault.write_file(path, content)
        return f"OK: written to {path} ({len(content)} chars)"
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


def handle_list_pages(vault: Vault, directory: str = "wiki") -> str:
    files = vault.list_files(directory)
    if not files:
        return f"No markdown files in {directory}/"
    return "\n".join(files)


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

    # Remove original file
    original = vault._resolve(path)
    if original.is_file():
        original.unlink()
        vault._git_commit(f"Archive {path} -> {archive_path}")

    vault.append_log("archive", path, reason)
    return f"OK: archived {path} -> {archive_path}"


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
    "write_page": handle_write_page,
    "search_vault": handle_search_vault,
    "list_pages": handle_list_pages,
    "append_log": handle_append_log,
    "archive_page": handle_archive_page,
    "fetch_url": handle_fetch_url,
}


def dispatch_tool(vault: Vault, name: str, arguments: dict) -> str:
    """Execute a tool call and return the result as a string."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return f"Error: unknown tool '{name}'"

    # Filter arguments to only those the handler accepts
    import inspect
    sig = inspect.signature(handler)
    valid_params = set(sig.parameters.keys()) - {"vault"}
    filtered_args = {k: v for k, v in arguments.items() if k in valid_params}

    return handler(vault, **filtered_args)
