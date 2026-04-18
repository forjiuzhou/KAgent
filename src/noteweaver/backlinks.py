"""Backlink index — tracks [[wiki-link]] relationships between pages.

Lives in .meta/backlinks.db (SQLite). Rebuildable from vault files.
Answers: "who links to this page?", "how many references?", "orphan pages?"
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _related_from_frontmatter(content: str) -> set[str]:
    """Extract titles from the ``related`` frontmatter field."""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return set()
    try:
        import yaml
        fm = yaml.safe_load(m.group(1)) or {}
    except Exception:
        return set()
    raw = fm.get("related") or []
    if not isinstance(raw, list):
        return set()
    return {str(r) for r in raw if r}


class BacklinkIndex:
    """SQLite-backed index of [[wiki-link]] relationships."""

    def __init__(self, meta_dir: Path) -> None:
        self._db_path = meta_dir / "backlinks.db"
        meta_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS links (
                source_path TEXT NOT NULL,
                target_title TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_source ON links(source_path);
            CREATE INDEX IF NOT EXISTS idx_target ON links(target_title);
            CREATE TABLE IF NOT EXISTS source_provenance (
                wiki_path TEXT NOT NULL,
                source_ref TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sp_wiki ON source_provenance(wiki_path);
            CREATE INDEX IF NOT EXISTS idx_sp_source ON source_provenance(source_ref);
        """)

    def update_page(self, path: str, content: str) -> None:
        """Re-index all outgoing links from a single page.

        Extracts targets from both ``[[wiki-links]]`` in body text and
        the ``related`` list in YAML frontmatter so that backlink queries
        are complete regardless of where the link is declared.
        """
        self._conn.execute("DELETE FROM links WHERE source_path = ?", (path,))
        targets = set(WIKILINK_PATTERN.findall(content))
        targets |= _related_from_frontmatter(content)
        for target in targets:
            self._conn.execute(
                "INSERT INTO links (source_path, target_title) VALUES (?, ?)",
                (path, target),
            )
        self._conn.commit()

    def remove_page(self, path: str) -> None:
        self._conn.execute("DELETE FROM links WHERE source_path = ?", (path,))
        self._conn.commit()

    def backlinks_for(self, title: str) -> list[str]:
        """Return all pages that link to a given title."""
        cursor = self._conn.execute(
            "SELECT DISTINCT source_path FROM links WHERE target_title = ?",
            (title,),
        )
        return [row[0] for row in cursor]

    def outlinks_for(self, path: str) -> list[str]:
        """Return all titles linked from a given page."""
        cursor = self._conn.execute(
            "SELECT DISTINCT target_title FROM links WHERE source_path = ?",
            (path,),
        )
        return [row[0] for row in cursor]

    def orphan_pages(self, all_titles: set[str]) -> list[str]:
        """Find titles that exist but have zero inbound links."""
        if not all_titles:
            return []
        linked_titles = set()
        cursor = self._conn.execute("SELECT DISTINCT target_title FROM links")
        for row in cursor:
            linked_titles.add(row[0])
        return sorted(all_titles - linked_titles)

    def reference_count(self, title: str) -> int:
        cursor = self._conn.execute(
            "SELECT COUNT(*) FROM links WHERE target_title = ?", (title,),
        )
        return cursor.fetchone()[0]

    def rebuild(self, pages: list[dict]) -> None:
        """Full rebuild from a list of {path, content} dicts."""
        self._conn.execute("DELETE FROM links")
        for p in pages:
            targets = set(WIKILINK_PATTERN.findall(p["content"]))
            targets |= _related_from_frontmatter(p["content"])
            for target in targets:
                self._conn.execute(
                    "INSERT INTO links (source_path, target_title) VALUES (?, ?)",
                    (p["path"], target),
                )
        self._conn.commit()

    def stats(self) -> dict:
        """Quick stats about the link graph."""
        total_links = self._conn.execute("SELECT COUNT(*) FROM links").fetchone()[0]
        unique_sources = self._conn.execute("SELECT COUNT(DISTINCT source_path) FROM links").fetchone()[0]
        unique_targets = self._conn.execute("SELECT COUNT(DISTINCT target_title) FROM links").fetchone()[0]
        return {
            "total_links": total_links,
            "pages_with_outlinks": unique_sources,
            "distinct_targets": unique_targets,
        }

    # ------------------------------------------------------------------
    # Source provenance helpers
    # ------------------------------------------------------------------

    def update_source_index(self, wiki_pages: list[dict]) -> None:
        """Rebuild the source-provenance index from wiki frontmatter.

        *wiki_pages* is a list of ``{"path": ..., "sources": [...]}``
        where ``sources`` comes from the frontmatter ``sources`` field.
        This lets us answer "which wiki pages cite this source?" and
        "which sources have no wiki page yet?"
        """
        self._conn.execute("DELETE FROM source_provenance")
        for wp in wiki_pages:
            for src in wp.get("sources") or []:
                self._conn.execute(
                    "INSERT INTO source_provenance (wiki_path, source_ref) VALUES (?, ?)",
                    (wp["path"], str(src)),
                )
        self._conn.commit()

    def wiki_pages_citing_source(self, source_ref: str) -> list[str]:
        """Return wiki pages whose frontmatter ``sources`` includes *source_ref*."""
        cursor = self._conn.execute(
            "SELECT DISTINCT wiki_path FROM source_provenance WHERE source_ref = ?",
            (source_ref,),
        )
        return [row[0] for row in cursor]

    def uncited_sources(self, all_source_paths: list[str]) -> list[str]:
        """Return source paths that are not cited by any wiki page."""
        cited = set()
        cursor = self._conn.execute("SELECT DISTINCT source_ref FROM source_provenance")
        for row in cursor:
            cited.add(row[0])
        return sorted(s for s in all_source_paths if s not in cited)

    def close(self) -> None:
        self._conn.close()
