"""Backlink index — tracks [[wiki-link]] relationships between pages.

Lives in .meta/backlinks.db (SQLite). Rebuildable from vault files.
Answers: "who links to this page?", "how many references?", "orphan pages?"
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

WIKILINK_PATTERN = re.compile(r"\[\[([^\]]+)\]\]")


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
        """)

    def update_page(self, path: str, content: str) -> None:
        """Re-index all outgoing links from a single page."""
        self._conn.execute("DELETE FROM links WHERE source_path = ?", (path,))
        targets = WIKILINK_PATTERN.findall(content)
        for target in set(targets):
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
            targets = WIKILINK_PATTERN.findall(p["content"])
            for target in set(targets):
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

    def close(self) -> None:
        self._conn.close()
