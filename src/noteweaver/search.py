"""SQLite FTS5 search index for the vault.

Lives in .meta/search.db — a derived cache that can be rebuilt from files.
Provides fast full-text search without scanning every file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


class SearchIndex:
    """FTS5-backed search over vault markdown files."""

    def __init__(self, meta_dir: Path) -> None:
        self._db_path = meta_dir / "search.db"
        meta_dir.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._conn.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(
                path, title, type, summary, tags, body,
                tokenize='unicode61'
            );
        """)

    def upsert(
        self,
        path: str,
        title: str = "",
        type: str = "",
        summary: str = "",
        tags: str = "",
        body: str = "",
    ) -> None:
        """Insert or replace a page in the index."""
        self._conn.execute("DELETE FROM pages WHERE path = ?", (path,))
        self._conn.execute(
            "INSERT INTO pages (path, title, type, summary, tags, body) VALUES (?, ?, ?, ?, ?, ?)",
            (path, title, type, summary, tags, body),
        )
        self._conn.commit()

    def remove(self, path: str) -> None:
        """Remove a page from the index."""
        self._conn.execute("DELETE FROM pages WHERE path = ?", (path,))
        self._conn.commit()

    def search(self, query: str, limit: int = 30) -> list[dict]:
        """Search across all indexed fields. Returns ranked results."""
        if not query.strip():
            return []

        # FTS5 query: escape special characters, use implicit AND
        safe_query = self._escape_query(query)

        try:
            cursor = self._conn.execute(
                """
                SELECT path, title, type, summary,
                       snippet(pages, 5, '>>>', '<<<', '...', 30) as snippet,
                       rank
                FROM pages
                WHERE pages MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (safe_query, limit),
            )
            results = []
            for row in cursor:
                results.append({
                    "path": row[0],
                    "title": row[1],
                    "type": row[2],
                    "summary": row[3],
                    "snippet": row[4],
                })
            return results
        except sqlite3.OperationalError:
            # Fallback for malformed queries
            return []

    def rebuild(self, pages: list[dict]) -> None:
        """Drop and rebuild the entire index from a list of page dicts."""
        self._conn.execute("DELETE FROM pages")
        for p in pages:
            self._conn.execute(
                "INSERT INTO pages (path, title, type, summary, tags, body) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    p.get("path", ""),
                    p.get("title", ""),
                    p.get("type", ""),
                    p.get("summary", ""),
                    p.get("tags", ""),
                    p.get("body", ""),
                ),
            )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def _escape_query(query: str) -> str:
        """Make a user query safe for FTS5 MATCH."""
        # Wrap each token in quotes to avoid FTS5 syntax errors
        tokens = query.split()
        if not tokens:
            return '""'
        return " ".join(f'"{t}"' for t in tokens)
