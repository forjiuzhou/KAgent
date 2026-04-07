"""Tests for the Vault layer."""

import pytest
from pathlib import Path
from noteweaver.vault import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path)
    v.init()
    return v


class TestVaultInit:
    def test_init_creates_structure(self, tmp_path: Path) -> None:
        v = Vault(tmp_path)
        assert not v.exists()
        v.init()
        assert v.exists()
        assert (tmp_path / "sources").is_dir()
        assert (tmp_path / "wiki" / "concepts").is_dir()
        assert (tmp_path / "wiki" / "entities").is_dir()
        assert (tmp_path / "wiki" / "journals").is_dir()
        assert (tmp_path / "wiki" / "synthesis").is_dir()
        assert (tmp_path / ".schema" / "schema.md").is_file()
        assert (tmp_path / "wiki" / "index.md").is_file()
        assert (tmp_path / "wiki" / "log.md").is_file()

    def test_init_is_idempotent(self, vault: Vault) -> None:
        original_schema = vault.read_file(".schema/schema.md")
        vault.init()
        assert vault.read_file(".schema/schema.md") == original_schema

    def test_seed_files_have_content(self, vault: Vault) -> None:
        assert "Wiki Index" in vault.read_file("wiki/index.md")
        assert "Operation Log" in vault.read_file("wiki/log.md")
        assert "Vault Schema" in vault.read_file(".schema/schema.md")


class TestReadWrite:
    def test_write_and_read(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/test.md", "# Test\nHello world")
        content = vault.read_file("wiki/concepts/test.md")
        assert content == "# Test\nHello world"

    def test_write_creates_parent_dirs(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/deep/nested/page.md", "# Deep")
        assert vault.read_file("wiki/concepts/deep/nested/page.md") == "# Deep"

    def test_read_nonexistent_raises(self, vault: Vault) -> None:
        with pytest.raises(FileNotFoundError):
            vault.read_file("wiki/nope.md")

    def test_write_to_sources_raises(self, vault: Vault) -> None:
        with pytest.raises(PermissionError, match="immutable"):
            vault.write_file("sources/attack.md", "bad data")

    def test_path_escape_raises(self, vault: Vault) -> None:
        with pytest.raises(PermissionError, match="escapes vault"):
            vault.read_file("../../etc/passwd")

    def test_overwrite_existing_file(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/x.md", "v1")
        vault.write_file("wiki/concepts/x.md", "v2")
        assert vault.read_file("wiki/concepts/x.md") == "v2"


class TestListFiles:
    def test_list_empty_dir(self, vault: Vault) -> None:
        files = vault.list_files("wiki/concepts")
        assert files == []

    def test_list_with_files(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/a.md", "a")
        vault.write_file("wiki/concepts/b.md", "b")
        files = vault.list_files("wiki/concepts")
        assert len(files) == 2
        assert "wiki/concepts/a.md" in files

    def test_list_nonexistent_dir(self, vault: Vault) -> None:
        files = vault.list_files("wiki/nonexistent")
        assert files == []

    def test_list_wiki_includes_index(self, vault: Vault) -> None:
        files = vault.list_files("wiki")
        assert "wiki/index.md" in files
        assert "wiki/log.md" in files


class TestSearch:
    def test_search_finds_match(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/ai.md", "# AI\nMachine learning is great")
        results = vault.search_content("machine learning", "wiki")
        assert len(results) >= 1
        assert any("ai.md" in r["path"] for r in results)

    def test_search_case_insensitive(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/x.md", "Transformer Architecture")
        results = vault.search_content("transformer", "wiki")
        assert len(results) >= 1

    def test_search_no_results(self, vault: Vault) -> None:
        results = vault.search_content("zzzznonexistentzzzz", "wiki")
        assert results == []

    def test_search_returns_line_numbers(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/y.md", "line1\nline2\nfindme here\nline4")
        results = vault.search_content("findme", "wiki")
        matches = results[0]["matches"]
        assert matches[0][0] == 3  # line number
        assert "findme" in matches[0][1]


class TestLog:
    def test_append_log(self, vault: Vault) -> None:
        vault.append_log("test", "My Test")
        log = vault.read_file("wiki/log.md")
        assert "test | My Test" in log

    def test_append_log_with_details(self, vault: Vault) -> None:
        vault.append_log("ingest", "Article X", "Created 3 pages")
        log = vault.read_file("wiki/log.md")
        assert "Created 3 pages" in log

    def test_multiple_log_entries(self, vault: Vault) -> None:
        vault.append_log("a", "First")
        vault.append_log("b", "Second")
        log = vault.read_file("wiki/log.md")
        assert "First" in log
        assert "Second" in log
