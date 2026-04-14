"""Search index and backlink management for the vault."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from noteweaver.vault.core import Vault


def index_file(vault: Vault, rel_path: str, content: str) -> None:
    """Update the search index for a single file."""
    from noteweaver.frontmatter import extract_frontmatter
    fm = extract_frontmatter(content) or {}
    tags = fm.get("tags", [])
    vault.search.upsert(
        path=rel_path,
        title=str(fm.get("title", "")),
        type=str(fm.get("type", "")),
        summary=str(fm.get("summary", "")),
        tags=", ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags),
        body=content,
    )


def rebuild_search_index(vault: Vault) -> int:
    """Rebuild the entire search index from vault files.

    Indexes both wiki/ and sources/ so that search_vault can find
    content everywhere in the vault.  Sources files without frontmatter
    are indexed with path-derived metadata.
    """
    from noteweaver.frontmatter import extract_frontmatter

    pages = []
    for rel_dir in ("wiki", "sources"):
        for rel_path in vault.list_files(rel_dir):
            try:
                content = vault.read_file(rel_path)
                fm = extract_frontmatter(content) or {}
                tags = fm.get("tags", [])
                title = str(fm.get("title", ""))
                if not title and not fm:
                    title = Path(rel_path).stem.replace("-", " ").replace("_", " ")
                pages.append({
                    "path": rel_path,
                    "title": title,
                    "type": str(fm.get("type", "") or ("source" if rel_path.startswith("sources/") else "")),
                    "summary": str(fm.get("summary", "")),
                    "tags": ", ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags),
                    "body": content,
                })
            except (FileNotFoundError, PermissionError):
                continue
    vault.search.rebuild(pages)
    return len(pages)


def rebuild_backlinks(vault: Vault) -> int:
    """Rebuild backlink index from all vault files."""
    pages = []
    for rel_path in vault.list_files("wiki"):
        try:
            content = vault.read_file(rel_path)
            pages.append({"path": rel_path, "content": content})
        except (FileNotFoundError, PermissionError):
            continue
    vault.backlinks.rebuild(pages)
    return len(pages)


def search_content(vault: Vault, query: str, directory: str = "wiki") -> list[dict]:
    """Full-text search using SQLite FTS5 index.

    Returns ranked results with snippets. Falls back to brute-force
    scan if FTS index is empty or returns no results.
    """
    fts_results = vault.search.search(query)
    if fts_results:
        filtered = [r for r in fts_results if r["path"].startswith(directory)]
        if filtered:
            return [
                {"path": r["path"], "matches": [(0, r["snippet"])]}
                for r in filtered
            ]

    results = []
    query_lower = query.lower()
    for rel_path in vault.list_files(directory):
        content = vault.read_file(rel_path)
        if query_lower in content.lower():
            lines = content.split("\n")
            matching_lines = [
                (i + 1, line.strip())
                for i, line in enumerate(lines)
                if query_lower in line.lower()
            ]
            results.append({
                "path": rel_path,
                "matches": matching_lines[:5],
            })
    return results
