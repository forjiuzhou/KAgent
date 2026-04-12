"""Vault — the knowledge base on disk.

A vault is a directory of Markdown files with a fixed structure:
  vault/
  ├── sources/        immutable raw materials
  ├── wiki/           agent-maintained structured knowledge
  │   ├── index.md    knowledge index
  │   ├── log.md      operation log
  │   ├── concepts/   concept pages
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
title: Wiki Schema
type: preference
updated: {date}
---

# Wiki Schema

Structure definition for this knowledge base. The agent loads this at
startup to understand how the wiki is organized.

For behavioral rules (how to read, write, maintain), see protocols.md.
For user preferences (language, style), see preferences.md.

## Core Principle: Progressive Disclosure

The wiki is a tree (top-down hierarchy) overlaid with a graph
(cross-references) and tags (horizontal slicing):

```
index.md  (root — lists Hubs, kept under ~1000 tokens)
  → Hub   (topic entry — overview + child page links)
    → Canonical / Note / Synthesis  (content pages)
```

Three navigation mechanisms:
- **Tree** (index → Hub → Page): structured, top-down
- **Tags** (frontmatter `tags` field): cross-cutting, horizontal
- **Links** ([[wiki-links]]): associative, point-to-point

**Inverted pyramid**: every page's first 1-2 sentences are a
self-contained summary. Reading only summaries should be enough
to judge relevance.

## Page Types

| Type | Role | Key rules |
|------|------|-----------|
| `hub` | Navigation entry for a topic | Concise. Lists child pages with one-line descriptions. No deep content. |
| `canonical` | Authoritative document on a topic | MUST have `sources`. One per topic. |
| `note` | Work-in-progress | Low barrier. Can be revised, merged, promoted. Duplicates OK. |
| `synthesis` | Cross-cutting analysis | Must cite ≥2 sources via [[wiki-links]]. |
| `journal` | Time-ordered captures, daily logs | Preserve original expression. Low-barrier entry. |
| `archive` | Retired page | Soft-deleted. Never hard-delete — always archive. |

Hub says "here's everything about X, go read these pages."
Canonical says "here's the definitive explanation of X."
If a page grows both navigation AND deep content, split it.

## Frontmatter

Required on all wiki pages (except index.md and log.md):

```yaml
---
title: Page Title
type: hub | canonical | note | synthesis | journal | archive
summary: One-sentence description of what this page covers
tags: [topic-a, topic-b]
sources: []          # required for canonical
related: []          # [[wiki-links]]
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

## Tags

Tags provide horizontal navigation across the tree. Create and
manage tags organically — no predefined taxonomy. Tags emerge
from content.

Special tag: `pinned` — these pages appear at the top of index.md.

## Writing Style

- File names: lowercase-hyphenated (e.g. `attention-mechanism.md`)
- Inverted pyramid: first 1-2 sentences = self-contained summary
- Every page ends with `## Related` listing [[wiki-links]]
- Hub pages: short overview, then [[link]] list with descriptions
- Canonical pages: summary → evidence → analysis → ## Related

## Directory Layout

```
vault/
├── sources/          immutable raw materials (read-only)
├── wiki/
│   ├── index.md      navigation root (lists Hubs only)
│   ├── log.md        operation log
│   ├── concepts/     hub, canonical, note pages
│   ├── journals/     daily entries, quick captures
│   ├── synthesis/    analysis, cross-cutting pages
│   └── archive/      retired pages
└── .schema/
    ├── schema.md       this file — wiki structure definition
    ├── protocols.md    behavioral rules for agents
    └── preferences.md  user preferences
```

## Journal → Knowledge Pipeline

Journals are the raw material pool. The promotion flow:

```
Conversation → Journal (raw capture, low barrier)
                 ↓
              Digest (periodic review, extracts insights)
                 ↓
           Note / Canonical (structured knowledge)
```
"""

INITIAL_PROTOCOLS = """\
---
title: Protocols
type: preference
updated: {date}
---

# Protocols

Behavioral rules for agents operating on this vault.
These are hard constraints and high-leverage workflow patterns —
not preferences, not suggestions.

## Observation Protocols

- **Read before write.** Always read a page before modifying it.
- **Search before create.** Before creating a new page, search for
  existing pages on the same topic. Prefer updating or appending
  to an existing page over creating a duplicate.
- **Scan before restructure.** Before any structural maintenance
  (hub creation, reorganization, bulk linking), read the world
  summary and understand the current shape of the wiki.

## Structure Protocols

- Every durable page (hub, canonical, note, synthesis) must have
  frontmatter with at least `title`, `type`, and `summary`.
- Every durable page should end with `## Related` containing
  [[wiki-links]] to connected pages.
- Canonical pages must have a non-empty `sources` field.
- When 3+ pages accumulate on a topic with no hub, create a hub.
- No orphan pages: every new page must link to at least one
  existing page, and at least one existing page should link back.
- Hub pages are navigation entries — keep them concise, list child
  pages with one-line descriptions, don't put deep content in hubs.

## Change Protocols

- **Small changes: brief notice then write.** Appending a section,
  adding a link, updating tags or metadata — briefly tell the user
  what you're about to do, then write. No need to wait for approval.
- **Larger changes: propose first.** Creating new pages or
  restructuring existing content — describe your plan in natural
  language and let the user confirm before writing.
- **When uncertain, ask.** If there are trade-offs or the user's
  intent is ambiguous, propose and ask rather than guess.
- **Journal is low-barrier.** Journal entries can be written freely
  without full structural compliance — they are raw material.
- **Never hard-delete.** Durable pages are never deleted, only
  archived via the archive mechanism.
- **Sources are immutable.** Never write to `sources/` — it is a
  read-only reference library.

## Conversation-to-Wiki Protocol

When a conversation produces an insight worth capturing:

1. Search existing wiki for related pages.
2. If a related canonical or note exists, propose appending or
   updating it — don't create a duplicate.
3. If it's genuinely new, create a note (not canonical — notes
   are the low-barrier entry point for new knowledge).
4. Add [[wiki-links]] connecting the new content to existing pages.
5. Check if a hub needs updating or creating.
6. Confirm the structural result: no orphans, links are bidirectional.

## Source Import Protocol

When importing external content (URL, file, etc.):

1. Fetch and save the raw source to `sources/`.
2. Search existing wiki to understand what already covers this topic.
3. Create or update wiki pages that synthesize the source material.
4. Link new pages to existing related pages.
5. If a hub exists for this topic, update it. If 3+ pages now exist
   without a hub, create one.
6. Update `wiki/index.md` if new hubs were created.
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
        self._write_if_missing(
            self.schema_dir / "protocols.md",
            INITIAL_PROTOCOLS.format(date=today),
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
            title=str(fm.get("title", "")),
            type=str(fm.get("type", "")),
            summary=str(fm.get("summary", "")),
            tags=", ".join(str(t) for t in tags) if isinstance(tags, list) else str(tags),
            body=content,
        )

    def rebuild_search_index(self) -> int:
        """Rebuild the entire search index from vault files.

        Indexes both wiki/ and sources/ so that search_vault can find
        content everywhere in the vault.  Sources files without frontmatter
        are indexed with path-derived metadata.
        """
        from noteweaver.frontmatter import extract_frontmatter

        pages = []
        for rel_dir in ("wiki", "sources"):
            for rel_path in self.list_files(rel_dir):
                try:
                    content = self.read_file(rel_path)
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

    _TAG_NORMALIZE_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff-]")

    @classmethod
    def normalize_tag(cls, tag: str | int | float) -> str:
        """Normalize a tag: lowercase, replace spaces/underscores with hyphens,
        strip non-alphanumeric characters (preserving CJK)."""
        t = str(tag).lower().strip().replace(" ", "-").replace("_", "-")
        t = cls._TAG_NORMALIZE_RE.sub("", t)
        t = re.sub(r"-{2,}", "-", t).strip("-")
        return t

    def _normalize_tags_in_content(self, content: str) -> str:
        """Normalize tags in frontmatter before writing."""
        from noteweaver.frontmatter import extract_frontmatter, FRONTMATTER_PATTERN
        fm = extract_frontmatter(content)
        if not fm or not fm.get("tags"):
            return content
        tags = fm["tags"]
        if not isinstance(tags, list):
            return content
        normalized = [self.normalize_tag(t) for t in tags if t]
        normalized = list(dict.fromkeys(t for t in normalized if t))
        if normalized == tags:
            return content
        fm["tags"] = normalized
        import yaml as _yaml
        fm_str = _yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
        body = FRONTMATTER_PATTERN.sub("", content, count=1)
        return f"---\n{fm_str}\n---\n{body}"

    def resolve_title(self, title: str) -> str | None:
        """Resolve a page title to its path.

        Tries in order: frontmatter title → first ``# heading`` → filename stem.
        Returns None if no match is found.
        """
        title_lower = str(title).lower()
        heading_match = None
        filename_match = None
        for rel_path in self.list_files("wiki"):
            try:
                content = self.read_file(rel_path)
            except (FileNotFoundError, PermissionError):
                continue
            from noteweaver.frontmatter import extract_frontmatter
            fm = extract_frontmatter(content)
            if fm and str(fm.get("title", "")).lower() == title_lower:
                return rel_path
            if heading_match is None:
                for line in content.split("\n")[:10]:
                    if line.startswith("# ") and line[2:].strip().lower() == title_lower:
                        heading_match = rel_path
                        break
            if filename_match is None:
                stem = Path(rel_path).stem.replace("-", " ").replace("_", " ").lower()
                if stem == title_lower:
                    filename_match = rel_path
        return heading_match or filename_match

    def write_file(self, rel_path: str, content: str) -> None:
        """Write a file in the wiki area. Refuses to write into sources/.

        Enforces title uniqueness: if another wiki file already has the
        same frontmatter title, the write is rejected.
        """
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
        if rel_path.startswith("wiki/"):
            content = self._normalize_tags_in_content(content)
            self._check_title_unique(rel_path, content)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._index_file(rel_path, content)
        self.backlinks.update_page(rel_path, content)
        if self._operation_depth > 0:
            self._operation_dirty = True
        else:
            self._git_commit(f"Update {rel_path}")

    _title_check_skip: set[str] = set()

    def _check_title_unique(self, rel_path: str, content: str) -> None:
        """Raise if another file already uses the same title.

        Exempts archive paths, the file being overwritten, and paths
        in ``_title_check_skip`` (used during file moves).
        """
        if "/archive/" in rel_path:
            return
        from noteweaver.frontmatter import extract_frontmatter
        fm = extract_frontmatter(content)
        if not fm or not fm.get("title"):
            return
        title = fm["title"]
        existing = self.resolve_title(title)
        if (existing and existing != rel_path
                and "/archive/" not in existing
                and existing not in self._title_check_skip):
            raise PermissionError(
                f"Title '{title}' already used by {existing}. "
                f"Titles must be unique because [[wiki-links]] depend on them. "
                f"Use read_page('{existing}') to see the existing page, then "
                f"either update it with append_section / append_to_section, "
                f"or choose a different title for the new page."
            )

    def save_source(self, rel_path: str, content: str) -> None:
        """Write a file to sources/. Only creates new files, never overwrites."""
        if not rel_path.startswith("sources/"):
            raise PermissionError(f"save_source only writes to sources/. Path: {rel_path}")
        path = self._resolve(rel_path)
        if path.exists():
            raise PermissionError(f"Source already exists and is immutable: {rel_path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        self._index_file(rel_path, content)
        if self._operation_depth > 0:
            self._operation_dirty = True
        else:
            self._git_commit(f"Save source {rel_path}")

    _SKIP_DIRS = frozenset({".git", ".meta", ".DS_Store", "__pycache__", "node_modules"})
    _SKIP_FILES = frozenset({".DS_Store", "Thumbs.db", ".gitignore"})

    @staticmethod
    def _is_junk_path(rel_path: str) -> bool:
        """Return True if *rel_path* passes through a directory or filename
        that should never appear in file listings (nested .git/, .DS_Store,
        __pycache__, etc.).
        """
        parts = rel_path.replace("\\", "/").split("/")
        for part in parts[:-1]:
            if part in Vault._SKIP_DIRS:
                return True
        if parts[-1] in Vault._SKIP_FILES:
            return True
        return False

    def list_files(self, rel_dir: str = "wiki", pattern: str = "*.md") -> list[str]:
        """List files matching a glob pattern under a vault subdirectory."""
        base = self._resolve(rel_dir)
        if not base.is_dir():
            return []
        results = []
        for p in base.rglob(pattern):
            if not p.is_file():
                continue
            rel = str(p.relative_to(self.root))
            if self._is_junk_path(rel):
                continue
            results.append(rel)
        return sorted(results)

    def list_all_files(self, rel_dir: str = ".", pattern: str = "*") -> list[dict]:
        """List all files under a vault subdirectory with metadata.

        Returns dicts with path, size_bytes, and suffix for each file.
        Excludes .git/, .meta/, .DS_Store, and other non-content paths
        at any nesting depth.
        """
        base = self._resolve(rel_dir)
        if not base.is_dir():
            return []
        results = []
        for p in sorted(base.rglob(pattern)):
            if not p.is_file():
                continue
            rel = str(p.relative_to(self.root))
            if self._is_junk_path(rel):
                continue
            results.append({
                "path": rel,
                "size_bytes": p.stat().st_size,
                "suffix": p.suffix,
            })
        return results

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
        """Read metadata from all .md files in a directory. No body text.

        Files with YAML frontmatter get their declared metadata.
        Files without frontmatter get path-derived metadata so they
        are still visible — reads should never hide existing files.
        """
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
                        "updated": ps.updated,
                        "has_frontmatter": True,
                    })
                else:
                    first_heading = ""
                    for line in content.split("\n")[:10]:
                        if line.startswith("# "):
                            first_heading = line[2:].strip()
                            break
                    title = first_heading or Path(rel_path).stem.replace("-", " ").replace("_", " ")
                    results.append({
                        "path": rel_path,
                        "title": title,
                        "type": "",
                        "summary": "",
                        "tags": [],
                        "has_frontmatter": False,
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
        unstructured = []

        for rel_path in self.list_files("wiki"):
            if rel_path in ("wiki/index.md", "wiki/log.md"):
                continue
            if "/archive/" in rel_path:
                continue
            try:
                content = self.read_file(rel_path)
                ps = page_summary_from_file(rel_path, content)
                if ps is None:
                    unstructured.append(rel_path)
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

        if unstructured:
            lines.append("")
            lines.append(f"## Unstructured ({len(unstructured)} files)\n")
            for p in unstructured:
                lines.append(f"- `{p}`")

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
        no_frontmatter_count = 0
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
                if ps is None:
                    no_frontmatter_count += 1
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
        metrics = {
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
            "missing_frontmatter": no_frontmatter_count,
            "hub_coverage": (
                f"{len(hubs)} hubs for {total - len(hubs)} content pages"
            ),
            "total_links": link_stats["total_links"],
            "avg_links_per_page": round(link_stats["total_links"] / total, 1) if total else 0,
        }

        source_count = len(self.list_files("sources"))
        if source_count:
            metrics["source_files"] = source_count

        return metrics

    # ------------------------------------------------------------------
    # Vault audit
    # ------------------------------------------------------------------

    def audit_vault(self) -> dict:
        """Full vault health audit. Pure code, no LLM.

        Scans frontmatter and content to produce a structured findings
        report.  Each finding category is a list of dicts with enough
        detail for an LLM or CLI to act on.
        """
        from noteweaver.frontmatter import extract_frontmatter

        all_pages: list[dict] = []
        missing_frontmatter: list[str] = []
        for rel_path in self.list_files("wiki"):
            if rel_path in ("wiki/index.md", "wiki/log.md"):
                continue
            if "/archive/" in rel_path:
                continue
            try:
                content = self.read_file(rel_path)
                fm = extract_frontmatter(content)
                if fm is None:
                    missing_frontmatter.append(rel_path)
                    continue
                all_pages.append({
                    "path": rel_path,
                    "fm": fm,
                    "content": content,
                })
            except (FileNotFoundError, PermissionError):
                continue

        if not all_pages and not missing_frontmatter:
            return {"summary": "0 issues found (vault is empty)"}

        # Rebuild backlink index from current files to avoid stale data
        bl_pages = [{"path": p["path"], "content": p["content"]} for p in all_pages]
        self.backlinks.rebuild(bl_pages)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        stale_imports: list[dict] = []
        hub_candidates: list[dict] = []
        orphan_pages: list[str] = []
        missing_summaries: list[str] = []
        broken_links: list[dict] = []
        missing_connections: list[dict] = []
        similar_tags: list[dict] = []

        # Pre-compute lookups
        titles_to_path: dict[str, str] = {}
        hubs: set[str] = set()
        hub_tags: set[str] = set()
        tag_pages: dict[str, list[str]] = {}

        for p in all_pages:
            fm = p["fm"]
            title = fm.get("title", "")
            ptype = fm.get("type", "")
            path = p["path"]
            if title:
                titles_to_path[title] = path
            if ptype == "hub":
                hubs.add(title)
                for t in (fm.get("tags") or []):
                    hub_tags.add(str(t).lower())
            for t in (fm.get("tags") or []):
                tag_pages.setdefault(str(t), []).append(path)

        # 1. Stale imports: tagged [imported] with updated > 7 days ago
        for p in all_pages:
            fm = p["fm"]
            tags = fm.get("tags") or []
            if "imported" not in tags:
                continue
            updated = str(fm.get("updated", ""))
            days = self._days_since(updated, today)
            if days is not None and days > 7:
                stale_imports.append({
                    "path": p["path"],
                    "days_since_update": days,
                })

        # 2. Hub candidates: tags with 3+ pages and no matching hub
        for tag, pages in tag_pages.items():
            if tag in ("imported", "journal", "pinned"):
                continue
            if len(pages) >= 3 and str(tag).lower() not in hub_tags:
                hub_candidates.append({
                    "tag": tag,
                    "page_count": len(pages),
                    "pages": pages[:5],
                })

        # 3. Orphan pages: no inbound links, not hub/journal
        for p in all_pages:
            fm = p["fm"]
            title = fm.get("title", "")
            ptype = fm.get("type", "")
            if ptype in ("hub", "journal"):
                continue
            if title and self.backlinks.reference_count(title) == 0:
                orphan_pages.append(p["path"])

        # 4. Missing summaries
        for p in all_pages:
            fm = p["fm"]
            summary = fm.get("summary", "")
            if not summary or summary.startswith("Imported from"):
                missing_summaries.append(p["path"])

        # 5. Broken links: [[Title]] pointing to non-existent pages
        from noteweaver.backlinks import WIKILINK_PATTERN
        for p in all_pages:
            links = WIKILINK_PATTERN.findall(p["content"])
            for link_title in set(links):
                if link_title not in titles_to_path:
                    broken_links.append({
                        "page": p["path"],
                        "link_title": link_title,
                    })

        # 6. Missing connections: pages sharing 2+ tags but no mutual link
        paths_by_tag: dict[str, set[str]] = {}
        for p in all_pages:
            fm = p["fm"]
            for t in (fm.get("tags") or []):
                paths_by_tag.setdefault(t, set()).add(p["path"])
        checked_pairs: set[tuple[str, str]] = set()
        for tag, paths in paths_by_tag.items():
            if len(paths) > 20:
                continue
            path_list = sorted(paths)
            for i, pa in enumerate(path_list):
                for pb in path_list[i + 1:]:
                    pair = (pa, pb)
                    if pair in checked_pairs:
                        continue
                    checked_pairs.add(pair)
                    shared = [
                        t for t in (tag_pages.keys())
                        if pa in tag_pages.get(t, []) and pb in tag_pages.get(t, [])
                    ]
                    if len(shared) < 2:
                        continue
                    outlinks_a = set(self.backlinks.outlinks_for(pa))
                    outlinks_b = set(self.backlinks.outlinks_for(pb))
                    title_a = next(
                        (p["fm"].get("title", "") for p in all_pages if p["path"] == pa), ""
                    )
                    title_b = next(
                        (p["fm"].get("title", "") for p in all_pages if p["path"] == pb), ""
                    )
                    if title_b not in outlinks_a and title_a not in outlinks_b:
                        missing_connections.append({
                            "page_a": pa,
                            "page_b": pb,
                            "shared_tags": shared[:5],
                        })

        # 7. Similar tags: potential duplicates
        all_tags = sorted(tag_pages.keys())
        checked_tag_pairs: set[tuple[str, str]] = set()
        for i, ta in enumerate(all_tags):
            for tb in all_tags[i + 1:]:
                pair = (ta, tb)
                if pair in checked_tag_pairs:
                    continue
                checked_tag_pairs.add(pair)
                reason = self._similar_tag_reason(ta, tb)
                if reason:
                    similar_tags.append({
                        "tag_a": ta, "tag_b": tb, "reason": reason,
                    })

        # Build summary line
        counts = []
        if missing_frontmatter:
            counts.append(f"{len(missing_frontmatter)} missing frontmatter")
        if stale_imports:
            counts.append(f"{len(stale_imports)} stale import(s)")
        if hub_candidates:
            counts.append(f"{len(hub_candidates)} hub candidate(s)")
        if orphan_pages:
            counts.append(f"{len(orphan_pages)} orphan page(s)")
        if missing_summaries:
            counts.append(f"{len(missing_summaries)} missing summary(ies)")
        if broken_links:
            counts.append(f"{len(broken_links)} broken link(s)")
        if missing_connections:
            counts.append(f"{len(missing_connections)} missing connection(s)")
        if similar_tags:
            counts.append(f"{len(similar_tags)} similar tag pair(s)")

        total = sum([
            len(missing_frontmatter), len(stale_imports), len(hub_candidates),
            len(orphan_pages), len(missing_summaries), len(broken_links),
            len(missing_connections), len(similar_tags),
        ])
        summary = (
            f"{total} issue(s) found: {', '.join(counts)}"
            if counts else "0 issues found"
        )

        return {
            "missing_frontmatter": missing_frontmatter,
            "stale_imports": stale_imports,
            "hub_candidates": hub_candidates,
            "orphan_pages": orphan_pages,
            "missing_summaries": missing_summaries,
            "broken_links": broken_links,
            "missing_connections": missing_connections,
            "similar_tags": similar_tags,
            "summary": summary,
        }

    @staticmethod
    def _similar_tag_reason(ta: str, tb: str) -> str | None:
        """Return the reason two tags are similar, or None if they are not.

        Checks (in order):
        1. Hyphen-insensitive: same after stripping hyphens (machine-learning / machinelearning)
        2. Plural: one is the plural of the other (model / models)
        3. Substring: one tag contained in the other (react / react-native)
        4. Edit distance ≤ 2 for tags longer than 3 chars (react / reactjs)
        """
        if ta == tb:
            return None

        ta_nohyp = ta.replace("-", "")
        tb_nohyp = tb.replace("-", "")
        if ta_nohyp == tb_nohyp:
            return "hyphen variant"

        if Vault._is_plural_pair(ta, tb):
            return "plural"

        if ta in tb or tb in ta:
            if len(ta) >= 2 and len(tb) >= 2:
                return "substring"

        if len(ta) > 3 and len(tb) > 3:
            dist = Vault._edit_distance(ta, tb)
            if dist <= 2:
                return f"edit distance {dist}"

        return None

    @staticmethod
    def _is_plural_pair(a: str, b: str) -> bool:
        """Check if one tag is a simple English plural of the other."""
        if a == b:
            return False
        short, long = (a, b) if len(a) <= len(b) else (b, a)
        if long == short + "s":
            return True
        if long == short + "es":
            return True
        if short.endswith("y") and long == short[:-1] + "ies":
            return True
        return False

    @staticmethod
    def _edit_distance(a: str, b: str) -> int:
        """Levenshtein distance between two strings."""
        if len(a) < len(b):
            return Vault._edit_distance(b, a)
        if not b:
            return len(a)
        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a):
            curr = [i + 1]
            for j, cb in enumerate(b):
                curr.append(min(
                    prev[j + 1] + 1,
                    curr[j] + 1,
                    prev[j] + (0 if ca == cb else 1),
                ))
            prev = curr
        return prev[-1]

    def save_audit_report(self, report: dict) -> Path:
        """Persist an audit report to ``.meta/audit-report.json``."""
        import json as _json
        path = self.meta_dir / "audit-report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _days_since(date_str: str, today_str: str) -> int | None:
        """Return days between *date_str* and *today_str* (YYYY-MM-DD)."""
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            t = datetime.strptime(today_str, "%Y-%m-%d")
            return (t - d).days
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------
    # Vault context (shared by scan_imports and session organize)
    # ------------------------------------------------------------------

    _UNORGANIZED_DISPLAY_LIMIT = 10

    def scan_vault_context(self) -> str:
        """Build a fixed-structure world summary for the LLM system prompt.

        Returns a consistent structural overview regardless of vault size.
        The summary shows hub-level aggregates and key signals; the agent
        uses ``list_pages`` to drill into any hub or directory for page cards,
        and ``read_page`` for full content.

        Sections:
        - Hubs with page counts (no child page listings)
        - Unorganized pages not under any hub (capped list)
        - Orphan pages (no inbound links)
        - Health signals (missing summaries, orphan rate)
        - Tags overview
        - Journal date range
        - Sources overview
        - Total page count
        """
        _SKIP_PATHS = {"wiki/index.md", "wiki/log.md"}
        all_summaries = self.read_frontmatters("wiki")
        existing_tags: set[str] = set()
        hubs: list[dict] = []
        content_pages: list[dict] = []
        journals: list[dict] = []
        unorganized: list[dict] = []
        total = 0
        no_summary_count = 0

        hub_tag_set: set[str] = set()

        for ps in all_summaries:
            if ps.get("path") in _SKIP_PATHS:
                continue
            ptype = ps.get("type", "")
            if ptype == "archive":
                continue
            for t in (ps.get("tags") or []):
                if t != "imported":
                    existing_tags.add(t)
            if ptype == "journal":
                journals.append(ps)
                continue
            total += 1
            if not ps.get("summary"):
                no_summary_count += 1
            if ptype == "hub":
                hubs.append(ps)
                for t in (ps.get("tags") or []):
                    hub_tag_set.add(t)
            else:
                content_pages.append(ps)

        # Build hub→children mapping
        hub_children: dict[str, list[dict]] = {h["path"]: [] for h in hubs}
        for ps in content_pages:
            page_tags = set(ps.get("tags") or [])
            matched = False
            for hub in hubs:
                hub_tags = set(hub.get("tags") or [])
                if page_tags & hub_tags:
                    hub_children[hub["path"]].append(ps)
                    matched = True
                    break
            if not matched:
                unorganized.append(ps)

        # Detect orphans via backlink index
        orphan_pages: list[dict] = []
        for ps in content_pages:
            title = ps.get("title", "")
            if title and ps.get("type") not in ("hub", "journal"):
                if self.backlinks.reference_count(title) == 0:
                    orphan_pages.append(ps)

        lines: list[str] = []

        # -- Hubs (aggregates only) --
        if hubs:
            lines.append("Hubs:")
            for hub in hubs:
                children = hub_children[hub["path"]]
                summary_part = f" — {hub['summary']}" if hub.get("summary") else ""
                lines.append(
                    f"  {hub['title']} ({len(children)} pages) → {hub['path']}{summary_part}"
                )

        # -- Unorganized pages (not under any hub) --
        if unorganized:
            lines.append(
                f"\nUnorganized ({len(unorganized)} page(s) not under any hub):"
            )
            shown = unorganized[:self._UNORGANIZED_DISPLAY_LIMIT]
            for ps in shown:
                lines.append(
                    f"  [{ps.get('type', '?')}] {ps['title']} → {ps['path']}"
                )
            remaining = len(unorganized) - len(shown)
            if remaining > 0:
                lines.append(f"  … and {remaining} more")

        # -- Orphan pages --
        if orphan_pages:
            lines.append(
                f"\nOrphan pages ({len(orphan_pages)} — no inbound links):"
            )
            shown = orphan_pages[:self._UNORGANIZED_DISPLAY_LIMIT]
            for ps in shown:
                lines.append(
                    f"  [{ps.get('type', '?')}] {ps['title']} → {ps['path']}"
                )
            remaining = len(orphan_pages) - len(shown)
            if remaining > 0:
                lines.append(f"  … and {remaining} more")

        # -- Tags --
        if existing_tags:
            sorted_tags = sorted(existing_tags)
            if len(sorted_tags) > 20:
                lines.append(f"\nTags ({len(sorted_tags)}): {', '.join(sorted_tags[:20])}, …")
            else:
                lines.append(f"\nTags: {', '.join(sorted_tags)}")

        # -- Journals --
        if journals:
            dates = sorted(
                ps.get("path", "") for ps in journals
            )
            lines.append(f"\nJournals: {len(journals)} entries")
            if len(dates) >= 2:
                first_stem = Path(dates[0]).stem
                last_stem = Path(dates[-1]).stem
                lines.append(f"  range: {first_stem} → {last_stem}")
            elif dates:
                lines.append(f"  latest: {Path(dates[0]).stem}")

        # -- Summary line --
        lines.append(f"\nTotal: {total} pages")

        # -- Sources --
        source_files = self.list_files("sources")
        if source_files:
            by_subdir: dict[str, list[str]] = {}
            for sf in source_files:
                parts = sf.split("/")
                subdir = parts[1] if len(parts) > 2 else "(root)"
                by_subdir.setdefault(subdir, []).append(sf)
            lines.append(f"\nSources: {len(source_files)} file(s)")
            for sd, files in sorted(by_subdir.items()):
                prefix = f"sources/{sd}" if sd != "(root)" else "sources/"
                samples = [Path(f).name for f in files[:3]]
                sample_str = ", ".join(samples)
                more = f", …" if len(files) > 3 else ""
                lines.append(f"  {prefix}: {len(files)} file(s) [{sample_str}{more}]")

        # -- Health signals --
        health_signals = []
        if no_summary_count:
            health_signals.append(f"{no_summary_count} pages missing summary")
        if orphan_pages:
            health_signals.append(f"{len(orphan_pages)} orphan pages")
        if unorganized:
            health_signals.append(f"{len(unorganized)} pages not under any hub")
        if health_signals:
            lines.append(f"\nHealth: {', '.join(health_signals)}")

        # -- Footer hint --
        lines.append("")
        lines.append(
            "Use list_pages to see page cards for any hub or directory, "
            "read_page on a hub to see its child pages, "
            "or search for keyword lookup."
        )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Organize imports: scan + apply
    # ------------------------------------------------------------------

    _TOTAL_SCAN_BUDGET = 80_000   # chars across all files (~24k tokens)
    _PER_FILE_MIN = 800
    _PER_FILE_MAX = 5_000

    def scan_imports(self) -> str:
        """Scan files needing organization and vault context for LLM-driven planning.

        Finds both files tagged ``[imported]`` and files without any
        frontmatter (which are even more in need of organization).
        Reads each within an adaptive character budget and assembles
        a structured summary for the LLM.
        """
        from noteweaver.frontmatter import extract_frontmatter, page_summary_from_file

        imported_pages: list[dict] = []
        for rel_path in self.list_files("wiki"):
            try:
                content = self.read_file(rel_path)
            except (FileNotFoundError, PermissionError):
                continue
            fm = extract_frontmatter(content)
            if fm:
                tags = fm.get("tags") or []
                if "imported" not in tags:
                    continue
            else:
                fm = {}
            imported_pages.append({
                "path": rel_path,
                "content": content,
                "fm": fm,
            })

        if not imported_pages:
            return "No files needing organization found. Nothing to organize."

        n = len(imported_pages)
        per_file = max(
            self._PER_FILE_MIN,
            min(self._PER_FILE_MAX, self._TOTAL_SCAN_BUDGET // n),
        )

        # Build per-file digest
        file_sections: list[str] = []
        for i, page in enumerate(imported_pages, 1):
            digest = self._build_file_digest(
                page["path"], page["content"], page["fm"], per_file,
            )
            file_sections.append(f"### File {i}: {page['path']}\n{digest}")

        vault_ctx = self.scan_vault_context()

        output_parts = [
            f"## Imported files to organize: {n}\n",
            f"Per-file character budget: {per_file}\n",
            "## Vault context\n",
            vault_ctx,
            "\n## File details\n",
            "\n\n".join(file_sections),
            "\n## Instructions\n",
            (
                "For EACH file above, output a JSON array. Each element:\n"
                "```json\n"
                "{\n"
                '  "path": "wiki/concepts/example.md",\n'
                '  "type": "note|canonical|journal|synthesis|hub",\n'
                '  "title": "Corrected Title",\n'
                '  "summary": "One-sentence summary of the page",\n'
                '  "tags": ["tag-a", "tag-b"],\n'
                '  "move_to": "wiki/journals/example.md or null if no move needed",\n'
                '  "related": ["Existing Page Title", "Another Page"],\n'
                '  "hub": "Existing or suggested hub name, or null",\n'
                '  "duplicate_of": "path of existing page if duplicate, else null",\n'
                '  "confidence": "high|low"\n'
                "}\n"
                "```\n"
                "Rules:\n"
                "- Use existing tags when possible; create new ones sparingly.\n"
                "- Set confidence=low for items you're unsure about.\n"
                "- Set duplicate_of only when content genuinely overlaps an existing page.\n"
                "- Suggest hub when 3+ pages (including existing) share a topic.\n"
                "- Respond ONLY with the JSON array. No extra text."
            ),
        ]
        return "\n".join(output_parts)

    @staticmethod
    def _build_file_digest(
        rel_path: str, content: str, fm: dict, budget: int,
    ) -> str:
        """Build a structured digest of a file within a character budget.

        Priority: full frontmatter > heading outline > opening body text.
        """
        import re as _re

        parts: list[str] = []
        used = 0

        # 1. Frontmatter block (always include in full)
        fm_match = _re.match(r"^---\s*\n.*?\n---\s*\n", content, _re.DOTALL)
        if fm_match:
            fm_text = fm_match.group(0)
            parts.append(fm_text.strip())
            used += len(fm_text)

        # 2. Heading outline
        headings = [
            line for line in content.split("\n")
            if _re.match(r"^#{1,4}\s", line)
        ]
        if headings:
            outline = "Headings: " + " | ".join(h.strip() for h in headings)
            if used + len(outline) < budget:
                parts.append(outline)
                used += len(outline)

        # 3. File length metadata
        meta = f"Total length: {len(content)} chars"
        parts.append(meta)
        used += len(meta)

        # 4. Fill remaining budget with body text from the start
        body_start = fm_match.end() if fm_match else 0
        remaining = budget - used
        if remaining > 50:
            body_slice = content[body_start:body_start + remaining].strip()
            if body_slice:
                parts.append(f"Content preview:\n{body_slice}")

        return "\n".join(parts)

    def apply_organize_plan(self, plan_json: str) -> str:
        """Apply an LLM-generated organization plan to imported files.

        Expects a JSON array of file plans. Performs: type/tag/summary
        updates, file moves, related-link insertion, and hub creation.
        Returns a structured report.
        """
        import json as _json
        from noteweaver.frontmatter import extract_frontmatter

        try:
            plan = _json.loads(plan_json)
        except _json.JSONDecodeError as e:
            return f"Error: invalid JSON — {e}"

        if not isinstance(plan, list):
            return "Error: expected a JSON array of file plans."

        results: list[str] = []
        processed = 0
        moved = 0
        links_added = 0
        needs_review: list[str] = []
        hubs_to_create: dict[str, list[str]] = {}
        hubs_created: list[str] = []

        with self.operation("Organize imported files"):
            for item in plan:
                path = item.get("path", "")
                if not path:
                    continue

                try:
                    content = self.read_file(path)
                except FileNotFoundError:
                    results.append(f"  ⚠ {path}: not found, skipped")
                    continue

                fm = extract_frontmatter(content)
                if not fm:
                    results.append(f"  ⚠ {path}: no frontmatter, skipped")
                    continue

                confidence = item.get("confidence", "high")
                duplicate_of = item.get("duplicate_of")

                if confidence == "low" or duplicate_of:
                    reason = f"duplicate_of={duplicate_of}" if duplicate_of else "low confidence"
                    needs_review.append(f"  - {path}: {reason}")
                    # Still apply safe metadata updates for low-confidence items
                    if confidence == "low" and not duplicate_of:
                        pass  # fall through to metadata update
                    else:
                        continue

                # Update frontmatter fields
                fm_updates: dict = {}
                if item.get("type") and item["type"] != fm.get("type"):
                    fm_updates["type"] = item["type"]
                if item.get("title") and item["title"] != fm.get("title"):
                    fm_updates["title"] = item["title"]
                if item.get("summary"):
                    fm_updates["summary"] = item["summary"]
                if item.get("tags"):
                    new_tags = [t for t in item["tags"] if t != "imported"]
                    fm_updates["tags"] = new_tags

                # Remove 'imported' tag even if no other tag changes
                if not fm_updates.get("tags"):
                    old_tags = fm.get("tags") or []
                    if "imported" in old_tags:
                        fm_updates["tags"] = [t for t in old_tags if t != "imported"]

                if fm_updates:
                    fm.update(fm_updates)
                    import yaml as _yaml
                    from noteweaver.frontmatter import FRONTMATTER_PATTERN
                    fm_str = _yaml.dump(
                        fm, default_flow_style=False, allow_unicode=True,
                    ).strip()
                    body = FRONTMATTER_PATTERN.sub("", content, count=1)
                    content = f"---\n{fm_str}\n---\n{body}"
                    self.write_file(path, content)

                # Move file if needed
                actual_path = path
                move_to = item.get("move_to")
                if move_to and move_to != path:
                    try:
                        self._title_check_skip.add(path)
                        self.write_file(move_to, content)
                        self._title_check_skip.discard(path)
                        original = self._resolve(path)
                        if original.is_file():
                            original.unlink()
                        self.search.remove(path)
                        self.backlinks.remove_page(path)
                        actual_path = move_to
                        moved += 1
                    except Exception as e:
                        self._title_check_skip.discard(path)
                        results.append(f"  ⚠ move {path} → {move_to} failed: {e}")

                # Add related links
                for related_title in (item.get("related") or []):
                    try:
                        existing = self.read_file(actual_path)
                        link = f"[[{related_title}]]"
                        if link not in existing:
                            related_pattern = re.compile(
                                r"(## Related\b.*)", re.IGNORECASE | re.DOTALL,
                            )
                            match = related_pattern.search(existing)
                            if match:
                                section = match.group(1)
                                new_section = section.rstrip() + f"\n- {link}\n"
                                new_content = existing[:match.start()] + new_section
                            else:
                                new_content = existing.rstrip() + f"\n\n## Related\n\n- {link}\n"
                            self.write_file(actual_path, new_content)
                            links_added += 1
                    except Exception:
                        pass

                # Collect hub assignments
                hub_name = item.get("hub")
                if hub_name:
                    title = item.get("title") or fm.get("title") or ""
                    if title:
                        hubs_to_create.setdefault(hub_name, []).append(title)

                processed += 1
                results.append(f"  ✓ {path}" + (f" → {move_to}" if move_to and move_to != path else ""))

            # Create/update hubs
            for hub_name, page_titles in hubs_to_create.items():
                hub_slug = str(hub_name).lower().replace(" ", "-")
                hub_slug = re.sub(r"[^a-z0-9-]", "", hub_slug)
                hub_slug = re.sub(r"-{2,}", "-", hub_slug).strip("-")[:60]
                hub_path = f"wiki/concepts/{hub_slug}.md"

                try:
                    existing_hub = self.read_file(hub_path)
                    for pt in page_titles:
                        link = f"[[{pt}]]"
                        if link not in existing_hub:
                            existing_hub = existing_hub.rstrip() + f"\n- {link}\n"
                    self.write_file(hub_path, existing_hub)
                    results.append(f"  ✓ Updated hub: {hub_path} (+{len(page_titles)} links)")
                except FileNotFoundError:
                    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                    links_block = "\n".join(f"- [[{pt}]]" for pt in page_titles)
                    hub_content = (
                        f"---\ntitle: {hub_name}\ntype: hub\n"
                        f"summary: Hub for {hub_name} topics\n"
                        f"tags: [{hub_slug}]\n"
                        f"created: {today}\nupdated: {today}\n---\n\n"
                        f"# {hub_name}\n\n"
                        f"Overview page for {hub_name} related content.\n\n"
                        f"## Pages\n\n{links_block}\n\n"
                        f"## Related\n"
                    )
                    self.write_file(hub_path, hub_content)
                    hubs_created.append(hub_name)
                    results.append(f"  ✓ Created hub: {hub_path} ({len(page_titles)} pages)")

            self.rebuild_index()
            self.append_log(
                "organize",
                f"Organized {processed} imported files",
                f"Moved: {moved}, Links added: {links_added}, "
                f"Hubs created: {len(hubs_created)}",
            )

        # Build report
        report_lines = [
            f"Organized {processed}/{len(plan)} files:\n",
            "\n".join(results),
        ]
        if hubs_created:
            report_lines.append(f"\nNew hubs: {', '.join(hubs_created)}")
        if needs_review:
            report_lines.append(f"\n⚠ Needs review ({len(needs_review)} files):")
            report_lines.append("\n".join(needs_review))
        report_lines.append(
            f"\nSummary: {processed} processed, {moved} moved, "
            f"{links_added} links added, {len(hubs_created)} hubs created"
        )
        return "\n".join(report_lines)

    def import_directory(self, source_dir: str) -> str:
        """Import .md files from an external directory into the vault.

        Accepts both absolute paths (/home/user/notes) and vault-relative
        paths (sources/typora).  Uses an operation context so all writes
        produce a single git commit.
        """
        from noteweaver.frontmatter import extract_frontmatter

        candidate = Path(source_dir)
        if not candidate.is_absolute():
            candidate = self.root / source_dir
        src = candidate.resolve()
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
