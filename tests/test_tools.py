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
        assert len(TOOL_SCHEMAS) == 7


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

    def test_search_vault(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/ai.md", "---\ntitle: AI\ntype: note\n---\n# AI\nNeural networks")
        result = dispatch_tool(vault, "search_vault", {"query": "neural"})
        assert "ai.md" in result

    def test_search_vault_no_results(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "search_vault", {"query": "xyznonexistent"})
        assert "No results" in result

    def test_list_pages(self, vault: Vault) -> None:
        vault.write_file("wiki/concepts/a.md", "---\ntitle: A\ntype: note\n---\na")
        result = dispatch_tool(vault, "list_pages", {"directory": "wiki/concepts"})
        assert "a.md" in result

    def test_list_pages_empty(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "list_pages", {"directory": "wiki/concepts"})
        assert "No markdown files" in result

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
