"""Tests for SQLite FTS5 search index."""

import pytest
from pathlib import Path
from noteweaver.search import SearchIndex
from noteweaver.vault import Vault


@pytest.fixture
def index(tmp_path: Path) -> SearchIndex:
    idx = SearchIndex(tmp_path)
    yield idx
    idx.close()


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


class TestSearchIndex:
    def test_upsert_and_search(self, index: SearchIndex) -> None:
        index.upsert(path="wiki/concepts/ai.md", title="AI", body="Artificial intelligence overview")
        results = index.search("artificial intelligence")
        assert len(results) >= 1
        assert results[0]["path"] == "wiki/concepts/ai.md"

    def test_search_by_title(self, index: SearchIndex) -> None:
        index.upsert(path="a.md", title="Transformer Architecture", body="content")
        results = index.search("transformer")
        assert len(results) >= 1

    def test_search_by_tags(self, index: SearchIndex) -> None:
        index.upsert(path="a.md", title="X", tags="machine-learning, deep-learning", body="stuff")
        results = index.search("machine-learning")
        assert len(results) >= 1

    def test_search_no_results(self, index: SearchIndex) -> None:
        index.upsert(path="a.md", title="X", body="hello")
        results = index.search("nonexistent")
        assert results == []

    def test_upsert_replaces(self, index: SearchIndex) -> None:
        index.upsert(path="a.md", title="Old", body="old content")
        index.upsert(path="a.md", title="New", body="new content")
        results = index.search("old content")
        assert len(results) == 0
        results = index.search("new content")
        assert len(results) == 1

    def test_remove(self, index: SearchIndex) -> None:
        index.upsert(path="a.md", title="X", body="findme")
        index.remove("a.md")
        results = index.search("findme")
        assert results == []

    def test_rebuild(self, index: SearchIndex) -> None:
        index.upsert(path="old.md", title="Old", body="old")
        index.rebuild([
            {"path": "new.md", "title": "New", "body": "fresh content"},
        ])
        assert index.search("old") == []
        assert len(index.search("fresh")) == 1

    def test_empty_query(self, index: SearchIndex) -> None:
        assert index.search("") == []
        assert index.search("   ") == []


class TestVaultSearchIntegration:
    def test_write_updates_index(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/test.md",
            "---\ntitle: Test Topic\ntype: note\nsummary: A test\n---\n# Body with unique xyzzy content",
        )
        results = vault.search.search("xyzzy")
        assert len(results) >= 1
        assert results[0]["path"] == "wiki/concepts/test.md"

    def test_search_content_uses_fts(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/ai.md",
            "---\ntitle: AI\ntype: note\n---\n# Artificial Intelligence\nDeep learning rocks",
        )
        results = vault.search_content("deep learning")
        assert len(results) >= 1
        assert any("ai.md" in r["path"] for r in results)

    def test_rebuild_search_index(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/a.md",
            "---\ntitle: A\ntype: note\n---\ncontent alpha",
        )
        vault.write_file(
            "wiki/concepts/b.md",
            "---\ntitle: B\ntype: note\n---\ncontent beta",
        )
        count = vault.rebuild_search_index()
        assert count >= 2
        assert len(vault.search.search("alpha")) >= 1
        assert len(vault.search.search("beta")) >= 1
