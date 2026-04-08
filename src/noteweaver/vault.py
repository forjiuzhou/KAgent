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

This is the operating manual for this knowledge base. Any agent (LLM or human)
maintaining this vault should read this file first.

## Why This Design

The structured document is the primary asset. The LLM is the maintainer and
executor — it maintains the structure during use, and draws on the structure
plus its own capabilities when executing tasks. The knowledge base must remain
valuable even if the model is replaced, and navigable even without any model.

## Core Principle: Progressive Disclosure

The knowledge base is a tree (hierarchy for top-down access) overlaid with
a graph (cross-references) and tags (horizontal slicing). This gives O(log n)
access to any knowledge:

```
index.md  (root — Hubs + pinned pages, kept under ~1000 tokens)
  → Hub   (topic entry — overview + child page links with descriptions)
    → Canonical / Note / Synthesis  (actual content)
```

Three navigation mechanisms:
- **Tree** (index → Hub → Page): structured, top-down
- **Tags** (frontmatter `tags` field): cross-cutting, horizontal
- **Links** ([[wiki-links]]): associative, point-to-point

**Inverted pyramid**: every page's first 1-2 sentences are a self-contained
summary. Reading only summaries should be enough to judge relevance.

## Three Levels of Reading

| Level | How | Cost/page | When |
|-------|-----|-----------|------|
| Scan | `list_page_summaries` | ~30 tokens | Surveying, filtering by tag |
| Shallow | `read_page(max_chars=500)` | ~150 tokens | Relevance check |
| Deep | `read_page` (full) | ~2000 tokens | Reading relevant content |

Always scan or shallow-read before deep-reading.

## Knowledge Object Types

| Type | Role | Key rules |
|------|------|-----------|
| `hub` | Navigation entry for a topic | Keep concise. List child pages with descriptions. |
| `canonical` | Authoritative main document | MUST have `sources`. One per topic. |
| `journal` | Time-ordered captures, daily logs | Preserve original expression. |
| `synthesis` | Cross-cutting analysis, source summaries | Cite sources via [[links]]. |
| `note` | Work-in-progress | Can be revised, merged, promoted. |
| `archive` | Retired page | Created by archive_page tool only. |

Hub says "here's everything about X, go read these pages."
Canonical says "here's the definitive explanation of X."
If a page grows both navigation AND deep content, split it.

## Frontmatter

```yaml
---
title: Page Title
type: hub | canonical | journal | synthesis | note | archive
summary: One-sentence description of what this page covers
tags: [topic-a, topic-b]      # cross-cutting labels, agent-managed
sources: []                     # required for canonical
related: []                     # [[wiki-links]]
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

## Tags

Tags provide horizontal navigation across the tree. Create and manage tags
organically — no predefined taxonomy. Tags emerge from content.

Special tag: `pinned` — these pages appear at the top of index.md.

## Writing Style

- File names: lowercase, hyphenated (`attention-mechanism.md`)
- Hub pages: short overview, then [[links]] with one-line descriptions
- Canonical pages: summary → evidence → analysis → ## Related
- Every page ends with `## Related` listing [[wiki-links]]

## Workflows

### Ingest (URL or content)
1. `fetch_url` to get content
2. `list_page_summaries` to see what exists
3. Create synthesis page at `wiki/synthesis/summary-SLUG.md`
4. Update or create concept pages, add [[links]] and tags
5. If 3+ pages on a topic and no Hub, create a Hub
6. Update `wiki/index.md` and `append_log`

### Query
1. `read_page("wiki/index.md")` → find relevant Hub
2. Read Hub → scan child pages → deep-read relevant ones
3. Synthesize answer with [[wiki-link]] citations
4. Offer to file valuable answers as wiki pages

### Quick capture
1. Append to today's journal (`wiki/journals/YYYY-MM-DD.md`)
2. Add tags, note connections to existing pages
3. Brief response: confirm + what it connects to

### Lint
1. `list_page_summaries("wiki")` for full scan
2. Check: orphans, missing pages, contradictions, stale info
3. Report findings, suggest improvements

### Tree maintenance
- index.md lists Hubs (not individual pages), under ~1000 tokens
- Create Hub when 3+ related pages accumulate
- Each Hub lists child pages with one-line descriptions

## Hard Constraints (system-enforced)

- `sources/` is immutable — writes rejected
- Frontmatter must have `title` and `type`
- Canonical must have non-empty `sources`
- `tags` must be a list
- Pages are never deleted — use `archive_page`

## Directory Layout

```
vault/
├── sources/          immutable raw materials
├── wiki/
│   ├── index.md      navigation root
│   ├── log.md        operation log
│   ├── concepts/     hub, canonical, note pages
│   ├── journals/     daily entries, quick captures
│   ├── synthesis/    analysis, source summaries
│   └── archive/      retired pages
└── .schema/
    ├── schema.md        this file — operating manual
    └── preferences.md   user preferences — how the agent should behave
```

## User Preferences

`.schema/preferences.md` records how this specific user wants the system to
work. The agent reads it at startup and follows these preferences.

Preferences are different from knowledge — they answer "how should the agent
behave?" not "what is true about the world?". Examples:

- Response language and style
- Organization strategy (by topic, by project, by time)
- Naming and tagging conventions
- What's worth promoting to canonical vs keeping as notes
- How proactive the agent should be
"""

INITIAL_PREFERENCES = """\
---
title: User Preferences
type: preference
updated: {date}
---

# User Preferences

This file tells the agent how you want it to behave. Edit it anytime.
The agent reads this at startup and adapts accordingly.

## Language
- Respond in: (auto-detect from user input)

## Organization Style
- (default: organize by topic, create Hubs when 3+ pages accumulate)

## Other Preferences
- (add your preferences here as you discover them)
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
        self._write_if_missing(
            self.schema_dir / "preferences.md",
            INITIAL_PREFERENCES.format(date=today),
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
        self._git_commit(f"Log: [{entry_type}] {title}")

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
