"""Tests for prompt management: compression, trimming, prompt structure."""

from __future__ import annotations
from unittest.mock import MagicMock
from pathlib import Path

import pytest
from noteweaver.agent import KnowledgeAgent, PROMPT_IDENTITY, PROMPT_TOOLS, SYSTEM_PROMPT
from noteweaver.vault import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


@pytest.fixture
def agent(vault: Vault) -> KnowledgeAgent:
    mock_provider = MagicMock()
    return KnowledgeAgent(vault=vault, provider=mock_provider)


class TestPromptStructure:
    def test_identity_has_mission(self) -> None:
        assert "Your Mission" in PROMPT_IDENTITY
        assert "primary asset" in PROMPT_IDENTITY

    def test_identity_has_tree_model(self) -> None:
        assert "index.md" in PROMPT_IDENTITY
        assert "Hub" in PROMPT_IDENTITY
        assert "O(log n)" in PROMPT_IDENTITY

    def test_identity_has_object_types(self) -> None:
        assert "Canonical" in PROMPT_IDENTITY
        assert "Hub" in PROMPT_IDENTITY
        assert "Journal" in PROMPT_IDENTITY
        assert "Archive" in PROMPT_IDENTITY

    def test_identity_has_frontmatter_template(self) -> None:
        assert "type: hub | canonical" in PROMPT_IDENTITY
        assert "summary:" in PROMPT_IDENTITY
        assert "tags:" in PROMPT_IDENTITY

    def test_identity_has_inverted_pyramid(self) -> None:
        assert "Inverted pyramid" in PROMPT_IDENTITY

    def test_tools_has_all_tools(self) -> None:
        for tool in ["list_page_summaries", "read_page", "write_page",
                      "search_vault", "save_source", "fetch_url",
                      "import_files", "archive_page", "vault_stats", "append_log"]:
            assert tool in PROMPT_TOOLS, f"Missing tool: {tool}"

    def test_tools_has_common_requests(self) -> None:
        assert "import_files" in PROMPT_TOOLS
        assert "fetch_url" in PROMPT_TOOLS
        assert "vault_stats" in PROMPT_TOOLS

    def test_schema_not_in_system_prompt(self, agent: KnowledgeAgent) -> None:
        system_msg = agent.messages[0]["content"]
        assert "This is the operating manual" not in system_msg

    def test_preferences_in_system_prompt(self, vault: Vault) -> None:
        prefs = vault.schema_dir / "preferences.md"
        prefs.write_text("---\ntitle: Prefs\ntype: preference\n---\nUse Chinese")
        mock_provider = MagicMock()
        a = KnowledgeAgent(vault=vault, provider=mock_provider)
        system_msg = a.messages[0]["content"]
        assert "Use Chinese" in system_msg

    def test_prompt_token_budget(self) -> None:
        # Identity + Tools should be under ~1200 tokens (~4800 chars)
        assert len(SYSTEM_PROMPT) < 5000, f"System prompt too large: {len(SYSTEM_PROMPT)} chars"


class TestHistoryCompression:
    def test_no_compression_under_threshold(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "hello"})
        agent.messages.append({"role": "assistant", "content": "hi"})
        before = len(agent.messages)
        agent._maybe_compress_history()
        assert len(agent.messages) == before

    def test_compression_when_over_threshold(self, agent: KnowledgeAgent) -> None:
        for i in range(20):
            agent.messages.append({"role": "user", "content": f"msg {i} " + "x" * 3000})
            agent.messages.append({"role": "assistant", "content": f"reply {i} " + "y" * 3000})
        assert agent._estimate_chars() > agent._MAX_CONTEXT_CHARS
        agent._maybe_compress_history()
        assert len(agent.messages) < 40
        assert agent.messages[0]["role"] == "system"
        assert "summary" in agent.messages[1]["content"].lower()

    def test_compression_preserves_recent(self, agent: KnowledgeAgent) -> None:
        for i in range(20):
            agent.messages.append({"role": "user", "content": f"msg {i} " + "x" * 3000})
            agent.messages.append({"role": "assistant", "content": f"reply {i} " + "y" * 3000})
        last_user = agent.messages[-2]
        agent._maybe_compress_history()
        assert last_user in agent.messages


class TestToolResultTrimming:
    def test_trims_old_large_results(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "q"})
        agent.messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "1", "function": {"name": "read_page"}}],
        })
        agent.messages.append({
            "role": "tool",
            "tool_call_id": "1",
            "content": "A" * 5000,
        })
        agent.messages.append({"role": "assistant", "content": "here's what I found"})
        agent.messages.append({"role": "user", "content": "next question"})

        agent._trim_old_tool_results()
        tool_msg = [m for m in agent.messages if isinstance(m, dict) and m.get("role") == "tool"][0]
        assert len(tool_msg["content"]) < 5000
        assert "trimmed" in tool_msg["content"]

    def test_does_not_trim_recent_results(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "q"})
        agent.messages.append({
            "role": "tool",
            "tool_call_id": "1",
            "content": "B" * 5000,
        })
        agent._trim_old_tool_results()
        tool_msg = [m for m in agent.messages if isinstance(m, dict) and m.get("role") == "tool"][0]
        assert len(tool_msg["content"]) == 5000
