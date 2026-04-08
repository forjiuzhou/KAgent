"""Vault — the knowledge base on disk.

A vault is a directory of Markdown files with a fixed structure:
  vault/
  ├── sources/        immutable raw materials
  ├── wiki/           agent-maintained structured knowledge
  │   ├── index.md    knowledge index
  │   ├── log.md      operation log
  │   ├── concepts/   concept pages
  │   ├── entities/   entity pages
  │   ├── journals/   daily journals & inbox
  │   └── synthesis/  synthesis & analysis pages
  ├── .schema/        vault constitution
  │   └── schema.md   structure conventions
  └── .meta/          derived data (rebuildable)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

WIKI_DIRS = ["concepts", "entities", "journals", "synthesis", "archive"]

INITIAL_SCHEMA = """\
---
title: Vault Schema
updated: {date}
---

# Vault Schema

This file defines the conventions the agent follows when maintaining the wiki.
It evolves over time as you and the agent figure out what works.

## Core Principle: Progressive Disclosure

The knowledge base is designed so that both LLMs and humans can navigate it
by starting from a high-level overview and drilling down to specifics.

The structure forms a **tree** (for O(log n) access) overlaid with a **graph**
(cross-references via [[wiki-links]] for lateral discovery):

```
index.md (root — lists Hubs with one-line descriptions)
  → Hub pages (navigation — overview + links to Canonicals)
    → Canonical pages (content — authoritative documents)
      → Sources (evidence — raw referenced materials)
```

Every page must follow the "inverted pyramid" rule:
- First 1-2 sentences: self-contained summary answering "what is this page about?"
- Then: organized detail, evidence, and cross-references
- An LLM reading only the first paragraph of each page should be able to
  judge relevance and decide whether to read further.

## Knowledge Object Types

| Type | Role | Directory |
|------|------|-----------|
| `hub` | Navigation entry for a topic. Overview + links to related pages. | `wiki/concepts/` |
| `canonical` | Authoritative main document. Must have sources. | `wiki/concepts/` |
| `journal` | Time-ordered captures, daily logs. | `wiki/journals/` |
| `synthesis` | Cross-cutting analysis, source summaries, comparisons. | `wiki/synthesis/` |
| `note` | Work-in-progress, not yet mature. | `wiki/concepts/` |
| `archive` | Retired page, preserved for history. | `wiki/archive/` |

## Page Conventions

Every wiki page uses YAML frontmatter:

```yaml
---
title: Page Title
type: hub | canonical | journal | synthesis | note | archive
sources: []       # URLs or source references (required for canonical)
related: []       # [[wiki-links]] to related pages
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

## Hard Constraints (enforced by the system)

- `sources/` is immutable — the agent cannot write to it
- All wiki pages must have valid frontmatter with `title` and `type`
- Canonical pages must have a non-empty `sources` field
- Pages are never deleted — they are archived to `wiki/archive/`

## Index Structure

`wiki/index.md` is the root of the navigation tree. It should:
- List each Hub with a one-line description (not a flat dump of all pages)
- Stay concise (aim for <1000 tokens) so LLMs can read it in one pass
- Group Hubs by broad domain if the knowledge base spans multiple areas

Individual Hub pages then list the pages under their topic. This keeps
index.md lightweight and gives the LLM a two-hop path to any content.

## When to Create a Hub

Create a new Hub when a topic area accumulates 3+ related pages (canonicals,
notes, synthesis) that would benefit from a shared entry point. The Hub
provides the overview and navigation; the individual pages provide depth.

## Link Conventions

Use `[[wiki-link]]` syntax for internal links (Obsidian-compatible).
"""

INITIAL_INDEX = """\
---
title: Wiki Index
updated: {date}
---

# Wiki Index

Root of the knowledge base. Start here to navigate.

## Topics

(no hubs yet — as content grows, the agent creates Hub pages here)

## Recent

(no pages yet)
"""

INITIAL_LOG = """\
---
title: Operation Log
---

# Operation Log

Chronological record of all agent operations.

## [{date}] init | Vault Created

Vault initialized. Ready for knowledge.
"""


class Vault:
    """Handle to an on-disk knowledge vault."""

    def __init__(self, root: str | Path, auto_git: bool = True) -> None:
        self.root = Path(root).resolve()
        self.sources_dir = self.root / "sources"
        self.wiki_dir = self.root / "wiki"
        self.schema_dir = self.root / ".schema"
        self.meta_dir = self.root / ".meta"
        self._auto_git = auto_git
        self._repo = None

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    def exists(self) -> bool:
        return (self.schema_dir / "schema.md").is_file()

    def init(self) -> None:
        """Create the vault directory structure and seed files."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        self.sources_dir.mkdir(parents=True, exist_ok=True)
        self.schema_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)

        for sub in WIKI_DIRS:
            (self.wiki_dir / sub).mkdir(parents=True, exist_ok=True)

        self._write_if_missing(
            self.schema_dir / "schema.md",
            INITIAL_SCHEMA.format(date=today),
        )
        self._write_if_missing(
            self.wiki_dir / "index.md",
            INITIAL_INDEX.format(date=today),
        )
        self._write_if_missing(
            self.wiki_dir / "log.md",
            INITIAL_LOG.format(date=today),
        )

        # Write .gitignore for .meta/ (derived data, not versioned)
        self._write_if_missing(
            self.root / ".gitignore",
            ".meta/\n",
        )

        self._git_init()
        self._git_commit("Vault initialized")

    # ------------------------------------------------------------------
    # File operations (used by tools)
    # ------------------------------------------------------------------

    def read_file(self, rel_path: str) -> str:
        """Read a file from the vault. Path is relative to vault root."""
        path = self._resolve(rel_path)
        return path.read_text(encoding="utf-8")

    def write_file(self, rel_path: str, content: str) -> None:
        """Write a file in the wiki area. Refuses to write into sources/."""
        path = self._resolve(rel_path)
        if self._is_in_sources(path):
            raise PermissionError(
                f"Cannot write to sources/ — it is immutable. Path: {rel_path}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._git_commit(f"Update {rel_path}")

    def list_files(self, rel_dir: str = "wiki", pattern: str = "*.md") -> list[str]:
        """List files matching a glob pattern under a vault subdirectory."""
        base = self._resolve(rel_dir)
        if not base.is_dir():
            return []
        return sorted(
            str(p.relative_to(self.root))
            for p in base.rglob(pattern)
            if p.is_file()
        )

    def search_content(self, query: str, directory: str = "wiki") -> list[dict]:
        """Naive full-text search across markdown files. Returns matches."""
        results = []
        query_lower = query.lower()
        for rel_path in self.list_files(directory):
            content = self.read_file(rel_path)
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

    def read_file_partial(self, rel_path: str, max_chars: int) -> str:
        """Read the first max_chars characters of a file."""
        path = self._resolve(rel_path)
        with open(path, encoding="utf-8") as f:
            return f.read(max_chars)

    def read_frontmatters(self, rel_dir: str = "wiki") -> list[dict]:
        """Read frontmatter from all .md files in a directory. No body text."""
        from noteweaver.frontmatter import page_summary_from_file

        results = []
        for rel_path in self.list_files(rel_dir):
            try:
                content = self.read_file(rel_path)
                ps = page_summary_from_file(rel_path, content)
                if ps is not None:
                    results.append({
                        "path": ps.path,
                        "title": ps.title,
                        "type": ps.type,
                        "summary": ps.summary,
                        "tags": ps.tags,
                    })
            except (FileNotFoundError, PermissionError):
                continue
        return results

    def rebuild_index(self) -> str:
        """Rebuild index.md from actual file frontmatter. Self-healing."""
        from noteweaver.frontmatter import page_summary_from_file
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hubs = []
        other_pages = []

        for rel_path in self.list_files("wiki"):
            if rel_path in ("wiki/index.md", "wiki/log.md"):
                continue
            if "/archive/" in rel_path:
                continue
            try:
                content = self.read_file(rel_path)
                ps = page_summary_from_file(rel_path, content)
                if ps is None:
                    continue
                entry = {
                    "path": rel_path,
                    "title": ps.title or rel_path,
                    "type": ps.type,
                    "summary": ps.summary,
                    "tags": ps.tags,
                }
                if ps.type == "hub":
                    hubs.append(entry)
                else:
                    other_pages.append(entry)
            except (FileNotFoundError, PermissionError):
                continue

        lines = [
            f"---\ntitle: Wiki Index\nupdated: {today}\n---\n",
            "# Wiki Index\n",
            "Root of the knowledge base. Start here to navigate.\n",
        ]

        # Pinned pages
        pinned = [p for p in (hubs + other_pages) if "pinned" in p["tags"]]
        if pinned:
            lines.append("## Pinned\n")
            for p in pinned:
                desc = f" — {p['summary']}" if p['summary'] else ""
                lines.append(f"- [[{p['title']}]]{desc}")
            lines.append("")

        # Hubs
        lines.append("## Topics\n")
        if hubs:
            for h in sorted(hubs, key=lambda x: x["title"]):
                desc = f" — {h['summary']}" if h['summary'] else ""
                lines.append(f"- [[{h['title']}]]{desc}")
        else:
            lines.append("(no hubs yet)")
        lines.append("")

        # Recent non-hub pages (last 10)
        lines.append("## Recent\n")
        if other_pages:
            for p in other_pages[-10:]:
                desc = f" — {p['summary']}" if p['summary'] else ""
                lines.append(f"- [[{p['title']}]] ({p['type']}){desc}")
        else:
            lines.append("(no pages yet)")

        content = "\n".join(lines) + "\n"
        self.write_file("wiki/index.md", content)
        return content

    def stats(self) -> dict:
        """Return vault statistics."""
        return {
            "concepts": len(self.list_files("wiki/concepts")),
            "entities": len(self.list_files("wiki/entities")),
            "journals": len(self.list_files("wiki/journals")),
            "synthesis": len(self.list_files("wiki/synthesis")),
            "sources": len(self.list_files("sources")),
        }

    def append_log(self, entry_type: str, title: str, details: str = "") -> None:
        """Append an entry to wiki/log.md."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = f"\n## [{today}] {entry_type} | {title}\n"
        if details:
            entry += f"\n{details}\n"

        log_path = self.wiki_dir / "log.md"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve(self, rel_path: str) -> Path:
        """Resolve a relative path within the vault, preventing escape."""
        resolved = (self.root / rel_path).resolve()
        if not str(resolved).startswith(str(self.root)):
            raise PermissionError(f"Path escapes vault: {rel_path}")
        return resolved

    def _is_in_sources(self, path: Path) -> bool:
        return str(path.resolve()).startswith(str(self.sources_dir.resolve()))

    # ------------------------------------------------------------------
    # Git integration
    # ------------------------------------------------------------------

    def _git_init(self) -> None:
        """Initialize a git repo in the vault if auto_git is enabled."""
        if not self._auto_git:
            return
        try:
            from git import Repo, InvalidGitRepositoryError
            try:
                self._repo = Repo(self.root)
            except InvalidGitRepositoryError:
                self._repo = Repo.init(self.root)
                self._repo.config_writer().set_value(
                    "user", "name", "NoteWeaver"
                ).release()
                self._repo.config_writer().set_value(
                    "user", "email", "agent@noteweaver"
                ).release()
        except ImportError:
            log.debug("gitpython not installed, git auto-commit disabled")
            self._auto_git = False

    def _git_commit(self, message: str) -> None:
        """Stage all changes and commit if there are any."""
        if not self._auto_git or self._repo is None:
            return
        try:
            self._repo.git.add(A=True)
            if self._repo.is_dirty(untracked_files=True):
                self._repo.index.commit(message)
        except Exception as e:
            log.debug("git commit failed: %s", e)

    @staticmethod
    def _write_if_missing(path: Path, content: str) -> None:
        if not path.exists():
            path.write_text(content, encoding="utf-8")
