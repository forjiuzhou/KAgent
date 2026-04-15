"""Tests for the organize-imports pipeline and new organize/ingest tools."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from noteweaver.vault import Vault
from noteweaver.tools.definitions import (
    TOOL_SCHEMAS,
    TOOL_HANDLERS,
    dispatch_tool,
)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


def _write_imported_page(vault: Vault, name: str, content: str) -> str:
    """Helper: write a page tagged [imported] into wiki/concepts/."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not content.startswith("---"):
        title = name.replace(".md", "").replace("-", " ").title()
        content = (
            f"---\ntitle: {title}\ntype: note\n"
            f"summary: Imported from {name}\n"
            f"tags: [imported]\ncreated: {today}\nupdated: {today}\n---\n\n"
            + content
        )
    path = f"wiki/concepts/{name}"
    vault.write_file(path, content)
    return path


# ======================================================================
# Tool registration (new tool system)
# ======================================================================

class TestToolRegistration:
    def test_organize_in_schemas(self) -> None:
        schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        assert "organize" not in schema_names
        assert "organize" in TOOL_HANDLERS
        assert len(schema_names) == 12

    def test_ingest_in_schemas(self) -> None:
        schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        assert "ingest" not in schema_names
        assert "ingest" in TOOL_HANDLERS

    def test_organize_in_handlers(self) -> None:
        assert "organize" in TOOL_HANDLERS

    def test_ingest_in_handlers(self) -> None:
        assert "ingest" in TOOL_HANDLERS

    def test_legacy_import_tools_removed(self) -> None:
        names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        assert "scan_imports" not in names
        assert "apply_organize_plan" not in names


# ======================================================================
# organize(target='imported', action='classify') → vault.scan_imports()
# ======================================================================

class TestScanImports:
    def test_no_imported_files(self, vault: Vault) -> None:
        result = vault.scan_imports()
        assert "Nothing to organize" in result

    def test_finds_imported_files(self, vault: Vault) -> None:
        _write_imported_page(vault, "react-hooks.md", "# React Hooks\n\nContent about hooks.")
        _write_imported_page(vault, "python-basics.md", "# Python Basics\n\nIntro to Python.")

        result = vault.scan_imports()
        assert "react-hooks.md" in result
        assert "python-basics.md" in result
        assert "Imported files to organize: 2" in result

    def test_includes_vault_context(self, vault: Vault) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        vault.write_file(
            "wiki/concepts/existing.md",
            f"---\ntitle: Existing Page\ntype: note\nsummary: Pre-existing\n"
            f"tags: [javascript, web]\ncreated: {today}\nupdated: {today}\n---\n\n# Existing\n",
        )
        _write_imported_page(vault, "new-page.md", "# New stuff")

        result = vault.scan_imports()
        assert "javascript" in result
        assert "Existing Page" in result

    def test_skips_non_imported(self, vault: Vault) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        vault.write_file(
            "wiki/concepts/regular.md",
            f"---\ntitle: Regular\ntype: note\nsummary: Not imported\n"
            f"tags: [misc]\ncreated: {today}\nupdated: {today}\n---\n\nContent\n",
        )
        result = vault.scan_imports()
        assert "Nothing to organize" in result

    def test_includes_headings_in_digest(self, vault: Vault) -> None:
        content = (
            "# Main Title\n\n"
            "Some intro text.\n\n"
            "## Section One\n\nDetails.\n\n"
            "## Section Two\n\nMore details.\n"
        )
        _write_imported_page(vault, "structured.md", content)
        result = vault.scan_imports()
        assert "Section One" in result
        assert "Section Two" in result

    def test_includes_json_instructions(self, vault: Vault) -> None:
        _write_imported_page(vault, "test.md", "# Test")
        result = vault.scan_imports()
        assert '"type"' in result
        assert '"confidence"' in result
        assert "JSON array" in result

    def test_dispatch_organize_classify_imported(self, vault: Vault) -> None:
        _write_imported_page(vault, "test.md", "# Test")
        result = dispatch_tool(vault, "organize", {
            "target": "imported",
            "action": "classify",
        })
        assert "test.md" in result

    def test_adaptive_budget(self, vault: Vault) -> None:
        for i in range(10):
            _write_imported_page(vault, f"page-{i}.md", f"# Page {i}\n\nContent for page {i}.")
        result = vault.scan_imports()
        assert "Imported files to organize: 10" in result
        for i in range(10):
            assert f"page-{i}.md" in result


# ======================================================================
# apply_organize_plan (vault only)
# ======================================================================

class TestApplyOrganizePlan:
    def test_invalid_json(self, vault: Vault) -> None:
        result = vault.apply_organize_plan("not json")
        assert "Error" in result

    def test_not_array(self, vault: Vault) -> None:
        result = vault.apply_organize_plan('{"path": "x"}')
        assert "Error" in result

    def test_basic_metadata_update(self, vault: Vault) -> None:
        path = _write_imported_page(vault, "hooks.md", "# React Hooks\n\nContent.")
        plan = json.dumps([{
            "path": path,
            "type": "canonical",
            "title": "React Hooks Guide",
            "summary": "Comprehensive guide to React Hooks",
            "tags": ["react", "javascript"],
            "move_to": None,
            "related": [],
            "hub": None,
            "duplicate_of": None,
            "confidence": "high",
        }])
        result = vault.apply_organize_plan(plan)
        assert "✓" in result
        assert "1 processed" in result

        from noteweaver.frontmatter import extract_frontmatter
        content = vault.read_file(path)
        fm = extract_frontmatter(content)
        assert fm["type"] == "canonical"
        assert fm["title"] == "React Hooks Guide"
        assert "imported" not in fm.get("tags", [])
        assert "react" in fm["tags"]

    def test_removes_imported_tag(self, vault: Vault) -> None:
        path = _write_imported_page(vault, "simple.md", "# Simple")
        plan = json.dumps([{
            "path": path,
            "type": "note",
            "title": "Simple",
            "summary": "A simple page",
            "tags": ["misc"],
            "move_to": None,
            "related": [],
            "hub": None,
            "duplicate_of": None,
            "confidence": "high",
        }])
        vault.apply_organize_plan(plan)

        from noteweaver.frontmatter import extract_frontmatter
        content = vault.read_file(path)
        fm = extract_frontmatter(content)
        assert "imported" not in (fm.get("tags") or [])

    def test_file_move(self, vault: Vault) -> None:
        path = _write_imported_page(vault, "diary-2024.md", "# Diary entry\n\nToday I learned...")
        plan = json.dumps([{
            "path": path,
            "type": "journal",
            "title": "Diary 2024",
            "summary": "Daily diary entry",
            "tags": ["daily"],
            "move_to": "wiki/journals/diary-2024.md",
            "related": [],
            "hub": None,
            "duplicate_of": None,
            "confidence": "high",
        }])
        result = vault.apply_organize_plan(plan)
        assert "→" in result
        assert "1 moved" in result

        content = vault.read_file("wiki/journals/diary-2024.md")
        assert "Diary" in content
        assert not (vault.root / path).exists()

    def test_related_links_added(self, vault: Vault) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        vault.write_file(
            "wiki/concepts/existing-react.md",
            f"---\ntitle: React Basics\ntype: note\nsummary: Basics\n"
            f"tags: [react]\ncreated: {today}\nupdated: {today}\n---\n\n# React\n",
        )
        path = _write_imported_page(vault, "hooks.md", "# Hooks\n\nReact hooks guide.")
        plan = json.dumps([{
            "path": path,
            "type": "note",
            "title": "Hooks",
            "summary": "React hooks",
            "tags": ["react"],
            "move_to": None,
            "related": ["React Basics"],
            "hub": None,
            "duplicate_of": None,
            "confidence": "high",
        }])
        result = vault.apply_organize_plan(plan)
        assert "1 links added" in result

        content = vault.read_file(path)
        assert "[[React Basics]]" in content

    def test_hub_creation(self, vault: Vault) -> None:
        paths = []
        for name in ["react-hooks.md", "react-state.md", "react-context.md"]:
            p = _write_imported_page(vault, name, f"# {name}\n\nContent.")
            paths.append(p)

        plan = json.dumps([
            {
                "path": p,
                "type": "note",
                "title": p.split("/")[-1].replace(".md", "").replace("-", " ").title(),
                "summary": f"About {p}",
                "tags": ["react"],
                "move_to": None,
                "related": [],
                "hub": "React",
                "duplicate_of": None,
                "confidence": "high",
            }
            for p in paths
        ])
        result = vault.apply_organize_plan(plan)
        assert "Created hub" in result
        assert "React" in result

        hub_content = vault.read_file("wiki/concepts/react.md")
        assert "type: hub" in hub_content
        assert "React Hooks" in hub_content
        assert "React State" in hub_content
        assert "React Context" in hub_content

    def test_duplicate_flagged_for_review(self, vault: Vault) -> None:
        path = _write_imported_page(vault, "dup.md", "# Duplicate content")
        plan = json.dumps([{
            "path": path,
            "type": "note",
            "title": "Duplicate",
            "summary": "Duplicate page",
            "tags": ["misc"],
            "move_to": None,
            "related": [],
            "hub": None,
            "duplicate_of": "wiki/concepts/existing.md",
            "confidence": "high",
        }])
        result = vault.apply_organize_plan(plan)
        assert "Needs review" in result
        assert "duplicate_of" in result

    def test_low_confidence_flagged(self, vault: Vault) -> None:
        path = _write_imported_page(vault, "unclear.md", "# Unclear page")
        plan = json.dumps([{
            "path": path,
            "type": "note",
            "title": "Unclear",
            "summary": "Unclear page",
            "tags": ["misc"],
            "move_to": None,
            "related": [],
            "hub": None,
            "duplicate_of": None,
            "confidence": "low",
        }])
        result = vault.apply_organize_plan(plan)
        assert "Needs review" in result
        assert "low confidence" in result

    def test_nonexistent_file_skipped(self, vault: Vault) -> None:
        plan = json.dumps([{
            "path": "wiki/concepts/nonexistent.md",
            "type": "note",
            "title": "Ghost",
            "summary": "Does not exist",
            "tags": [],
            "move_to": None,
            "related": [],
            "hub": None,
            "duplicate_of": None,
            "confidence": "high",
        }])
        result = vault.apply_organize_plan(plan)
        assert "not found" in result

    def test_apply_plan_via_vault_only(self, vault: Vault) -> None:
        path = _write_imported_page(vault, "test.md", "# Test")
        plan = json.dumps([{
            "path": path,
            "type": "note",
            "title": "Test",
            "summary": "Test page",
            "tags": ["test"],
            "move_to": None,
            "related": [],
            "hub": None,
            "duplicate_of": None,
            "confidence": "high",
        }])
        result = vault.apply_organize_plan(plan)
        assert "1 processed" in result

    def test_multiple_files_batch(self, vault: Vault) -> None:
        paths = []
        for i in range(5):
            p = _write_imported_page(vault, f"batch-{i}.md", f"# Batch {i}\n\nContent {i}.")
            paths.append(p)

        plan = json.dumps([
            {
                "path": p,
                "type": "note",
                "title": f"Batch {i}",
                "summary": f"Batch page {i}",
                "tags": ["batch"],
                "move_to": None,
                "related": [],
                "hub": "Batch Processing" if i < 3 else None,
                "duplicate_of": None,
                "confidence": "high",
            }
            for i, p in enumerate(paths)
        ])
        result = vault.apply_organize_plan(plan)
        assert "5 processed" in result
        assert "Batch Processing" in result


# ======================================================================
# ingest(source_type='directory')
# ======================================================================

class TestIngestDirectory:
    def test_import_directory_via_ingest(self, vault: Vault, tmp_path: Path) -> None:
        source_dir = tmp_path / "notes"
        source_dir.mkdir()
        (source_dir / "a.md").write_text("# A\n\nBody.", encoding="utf-8")
        result = dispatch_tool(vault, "ingest", {
            "source": str(source_dir),
            "source_type": "directory",
        })
        assert "Imported" in result or "import" in result.lower()


# ======================================================================
# End-to-end: import → scan → plan → apply
# ======================================================================

class TestEndToEnd:
    def test_full_pipeline(self, vault: Vault, tmp_path: Path) -> None:
        source_dir = tmp_path / "external_notes"
        source_dir.mkdir()

        (source_dir / "machine-learning.md").write_text(
            "# Machine Learning\n\n## Supervised Learning\n\nClassification and regression.\n\n"
            "## Unsupervised Learning\n\nClustering and dimensionality reduction.\n"
        )
        (source_dir / "deep-learning.md").write_text(
            "# Deep Learning\n\n## Neural Networks\n\nLayers and activations.\n\n"
            "## CNNs\n\nConvolutional neural networks for images.\n"
        )
        (source_dir / "daily-log.md").write_text(
            "# 2024-03-15 Daily Log\n\nToday I studied ML concepts.\n"
        )

        import_result = vault.import_directory(str(source_dir))
        assert "3/3" in import_result

        scan_result = vault.scan_imports()
        assert "Imported files to organize: 3" in scan_result
        assert "Machine Learning" in scan_result or "machine-learning" in scan_result
        assert "Deep Learning" in scan_result or "deep-learning" in scan_result
        assert "daily-log" in scan_result

        tool_scan = dispatch_tool(vault, "organize", {
            "target": "imported",
            "action": "classify",
        })
        assert "machine-learning" in tool_scan or "Machine Learning" in tool_scan

        plan = json.dumps([
            {
                "path": "wiki/concepts/machine-learning.md",
                "type": "canonical",
                "title": "Machine Learning",
                "summary": "Overview of ML: supervised and unsupervised learning",
                "tags": ["ml", "ai"],
                "move_to": None,
                "related": ["Deep Learning"],
                "hub": "AI & ML",
                "duplicate_of": None,
                "confidence": "high",
            },
            {
                "path": "wiki/concepts/deep-learning.md",
                "type": "canonical",
                "title": "Deep Learning",
                "summary": "Neural networks, CNNs, and deep learning fundamentals",
                "tags": ["ml", "ai", "deep-learning"],
                "move_to": None,
                "related": ["Machine Learning"],
                "hub": "AI & ML",
                "duplicate_of": None,
                "confidence": "high",
            },
            {
                "path": "wiki/concepts/daily-log.md",
                "type": "journal",
                "title": "2024-03-15 Daily Log",
                "summary": "Daily study log about ML concepts",
                "tags": ["daily"],
                "move_to": "wiki/journals/daily-log.md",
                "related": ["Machine Learning"],
                "hub": None,
                "duplicate_of": None,
                "confidence": "high",
            },
        ])
        apply_result = vault.apply_organize_plan(plan)
        assert "3 processed" in apply_result
        assert "1 moved" in apply_result
        assert "AI & ML" in apply_result or "ai-ml" in apply_result

        from noteweaver.frontmatter import extract_frontmatter

        ml_content = vault.read_file("wiki/concepts/machine-learning.md")
        ml_fm = extract_frontmatter(ml_content)
        assert ml_fm["type"] == "canonical"
        assert "imported" not in ml_fm.get("tags", [])
        assert "[[Deep Learning]]" in ml_content

        dl_content = vault.read_file("wiki/concepts/deep-learning.md")
        dl_fm = extract_frontmatter(dl_content)
        assert "ml" in dl_fm["tags"]

        journal = vault.read_file("wiki/journals/daily-log.md")
        assert "Daily Log" in journal

        hub = vault.read_file("wiki/concepts/ai-ml.md")
        assert "type: hub" in hub
