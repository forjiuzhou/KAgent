"""Tests for attended/unattended policy and write-target classification."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from noteweaver.tools.policy import (
    PolicyContext,
    WriteTarget,
    TOOL_TIERS,
    RiskTier,
    classify_write_target,
    check_pre_dispatch,
)
from noteweaver.agent import KnowledgeAgent
from noteweaver.vault import Vault


# ======================================================================
# Write-target classification
# ======================================================================

class TestClassifyWriteTarget:
    def test_meta_is_runtime(self) -> None:
        assert classify_write_target("write_page", ".meta/session-memory.md") == WriteTarget.RUNTIME

    def test_transcripts_is_runtime(self) -> None:
        assert classify_write_target("write_page", ".meta/transcripts/x.json") == WriteTarget.RUNTIME

    def test_index_is_structure(self) -> None:
        assert classify_write_target("write_page", "wiki/index.md") == WriteTarget.STRUCTURE

    def test_log_is_structure(self) -> None:
        assert classify_write_target("write_page", "wiki/log.md") == WriteTarget.STRUCTURE

    def test_restructure_is_structure_regardless_of_path(self) -> None:
        assert classify_write_target("restructure", "wiki/concepts/x.md") == WriteTarget.STRUCTURE
        assert classify_write_target("restructure", "") == WriteTarget.STRUCTURE

    def test_organize_is_content_for_wiki_path(self) -> None:
        assert classify_write_target("organize", "wiki/concepts/x.md") == WriteTarget.CONTENT

    def test_journals_is_journal(self) -> None:
        assert classify_write_target("write_page", "wiki/journals/2025-04-09.md") == WriteTarget.JOURNAL

    def test_concepts_is_content(self) -> None:
        assert classify_write_target("write_page", "wiki/concepts/attention.md") == WriteTarget.CONTENT

    def test_synthesis_is_content(self) -> None:
        assert classify_write_target("write_page", "wiki/synthesis/comparison.md") == WriteTarget.CONTENT

    def test_preferences_is_content(self) -> None:
        assert classify_write_target("write_page", ".schema/preferences.md") == WriteTarget.CONTENT

    def test_sources_is_source(self) -> None:
        assert classify_write_target("ingest", "sources/articles/x.md") == WriteTarget.SOURCE


# ======================================================================
# Tool tiers
# ======================================================================

class TestToolTiers:
    def test_read_tools(self) -> None:
        for name in (
            "read_page",
            "search",
            "survey_topic",
            "get_backlinks",
            "list_pages",
            "fetch_url",
        ):
            assert TOOL_TIERS[name] == RiskTier.READ

    def test_medium_write_tools(self) -> None:
        for name in ("capture", "ingest", "organize"):
            assert TOOL_TIERS[name] == RiskTier.MEDIUM_WRITE

    def test_high_write_tools(self) -> None:
        for name in ("restructure", "write_page"):
            assert TOOL_TIERS[name] == RiskTier.HIGH_WRITE


# ======================================================================
# Policy context — survey / navigation tracking
# ======================================================================

class TestPolicyContextRecording:
    def test_survey_topic_records_topic_and_navigation(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("survey_topic", {"topic": "Attention"})
        assert "attention" in ctx.topics_surveyed
        assert ctx.navigation_done is True

    def test_search_records_query_and_navigation(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("search", {"query": "neural networks"})
        assert "neural networks" in ctx.topics_surveyed
        assert ctx.navigation_done is True

    def test_read_page_sets_navigation_and_pages_read(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("read_page", {"path": "wiki/concepts/x.md"})
        assert ctx.navigation_done is True
        assert ctx.pages_read == ["wiki/concepts/x.md"]

    def test_list_pages_sets_navigation(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call("list_pages", {})
        assert ctx.navigation_done is True

    def test_write_page_records_pages_written(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call(
            "write_page",
            {"path": "wiki/concepts/new.md", "content": "---\n---\n"},
        )
        assert "wiki/concepts/new.md" in ctx.pages_written

    def test_capture_records_target_in_pages_written(self) -> None:
        ctx = PolicyContext()
        ctx.record_tool_call(
            "capture",
            {"content": "x", "title": "T", "target": "wiki/concepts/a.md"},
        )
        assert "wiki/concepts/a.md" in ctx.pages_written


# ======================================================================
# Attended / unattended policy
# ======================================================================

class TestAttendedPolicy:
    def test_attended_allows_content_write_after_navigation(self) -> None:
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("survey_topic", {"topic": "Tests"})
        v = check_pre_dispatch("write_page", {"path": "wiki/concepts/test.md", "content": "..."}, ctx)
        assert v.allowed

    def test_attended_blocks_new_page_without_navigation(self) -> None:
        ctx = PolicyContext(attended=True)
        v = check_pre_dispatch("write_page", {"path": "wiki/concepts/test.md", "content": "..."}, ctx)
        assert not v.allowed
        assert "survey" in (v.warning or "").lower() or "search" in (v.warning or "").lower()

    def test_unattended_blocks_content_write(self) -> None:
        ctx = PolicyContext(attended=False)
        ctx.record_tool_call("survey_topic", {"topic": "Test"})
        v = check_pre_dispatch("write_page", {"path": "wiki/concepts/test.md", "content": "..."}, ctx)
        assert not v.allowed
        assert "Promotion Candidates" in (v.warning or "")

    def test_unattended_blocks_organize_to_content(self) -> None:
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch(
            "organize",
            {"target": "wiki/concepts/attention.md", "action": "classify"},
            ctx,
        )
        assert not v.allowed

    def test_unattended_allows_journal_write(self) -> None:
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/journals/2025-04-09.md", "content": "..."},
            ctx,
        )
        assert v.allowed

    def test_unattended_allows_structure_write(self) -> None:
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch(
            "write_page",
            {"path": "wiki/index.md", "content": "..."},
            ctx,
        )
        assert v.allowed

    def test_unattended_allows_restructure(self) -> None:
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch(
            "restructure",
            {"scope": "vault", "action": "audit"},
            ctx,
        )
        assert v.allowed

    def test_unattended_blocks_ingest_that_targets_sources(self) -> None:
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch(
            "ingest",
            {"source": "https://example.com", "source_type": "url"},
            ctx,
        )
        assert not v.allowed

    def test_unattended_allows_read(self) -> None:
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch("read_page", {"path": "wiki/concepts/x.md"}, ctx)
        assert v.allowed

    def test_unattended_allows_search(self) -> None:
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch("search", {"query": "attention"}, ctx)
        assert v.allowed

    def test_unattended_allows_capture_to_journal(self) -> None:
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch(
            "capture",
            {
                "content": "note",
                "title": "Digest note",
                "target": "wiki/journals/2025-04-09.md",
            },
            ctx,
        )
        assert v.allowed


# ======================================================================
# Agent attended mode
# ======================================================================

@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


class TestAgentAttendedMode:
    def test_default_is_attended(self, vault: Vault) -> None:
        provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=provider)
        assert agent._policy_ctx.attended is True

    def test_set_attended(self, vault: Vault) -> None:
        provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=provider)
        agent.set_attended(False)
        assert agent._policy_ctx.attended is False
        agent.set_attended(True)
        assert agent._policy_ctx.attended is True


# ======================================================================
# Digest proposal scanning
# ======================================================================

class TestDigestProposalScanning:
    def test_no_proposals_when_empty(self, vault: Vault) -> None:
        provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=provider)
        assert agent._scan_pending_proposals() == ""

    def test_finds_promotion_candidates(self, vault: Vault) -> None:
        journal_content = (
            "---\ntitle: Journal 2025-04-09\ntype: journal\n"
            "summary: Daily journal\ntags: [journal]\n"
            "created: 2025-04-09\nupdated: 2025-04-09\n---\n\n"
            "# 2025-04-09\n\n"
            "### Digest session (03:00)\n\n"
            "#### Promotion Candidates\n\n"
            "- **Attention Mechanisms** (note): Key insight about multi-head "
            "attention from sessions on 04-07 and 04-08.\n"
            "- **KV Cache Optimization** (canonical): Repeated topic across 3 "
            "sessions, has external sources.\n\n"
            "### Chat session (10:00)\n\n"
            "Regular chat content here.\n"
        )
        vault.write_file("wiki/journals/2025-04-09.md", journal_content)

        provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=provider)
        proposals = agent._scan_pending_proposals()

        assert "Attention Mechanisms" in proposals
        assert "KV Cache Optimization" in proposals
        assert "2025-04-09" in proposals

    def test_trims_at_next_section(self, vault: Vault) -> None:
        journal_content = (
            "---\ntitle: Journal 2025-04-09\ntype: journal\n"
            "summary: Daily journal\ntags: [journal]\n"
            "created: 2025-04-09\nupdated: 2025-04-09\n---\n\n"
            "#### Promotion Candidates\n\n"
            "- **Topic A** (note): Some insight\n\n"
            "### Next Section\n\n"
            "This should not appear in proposals.\n"
        )
        vault.write_file("wiki/journals/2025-04-09.md", journal_content)

        provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=provider)
        proposals = agent._scan_pending_proposals()

        assert "Topic A" in proposals
        assert "should not appear" not in proposals

    def test_proposals_injected_into_query_view(self, vault: Vault) -> None:
        journal_content = (
            "---\ntitle: Journal 2025-04-09\ntype: journal\n"
            "summary: Daily journal\ntags: [journal]\n"
            "created: 2025-04-09\nupdated: 2025-04-09\n---\n\n"
            "#### Promotion Candidates\n\n"
            "- **Test Insight** (note): Worth promoting\n"
        )
        vault.write_file("wiki/journals/2025-04-09.md", journal_content)

        provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=provider)
        agent.messages.append({"role": "user", "content": "hello"})

        query = agent._build_messages_for_query()
        system = query[0]["content"]
        assert "Pending Promotion Candidates" in system
        assert "Test Insight" in system

    def test_proposals_not_injected_when_unattended(self, vault: Vault) -> None:
        journal_content = (
            "---\ntitle: Journal 2025-04-09\ntype: journal\n"
            "summary: Daily journal\ntags: [journal]\n"
            "created: 2025-04-09\nupdated: 2025-04-09\n---\n\n"
            "#### Promotion Candidates\n\n"
            "- **Test Insight** (note): Worth promoting\n"
        )
        vault.write_file("wiki/journals/2025-04-09.md", journal_content)

        provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=provider)
        agent.set_attended(False)
        agent.messages.append({"role": "user", "content": "hello"})

        query = agent._build_messages_for_query()
        system = query[0]["content"]
        assert "Pending Promotion Candidates" not in system
