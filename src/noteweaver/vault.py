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
import re
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

WIKI_DIRS = ["concepts", "journals", "synthesis", "archive"]

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

## Declaration vs Use (like code)

Think of the knowledge base like a codebase:
- **Hub** = package index / directory. Entry point that organizes.
- **Canonical** = main definition / implementation. Where a topic is defined.
- **Other pages** (note, synthesis, journal) = usage sites. They reference
  concepts but don't own the definition.

When updating knowledge: find the Canonical (definition site) and update
there. Don't create a second definition — link to the existing one.
When navigating: start at Hub (index), drill into Canonical (definition).

## Three Modes of Operation

### Mode 1: Conversation (default)
The user is thinking, discussing, exploring. The agent responds naturally.
It may reference the knowledge base when relevant, but does NOT need to
touch the vault on every message. Most interactions are just conversations.

If a discussion produces a valuable insight, the agent OFFERS to capture
it — it does not silently write.

### Mode 2: Capture
Triggered by explicit requests ("remember this", "import this URL") or
by the agent recognizing something worth recording.

Two sub-types:
- **Immediate capture**: user explicitly asks, or a clear conclusion emerges
- **Journal capture**: quick thoughts → append to `wiki/journals/YYYY-MM-DD.md`

### Mode 3: Organize
Triggered by explicit requests ("clean up", "check health") or by the
system (`nw lint`, `nw digest`).

Includes: ingest, import, lint, digest (journal→knowledge promotion),
archive, tree maintenance (Hub creation, index updates).

## Journal → Knowledge Pipeline

Journals are the raw material pool for knowledge. The flow:

```
Conversation → Journal (raw log, low cost)
                 ↓
              Digest (periodic review, extracts insights)
                 ↓
           Note / Canonical (structured knowledge)
```

The `digest` operation reviews recent journals looking for:
- Conclusions worth promoting to Notes
- Topics mentioned repeatedly that deserve their own page
- Connections between conversations not yet linked
- User preferences or patterns to record

## Writing Style

- File names: lowercase, hyphenated (`attention-mechanism.md`)
- Hub pages: short overview, then [[links]] with one-line descriptions
- Canonical pages: summary → evidence → analysis → ## Related
- Every page ends with `## Related` listing [[wiki-links]]

## Workflows

### Ingest a URL
1. `fetch_url` to get content
2. `save_source` to archive raw content to sources/
3. `list_page_summaries` to see what exists
4. Create synthesis page at `wiki/synthesis/summary-SLUG.md`
5. Update or create concept pages, add [[links]] and tags
6. If 3+ pages on a topic and no Hub, create a Hub
7. Update `wiki/index.md` and `append_log`

### Import local files
1. `import_files(directory)` — batch imports all .md files
2. Review summary, optionally reorganize

### Query (within conversation)
1. Search or scan pages relevant to the question
2. Synthesize answer with [[wiki-link]] citations
3. Offer to file valuable answers as wiki pages

### Quick capture
1. Append to today's journal (`wiki/journals/YYYY-MM-DD.md`)
2. Add tags, note connections
3. Brief confirmation

### Digest (journal → knowledge)
1. Scan recent journals
2. Identify insights worth promoting
3. Create Note/Canonical pages, or ask user to confirm
4. Update index and log

### Health check
1. `vault_stats` for overview
2. Scan for orphans, missing pages, contradictions
3. Report findings

### Archive
1. `archive_page(path, reason)`
2. Update index.md
3. `append_log`

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
        self._operation_depth = 0
        self._operation_dirty = False
        self._search_index = None
        self._backlink_index = None

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
        self.rebuild_search_index()

    # ------------------------------------------------------------------
    # Search index
    # ------------------------------------------------------------------

    @property
    def search(self):
        """Lazy-initialized FTS5 search index."""
        if self._search_index is None:
            from noteweaver.search import SearchIndex
            self._search_index = SearchIndex(self.meta_dir)
        return self._search_index

    @property
    def backlinks(self):
        """Lazy-initialized backlink index."""
        if self._backlink_index is None:
            from noteweaver.backlinks import BacklinkIndex
            self._backlink_index = BacklinkIndex(self.meta_dir)
        return self._backlink_index

    def _index_file(self, rel_path: str, content: str) -> None:
        """Update the search index for a single file."""
        from noteweaver.frontmatter import extract_frontmatter
        fm = extract_frontmatter(content) or {}
        tags = fm.get("tags", [])
        self.search.upsert(
            path=rel_path,
            title=fm.get("title", ""),
            type=fm.get("type", ""),
            summary=fm.get("summary", ""),
            tags=", ".join(tags) if isinstance(tags, list) else str(tags),
            body=content,
        )

    def rebuild_search_index(self) -> int:
        """Rebuild the entire search index from vault files."""
        from noteweaver.frontmatter import extract_frontmatter
        pages = []
        for rel_path in self.list_files("wiki"):
            try:
                content = self.read_file(rel_path)
                fm = extract_frontmatter(content) or {}
                tags = fm.get("tags", [])
                pages.append({
                    "path": rel_path,
                    "title": fm.get("title", ""),
                    "type": fm.get("type", ""),
                    "summary": fm.get("summary", ""),
                    "tags": ", ".join(tags) if isinstance(tags, list) else str(tags),
                    "body": content,
                })
            except (FileNotFoundError, PermissionError):
                continue
        self.search.rebuild(pages)
        return len(pages)

    # ------------------------------------------------------------------
    # File operations (used by tools)
    # ------------------------------------------------------------------

    def read_file(self, rel_path: str) -> str:
        """Read a file from the vault. Path is relative to vault root."""
        path = self._resolve(rel_path)
        return path.read_text(encoding="utf-8")

    _SKIP_UPDATED = frozenset({"wiki/index.md", "wiki/log.md"})
    _UPDATED_RE = re.compile(r"^(updated:\s*)\S+", re.MULTILINE)

    def _touch_updated(self, content: str) -> str:
        """Set the frontmatter ``updated`` field to today if it already exists."""
        from noteweaver.frontmatter import FRONTMATTER_PATTERN
        if not FRONTMATTER_PATTERN.match(content):
            return content
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        new_content, n = self._UPDATED_RE.subn(rf"\g<1>{today}", content, count=1)
        return new_content if n else content

    def write_file(self, rel_path: str, content: str) -> None:
        """Write a file in the wiki area. Refuses to write into sources/."""
        path = self._resolve(rel_path)
        if self._is_in_sources(path):
            raise PermissionError(
                f"Cannot write to sources/ — it is immutable. Path: {rel_path}"
            )
        if not rel_path.startswith("wiki/") and not rel_path.startswith(".schema/"):
            raise PermissionError(
                f"Can only write to wiki/ or .schema/. Path: {rel_path}"
            )
        if rel_path.startswith("wiki/") and rel_path not in self._SKIP_UPDATED:
            content = self._touch_updated(content)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._index_file(rel_path, content)
        self.backlinks.update_page(rel_path, content)
        if self._operation_depth > 0:
            self._operation_dirty = True
        else:
            self._git_commit(f"Update {rel_path}")

    def save_source(self, rel_path: str, content: str) -> None:
        """Write a file to sources/. Only creates new files, never overwrites."""
        if not rel_path.startswith("sources/"):
            raise PermissionError(f"save_source only writes to sources/. Path: {rel_path}")
        path = self._resolve(rel_path)
        if path.exists():
            raise PermissionError(f"Source already exists and is immutable: {rel_path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if self._operation_depth > 0:
            self._operation_dirty = True
        else:
            self._git_commit(f"Save source {rel_path}")

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
        """Full-text search using SQLite FTS5 index.

        Returns ranked results with snippets. Falls back to brute-force
        scan if FTS index is empty or returns no results.
        """
        # Try FTS first
        fts_results = self.search.search(query)
        if fts_results:
            # Filter by directory prefix
            filtered = [r for r in fts_results if r["path"].startswith(directory)]
            if filtered:
                return [
                    {"path": r["path"], "matches": [(0, r["snippet"])]}
                    for r in filtered
                ]

        # Fallback: brute-force scan (covers unindexed files)
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
        from noteweaver.frontmatter import page_summary_from_file, extract_frontmatter
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
                fm = extract_frontmatter(content) or {}
                entry = {
                    "path": rel_path,
                    "title": ps.title or rel_path,
                    "type": ps.type,
                    "summary": ps.summary,
                    "tags": ps.tags,
                    "updated": str(fm.get("updated", "")),
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

        # Recent non-hub pages sorted by updated date (newest first, last 10)
        lines.append("## Recent\n")
        if other_pages:
            sorted_pages = sorted(
                other_pages,
                key=lambda x: x.get("updated", ""),
                reverse=True,
            )
            for p in sorted_pages[:10]:
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
            "journals": len(self.list_files("wiki/journals")),
            "synthesis": len(self.list_files("wiki/synthesis")),
            "sources": len(self.list_files("sources")),
        }

    def rebuild_backlinks(self) -> int:
        """Rebuild backlink index from all vault files."""
        pages = []
        for rel_path in self.list_files("wiki"):
            try:
                content = self.read_file(rel_path)
                pages.append({"path": rel_path, "content": content})
            except (FileNotFoundError, PermissionError):
                continue
        self.backlinks.rebuild(pages)
        return len(pages)

    def health_metrics(self) -> dict:
        """Compute quantitative health metrics for the knowledge base."""
        import re
        from noteweaver.frontmatter import page_summary_from_file

        all_pages = []
        all_content = {}
        for rel_path in self.list_files("wiki"):
            if rel_path in ("wiki/index.md", "wiki/log.md"):
                continue
            if "/archive/" in rel_path:
                continue
            try:
                content = self.read_file(rel_path)
                ps = page_summary_from_file(rel_path, content)
                all_pages.append({"path": rel_path, "ps": ps, "content": content})
                all_content[rel_path] = content
            except (FileNotFoundError, PermissionError):
                continue

        total = len(all_pages)
        if total == 0:
            return {"total_pages": 0}

        # Count types
        hubs = [p for p in all_pages if p["ps"] and p["ps"].type == "hub"]
        canonicals = [p for p in all_pages if p["ps"] and p["ps"].type == "canonical"]
        canonicals_with_sources = [
            c for c in canonicals if c["ps"].sources
        ]

        # Use backlink index for orphan detection
        page_titles = {p["ps"].title for p in all_pages if p["ps"] and p["ps"].title}
        orphans = [
            p for p in all_pages
            if p["ps"] and p["ps"].title
            and self.backlinks.reference_count(p["ps"].title) == 0
            and p["ps"].type not in ("hub", "journal")
        ]

        # Pages missing summary
        no_summary = [p for p in all_pages if p["ps"] and not p["ps"].summary]

        link_stats = self.backlinks.stats()
        return {
            "total_pages": total,
            "hubs": len(hubs),
            "canonicals": len(canonicals),
            "canonical_source_ratio": (
                f"{len(canonicals_with_sources)}/{len(canonicals)}"
                if canonicals else "n/a"
            ),
            "orphan_pages": len(orphans),
            "orphan_rate": f"{len(orphans)}/{total}" if total else "n/a",
            "pages_without_summary": len(no_summary),
            "hub_coverage": (
                f"{len(hubs)} hubs for {total - len(hubs)} content pages"
            ),
            "total_links": link_stats["total_links"],
            "avg_links_per_page": round(link_stats["total_links"] / total, 1) if total else 0,
        }

    def import_directory(self, source_dir: str) -> str:
        """Import .md files from an external directory into the vault.

        Uses an operation context so all writes produce a single git commit.
        """
        from noteweaver.frontmatter import extract_frontmatter

        src = Path(source_dir).resolve()
        if not src.is_dir():
            return f"Error: not a directory: {source_dir}"

        md_files = sorted(src.rglob("*.md"))
        if not md_files:
            return f"No .md files found in {source_dir}"

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        imported = 0
        results = []

        with self.operation(f"Import {len(md_files)} files from {source_dir}"):
            for f in md_files:
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                except Exception as e:
                    results.append(f"  Error reading {f.name}: {e}")
                    continue

                fm = extract_frontmatter(content)
                rel_name = f.name
                page_type = fm.get("type") if fm else None

                if page_type == "synthesis":
                    dest = f"wiki/synthesis/{rel_name}"
                elif page_type == "journal":
                    dest = f"wiki/journals/{rel_name}"
                elif page_type in ("hub", "canonical", "note"):
                    dest = f"wiki/concepts/{rel_name}"
                else:
                    title = f.stem.replace("-", " ").replace("_", " ").title()
                    header = (
                        f"---\ntitle: {title}\ntype: note\n"
                        f"summary: Imported from {f.name}\n"
                        f"tags: [imported]\ncreated: {today}\nupdated: {today}\n---\n\n"
                    )
                    content = header + content
                    dest = f"wiki/concepts/{rel_name}"

                try:
                    self.write_file(dest, content)
                    imported += 1
                    results.append(f"  ✓ {f.name} → {dest}")
                except Exception as e:
                    results.append(f"  Error writing {f.name}: {e}")

            self.rebuild_index()
            self.append_log("import", f"Imported {imported} files from {source_dir}")

        summary = f"Imported {imported}/{len(md_files)} files from {source_dir}\n"
        summary += "\n".join(results[:20])
        if len(results) > 20:
            summary += f"\n  ... and {len(results) - 20} more"
        return summary

    def append_log(self, entry_type: str, title: str, details: str = "") -> None:
        """Append an entry to wiki/log.md."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entry = f"\n## [{today}] {entry_type} | {title}\n"
        if details:
            entry += f"\n{details}\n"

        log_path = self.wiki_dir / "log.md"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(entry)
        if self._operation_depth > 0:
            self._operation_dirty = True
        else:
            self._git_commit(f"Log: [{entry_type}] {title}")

    def operation(self, message: str = "Agent operation"):
        """Context manager for batching writes into a single git commit."""
        return _OperationContext(self, message)

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
            log.warning("git commit failed: %s", e)

    @staticmethod
    def _write_if_missing(path: Path, content: str) -> None:
        if not path.exists():
            path.write_text(content, encoding="utf-8")


class _OperationContext:
    """Batches all vault writes into a single git commit.

    Supports nesting: only the outermost context triggers the commit.
    """

    def __init__(self, vault: Vault, message: str) -> None:
        self._vault = vault
        self._message = message

    def __enter__(self) -> Vault:
        self._vault._operation_depth += 1
        return self._vault

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self._vault._operation_depth -= 1
        if self._vault._operation_depth == 0 and self._vault._operation_dirty:
            self._vault._git_commit(self._message)
            self._vault._operation_dirty = False
        return None
