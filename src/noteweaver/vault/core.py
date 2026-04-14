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
import re
from datetime import datetime, timezone
from pathlib import Path

from noteweaver.constants import (
    WIKI_DIRS,
    STRUCTURE_PATHS,
    SKIP_DIRS,
    SKIP_FILES,
)

from noteweaver.vault.seeds import (
    INITIAL_SCHEMA,
    INITIAL_PROTOCOLS,
    INITIAL_PREFERENCES,
    INITIAL_INDEX,
    INITIAL_LOG,
)
from noteweaver.vault import git as _git
from noteweaver.vault import indexing as _indexing
from noteweaver.vault import audit as _audit
from noteweaver.vault import context as _context
from noteweaver.vault import organize as _organize

log = logging.getLogger(__name__)


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

        self._seed_default_skills()

        self._write_if_missing(
            self.root / ".gitignore",
            ".meta/\n",
        )

        self._git_init()
        self._git_commit("Vault initialized")
        self.rebuild_search_index()

    def _seed_default_skills(self) -> None:
        """Copy bundled SKILL.md files into .schema/skills/ if missing."""
        import importlib.resources
        bundled_root = importlib.resources.files("noteweaver") / ".schema_default" / "skills"
        skills_dir = self.schema_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        try:
            for entry in bundled_root.iterdir():
                if not entry.is_dir():
                    continue
                skill_md = entry / "SKILL.md"
                if not skill_md.is_file():
                    continue
                target_dir = skills_dir / entry.name
                target_dir.mkdir(parents=True, exist_ok=True)
                target_file = target_dir / "SKILL.md"
                if not target_file.exists():
                    target_file.write_text(
                        skill_md.read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )
        except (TypeError, FileNotFoundError):
            pass

    def load_skills(self) -> list[dict]:
        """Scan .schema/skills/ for SKILL.md files and return metadata."""
        import yaml as _yaml

        skills_dir = self.schema_dir / "skills"
        if not skills_dir.is_dir():
            return []

        results = []
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_file = entry / "SKILL.md"
            if not skill_file.is_file():
                continue
            content = skill_file.read_text(encoding="utf-8")
            if not content.startswith("---"):
                continue
            end = content.find("---", 3)
            if end == -1:
                continue
            try:
                fm = _yaml.safe_load(content[3:end])
            except Exception:
                continue
            if not isinstance(fm, dict):
                continue
            name = fm.get("name", entry.name)
            description = fm.get("description", "")
            if not description:
                continue
            rel_path = f".schema/skills/{entry.name}/SKILL.md"
            results.append({
                "name": name,
                "description": description,
                "location": rel_path,
            })
        return results

    # ------------------------------------------------------------------
    # Search index (lazy properties)
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

    # ------------------------------------------------------------------
    # Delegated: indexing
    # ------------------------------------------------------------------

    def _index_file(self, rel_path: str, content: str) -> None:
        _indexing.index_file(self, rel_path, content)

    def rebuild_search_index(self) -> int:
        return _indexing.rebuild_search_index(self)

    def rebuild_backlinks(self) -> int:
        return _indexing.rebuild_backlinks(self)

    def search_content(self, query: str, directory: str = "wiki") -> list[dict]:
        return _indexing.search_content(self, query, directory)

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    def read_file(self, rel_path: str) -> str:
        """Read a file from the vault. Path is relative to vault root."""
        path = self._resolve(rel_path)
        return path.read_text(encoding="utf-8")

    _SKIP_UPDATED = STRUCTURE_PATHS
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
        """Resolve a page title to its path."""
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
        """Raise if another file already uses the same title."""
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

    _SKIP_DIRS = SKIP_DIRS
    _SKIP_FILES = SKIP_FILES

    @staticmethod
    def _is_junk_path(rel_path: str) -> bool:
        """Return True if *rel_path* passes through a junk directory or filename."""
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
        """List all files under a vault subdirectory with metadata."""
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

    def read_file_partial(self, rel_path: str, max_chars: int) -> str:
        """Read the first max_chars characters of a file."""
        path = self._resolve(rel_path)
        with open(path, encoding="utf-8") as f:
            return f.read(max_chars)

    def read_frontmatters(self, rel_dir: str = "wiki") -> list[dict]:
        """Read metadata from all .md files in a directory. No body text."""
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

    # ------------------------------------------------------------------
    # Delegated: audit & metrics
    # ------------------------------------------------------------------

    def stats(self) -> dict:
        return _audit.stats(self)

    def health_metrics(self) -> dict:
        return _audit.health_metrics(self)

    def audit_vault(self) -> dict:
        return _audit.audit_vault(self)

    def save_audit_report(self, report: dict) -> Path:
        return _audit.save_audit_report(self, report)

    @staticmethod
    def _similar_tag_reason(ta: str, tb: str) -> str | None:
        return _audit.similar_tag_reason(ta, tb)

    @staticmethod
    def _is_plural_pair(a: str, b: str) -> bool:
        return _audit._is_plural_pair(a, b)

    @staticmethod
    def _edit_distance(a: str, b: str) -> int:
        return _audit._edit_distance(a, b)

    @staticmethod
    def _days_since(date_str: str, today_str: str) -> int | None:
        return _audit._days_since(date_str, today_str)

    # ------------------------------------------------------------------
    # Delegated: LLM context
    # ------------------------------------------------------------------

    _UNORGANIZED_DISPLAY_LIMIT = _context._UNORGANIZED_DISPLAY_LIMIT
    _TOTAL_SCAN_BUDGET = _context._TOTAL_SCAN_BUDGET
    _PER_FILE_MIN = _context._PER_FILE_MIN
    _PER_FILE_MAX = _context._PER_FILE_MAX

    def scan_vault_context(self) -> str:
        return _context.scan_vault_context(self)

    def scan_imports(self) -> str:
        return _context.scan_imports(self)

    @staticmethod
    def _build_file_digest(rel_path: str, content: str, fm: dict, budget: int) -> str:
        return _context.build_file_digest(rel_path, content, fm, budget)

    # ------------------------------------------------------------------
    # Delegated: organize & import
    # ------------------------------------------------------------------

    def rebuild_index(self) -> str:
        return _organize.rebuild_index(self)

    def apply_organize_plan(self, plan_json: str) -> str:
        return _organize.apply_organize_plan(self, plan_json)

    def import_directory(self, source_dir: str) -> str:
        return _organize.import_directory(self, source_dir)

    # ------------------------------------------------------------------
    # Operation log
    # ------------------------------------------------------------------

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
        return _git.OperationContext(self, message)

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
    # Git integration (delegated)
    # ------------------------------------------------------------------

    def _git_init(self) -> None:
        _git.git_init(self)

    def _git_commit(self, message: str) -> None:
        _git.git_commit(self, message)

    @staticmethod
    def _write_if_missing(path: Path, content: str) -> None:
        if not path.exists():
            path.write_text(content, encoding="utf-8")
