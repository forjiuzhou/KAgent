"""Tests for the Vault layer."""

import pytest
from pathlib import Path
from noteweaver.vault import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
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

    def test_search_returns_matching_content(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/y.md", "---\ntitle: Y\ntype: note\n---\nline1\nline2\nfindme here\nline4")
        results = vault.search_content("findme", "wiki")
        assert len(results) >= 1
        assert any("findme" in str(m) for m in results[0]["matches"])


class TestPreferences:
    def test_init_creates_preferences(self, vault: Vault) -> None:
        prefs_path = vault.schema_dir / "preferences.md"
        assert prefs_path.is_file()
        content = prefs_path.read_text()
        assert "User Preferences" in content
        assert "preference" in content

    def test_preferences_has_valid_frontmatter(self, vault: Vault) -> None:
        from noteweaver.frontmatter import validate_frontmatter
        content = vault.read_file(".schema/preferences.md")
        result = validate_frontmatter(".schema/preferences.md", content)
        assert result.valid


class TestRebuildIndex:
    def test_rebuild_index_with_hub(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/ml-hub.md",
            "---\ntitle: Machine Learning\ntype: hub\nsummary: ML overview\ntags: [ml]\n---\n# ML",
        )
        vault.write_file(
            "wiki/concepts/attention.md",
            "---\ntitle: Attention\ntype: canonical\nsummary: Attention mechanism\nsources: [paper.pdf]\ntags: [ml, pinned]\n---\n# Att",
        )
        content = vault.rebuild_index()
        assert "Machine Learning" in content
        assert "Attention" in content
        assert "Pinned" in content  # pinned section for tagged pages

    def test_rebuild_index_empty_vault(self, vault: Vault) -> None:
        content = vault.rebuild_index()
        assert "no hubs yet" in content

    def test_rebuild_index_excludes_archive(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/archive/old.md",
            "---\ntitle: Old Page\ntype: archive\n---\n",
        )
        content = vault.rebuild_index()
        assert "Old Page" not in content


class TestReadPartial:
    def test_read_partial(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/big.md", "A" * 5000)
        partial = vault.read_file_partial("wiki/concepts/big.md", 100)
        assert len(partial) == 100

    def test_read_partial_short_file(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/tiny.md", "short")
        partial = vault.read_file_partial("wiki/concepts/tiny.md", 1000)
        assert partial == "short"


class TestReadFrontmatters:
    def test_read_frontmatters(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/test.md",
            "---\ntitle: Test\ntype: note\nsummary: A test\ntags: [x]\n---\n# Body",
        )
        results = vault.read_frontmatters("wiki/concepts")
        assert len(results) == 1
        assert results[0]["title"] == "Test"
        assert results[0]["tags"] == ["x"]
        assert results[0]["summary"] == "A test"


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


class TestImportDirectory:
    def test_import_synthesis_to_correct_dir(self, vault: Vault, tmp_path: Path) -> None:
        """synthesis files must land in wiki/synthesis/, not wiki/concepts/."""
        import_dir = tmp_path / "ext"
        import_dir.mkdir()
        (import_dir / "my-synthesis.md").write_text(
            "---\ntitle: Cross-cutting Analysis\ntype: synthesis\n"
            "summary: A synthesis page\ntags: [test]\n---\n\n"
            "# Cross-cutting Analysis\n\nBody with [[Link A]] and [[Link B]].\n"
        )
        result = vault.import_directory(str(import_dir))
        assert "Imported 1" in result
        synthesis_files = vault.list_files("wiki/synthesis")
        assert any("my-synthesis.md" in f for f in synthesis_files)
        concept_files = vault.list_files("wiki/concepts")
        assert not any("my-synthesis.md" in f for f in concept_files)

    def test_import_note_to_concepts(self, vault: Vault, tmp_path: Path) -> None:
        import_dir = tmp_path / "ext"
        import_dir.mkdir()
        (import_dir / "my-note.md").write_text(
            "---\ntitle: A Note\ntype: note\nsummary: s\ntags: []\n---\n# Note"
        )
        result = vault.import_directory(str(import_dir))
        assert "Imported 1" in result
        assert any("my-note.md" in f for f in vault.list_files("wiki/concepts"))

    def test_import_journal_to_journals(self, vault: Vault, tmp_path: Path) -> None:
        import_dir = tmp_path / "ext"
        import_dir.mkdir()
        (import_dir / "2025-01-01.md").write_text(
            "---\ntitle: Journal 2025-01-01\ntype: journal\n"
            "summary: Daily\ntags: [journal]\n---\n# 2025-01-01"
        )
        result = vault.import_directory(str(import_dir))
        assert "Imported 1" in result
        assert any("2025-01-01.md" in f for f in vault.list_files("wiki/journals"))

    def test_import_bare_file_wrapped_as_note(self, vault: Vault, tmp_path: Path) -> None:
        import_dir = tmp_path / "ext"
        import_dir.mkdir()
        (import_dir / "raw-stuff.md").write_text("# Just some markdown\nNo frontmatter.")
        result = vault.import_directory(str(import_dir))
        assert "Imported 1" in result
        assert any("raw-stuff.md" in f for f in vault.list_files("wiki/concepts"))


class TestUpdatedTimestamp:
    def test_append_section_updates_timestamp(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        from noteweaver.frontmatter import extract_frontmatter

        page = (
            "---\ntitle: Old Page\ntype: note\nsummary: s\ntags: []\n"
            "created: 2020-01-01\nupdated: 2020-01-01\n---\n\n# Old Page\n"
        )
        vault.write_file("wiki/concepts/old.md", page)
        dispatch_tool(vault, "append_section", {
            "path": "wiki/concepts/old.md",
            "heading": "New Section",
            "content": "Added later.",
        })
        content = vault.read_file("wiki/concepts/old.md")
        fm = extract_frontmatter(content)
        assert fm["updated"] != "2020-01-01"

    def test_promote_to_existing_updates_timestamp(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        from noteweaver.frontmatter import extract_frontmatter

        page = (
            "---\ntitle: Quantum\ntype: note\nsummary: s\ntags: []\n"
            "created: 2020-01-01\nupdated: 2020-01-01\n---\n\n"
            "# Quantum\n\nIntro.\n\n## Related\n"
        )
        vault.write_file("wiki/concepts/quantum.md", page)
        dispatch_tool(vault, "promote_insight", {
            "title": "Quantum",
            "content": "New insight.",
        })
        content = vault.read_file("wiki/concepts/quantum.md")
        fm = extract_frontmatter(content)
        assert fm["updated"] != "2020-01-01"

    def test_write_file_skips_update_when_no_frontmatter(self, vault: Vault) -> None:
        raw = "# No frontmatter here\nJust text."
        vault.write_file("wiki/concepts/raw.md", raw)
        assert vault.read_file("wiki/concepts/raw.md") == raw

    def test_write_file_skips_index_and_log(self, vault: Vault) -> None:
        """index.md and log.md should not get updated timestamps."""
        original_index = vault.read_file("wiki/index.md")
        vault.write_file("wiki/index.md", original_index)
        assert vault.read_file("wiki/index.md") == original_index

    def test_add_related_link_updates_timestamp(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool
        from noteweaver.frontmatter import extract_frontmatter

        page = (
            "---\ntitle: Linked\ntype: note\nsummary: s\ntags: []\n"
            "created: 2020-01-01\nupdated: 2020-01-01\n---\n\n# Linked\n"
        )
        vault.write_file("wiki/concepts/linked.md", page)
        dispatch_tool(vault, "add_related_link", {
            "path": "wiki/concepts/linked.md",
            "title": "Other Page",
        })
        content = vault.read_file("wiki/concepts/linked.md")
        fm = extract_frontmatter(content)
        assert fm["updated"] != "2020-01-01"
