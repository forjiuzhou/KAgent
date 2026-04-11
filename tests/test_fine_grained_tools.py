"""Tests for capture/organize flows that replace fine-grained editing tools."""

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
# capture — new section (replaces append_section)
# ======================================================================


class TestCaptureAppendSection:
    def test_appends_before_related(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "capture", {
            "target": "wiki/concepts/attention.md",
            "title": "Multi-Head Attention",
            "content": "Instead of one attention, use h parallel heads.",
        })
        assert "OK" in result
        content = vault_with_page.read_file("wiki/concepts/attention.md")
        assert "## Multi-Head Attention" in content
        mha_pos = content.index("## Multi-Head Attention")
        related_pos = content.index("## Related")
        assert mha_pos < related_pos

    def test_appends_at_end_if_no_related(self, vault: Vault) -> None:
        page = "---\ntitle: T\ntype: note\ntags: []\n---\n\n# Test\n\nSome content.\n"
        vault.write_file("wiki/concepts/test.md", page)
        result = dispatch_tool(vault, "capture", {
            "target": "wiki/concepts/test.md",
            "title": "New Section",
            "content": "New content here.",
        })
        assert "OK" in result
        content = vault.read_file("wiki/concepts/test.md")
        assert "## New Section" in content
        assert "New content here." in content

    def test_rejects_non_wiki_path(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "capture", {
            "target": "sources/test.md",
            "title": "H",
            "content": "C",
        })
        assert "Error" in result

    def test_rejects_missing_file(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "capture", {
            "target": "wiki/concepts/nope.md",
            "title": "H",
            "content": "C",
        })
        assert "Error" in result


# ======================================================================
# capture — append to page (replaces append_to_section)
# ======================================================================


class TestCaptureAppendToSection:
    def test_appends_new_section_with_content(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "capture", {
            "target": "wiki/concepts/attention.md",
            "title": "Scaled Dot-Product — notes",
            "content": "- Also known as: self-attention when Q=K=V.",
        })
        assert "OK" in result
        content = vault_with_page.read_file("wiki/concepts/attention.md")
        assert "Also known as: self-attention" in content

    def test_error_on_missing_target(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "capture", {
            "target": "wiki/concepts/missing.md",
            "title": "Section",
            "content": "won't work",
        })
        assert "Error" in result

    def test_page_with_numeric_title_and_tags(self, vault: Vault) -> None:
        page = (
            "---\ntitle: 2026\ntype: note\n"
            "summary: Year\ntags: [2026, review]\n"
            "created: 2026-01-01\nupdated: 2026-01-01\n---\n\n"
            "# 2026\n\n## Highlights\n\nSome highlights.\n"
        )
        vault.write_file("wiki/concepts/year-2026.md", page)
        result = dispatch_tool(vault, "capture", {
            "target": "wiki/concepts/year-2026.md",
            "title": "Highlights follow-up",
            "content": "- New highlight added.",
        })
        assert "OK" in result
        content = vault.read_file("wiki/concepts/year-2026.md")
        assert "New highlight added" in content


# ======================================================================
# organize update_metadata (replaces update_frontmatter)
# ======================================================================


class TestOrganizeUpdateMetadata:
    def test_updates_tags(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "organize", {
            "target": "wiki/concepts/attention.md",
            "action": "update_metadata",
            "metadata": {"tags": ["ai", "nlp", "transformers", "deep-learning"]},
        })
        assert "OK" in result
        content = vault_with_page.read_file("wiki/concepts/attention.md")
        assert "deep-learning" in content

    def test_updates_summary(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "organize", {
            "target": "wiki/concepts/attention.md",
            "action": "update_metadata",
            "metadata": {"summary": "Updated summary for attention"},
        })
        assert "OK" in result

    def test_preserves_body(self, vault_with_page: Vault) -> None:
        dispatch_tool(vault_with_page, "organize", {
            "target": "wiki/concepts/attention.md",
            "action": "update_metadata",
            "metadata": {"tags": ["updated"]},
        })
        content = vault_with_page.read_file("wiki/concepts/attention.md")
        assert "Attention allows a model" in content
        assert "Scaled Dot-Product" in content

    def test_rejects_invalid_update(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "organize", {
            "target": "wiki/concepts/attention.md",
            "action": "update_metadata",
            "metadata": {"type": "invalid_type"},
        })
        assert "Error" in result

    def test_rejects_missing_file(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "organize", {
            "target": "wiki/concepts/nope.md",
            "action": "update_metadata",
            "metadata": {"tags": ["test"]},
        })
        assert "Error" in result


# ======================================================================
# organize link (replaces add_related_link)
# ======================================================================


class TestOrganizeLink:
    def test_adds_to_existing_related(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "organize", {
            "target": "wiki/concepts/attention.md",
            "action": "link",
            "link_to": "Multi-Head Attention",
        })
        assert "OK" in result
        content = vault_with_page.read_file("wiki/concepts/attention.md")
        assert "[[Multi-Head Attention]]" in content

    def test_creates_related_if_absent(self, vault: Vault) -> None:
        page = "---\ntitle: T\ntype: note\ntags: []\n---\n\n# Test\n\nContent.\n"
        vault.write_file("wiki/concepts/test.md", page)
        result = dispatch_tool(vault, "organize", {
            "target": "wiki/concepts/test.md",
            "action": "link",
            "link_to": "Other Page",
        })
        assert "OK" in result
        content = vault.read_file("wiki/concepts/test.md")
        assert "## Related" in content
        assert "[[Other Page]]" in content

    def test_skips_duplicate_link(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "organize", {
            "target": "wiki/concepts/attention.md",
            "action": "link",
            "link_to": "Transformer Architecture",
        })
        assert "already exists" in result


# ======================================================================
# survey_topic / search (replaces find_existing_page / search_vault)
# ======================================================================


class TestSurveyTopicAndSearch:
    def test_survey_finds_by_title(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "survey_topic", {
            "topic": "Attention Mechanism",
        })
        assert "attention" in result.lower()
        assert "wiki/concepts/attention.md" in result

    def test_search_finds_by_query(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "search", {
            "query": "attention",
        })
        assert "wiki/concepts/attention.md" in result

    def test_survey_suggests_new_topic_when_empty(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "survey_topic", {
            "topic": "Quantum Computing",
        })
        assert "new" in result.lower() or "None" in result

    def test_numeric_title_page_in_survey(self, vault: Vault) -> None:
        page = (
            "---\ntitle: 2026\ntype: note\n"
            "summary: Year\ntags: [2026]\n"
            "created: 2026-01-01\nupdated: 2026-01-01\n---\n\n"
            "# 2026\n\nContent.\n"
        )
        vault.write_file("wiki/concepts/year-2026.md", page)
        result = dispatch_tool(vault, "survey_topic", {"topic": "2026"})
        assert "Error" not in result


class TestSearchMetadata:
    def test_search_returns_metadata(self, vault_with_page: Vault) -> None:
        result = dispatch_tool(vault_with_page, "search", {
            "query": "attention",
        })
        assert "canonical" in result.lower() or "Attention Mechanism" in result
        assert "Tags:" in result or "Summary:" in result
