"""Tests for the runtime policy layer (tools/policy.py)."""

from __future__ import annotations

import pytest

from noteweaver.tools.policy import (
    PolicyContext,
    PolicyVerdict,
    RiskTier,
    TOOL_TIERS,
    check_pre_dispatch,
)


class TestToolTierMapping:
    def test_all_read_tools_are_read_tier(self) -> None:
        read_tools = [
            "read_page", "list_page_summaries", "search_vault",
            "vault_stats", "get_backlinks", "find_existing_page",
            "read_transcript", "fetch_url",
        ]
        for tool in read_tools:
            assert TOOL_TIERS[tool] == RiskTier.READ, f"{tool} should be READ"

    def test_write_page_is_high_write(self) -> None:
        assert TOOL_TIERS["write_page"] == RiskTier.HIGH_WRITE

    def test_fine_grained_tools_are_low_write(self) -> None:
        low_tools = [
            "append_to_section", "update_frontmatter",
            "add_related_link", "append_log",
        ]
        for tool in low_tools:
            assert TOOL_TIERS[tool] == RiskTier.LOW_WRITE, f"{tool} should be LOW_WRITE"

    def test_promote_insight_is_medium_write(self) -> None:
        assert TOOL_TIERS["promote_insight"] == RiskTier.MEDIUM_WRITE


class TestPolicyContext:
    def test_record_find_existing_page(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("find_existing_page", {"title": "Test Page"})
        assert "test page" in ctx.dedup_checked_titles
        assert "find_existing_page" in ctx.tools_called

    def test_record_read_page(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("read_page", {"path": "wiki/index.md"})
        assert "wiki/index.md" in ctx.pages_read
        assert ctx.navigation_done

    def test_record_write_page(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("write_page", {"path": "wiki/concepts/test.md"})
        assert "wiki/concepts/test.md" in ctx.pages_written

    def test_navigation_done_from_list(self) -> None:
        ctx = PolicyContext()
        assert not ctx.navigation_done
        ctx.record_tool_call("list_page_summaries", {"directory": "wiki"})
        assert ctx.navigation_done

    def test_navigation_done_from_search(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("search_vault", {"query": "test"})
        assert ctx.navigation_done

    def test_no_duplicate_entries(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("read_page", {"path": "wiki/x.md"})
        ctx.record_tool_call("read_page", {"path": "wiki/x.md"})
        assert ctx.pages_read.count("wiki/x.md") == 1


class TestCheckPreDispatch:
    def test_read_always_allowed(self) -> None:
        ctx = PolicyContext()
        v = check_pre_dispatch("read_page", {"path": "wiki/index.md"}, ctx)
        assert v.allowed

    def test_low_write_structure_always_allowed(self) -> None:
        """Structure-targeting low writes pass without prior read."""
        ctx = PolicyContext()
        v = check_pre_dispatch("append_log", {"entry_type": "test", "title": "x"}, ctx)
        assert v.allowed

    def test_low_write_content_needs_read(self) -> None:
        """Content-targeting low writes need read-before-write."""
        ctx = PolicyContext()
        v = check_pre_dispatch("update_frontmatter", {"path": "wiki/concepts/x.md"}, ctx)
        assert not v.allowed
        ctx.record_tool_call("read_page", {"path": "wiki/concepts/x.md"})
        v = check_pre_dispatch("update_frontmatter", {"path": "wiki/concepts/x.md"}, ctx)
        assert v.allowed

    def test_write_page_blocked_without_dedup(self) -> None:
        ctx = PolicyContext()
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/concepts/new-topic.md", "content": "..."},
            ctx,
        )
        assert not v.allowed
        assert "find_existing_page" in (v.warning or "")

    def test_write_page_allowed_after_dedup(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("find_existing_page", {"title": "New Topic"})
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/concepts/new-topic.md", "content": "..."},
            ctx,
        )
        assert v.allowed

    def test_write_page_allowed_for_known_page(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("read_page", {"path": "wiki/concepts/existing.md"})
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/concepts/existing.md", "content": "..."},
            ctx,
        )
        assert v.allowed

    def test_write_page_allowed_for_previously_written(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("write_page", {"path": "wiki/concepts/x.md"})
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/concepts/x.md", "content": "updated"},
            ctx,
        )
        assert v.allowed

    def test_write_index_exempt(self) -> None:
        ctx = PolicyContext()
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/index.md", "content": "..."},
            ctx,
        )
        assert v.allowed

    def test_write_log_exempt(self) -> None:
        ctx = PolicyContext()
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/log.md", "content": "..."},
            ctx,
        )
        assert v.allowed

    def test_promote_insight_allowed_without_dedup(self) -> None:
        """promote_insight has built-in dedup, no need for external check."""
        ctx = PolicyContext()
        v = check_pre_dispatch(
            "promote_insight",
            {"title": "test", "content": "insight text"},
            ctx,
        )
        assert v.allowed

    def test_unknown_tool_defaults_medium(self) -> None:
        ctx = PolicyContext()
        v = check_pre_dispatch("unknown_tool", {}, ctx)
        assert v.allowed
