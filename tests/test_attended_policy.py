"""Tests for attended/unattended policy and write-target classification."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from noteweaver.tools.policy import (
    PolicyContext,
    PolicyVerdict,
    WriteTarget,
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
        assert classify_write_target("append_log", "wiki/log.md") == WriteTarget.STRUCTURE

    def test_append_log_is_structure_regardless_of_path(self) -> None:
        assert classify_write_target("append_log", "") == WriteTarget.STRUCTURE

    def test_add_related_link_is_structure(self) -> None:
        assert classify_write_target("add_related_link", "wiki/concepts/x.md") == WriteTarget.STRUCTURE

    def test_journals_is_journal(self) -> None:
        assert classify_write_target("write_page", "wiki/journals/2025-04-09.md") == WriteTarget.JOURNAL

    def test_concepts_is_content(self) -> None:
        assert classify_write_target("write_page", "wiki/concepts/attention.md") == WriteTarget.CONTENT

    def test_synthesis_is_content(self) -> None:
        assert classify_write_target("append_section", "wiki/synthesis/comparison.md") == WriteTarget.CONTENT

    def test_preferences_is_content(self) -> None:
        assert classify_write_target("write_page", ".schema/preferences.md") == WriteTarget.CONTENT

    def test_sources_is_source(self) -> None:
        assert classify_write_target("save_source", "sources/articles/x.md") == WriteTarget.SOURCE


# ======================================================================
# Attended / unattended policy
# ======================================================================

class TestAttendedPolicy:
    def test_attended_allows_content_write(self) -> None:
        ctx = PolicyContext(attended=True)
        ctx.record_tool_call("find_existing_page", {"title": "Test"})
        v = check_pre_dispatch("write_page", {"path": "wiki/concepts/test.md", "content": "..."}, ctx)
        assert v.allowed

    def test_unattended_blocks_content_write(self) -> None:
        ctx = PolicyContext(attended=False)
        ctx.record_tool_call("find_existing_page", {"title": "Test"})
        v = check_pre_dispatch("write_page", {"path": "wiki/concepts/test.md", "content": "..."}, ctx)
        assert not v.allowed
        assert "Promotion Candidates" in (v.warning or "")

    def test_unattended_blocks_append_section_to_content(self) -> None:
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch(
            "append_section",
            {"path": "wiki/concepts/attention.md", "heading": "New", "content": "..."},
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

    def test_unattended_allows_log(self) -> None:
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch(
            "append_log",
            {"entry_type": "digest", "title": "test"},
            ctx,
        )
        assert v.allowed

    def test_unattended_blocks_source_write(self) -> None:
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch(
            "save_source",
            {"path": "sources/articles/x.md", "content": "..."},
            ctx,
        )
        assert not v.allowed

    def test_unattended_blocks_promote_insight(self) -> None:
        """promote_insight writes to content layer, should be blocked."""
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch(
            "promote_insight",
            {"title": "test", "content": "insight"},
            ctx,
        )
        assert not v.allowed

    def test_unattended_allows_read(self) -> None:
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch("read_page", {"path": "wiki/concepts/x.md"}, ctx)
        assert v.allowed

    def test_unattended_allows_search(self) -> None:
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch("search_vault", {"query": "attention"}, ctx)
        assert v.allowed

    def test_unattended_allows_related_link(self) -> None:
        """add_related_link is structure maintenance, not content."""
        ctx = PolicyContext(attended=False)
        v = check_pre_dispatch(
            "add_related_link",
            {"path": "wiki/concepts/x.md", "title": "Y"},
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
