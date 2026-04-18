"""Tests for the runtime policy layer (tools/policy.py)."""

from __future__ import annotations

import pytest

from noteweaver.tools.policy import (
    PolicyContext,
    RiskTier,
    TOOL_TIERS,
    check_pre_dispatch,
)


class TestToolTierMapping:
    def test_observation_tools_are_read_tier(self) -> None:
        read_tools = [
            "read_page",
            "search",
            "survey_topic",
            "get_backlinks",
            "list_pages",
            "fetch_url",
        ]
        for tool in read_tools:
            assert TOOL_TIERS[tool] == RiskTier.READ, f"{tool} should be READ"

    def test_action_tools_medium_write(self) -> None:
        for tool in ("capture", "ingest", "organize"):
            assert TOOL_TIERS[tool] == RiskTier.MEDIUM_WRITE, f"{tool} should be MEDIUM_WRITE"

    def test_restructure_and_write_page_high_write(self) -> None:
        assert TOOL_TIERS["restructure"] == RiskTier.HIGH_WRITE
        assert TOOL_TIERS["write_page"] == RiskTier.HIGH_WRITE


class TestPolicyContext:
    def test_record_survey_topic(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("survey_topic", {"topic": "Test Page"})
        assert "test page" in ctx.topics_surveyed
        assert "survey_topic" in ctx.tools_called

    def test_record_read_page(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("read_page", {"path": "wiki/index.md"})
        assert "wiki/index.md" in ctx.pages_read
        assert ctx.navigation_done

    def test_record_write_page(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("write_page", {"path": "wiki/concepts/test.md"})
        assert "wiki/concepts/test.md" in ctx.pages_written

    def test_record_capture_target(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("capture", {
            "target": "wiki/concepts/x.md",
            "title": "S",
            "content": "c",
        })
        assert "wiki/concepts/x.md" in ctx.pages_written

    def test_navigation_done_from_list_pages(self) -> None:
        ctx = PolicyContext()
        assert not ctx.navigation_done
        ctx.record_tool_call("list_pages", {"directory": "wiki"})
        assert ctx.navigation_done

    def test_navigation_done_from_search(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("search", {"query": "test"})
        assert ctx.navigation_done

    def test_no_duplicate_read_entries(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("read_page", {"path": "wiki/x.md"})
        ctx.record_tool_call("read_page", {"path": "wiki/x.md"})
        assert ctx.pages_read.count("wiki/x.md") == 1


class TestCheckPreDispatch:
    def test_read_always_allowed(self) -> None:
        ctx = PolicyContext()
        v = check_pre_dispatch("read_page", {"path": "wiki/index.md"}, ctx)
        assert v.allowed

    def test_organize_update_metadata_needs_read(self) -> None:
        ctx = PolicyContext()
        v = check_pre_dispatch("organize", {
            "target": "wiki/concepts/x.md",
            "action": "update_metadata",
            "metadata": {"tags": ["a"]},
        }, ctx)
        assert not v.allowed
        ctx.record_tool_call("read_page", {"path": "wiki/concepts/x.md"})
        v = check_pre_dispatch("organize", {
            "target": "wiki/concepts/x.md",
            "action": "update_metadata",
            "metadata": {"tags": ["a"]},
        }, ctx)
        assert v.allowed

    def test_organize_link_needs_read(self) -> None:
        ctx = PolicyContext()
        v = check_pre_dispatch("organize", {
            "target": "wiki/concepts/x.md",
            "action": "link",
            "link_to": "Other",
        }, ctx)
        assert not v.allowed
        ctx.record_tool_call("read_page", {"path": "wiki/concepts/x.md"})
        v = check_pre_dispatch("organize", {
            "target": "wiki/concepts/x.md",
            "action": "link",
            "link_to": "Other",
        }, ctx)
        assert v.allowed

    def test_write_page_blocked_without_navigation(self) -> None:
        ctx = PolicyContext()
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/concepts/new-topic.md", "content": "..."},
            ctx,
        )
        assert not v.allowed
        assert "survey_topic" in (v.warning or "") or "search" in (v.warning or "")

    def test_write_page_allowed_after_survey(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("survey_topic", {"topic": "New Topic"})
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

    def test_capture_allowed_without_prior_read(self) -> None:
        ctx = PolicyContext()
        v = check_pre_dispatch(
            "capture",
            {"title": "test", "content": "insight text"},
            ctx,
        )
        assert v.allowed

    def test_unknown_tool_defaults_medium(self) -> None:
        ctx = PolicyContext()
        v = check_pre_dispatch("unknown_tool", {}, ctx)
        assert v.allowed

    def test_create_job_always_allowed(self) -> None:
        ctx = PolicyContext(attended=True)
        v = check_pre_dispatch("create_job", {
            "description": "test", "goal": "g", "criteria": ["c"],
        }, ctx)
        assert v.allowed

    def test_create_job_allowed_unattended(self) -> None:
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch("create_job", {
            "description": "test", "goal": "g", "criteria": ["c"],
        }, ctx)
        assert v.allowed
