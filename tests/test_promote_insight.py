"""Tests for capture (replaces promote_insight) creating and extending pages."""

from __future__ import annotations

from pathlib import Path

import pytest
from noteweaver.vault import Vault
from noteweaver.tools.definitions import dispatch_tool


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


class TestCapturePromoteStyle:
    def test_creates_new_note_when_no_existing(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "capture", {
            "title": "Quantum Computing Basics",
            "content": "Qubits can exist in superposition.",
            "tags": ["physics", "computing"],
        })
        assert "OK" in result
        assert "note" in result.lower()

        files = vault.list_files("wiki/concepts")
        assert any("quantum" in f for f in files)

        content = None
        for f in files:
            if "quantum" in f:
                content = vault.read_file(f)
                break
        assert content is not None
        assert "Quantum Computing Basics" in content
        assert "superposition" in content
        assert "physics" in content

    def test_appends_to_existing_page(self, vault: Vault) -> None:
        page = (
            "---\ntitle: Quantum Computing\ntype: note\n"
            "summary: Notes on quantum computing\ntags: [physics]\n"
            "created: 2025-01-01\nupdated: 2025-01-01\n---\n\n"
            "# Quantum Computing\n\nIntro text.\n\n## Related\n"
        )
        vault.write_file("wiki/concepts/quantum-computing.md", page)

        result = dispatch_tool(vault, "capture", {
            "target": "wiki/concepts/quantum-computing.md",
            "title": "Insight",
            "content": "New insight about error correction.",
        })
        assert "OK" in result
        assert "appended" in result.lower()

        content = vault.read_file("wiki/concepts/quantum-computing.md")
        assert "error correction" in content
        assert "## Related" in content

    def test_slug_generation(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "capture", {
            "title": "LLM's Impact on AI/ML Systems!",
            "content": "Important insight.",
        })
        assert "OK" in result
        files = vault.list_files("wiki/concepts")
        for f in files:
            if "llm" in f:
                assert " " not in f
                assert "!" not in f
                break

    def test_creates_synthesis_in_correct_dir(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "capture", {
            "title": "Cross-Cutting Analysis",
            "content": "Insight spanning multiple topics.",
            "type": "synthesis",
        })
        assert "OK" in result
        assert "wiki/synthesis/" in result

        files = vault.list_files("wiki/synthesis")
        assert any("cross-cutting" in f for f in files)

    def test_canonical_capture_validates_sources(self, vault: Vault) -> None:
        """Canonical type requires non-empty sources in frontmatter; capture template uses []."""
        result = dispatch_tool(vault, "capture", {
            "title": "Raft Protocol",
            "content": "Authoritative reference for Raft.",
            "type": "canonical",
        })
        assert "Error" in result
        assert "sources" in result.lower()

    def test_default_type_unchanged(self, vault: Vault) -> None:
        from noteweaver.frontmatter import extract_frontmatter

        result = dispatch_tool(vault, "capture", {
            "title": "Default Type Test",
            "content": "Should be a note.",
        })
        assert "note" in result.lower()

        files = vault.list_files("wiki/concepts")
        path = next(f for f in files if "default-type" in f)
        content = vault.read_file(path)
        fm = extract_frontmatter(content)
        assert fm["type"] == "note"

    def test_invalid_page_type_falls_back_to_note(self, vault: Vault) -> None:
        """capture only allows note|canonical|synthesis; unknown types become note."""
        from noteweaver.frontmatter import extract_frontmatter

        result = dispatch_tool(vault, "capture", {
            "title": "Bad Type",
            "content": "Content.",
            "type": "journal",
        })
        assert "OK" in result
        files = vault.list_files("wiki/concepts")
        path = next(f for f in files if "bad-type" in f)
        fm = extract_frontmatter(vault.read_file(path))
        assert fm["type"] == "note"

    def test_numeric_title_in_existing_page(self, vault: Vault) -> None:
        page = (
            "---\ntitle: 2026\ntype: note\n"
            "summary: Year summary\ntags: [journal]\n"
            "created: 2026-01-01\nupdated: 2026-01-01\n---\n\n"
            "# 2026\n\nYear overview.\n\n## Related\n"
        )
        vault.write_file("wiki/concepts/year-2026.md", page)

        result = dispatch_tool(vault, "capture", {
            "target": "wiki/concepts/year-2026.md",
            "title": "2026 Highlights",
            "content": "Key events of the year.",
        })
        assert "OK" in result

    def test_numeric_tags_in_existing_page(self, vault: Vault) -> None:
        page = (
            "---\ntitle: Year Review\ntype: note\n"
            "summary: Review\ntags: [2026, review]\n"
            "created: 2026-01-01\nupdated: 2026-01-01\n---\n\n"
            "# Year Review\n\nContent.\n"
        )
        vault.write_file("wiki/concepts/year-review.md", page)

        result = dispatch_tool(vault, "capture", {
            "target": "wiki/concepts/year-review.md",
            "title": "More notes",
            "content": "Additional review insight.",
        })
        assert "OK" in result
        assert "appended" in result.lower()
