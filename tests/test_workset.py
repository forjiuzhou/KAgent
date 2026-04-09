"""Tests for enhanced session memory and workset (open items tracking)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from noteweaver.agent import KnowledgeAgent
from noteweaver.vault import Vault
from noteweaver.adapters.provider import CompletionResult


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


def _make_agent(vault: Vault) -> KnowledgeAgent:
    provider = MagicMock()
    return KnowledgeAgent(vault=vault, provider=provider)


class TestOpenItemsExtraction:
    def test_extract_open_items_from_memory(self) -> None:
        memory_text = (
            "## Active Workset\n"
            "Recent topics: ai, ml\n\n"
            "## Open Items\n"
            "- How does attention scale with sequence length?\n"
            "- Why is RLHF preferred over DPO?\n\n"
        )
        items = KnowledgeAgent._extract_open_items(memory_text)
        assert len(items) == 2
        assert "attention" in items[0].lower()

    def test_extract_open_items_empty(self) -> None:
        items = KnowledgeAgent._extract_open_items(None)
        assert items == []

    def test_extract_open_items_no_section(self) -> None:
        memory_text = "## Active Workset\nRecent topics: ai\n"
        items = KnowledgeAgent._extract_open_items(memory_text)
        assert items == []

    def test_extract_open_items_stops_at_next_section(self) -> None:
        memory_text = (
            "## Open Items\n"
            "- Question one?\n"
            "## Something Else\n"
            "- Not a question\n"
        )
        items = KnowledgeAgent._extract_open_items(memory_text)
        assert len(items) == 1


class TestOpenItemsFromTranscript:
    def test_extracts_questions_from_user_messages(self, vault: Vault) -> None:
        agent = _make_agent(vault)
        agent.messages.extend([
            {"role": "user", "content": "What is the difference between GPT and BERT?"},
            {"role": "assistant", "content": "GPT is autoregressive..."},
            {"role": "user", "content": "Tell me more about transformers"},
            {"role": "user", "content": "How does attention actually work in practice?"},
        ])
        items = agent._extract_open_items_from_transcript()
        assert len(items) >= 1
        assert any("?" in item for item in items)

    def test_skips_short_questions(self, vault: Vault) -> None:
        agent = _make_agent(vault)
        agent.messages.extend([
            {"role": "user", "content": "Why?"},
        ])
        items = agent._extract_open_items_from_transcript()
        assert len(items) == 0

    def test_limits_to_five(self, vault: Vault) -> None:
        agent = _make_agent(vault)
        for i in range(10):
            agent.messages.append({
                "role": "user",
                "content": f"What is the meaning of concept number {i} in this context?",
            })
        items = agent._extract_open_items_from_transcript()
        assert len(items) <= 5


class TestSessionMemoryWithOpenItems:
    def test_save_session_memory_includes_open_items(self, vault: Vault) -> None:
        agent = _make_agent(vault)
        agent.messages.extend([
            {"role": "user", "content": "What is the best approach to knowledge management systems?"},
            {"role": "assistant", "content": "There are several approaches..."},
            {"role": "user", "content": "How does the journal promotion pipeline work in practice?"},
            {"role": "assistant", "content": "Journals are reviewed..."},
        ])
        mem_path = agent.save_session_memory()
        assert mem_path is not None

        content = mem_path.read_text(encoding="utf-8")
        assert "## Open Items" in content
        assert "?" in content

    def test_open_items_carried_forward(self, vault: Vault) -> None:
        """Previous session open items should be merged."""
        prev_memory = (
            "## Open Items\n"
            "- How does RAG compare to compiled knowledge bases?\n"
        )
        mem_path = vault.meta_dir / "session-memory.md"
        mem_path.write_text(prev_memory, encoding="utf-8")

        agent = _make_agent(vault)
        agent.messages.extend([
            {"role": "user", "content": "Let's talk about neural networks"},
            {"role": "assistant", "content": "Sure!"},
            {"role": "user", "content": "What is the vanishing gradient problem?"},
            {"role": "assistant", "content": "It's when gradients shrink..."},
        ])
        new_mem_path = agent.save_session_memory()
        assert new_mem_path is not None

        content = new_mem_path.read_text(encoding="utf-8")
        assert "## Open Items" in content
        assert "vanishing gradient" in content.lower()
        assert "RAG" in content


class TestPolicyContextInAgent:
    def test_agent_has_policy_context(self, vault: Vault) -> None:
        agent = _make_agent(vault)
        assert hasattr(agent, "_policy_ctx")
        assert isinstance(agent._policy_ctx, object)
