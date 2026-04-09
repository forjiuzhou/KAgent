"""Tests for the promote_insight tool."""

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


class TestPromoteInsight:
    def test_creates_new_note_when_no_existing(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "promote_insight", {
            "title": "Quantum Computing Basics",
            "content": "Qubits can exist in superposition.",
            "tags": ["physics", "computing"],
        })
        assert "OK" in result
        assert "created new note page" in result

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

        result = dispatch_tool(vault, "promote_insight", {
            "title": "Quantum Computing",
            "content": "New insight about error correction.",
            "source_journal": "wiki/journals/2025-04-09.md",
        })
        assert "OK" in result
        assert "existing page" in result

        content = vault.read_file("wiki/concepts/quantum-computing.md")
        assert "Promoted Insight" in content
        assert "error correction" in content
        assert "## Related" in content
        assert "Promoted from" in content

    def test_includes_source_journal_reference(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "promote_insight", {
            "title": "Test Insight",
            "content": "Some valuable insight.",
            "source_journal": "wiki/journals/2025-04-09.md",
        })
        assert "OK" in result
        files = vault.list_files("wiki/concepts")
        for f in files:
            if "test-insight" in f:
                content = vault.read_file(f)
                assert "wiki/journals/2025-04-09.md" in content
                break

    def test_slug_generation(self, vault: Vault) -> None:
        """Title with special chars gets a clean slug."""
        result = dispatch_tool(vault, "promote_insight", {
            "title": "LLM's Impact on AI/ML Systems!",
            "content": "Important insight.",
        })
        assert "OK" in result
        files = vault.list_files("wiki/concepts")
        for f in files:
            assert " " not in f
            assert "!" not in f

    def test_creates_synthesis_in_correct_dir(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "promote_insight", {
            "title": "Cross-Cutting Analysis",
            "content": "Insight spanning multiple topics.",
            "target_type": "synthesis",
        })
        assert "OK" in result
        assert "created new synthesis page" in result
        assert "wiki/synthesis/" in result

        files = vault.list_files("wiki/synthesis")
        assert any("cross-cutting" in f for f in files)

    def test_creates_canonical_with_sources(self, vault: Vault) -> None:
        from noteweaver.frontmatter import extract_frontmatter

        result = dispatch_tool(vault, "promote_insight", {
            "title": "Raft Protocol",
            "content": "Authoritative reference for Raft.",
            "source_journal": "wiki/journals/2025-04-09.md",
            "target_type": "canonical",
        })
        assert "OK" in result
        assert "created new canonical page" in result
        assert "wiki/concepts/" in result

        files = vault.list_files("wiki/concepts")
        path = next(f for f in files if "raft" in f)
        content = vault.read_file(path)
        fm = extract_frontmatter(content)
        assert fm["type"] == "canonical"
        assert fm["sources"]

    def test_default_type_unchanged(self, vault: Vault) -> None:
        """Not passing target_type should still create a note."""
        from noteweaver.frontmatter import extract_frontmatter

        result = dispatch_tool(vault, "promote_insight", {
            "title": "Default Type Test",
            "content": "Should be a note.",
        })
        assert "created new note page" in result

        files = vault.list_files("wiki/concepts")
        path = next(f for f in files if "default-type" in f)
        content = vault.read_file(path)
        fm = extract_frontmatter(content)
        assert fm["type"] == "note"

    def test_invalid_target_type_rejected(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "promote_insight", {
            "title": "Bad Type",
            "content": "Content.",
            "target_type": "journal",
        })
        assert "Error" in result
