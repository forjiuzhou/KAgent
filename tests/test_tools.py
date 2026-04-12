"""Tests for tool definitions and dispatch."""

from unittest.mock import MagicMock, patch

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
        # Handlers may include legacy tools not exposed in current schemas.
        assert schema_names <= handler_names

    def test_schema_count(self) -> None:
        assert len(TOOL_SCHEMAS) == 9


class TestDispatch:
    def test_read_page(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "read_page", {"path": "wiki/index.md"})
        assert "Wiki Index" in result

    def test_read_page_not_found(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "read_page", {"path": "wiki/nope.md"})
        assert "Error" in result

    def test_read_page_meta_path(self, vault: Vault) -> None:
        """Transcript-style paths are read via read_page like any other file."""
        meta_dir = vault.root / ".meta" / "sessions"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / "t.json").write_text('{"hello": true}', encoding="utf-8")
        result = dispatch_tool(
            vault, "read_page", {"path": ".meta/sessions/t.json"},
        )
        assert "hello" in result

    def test_read_page_section(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/sections.md",
            "---\ntitle: Sections\ntype: note\n---\n"
            "# Sections\n\nIntro.\n\n## Alpha\n\nAlpha body.\n\n## Beta\n\nBeta body.\n",
        )
        result = dispatch_tool(
            vault, "read_page",
            {"path": "wiki/concepts/sections.md", "section": "Alpha"},
        )
        assert "## Alpha" in result
        assert "Alpha body" in result
        assert "Beta body" not in result

    def test_read_page_section_missing(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/nosec.md",
            "---\ntitle: NoSec\ntype: note\n---\n# Only\n",
        )
        result = dispatch_tool(
            vault, "read_page",
            {"path": "wiki/concepts/nosec.md", "section": "Missing"},
        )
        assert "Error" in result
        assert "not found" in result.lower()

    def test_write_page(self, vault: Vault) -> None:
        content = "---\ntitle: Test\ntype: note\n---\n# Test"
        result = dispatch_tool(
            vault,
            "write_page",
            {"path": "wiki/concepts/test.md", "content": content},
        )
        assert "OK" in result
        assert vault.read_file("wiki/concepts/test.md") == content

    def test_write_page_rejects_bad_frontmatter(self, vault: Vault) -> None:
        result = dispatch_tool(
            vault,
            "write_page",
            {"path": "wiki/concepts/bad.md", "content": "# No frontmatter"},
        )
        assert "Error" in result
        assert "frontmatter" in result.lower()

    def test_write_page_sources_blocked(self, vault: Vault) -> None:
        result = dispatch_tool(
            vault,
            "write_page",
            {"path": "sources/evil.md", "content": "bad"},
        )
        assert "Error" in result

    def test_write_page_rejects_schema_path(self, vault: Vault) -> None:
        result = dispatch_tool(
            vault,
            "write_page",
            {"path": ".schema/schema.md", "content": "overwritten"},
        )
        assert "Error" in result
        assert "wiki/" in result
        original = vault.read_file(".schema/schema.md")
        assert "Wiki Schema" in original

    def test_write_page_rejects_meta_path(self, vault: Vault) -> None:
        result = dispatch_tool(
            vault,
            "write_page",
            {"path": ".meta/config.yaml", "content": "evil: true"},
        )
        assert "Error" in result
        assert "wiki/" in result

    def test_write_page_rejects_root_path(self, vault: Vault) -> None:
        result = dispatch_tool(
            vault,
            "write_page",
            {"path": "random.md", "content": "# Nope"},
        )
        assert "Error" in result
        assert "wiki/" in result

    def test_search_keyword_hit(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/ai.md",
            "---\ntitle: AI\ntype: note\n---\n# AI\nNeural networks",
        )
        vault.rebuild_search_index()
        result = dispatch_tool(vault, "search", {"query": "neural"})
        assert "ai.md" in result

    def test_search_no_results(self, vault: Vault) -> None:
        result = dispatch_tool(
            vault, "search", {"query": "xyznonexistent"},
        )
        assert "No results" in result

    def test_search_scope_wiki(self, vault: Vault) -> None:
        vault.save_source("sources/articles/ext.md", "# External\nsecret-token-abc")
        vault.write_file(
            "wiki/concepts/w.md",
            "---\ntitle: W\ntype: note\n---\n# W\nsecret-token-abc",
        )
        vault.rebuild_search_index()
        wiki_only = dispatch_tool(
            vault, "search", {"query": "secret-token-abc", "scope": "wiki"},
        )
        assert "wiki/" in wiki_only
        assert "sources/" not in wiki_only

    def test_search_title_near_match(self, vault: Vault) -> None:
        """Title similarity supplements FTS (replaces find_existing_page)."""
        vault.write_file(
            "wiki/concepts/zetaflux.md",
            "---\ntitle: Zeta Flux Engine\ntype: note\n---\n# Zeta Flux Engine\n"
            "No shared keywords with query.",
        )
        vault.rebuild_search_index()
        result = dispatch_tool(vault, "search", {"query": "zeta flux"})
        assert "Title matches" in result or "Zeta" in result

    def test_survey_topic(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/ml.md",
            "---\ntitle: Machine Learning\ntype: note\n"
            "summary: ML basics\ntags: [ml, ai]\n---\n# ML\n[[Deep Learning]]",
        )
        vault.rebuild_search_index()
        result = dispatch_tool(vault, "survey_topic", {"topic": "machine learning"})
        assert "Topic Survey" in result or "machine learning" in result.lower()
        assert "Machine Learning" in result or "ml.md" in result

    def test_get_backlinks(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/target.md",
            "---\ntitle: Target Page\ntype: note\n---\n# Target Page\n",
        )
        vault.write_file(
            "wiki/concepts/linker.md",
            "---\ntitle: Linker\ntype: note\n---\n# Linker\nSee [[Target Page]].\n",
        )
        vault.rebuild_index()
        result = dispatch_tool(
            vault, "get_backlinks", {"title": "Target Page"},
        )
        assert "linker" in result.lower() or "Linker" in result

    def test_get_backlinks_empty(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "get_backlinks", {"title": "Nobody"})
        assert "No pages link" in result

    def test_list_pages_summaries(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/ai.md",
            "---\ntitle: AI\ntype: hub\nsummary: Artificial intelligence overview\n"
            "tags: [ai, ml]\n---\n# AI",
        )
        result = dispatch_tool(vault, "list_pages", {"directory": "wiki"})
        assert "AI" in result
        assert "hub" in result
        assert "ai, ml" in result or "ai" in result
        assert "Artificial intelligence overview" in result

    def test_list_pages_page_card_format(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/test.md",
            "---\ntitle: Test Page\ntype: note\nsummary: A test note\n"
            "tags: [testing, demo]\nupdated: 2026-04-12\n---\n# Test Page",
        )
        result = dispatch_tool(vault, "list_pages", {"directory": "wiki"})
        assert "Page cards for" in result
        assert "Test Page" in result
        assert "note" in result
        assert "A test note" in result
        assert "testing" in result
        assert "2026-04-12" in result

    def test_list_pages_empty_subdir(self, vault: Vault) -> None:
        result = dispatch_tool(
            vault, "list_pages", {"directory": "wiki/concepts"},
        )
        # Default vault has index/log under wiki/ but concepts may be empty of md
        # after filtering — at least handler returns a string
        assert isinstance(result, str)

    def test_list_pages_include_raw_directory_listing(self, vault: Vault) -> None:
        """include_raw replaces list_directory raw file listing."""
        src_dir = vault.root / "sources" / "typora"
        src_dir.mkdir(parents=True)
        (src_dir / "note1.md").write_text("# Plain markdown, no frontmatter")
        (src_dir / "note2.md").write_text("Another plain file")
        (src_dir / "image.png").write_bytes(b"\x89PNG\r\n")
        result = dispatch_tool(
            vault,
            "list_pages",
            {"directory": "sources/typora", "include_raw": True},
        )
        assert "3 total" in result
        assert "note1.md" in result
        assert "note2.md" in result
        assert "image.png" in result

    def test_list_pages_include_raw_empty_dir(self, vault: Vault) -> None:
        empty_dir = vault.root / "sources" / "empty"
        empty_dir.mkdir(parents=True)
        result = dispatch_tool(
            vault,
            "list_pages",
            {"directory": "sources/empty", "include_raw": True},
        )
        assert "No files" in result

    def test_list_pages_include_raw_not_found(self, vault: Vault) -> None:
        result = dispatch_tool(
            vault,
            "list_pages",
            {"directory": "nonexistent", "include_raw": True},
        )
        assert "Error" in result or "not found" in result

    def test_list_pages_shows_no_frontmatter_files(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/good.md",
            "---\ntitle: Good\ntype: note\n---\n# Good page",
        )
        raw_path = vault.root / "wiki" / "concepts" / "raw.md"
        raw_path.write_text("# Just plain markdown, no frontmatter")
        result = dispatch_tool(vault, "list_pages", {"directory": "wiki"})
        assert "Good" in result
        assert "without frontmatter" in result
        assert "wiki/concepts/raw.md" in result

    def test_fetch_url_mocked(self, vault: Vault) -> None:
        html = (
            "<html><head><title>Example</title></head>"
            "<body><article><p>Hello from article</p></article></body></html>"
        )
        mock_resp = MagicMock()
        mock_resp.headers = {"content-type": "text/html; charset=utf-8"}
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()
        with patch("httpx.get", return_value=mock_resp):
            result = dispatch_tool(
                vault, "fetch_url", {"url": "https://example.com/doc"},
            )
        assert "Error" not in result[:80]
        assert "Hello" in result or "hello" in result.lower()

    def test_capture_new_page(self, vault: Vault) -> None:
        result = dispatch_tool(
            vault,
            "capture",
            {
                "title": "Fresh Note",
                "content": "Body **markdown** here.",
                "tags": ["idea"],
            },
        )
        assert "OK" in result
        assert "wiki/concepts" in result
        created = vault.read_file("wiki/concepts/fresh-note.md")
        assert "Fresh Note" in created
        assert "Body **markdown** here." in created

    def test_capture_append_to_existing(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/base.md",
            "---\ntitle: Base\ntype: note\ntags: [x]\n---\n"
            "# Base\n\nOrig.\n\n## Related\n",
        )
        result = dispatch_tool(
            vault,
            "capture",
            {
                "title": "New Section",
                "content": "Appended paragraph.",
                "target": "wiki/concepts/base.md",
                "tags": ["y"],
            },
        )
        assert "OK" in result
        assert "appended" in result.lower()
        body = vault.read_file("wiki/concepts/base.md")
        assert "New Section" in body
        assert "Appended paragraph." in body

    def test_ingest_directory(self, vault: Vault, tmp_path: Path) -> None:
        import_dir = tmp_path / "ext_notes"
        import_dir.mkdir()
        (import_dir / "note1.md").write_text("# My Note\nSome content")
        (import_dir / "note2.md").write_text(
            "---\ntitle: Existing\ntype: note\nsummary: s\n---\n# Existing"
        )
        result = dispatch_tool(
            vault,
            "ingest",
            {"source": str(import_dir), "source_type": "directory"},
        )
        assert "Imported" in result
        assert vault.list_files("wiki/concepts")

    def test_ingest_directory_bad_dir(self, vault: Vault) -> None:
        result = dispatch_tool(
            vault,
            "ingest",
            {"source": "/nonexistent-dir-xyz", "source_type": "directory"},
        )
        assert "Error" in result or "not a directory" in result

    def test_ingest_file(self, vault: Vault, tmp_path: Path) -> None:
        f = tmp_path / "single.md"
        f.write_text("# One-off\nContent here.", encoding="utf-8")
        result = dispatch_tool(
            vault,
            "ingest",
            {"source": str(f), "source_type": "file"},
        )
        assert "Read:" in result or "chars" in result

    def test_organize_archive(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/old.md",
            "---\ntitle: Old\ntype: note\n---\n# Old",
        )
        result = dispatch_tool(
            vault,
            "organize",
            {
                "target": "wiki/concepts/old.md",
                "action": "archive",
                "reason": "replaced by new version",
            },
        )
        assert "OK" in result
        assert "archive" in result.lower()
        archived = vault.read_file("wiki/archive/old.md")
        assert "archive" in archived.lower() or "Old" in archived

    def test_organize_update_metadata(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/meta.md",
            "---\ntitle: Meta\ntype: note\nsummary: old\n---\n# Meta\n",
        )
        result = dispatch_tool(
            vault,
            "organize",
            {
                "target": "wiki/concepts/meta.md",
                "action": "update_metadata",
                "metadata": {"summary": "new summary text"},
            },
        )
        assert "OK" in result
        body = vault.read_file("wiki/concepts/meta.md")
        assert "new summary text" in body

    def test_organize_link(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/a.md",
            "---\ntitle: Page A\ntype: note\n---\n# Page A\n\n## Related\n",
        )
        result = dispatch_tool(
            vault,
            "organize",
            {
                "target": "wiki/concepts/a.md",
                "action": "link",
                "link_to": "Page B",
            },
        )
        assert "OK" in result
        assert "[[Page B]]" in vault.read_file("wiki/concepts/a.md")

    def test_restructure_merge_tags(self, vault: Vault) -> None:
        vault.write_file(
            "wiki/concepts/t1.md",
            "---\ntitle: T1\ntype: note\ntags: [foo, bar]\n---\n# T1\n",
        )
        vault.write_file(
            "wiki/concepts/t2.md",
            "---\ntitle: T2\ntype: note\ntags: [foo]\n---\n# T2\n",
        )
        result = dispatch_tool(
            vault,
            "restructure",
            {
                "scope": "vault",
                "action": "merge_tags",
                "old_tag": "foo",
                "new_tag": "baz",
            },
        )
        assert "OK" in result
        assert "baz" in vault.read_file("wiki/concepts/t1.md")

    def test_restructure_audit(self, vault: Vault) -> None:
        result = dispatch_tool(
            vault,
            "restructure",
            {"scope": "vault", "action": "audit"},
        )
        assert "Audit" in result or "audit" in result.lower()
        assert "issues" in result.lower() or "0 issues" in result

    def test_unknown_tool(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "rm_rf_slash", {})
        assert "unknown tool" in result

    def test_extra_args_ignored(self, vault: Vault) -> None:
        result = dispatch_tool(
            vault,
            "read_page",
            {"path": "wiki/index.md", "extra_evil_param": "haha"},
        )
        assert "Wiki Index" in result

    def test_read_page_partial(self, vault: Vault) -> None:
        long_content = (
            "---\ntitle: Long\ntype: note\n---\n# Long Page\n" + "x" * 5000
        )
        vault.write_file("wiki/concepts/long.md", long_content)
        result = dispatch_tool(
            vault,
            "read_page",
            {"path": "wiki/concepts/long.md", "max_chars": 100},
        )
        assert len(result) < 5000
        assert "truncated" in result

    def test_write_page_index_budget_warning(self, vault: Vault) -> None:
        big_index = "# Index\n" + "- hub " * 2000
        result = dispatch_tool(
            vault,
            "write_page",
            {"path": "wiki/index.md", "content": big_index},
        )
        assert "Warning" in result
