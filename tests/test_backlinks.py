"""Tests for backlink index."""

import pytest
from pathlib import Path
from noteweaver.backlinks import BacklinkIndex
from noteweaver.vault import Vault


@pytest.fixture
def index(tmp_path: Path) -> BacklinkIndex:
    idx = BacklinkIndex(tmp_path)
    yield idx
    idx.close()


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


class TestBacklinkIndex:
    def test_update_and_query(self, index: BacklinkIndex) -> None:
        index.update_page("a.md", "See [[Topic B]] and [[Topic C]]")
        assert index.backlinks_for("Topic B") == ["a.md"]
        assert index.backlinks_for("Topic C") == ["a.md"]

    def test_outlinks(self, index: BacklinkIndex) -> None:
        index.update_page("a.md", "[[X]] and [[Y]]")
        out = index.outlinks_for("a.md")
        assert set(out) == {"X", "Y"}

    def test_no_backlinks(self, index: BacklinkIndex) -> None:
        assert index.backlinks_for("Nonexistent") == []

    def test_update_replaces(self, index: BacklinkIndex) -> None:
        index.update_page("a.md", "[[Old]]")
        index.update_page("a.md", "[[New]]")
        assert index.backlinks_for("Old") == []
        assert index.backlinks_for("New") == ["a.md"]

    def test_reference_count(self, index: BacklinkIndex) -> None:
        index.update_page("a.md", "[[Topic]]")
        index.update_page("b.md", "[[Topic]]")
        assert index.reference_count("Topic") == 2

    def test_orphan_pages(self, index: BacklinkIndex) -> None:
        index.update_page("a.md", "[[B]]")
        orphans = index.orphan_pages({"A", "B", "C"})
        assert "A" in orphans
        assert "C" in orphans
        assert "B" not in orphans

    def test_rebuild(self, index: BacklinkIndex) -> None:
        index.update_page("old.md", "[[X]]")
        index.rebuild([
            {"path": "new.md", "content": "[[Y]] and [[Z]]"},
        ])
        assert index.backlinks_for("X") == []
        assert index.backlinks_for("Y") == ["new.md"]

    def test_stats(self, index: BacklinkIndex) -> None:
        index.update_page("a.md", "[[X]] [[Y]]")
        index.update_page("b.md", "[[X]]")
        s = index.stats()
        assert s["total_links"] == 3
        assert s["pages_with_outlinks"] == 2


class TestVaultBacklinkIntegration:
    def test_write_updates_backlinks(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/a.md",
            "---\ntitle: A\ntype: note\n---\nSee [[B Topic]]",
        )
        assert vault.backlinks.backlinks_for("B Topic") == ["wiki/concepts/a.md"]

    def test_health_metrics_uses_backlinks(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/hub.md",
            "---\ntitle: Hub\ntype: hub\nsummary: s\n---\n[[Orphan Test]]",
        )
        vault.write_file(
            "wiki/concepts/orphan.md",
            "---\ntitle: Orphan Test\ntype: note\nsummary: s\n---\nNo links to me from others",
        )
        vault.write_file(
            "wiki/concepts/linked.md",
            "---\ntitle: Linked\ntype: note\nsummary: s\n---\nContent",
        )
        vault.write_file(
            "wiki/concepts/linker.md",
            "---\ntitle: Linker\ntype: note\nsummary: s\n---\n[[Linked]]",
        )
        metrics = vault.health_metrics()
        assert metrics["total_links"] > 0


class TestFrontmatterRelatedInBacklinks:
    """frontmatter ``related`` entries should appear in the backlink graph."""

    def test_related_field_indexed_on_update(self, index: BacklinkIndex) -> None:
        content = (
            "---\ntitle: A\ntype: note\nrelated:\n  - B\n  - C\n---\n"
            "Body text with no wiki-links."
        )
        index.update_page("a.md", content)
        assert index.backlinks_for("B") == ["a.md"]
        assert index.backlinks_for("C") == ["a.md"]

    def test_related_field_indexed_on_rebuild(self, index: BacklinkIndex) -> None:
        content = "---\ntitle: X\ntype: note\nrelated:\n  - Y\n---\nBody."
        index.rebuild([{"path": "x.md", "content": content}])
        assert index.backlinks_for("Y") == ["x.md"]

    def test_related_merged_with_body_links(self, index: BacklinkIndex) -> None:
        content = (
            "---\ntitle: A\ntype: note\nrelated:\n  - B\n---\n"
            "See [[C]] in the body."
        )
        index.update_page("a.md", content)
        out = set(index.outlinks_for("a.md"))
        assert out == {"B", "C"}

    def test_related_deduped_with_body_links(self, index: BacklinkIndex) -> None:
        content = (
            "---\ntitle: A\ntype: note\nrelated:\n  - B\n---\n"
            "Also in body: [[B]]"
        )
        index.update_page("a.md", content)
        assert index.reference_count("B") == 1

    def test_write_file_indexes_related(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/a.md",
            "---\ntitle: A\ntype: note\nrelated:\n  - Hidden Link\n---\nBody text.",
        )
        assert vault.backlinks.backlinks_for("Hidden Link") == ["wiki/concepts/a.md"]


class TestSourceProvenance:
    """Source provenance index: which wiki pages cite which sources."""

    def test_update_source_index(self, index: BacklinkIndex) -> None:
        index.update_source_index([
            {"path": "wiki/concepts/a.md", "sources": ["sources/paper.pdf"]},
            {"path": "wiki/concepts/b.md", "sources": ["sources/paper.pdf", "sources/other.md"]},
        ])
        result = index.wiki_pages_citing_source("sources/paper.pdf")
        assert set(result) == {"wiki/concepts/a.md", "wiki/concepts/b.md"}

    def test_uncited_sources(self, index: BacklinkIndex) -> None:
        index.update_source_index([
            {"path": "wiki/concepts/a.md", "sources": ["sources/used.pdf"]},
        ])
        uncited = index.uncited_sources(["sources/used.pdf", "sources/orphan.md"])
        assert uncited == ["sources/orphan.md"]

    def test_uncited_sources_empty(self, index: BacklinkIndex) -> None:
        index.update_source_index([])
        uncited = index.uncited_sources(["sources/a.md", "sources/b.md"])
        assert uncited == ["sources/a.md", "sources/b.md"]

    def test_rebuild_backlinks_builds_provenance(self, vault: Vault) -> None:
        vault.save_source("sources/raw.md", "Some raw content")
        vault.write_file(
            "wiki/concepts/a.md",
            "---\ntitle: A\ntype: canonical\nsources:\n  - sources/raw.md\n"
            "summary: s\n---\nContent from [[Other]].",
        )
        vault.rebuild_backlinks()
        citing = vault.backlinks.wiki_pages_citing_source("sources/raw.md")
        assert "wiki/concepts/a.md" in citing
        uncited = vault.backlinks.uncited_sources(vault.list_files("sources"))
        assert "sources/raw.md" not in uncited
