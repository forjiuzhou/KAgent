"""Tests for tool definitions and dispatch."""

import pytest
from pathlib import Path
from noteweaver.vault import Vault
from noteweaver.tools.definitions import (
    TOOL_SCHEMAS,
    TOOL_HANDLERS,
    dispatch_tool,
)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


class TestToolSchemas:
    def test_all_schemas_have_required_fields(self) -> None:
        for schema in TOOL_SCHEMAS:
            assert schema["type"] == "function"
            fn = schema["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn

    def test_every_schema_has_a_handler(self) -> None:
        schema_names = {s["function"]["name"] for s in TOOL_SCHEMAS}
        handler_names = set(TOOL_HANDLERS.keys())
        assert schema_names == handler_names

    def test_schema_count(self) -> None:
        assert len(TOOL_SCHEMAS) == 10


class TestDispatch:
    def test_read_page(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "read_page", {"path": "wiki/index.md"})
        assert "Wiki Index" in result

    def test_read_page_not_found(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "read_page", {"path": "wiki/nope.md"})
        assert "Error" in result

    def test_write_page(self, vault: Vault) -> None:
        content = "---\ntitle: Test\ntype: note\n---\n# Test"
        result = dispatch_tool(vault, "write_page", {
            "path": "wiki/concepts/test.md",
            "content": content,
        })
        assert "OK" in result
        assert vault.read_file("wiki/concepts/test.md") == content

    def test_write_page_rejects_bad_frontmatter(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "write_page", {
            "path": "wiki/concepts/bad.md",
            "content": "# No frontmatter",
        })
        assert "Error" in result
        assert "frontmatter" in result.lower()

    def test_write_page_sources_blocked(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "write_page", {
            "path": "sources/evil.md",
            "content": "bad",
        })
        assert "Error" in result

    def test_write_page_rejects_schema_path(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "write_page", {
            "path": ".schema/schema.md",
            "content": "overwritten",
        })
        assert "Error" in result
        assert "wiki/" in result
        original = vault.read_file(".schema/schema.md")
        assert "Vault Schema" in original

    def test_write_page_rejects_meta_path(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "write_page", {
            "path": ".meta/config.yaml",
            "content": "evil: true",
        })
        assert "Error" in result
        assert "wiki/" in result

    def test_write_page_rejects_root_path(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "write_page", {
            "path": "random.md",
            "content": "# Nope",
        })
        assert "Error" in result
        assert "wiki/" in result

    def test_search_vault(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/ai.md", "---\ntitle: AI\ntype: note\n---\n# AI\nNeural networks")
        result = dispatch_tool(vault, "search_vault", {"query": "neural"})
        assert "ai.md" in result

    def test_search_vault_no_results(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "search_vault", {"query": "xyznonexistent"})
        assert "No results" in result

    def test_append_log(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "append_log", {
            "entry_type": "test",
            "title": "Dispatch Test",
        })
        assert "OK" in result
        log = vault.read_file("wiki/log.md")
        assert "Dispatch Test" in log

    def test_unknown_tool(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "rm_rf_slash", {})
        assert "unknown tool" in result

    def test_extra_args_ignored(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "read_page", {
            "path": "wiki/index.md",
            "extra_evil_param": "haha",
        })
        assert "Wiki Index" in result

    def test_read_page_partial(self, vault: Vault) -> None:
        long_content = "---\ntitle: Long\ntype: note\n---\n# Long Page\n" + "x" * 5000
        vault.write_file("wiki/concepts/long.md", long_content)
        result = dispatch_tool(vault, "read_page", {
            "path": "wiki/concepts/long.md",
            "max_chars": 100,
        })
        assert len(result) < 200
        assert "truncated" in result

    def test_list_page_summaries(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/ai.md",
            "---\ntitle: AI\ntype: hub\nsummary: Artificial intelligence overview\ntags: [ai, ml]\n---\n# AI",
        )
        result = dispatch_tool(vault, "list_page_summaries", {"directory": "wiki"})
        assert "AI" in result
        assert "hub" in result
        assert "ai, ml" in result
        assert "Artificial intelligence overview" in result

    def test_list_page_summaries_empty(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "list_page_summaries", {"directory": "wiki/concepts"})
        assert "No pages" in result

    def test_write_page_index_budget_warning(self, vault: Vault) -> None:
        big_index = "# Index\n" + "- hub " * 2000
        result = dispatch_tool(vault, "write_page", {
            "path": "wiki/index.md",
            "content": big_index,
        })
        assert "Warning" in result

    def test_vault_stats_empty(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "vault_stats", {})
        assert "empty" in result.lower()

    def test_vault_stats_with_pages(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/test.md",
            "---\ntitle: Test\ntype: canonical\nsummary: x\nsources: [a]\n---\n# T",
        )
        result = dispatch_tool(vault, "vault_stats", {})
        assert "Total Pages" in result
        assert "Canonicals" in result

    def test_import_files(self, vault: Vault, tmp_path: Path) -> None:
        import_dir = tmp_path / "ext_notes"
        import_dir.mkdir()
        (import_dir / "note1.md").write_text("# My Note\nSome content")
        (import_dir / "note2.md").write_text(
            "---\ntitle: Existing\ntype: note\nsummary: s\n---\n# Existing"
        )
        result = dispatch_tool(vault, "import_files", {"directory": str(import_dir)})
        assert "Imported 2" in result
        assert vault.list_files("wiki/concepts")

    def test_import_files_bad_dir(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "import_files", {"directory": "/nonexistent"})
        assert "Error" in result or "not a directory" in result

    def test_save_source(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "save_source", {
            "path": "sources/articles/test.md",
            "content": "# Raw Article Content",
        })
        assert "OK" in result
        assert "immutable" in result
        assert vault.read_file("sources/articles/test.md") == "# Raw Article Content"

    def test_save_source_no_overwrite(self, vault: Vault) -> None:
        vault.save_source("sources/x.md", "original")
        result = dispatch_tool(vault, "save_source", {
            "path": "sources/x.md",
            "content": "overwrite attempt",
        })
        assert "Error" in result
        assert vault.read_file("sources/x.md") == "original"

    def test_archive_page(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/old.md", "---\ntitle: Old\ntype: note\n---\n# Old")
        result = dispatch_tool(vault, "archive_page", {
            "path": "wiki/concepts/old.md",
            "reason": "replaced by new version",
        })
        assert "OK" in result
        assert "archive" in result
        archived = vault.read_file("wiki/archive/old.md")
        assert "archive" in archived
