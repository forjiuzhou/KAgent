"""Tests for frontmatter validation — hard constraints on wiki pages."""

import pytest
from noteweaver.frontmatter import validate_frontmatter, extract_frontmatter


class TestExtract:
    def test_valid_frontmatter(self) -> None:
        content = "---\ntitle: Test\ntype: hub\n---\n# Content"
        fm = extract_frontmatter(content)
        assert fm == {"title": "Test", "type": "hub"}

    def test_no_frontmatter(self) -> None:
        assert extract_frontmatter("# Just content") is None

    def test_invalid_yaml(self) -> None:
        assert extract_frontmatter("---\n: broken: yaml:\n---\n") is None


class TestValidation:
    def test_valid_hub(self) -> None:
        content = "---\ntitle: My Hub\ntype: hub\n---\n# Hub"
        result = validate_frontmatter("wiki/concepts/test.md", content)
        assert result.valid

    def test_valid_canonical_with_sources(self) -> None:
        content = "---\ntitle: My Topic\ntype: canonical\nsources:\n  - https://example.com\n---\n# Topic"
        result = validate_frontmatter("wiki/concepts/test.md", content)
        assert result.valid

    def test_canonical_without_sources_fails(self) -> None:
        content = "---\ntitle: My Topic\ntype: canonical\n---\n# Topic"
        result = validate_frontmatter("wiki/concepts/test.md", content)
        assert not result.valid
        assert any("sources" in e for e in result.errors)

    def test_missing_frontmatter_fails(self) -> None:
        result = validate_frontmatter("wiki/concepts/test.md", "# No frontmatter")
        assert not result.valid

    def test_missing_title_fails(self) -> None:
        content = "---\ntype: note\n---\n# No title in frontmatter"
        result = validate_frontmatter("wiki/concepts/test.md", content)
        assert not result.valid
        assert any("title" in e for e in result.errors)

    def test_missing_type_fails(self) -> None:
        content = "---\ntitle: Test\n---\n# No type"
        result = validate_frontmatter("wiki/concepts/test.md", content)
        assert not result.valid
        assert any("type" in e for e in result.errors)

    def test_invalid_type_fails(self) -> None:
        content = "---\ntitle: Test\ntype: banana\n---\n# Bad type"
        result = validate_frontmatter("wiki/concepts/test.md", content)
        assert not result.valid
        assert any("banana" in e for e in result.errors)

    def test_exempt_paths_skip_validation(self) -> None:
        result = validate_frontmatter("wiki/index.md", "# No frontmatter needed")
        assert result.valid

    def test_non_wiki_paths_skip_validation(self) -> None:
        result = validate_frontmatter(".schema/schema.md", "# Schema file")
        assert result.valid

    def test_all_valid_types(self) -> None:
        for t in ["source", "journal", "hub", "canonical", "note", "synthesis", "archive"]:
            extra = "\nsources:\n  - x" if t == "canonical" else ""
            content = f"---\ntitle: T\ntype: {t}{extra}\n---\n"
            result = validate_frontmatter("wiki/concepts/test.md", content)
            assert result.valid, f"Type '{t}' should be valid but got: {result.errors}"
