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
