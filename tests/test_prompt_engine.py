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
    def test_identity_has_three_modes(self) -> None:
        assert "Three Modes" in PROMPT_IDENTITY
        assert "Conversation" in PROMPT_IDENTITY
        assert "Capture" in PROMPT_IDENTITY
        assert "Organize" in PROMPT_IDENTITY

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
                      "import_files", "archive_page", "vault_stats",
                      "read_transcript", "append_log",
                      "find_existing_page", "append_section",
                      "append_to_section", "update_frontmatter",
                      "add_related_link", "scan_imports",
                      "apply_organize_plan"]:
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
        assert len(SYSTEM_PROMPT) < 7500, f"System prompt too large: {len(SYSTEM_PROMPT)} chars"


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

        # Transcript is append-only — length unchanged
        assert len(agent.messages) == 41

        # Session summary is generated
        assert agent._session_summary is not None
        assert "SESSION CONTEXT" in agent._session_summary["text"]

        # Query view is shorter than the full transcript
        query = agent._build_messages_for_query()
        assert len(query) < len(agent.messages)
        assert query[0]["role"] == "system"
        assert "summary" in query[1]["content"].lower() or "SESSION CONTEXT" in query[1]["content"]

    def test_compression_preserves_recent(self, agent: KnowledgeAgent) -> None:
        for i in range(20):
            agent.messages.append({"role": "user", "content": f"msg {i} " + "x" * 3000})
            agent.messages.append({"role": "assistant", "content": f"reply {i} " + "y" * 3000})
        last_user_content = agent.messages[-2]["content"]
        agent._maybe_compress_history()

        # Recent messages preserved in query view
        query = agent._build_messages_for_query()
        query_contents = [m.get("content", "") for m in query if isinstance(m, dict)]
        assert last_user_content in query_contents

    def test_transcript_never_mutated(self, agent: KnowledgeAgent) -> None:
        """The core invariant: compression must not modify self.messages."""
        for i in range(20):
            agent.messages.append({"role": "user", "content": f"msg {i} " + "x" * 3000})
            agent.messages.append({"role": "assistant", "content": f"reply {i} " + "y" * 3000})
        original_messages = list(agent.messages)
        agent._maybe_compress_history()
        assert agent.messages == original_messages

    def test_session_summary_structure(self, agent: KnowledgeAgent) -> None:
        for i in range(20):
            agent.messages.append({"role": "user", "content": f"msg {i} " + "x" * 3000})
            agent.messages.append({"role": "assistant", "content": f"reply {i} " + "y" * 3000})
        agent._maybe_compress_history()

        s = agent._session_summary
        assert s is not None
        assert "boundary" in s
        assert "key_points" in s
        assert "text" in s
        assert isinstance(s["key_points"], list)


class TestToolResultTrimming:
    def test_trims_old_large_results(self, agent: KnowledgeAgent) -> None:
        """Old tool results are cleaned up in the query view, not the transcript."""
        big = "A" * 5000

        # Stale turn (will be cleared in view)
        agent.messages.append({"role": "user", "content": "q1"})
        agent.messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "1", "function": {"name": "read_page"}}],
        })
        agent.messages.append({"role": "tool", "tool_call_id": "1", "content": big})
        agent.messages.append({"role": "assistant", "content": "answer 1"})

        # Recent turn 1
        agent.messages.append({"role": "user", "content": "q2"})
        agent.messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "2", "function": {"name": "read_page"}}],
        })
        agent.messages.append({"role": "tool", "tool_call_id": "2", "content": big})
        agent.messages.append({"role": "assistant", "content": "answer 2"})

        # Recent turn 2
        agent.messages.append({"role": "user", "content": "q3"})
        agent.messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "3", "function": {"name": "read_page"}}],
        })
        agent.messages.append({"role": "tool", "tool_call_id": "3", "content": big})
        agent.messages.append({"role": "assistant", "content": "answer 3"})

        # Recent turn 3 (most recent)
        agent.messages.append({"role": "user", "content": "q4"})
        agent.messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "4", "function": {"name": "read_page"}}],
        })
        agent.messages.append({"role": "tool", "tool_call_id": "4", "content": big})
        agent.messages.append({"role": "assistant", "content": "answer 4"})

        # Transcript is never modified
        tool_msgs = [m for m in agent.messages if isinstance(m, dict) and m.get("role") == "tool"]
        assert all(len(t["content"]) == 5000 for t in tool_msgs)

        # Query view: oldest tool result should be cleaned up
        query = agent._build_messages_for_query()
        query_tools = [m for m in query if isinstance(m, dict) and m.get("role") == "tool"]
        assert "cleared" in query_tools[0]["content"].lower()
        # Most recent tool result should still be full
        assert len(query_tools[-1]["content"]) == 5000

    def test_does_not_trim_recent_results(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "q"})
        agent.messages.append({
            "role": "tool",
            "tool_call_id": "1",
            "content": "B" * 5000,
        })
        # No subsequent assistant message — this is the active turn
        query = agent._build_messages_for_query()
        query_tool = [m for m in query if isinstance(m, dict) and m.get("role") == "tool"][0]
        assert len(query_tool["content"]) == 5000


class TestQueryView:
    """Tests for the messages_for_query view layer."""

    def test_basic_query_view(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "hello"})
        query = agent._build_messages_for_query()
        assert query[0]["role"] == "system"
        assert query[1]["role"] == "user"
        assert query[1]["content"] == "hello"

    def test_query_view_includes_session_memory(self, agent: KnowledgeAgent) -> None:
        mem = agent.vault.meta_dir / "session-memory.md"
        mem.parent.mkdir(parents=True, exist_ok=True)
        mem.write_text("## Last Session\nTopic: Python async\n")

        agent.messages.append({"role": "user", "content": "hi"})
        query = agent._build_messages_for_query()
        system = query[0]["content"]
        assert "Last Session" in system
        assert "Python async" in system

    def test_query_view_tool_result_tiers(self, agent: KnowledgeAgent) -> None:
        """Older consumed tool results are cleaned up in the view."""
        big = "X" * 2000

        # Turn 1: tool call + result + assistant response
        agent.messages.append({"role": "user", "content": "q1"})
        agent.messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "t1", "function": {"name": "read_page"}}],
        })
        agent.messages.append({"role": "tool", "tool_call_id": "t1", "content": big})
        agent.messages.append({"role": "assistant", "content": "answer 1"})

        # Turn 2: tool call + result + assistant response
        agent.messages.append({"role": "user", "content": "q2"})
        agent.messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "t2", "function": {"name": "read_page"}}],
        })
        agent.messages.append({"role": "tool", "tool_call_id": "t2", "content": big})
        agent.messages.append({"role": "assistant", "content": "answer 2"})

        # Turn 3: tool call + result + assistant response
        agent.messages.append({"role": "user", "content": "q3"})
        agent.messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "t3", "function": {"name": "read_page"}}],
        })
        agent.messages.append({"role": "tool", "tool_call_id": "t3", "content": big})
        agent.messages.append({"role": "assistant", "content": "answer 3"})

        # Turn 4: tool call + result + assistant response
        agent.messages.append({"role": "user", "content": "q4"})
        agent.messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "t4", "function": {"name": "read_page"}}],
        })
        agent.messages.append({"role": "tool", "tool_call_id": "t4", "content": big})
        agent.messages.append({"role": "assistant", "content": "answer 4"})

        query = agent._build_messages_for_query()
        tool_results = [m for m in query if isinstance(m, dict) and m.get("role") == "tool"]

        # t1 is stale (age > full + preview)
        assert "cleared" in tool_results[0]["content"].lower()

        # t4 is the most recent completed turn — should have full content
        assert len(tool_results[3]["content"]) == 2000

    def test_query_view_does_not_modify_transcript(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "q"})
        agent.messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "t1", "function": {"name": "search"}}],
        })
        agent.messages.append({"role": "tool", "tool_call_id": "t1", "content": "Y" * 3000})
        agent.messages.append({"role": "assistant", "content": "done"})

        original = [dict(m) if isinstance(m, dict) else m for m in agent.messages]
        _ = agent._build_messages_for_query()
        for i, m in enumerate(agent.messages):
            if isinstance(m, dict):
                assert m == original[i], f"Message {i} was mutated"


class TestSessionSummary:
    """Tests for structured session summary (C2)."""

    def test_no_summary_under_threshold(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "short"})
        agent._update_session_summary()
        assert agent._session_summary is None

    def test_summary_generated_over_threshold(self, agent: KnowledgeAgent) -> None:
        for i in range(20):
            agent.messages.append({"role": "user", "content": f"msg {i} " + "x" * 3000})
            agent.messages.append({"role": "assistant", "content": f"reply {i} " + "y" * 3000})
        agent._update_session_summary()
        assert agent._session_summary is not None
        assert agent._summary_boundary > 1

    def test_summary_captures_tool_usage(self, agent: KnowledgeAgent) -> None:
        for i in range(15):
            agent.messages.append({"role": "user", "content": f"msg {i} " + "x" * 3000})
            agent.messages.append({
                "role": "assistant", "content": None,
                "tool_calls": [{"id": f"t{i}", "function": {
                    "name": "read_page",
                    "arguments": f'{{"path": "wiki/concepts/page-{i}.md"}}',
                }}],
            })
            agent.messages.append({
                "role": "tool", "tool_call_id": f"t{i}", "content": "result " + "z" * 1000,
            })
            agent.messages.append({"role": "assistant", "content": f"answer {i} " + "y" * 1000})

        agent._update_session_summary()
        s = agent._session_summary
        assert s is not None
        assert "read_page" in s.get("tools_used", [])
        assert any("wiki/" in p for p in s.get("pages_touched", []))


class TestMemoryIntegration:
    """Tests for long-term memory loading in system prompt."""

    def test_memory_md_loaded(self, vault: Vault) -> None:
        mem = vault.schema_dir / "memory.md"
        mem.write_text("Core topics: AI, NLP, transformers")
        mock_provider = MagicMock()
        a = KnowledgeAgent(vault=vault, provider=mock_provider)
        system_msg = a.messages[0]["content"]
        assert "Core topics: AI, NLP, transformers" in system_msg

    def test_memory_md_too_large_skipped(self, vault: Vault) -> None:
        mem = vault.schema_dir / "memory.md"
        mem.write_text("x" * 5000)
        mock_provider = MagicMock()
        a = KnowledgeAgent(vault=vault, provider=mock_provider)
        system_msg = a.messages[0]["content"]
        assert "x" * 5000 not in system_msg

    def test_no_memory_file_is_fine(self, vault: Vault) -> None:
        mock_provider = MagicMock()
        a = KnowledgeAgent(vault=vault, provider=mock_provider)
        assert "Knowledge Base Memory" not in a.messages[0]["content"]


class TestTranscriptPersistence:
    """Tests for transcript save/load."""

    def test_save_transcript(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "hello"})
        agent.messages.append({"role": "assistant", "content": "hi there"})

        path = agent.save_transcript()
        assert path.exists()
        assert path.suffix == ".json"
        assert path.parent.name == "transcripts"

        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data) == 3  # system + user + assistant

    def test_get_transcript_returns_copy(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "test"})
        t = agent.get_transcript()
        t.pop()
        assert len(agent.messages) == 2

    def test_save_session_memory(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "Tell me about attention"})
        agent.messages.append({"role": "assistant", "content": "Attention is..."})
        agent.messages.append({"role": "user", "content": "And transformers?"})
        agent.messages.append({"role": "assistant", "content": "Transformers use..."})

        path = agent.save_session_memory()
        assert path is not None
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "Last Session" in content
        assert "session_turns" in content

    def test_session_memory_empty_session(self, agent: KnowledgeAgent) -> None:
        path = agent.save_session_memory()
        assert path is None
