"""Tests for fine-grained editing tools, dedup, and enhanced search."""

from __future__ import annotations

from pathlib import Path

import pytest
from noteweaver.vault import Vault
from noteweaver.tools.definitions import dispatch_tool


SAMPLE_PAGE = """\
---
title: Attention Mechanism
type: canonical
summary: Core building block of transformers
tags: [ai, nlp, transformers]
sources: [arxiv:1706.03762]
created: 2025-01-01
updated: 2025-04-01
---

# Attention Mechanism

Attention allows a model to focus on relevant parts of the input.

## Scaled Dot-Product

The core formula is Q·K^T / √d_k, then softmax, then multiply by V.

## Related

- [[Transformer Architecture]]
"""


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


@pytest.fixture
def vault_with_page(vault: Vault) -> Vault:
    vault.write_file("wiki/concepts/attention.md", SAMPLE_PAGE)
    return vault


# ======================================================================
# append_section
# ======================================================================


class TestAppendSection:
    def test_appends_before_related(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "append_section", {
            "path": "wiki/concepts/attention.md",
            "heading": "Multi-Head Attention",
            "content": "Instead of one attention, use h parallel heads.",
        })
        assert "OK" in result
        content = vault_with_page.read_file("wiki/concepts/attention.md")
        assert "## Multi-Head Attention" in content
        # Must appear BEFORE Related
        mha_pos = content.index("## Multi-Head Attention")
        related_pos = content.index("## Related")
        assert mha_pos < related_pos

    def test_appends_at_end_if_no_related(self, vault: Vault) -> None:
        page = "---\ntitle: T\ntype: note\ntags: []\n---\n\n# Test\n\nSome content.\n"
        vault.write_file("wiki/concepts/test.md", page)
        result = dispatch_tool(vault, "append_section", {
            "path": "wiki/concepts/test.md",
            "heading": "New Section",
            "content": "New content here.",
        })
        assert "OK" in result
        content = vault.read_file("wiki/concepts/test.md")
        assert "## New Section" in content
        assert "New content here." in content

    def test_rejects_non_wiki_path(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "append_section", {
            "path": "sources/test.md",
            "heading": "H",
            "content": "C",
        })
        assert "Error" in result

    def test_rejects_missing_file(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "append_section", {
            "path": "wiki/concepts/nope.md",
            "heading": "H",
            "content": "C",
        })
        assert "Error" in result


# ======================================================================
# append_to_section
# ======================================================================


class TestAppendToSection:
    def test_appends_to_existing_section(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "append_to_section", {
            "path": "wiki/concepts/attention.md",
            "heading": "Scaled Dot-Product",
            "content": "- Also known as: self-attention when Q=K=V.",
        })
        assert "OK" in result
        content = vault_with_page.read_file("wiki/concepts/attention.md")
        assert "Also known as: self-attention" in content

    def test_error_on_missing_section(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "append_to_section", {
            "path": "wiki/concepts/attention.md",
            "heading": "Nonexistent Section",
            "content": "won't work",
        })
        assert "Error" in result
        assert "not found" in result

    def test_page_with_numeric_title_and_tags(self, vault: Vault) -> None:
        """Pages whose YAML title/tags parse as int should not crash."""
        page = (
            "---\ntitle: 2026\ntype: note\n"
            "summary: Year\ntags: [2026, review]\n"
            "created: 2026-01-01\nupdated: 2026-01-01\n---\n\n"
            "# 2026\n\n## Highlights\n\nSome highlights.\n"
        )
        vault.write_file("wiki/concepts/year-2026.md", page)
        result = dispatch_tool(vault, "append_to_section", {
            "path": "wiki/concepts/year-2026.md",
            "heading": "Highlights",
            "content": "- New highlight added.",
        })
        assert "OK" in result
        content = vault.read_file("wiki/concepts/year-2026.md")
        assert "New highlight added" in content


# ======================================================================
# update_frontmatter
# ======================================================================


class TestUpdateFrontmatter:
    def test_updates_tags(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "update_frontmatter", {
            "path": "wiki/concepts/attention.md",
            "fields": {"tags": ["ai", "nlp", "transformers", "deep-learning"]},
        })
        assert "OK" in result
        content = vault_with_page.read_file("wiki/concepts/attention.md")
        assert "deep-learning" in content

    def test_updates_summary(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "update_frontmatter", {
            "path": "wiki/concepts/attention.md",
            "fields": {"summary": "Updated summary for attention"},
        })
        assert "OK" in result

    def test_preserves_body(self, vault_with_page: Vault) -> None:
        dispatch_tool(vault_with_page, "update_frontmatter", {
            "path": "wiki/concepts/attention.md",
            "fields": {"tags": ["updated"]},
        })
        content = vault_with_page.read_file("wiki/concepts/attention.md")
        assert "Attention allows a model" in content
        assert "Scaled Dot-Product" in content

    def test_rejects_invalid_update(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "update_frontmatter", {
            "path": "wiki/concepts/attention.md",
            "fields": {"type": "invalid_type"},
        })
        assert "Error" in result

    def test_rejects_missing_file(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "update_frontmatter", {
            "path": "wiki/concepts/nope.md",
            "fields": {"tags": ["test"]},
        })
        assert "Error" in result


# ======================================================================
# add_related_link
# ======================================================================


class TestAddRelatedLink:
    def test_adds_to_existing_related(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "add_related_link", {
            "path": "wiki/concepts/attention.md",
            "title": "Multi-Head Attention",
        })
        assert "OK" in result
        content = vault_with_page.read_file("wiki/concepts/attention.md")
        assert "[[Multi-Head Attention]]" in content

    def test_creates_related_if_absent(self, vault: Vault) -> None:
        page = "---\ntitle: T\ntype: note\ntags: []\n---\n\n# Test\n\nContent.\n"
        vault.write_file("wiki/concepts/test.md", page)
        result = dispatch_tool(vault, "add_related_link", {
            "path": "wiki/concepts/test.md",
            "title": "Other Page",
        })
        assert "OK" in result
        content = vault.read_file("wiki/concepts/test.md")
        assert "## Related" in content
        assert "[[Other Page]]" in content

    def test_skips_duplicate_link(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "add_related_link", {
            "path": "wiki/concepts/attention.md",
            "title": "Transformer Architecture",
        })
        assert "already exists" in result


# ======================================================================
# find_existing_page
# ======================================================================


class TestFindExistingPage:
    def test_finds_by_title(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "find_existing_page", {
            "title": "Attention Mechanism",
        })
        assert "attention" in result.lower()
        assert "wiki/concepts/attention.md" in result

    def test_finds_by_partial_title(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "find_existing_page", {
            "title": "Attention",
        })
        assert "wiki/concepts/attention.md" in result

    def test_no_match(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "find_existing_page", {
            "title": "Quantum Computing",
        })
        assert "No existing pages" in result
        assert "Safe to create" in result

    def test_suggests_update(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "find_existing_page", {
            "title": "Attention",
        })
        assert "append_section" in result or "updating" in result.lower()

    def test_numeric_title_page(self, vault: Vault) -> None:
        """Pages with numeric YAML titles should not crash find_existing_page."""
        page = (
            "---\ntitle: 2026\ntype: note\n"
            "summary: Year\ntags: [2026]\n"
            "created: 2026-01-01\nupdated: 2026-01-01\n---\n\n"
            "# 2026\n\nContent.\n"
        )
        vault.write_file("wiki/concepts/year-2026.md", page)
        result = dispatch_tool(vault, "find_existing_page", {
            "title": "2026",
        })
        assert "Error" not in result


# ======================================================================
# Enhanced search
# ======================================================================


class TestEnhancedSearch:
    def test_search_returns_metadata(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "search_vault", {
            "query": "attention",
        })
        assert "canonical" in result.lower() or "Attention Mechanism" in result
        # Should include summary or tags
        assert "Tags:" in result or "Summary:" in result
