"""Tests for the full context management pipeline.

Covers transcript persistence, session memory, improved journal generation,
and the read_transcript tool.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from noteweaver.agent import KnowledgeAgent
from noteweaver.vault import Vault
from noteweaver.tools.definitions import dispatch_tool, TOOL_SCHEMAS, TOOL_HANDLERS


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


@pytest.fixture
def agent(vault: Vault) -> KnowledgeAgent:
    mock_provider = MagicMock()
    return KnowledgeAgent(vault=vault, provider=mock_provider)


# ======================================================================
# Transcript persistence
# ======================================================================


class TestTranscript:
    def test_save_and_read_transcript(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "hello"})
        agent.messages.append({"role": "assistant", "content": "hi there"})

        path = agent.save_transcript()
        data = json.loads(path.read_text(encoding="utf-8"))

        assert len(data) == 3
        assert data[0]["role"] == "system"
        assert data[1]["role"] == "user"
        assert data[1]["content"] == "hello"
        assert data[2]["role"] == "assistant"
        assert data[2]["content"] == "hi there"

    def test_transcript_dir_created(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "test"})
        path = agent.save_transcript()
        assert path.parent.name == "transcripts"
        assert path.parent.parent == agent.vault.meta_dir

    def test_transcript_includes_tool_calls(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "search"})
        agent.messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "t1", "function": {
                "name": "search_vault", "arguments": '{"query": "attention"}'
            }}],
        })
        agent.messages.append({
            "role": "tool",
            "tool_call_id": "t1",
            "content": "Found 3 results...",
        })
        agent.messages.append({"role": "assistant", "content": "Here are the results."})

        path = agent.save_transcript()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert len(data) == 5
        assert data[2].get("tool_calls") is not None
        assert data[3]["role"] == "tool"

    def test_get_transcript_is_independent_copy(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "test"})
        copy = agent.get_transcript()
        copy.pop()
        assert len(agent.messages) == 2

    def test_multiple_transcripts_different_files(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "msg1"})
        p1 = agent.save_transcript()
        import time
        time.sleep(1.1)
        agent.messages.append({"role": "user", "content": "msg2"})
        p2 = agent.save_transcript()
        assert p1 != p2
        assert p1.exists()
        assert p2.exists()


# ======================================================================
# Session memory
# ======================================================================


class TestSessionMemory:
    def test_save_session_memory(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "Tell me about AI"})
        agent.messages.append({"role": "assistant", "content": "AI is..."})

        path = agent.save_session_memory()
        assert path is not None
        assert path.exists()

        content = path.read_text(encoding="utf-8")
        assert "Last Session" in content
        assert "session_turns" in content

    def test_session_memory_records_pages(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "read something"})
        agent.messages.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "t1", "function": {
                "name": "read_page",
                "arguments": '{"path": "wiki/concepts/attention.md"}',
            }}],
        })
        agent.messages.append({
            "role": "tool", "tool_call_id": "t1", "content": "content..."
        })
        agent.messages.append({"role": "assistant", "content": "Here's what I found."})

        path = agent.save_session_memory()
        content = path.read_text(encoding="utf-8")
        assert "wiki/concepts/attention.md" in content
        assert "read_page" in content

    def test_session_memory_empty_session(self, agent: KnowledgeAgent) -> None:
        assert agent.save_session_memory() is None

    def test_session_memory_loaded_into_query(self, agent: KnowledgeAgent) -> None:
        mem = agent.vault.meta_dir / "session-memory.md"
        mem.parent.mkdir(parents=True, exist_ok=True)
        mem.write_text("## Last Session\nTopic: quantum computing\n")

        agent.messages.append({"role": "user", "content": "continue"})
        query = agent._build_messages_for_query()
        system = query[0]["content"]
        assert "quantum computing" in system
        assert "Session Context" in system

    def test_session_memory_not_in_transcript(self, agent: KnowledgeAgent) -> None:
        """Session memory is injected in the view only, not the transcript."""
        mem = agent.vault.meta_dir / "session-memory.md"
        mem.parent.mkdir(parents=True, exist_ok=True)
        mem.write_text("## Last Session\nTopic: test\n")

        agent.messages.append({"role": "user", "content": "hi"})
        _ = agent._build_messages_for_query()

        # Transcript system prompt should NOT contain session memory
        assert "Session Context" not in agent.messages[0]["content"]


# ======================================================================
# read_transcript tool
# ======================================================================


class TestReadTranscriptTool:
    def test_schema_exists(self) -> None:
        names = [s["function"]["name"] for s in TOOL_SCHEMAS]
        assert "read_transcript" in names

    def test_handler_exists(self) -> None:
        assert "read_transcript" in TOOL_HANDLERS

    def test_read_saved_transcript(self, vault: Vault, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "hello world"})
        agent.messages.append({"role": "assistant", "content": "hi there!"})
        path = agent.save_transcript()

        result = dispatch_tool(vault, "read_transcript", {"filename": path.name})
        assert "hello world" in result
        assert "hi there!" in result

    def test_read_nonexistent_transcript(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "read_transcript", {"filename": "nope.json"})
        assert "Error" in result

    def test_read_transcript_with_max_chars(self, vault: Vault, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "A" * 5000})
        agent.messages.append({"role": "assistant", "content": "B" * 5000})
        path = agent.save_transcript()

        result = dispatch_tool(vault, "read_transcript", {
            "filename": path.name, "max_chars": 100
        })
        assert len(result) < 200
        assert "truncated" in result

    def test_path_traversal_blocked(self, vault: Vault) -> None:
        result = dispatch_tool(vault, "read_transcript", {
            "filename": "../../etc/passwd"
        })
        assert "Error" in result

    def test_transcript_skips_system_messages(self, vault: Vault, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "test"})
        path = agent.save_transcript()

        result = dispatch_tool(vault, "read_transcript", {"filename": path.name})
        assert "NoteWeaver" not in result  # system prompt not shown
        assert "test" in result


# ======================================================================
# Long-term memory (.schema/memory.md)
# ======================================================================


class TestLongTermMemory:
    def test_memory_loaded_in_system_prompt(self, vault: Vault) -> None:
        mem = vault.schema_dir / "memory.md"
        mem.write_text("Core expertise: NLP, transformers, attention mechanisms")
        a = KnowledgeAgent(vault=vault, provider=MagicMock())
        assert "Core expertise" in a.messages[0]["content"]
        assert "Knowledge Base Memory" in a.messages[0]["content"]

    def test_oversized_memory_excluded(self, vault: Vault) -> None:
        mem = vault.schema_dir / "memory.md"
        mem.write_text("x" * 4000)
        a = KnowledgeAgent(vault=vault, provider=MagicMock())
        assert "x" * 4000 not in a.messages[0]["content"]

    def test_no_memory_file(self, vault: Vault) -> None:
        a = KnowledgeAgent(vault=vault, provider=MagicMock())
        assert "Knowledge Base Memory" not in a.messages[0]["content"]

    def test_memory_and_preferences_coexist(self, vault: Vault) -> None:
        prefs = vault.schema_dir / "preferences.md"
        prefs.write_text("---\ntitle: P\ntype: preference\n---\nRespond in Chinese")
        mem = vault.schema_dir / "memory.md"
        mem.write_text("Key concept: attention is all you need")

        a = KnowledgeAgent(vault=vault, provider=MagicMock())
        system = a.messages[0]["content"]
        assert "Respond in Chinese" in system
        assert "attention is all you need" in system


# ======================================================================
# Finalization pipeline
# ======================================================================


class TestFinalization:
    def test_finalize_creates_all_artifacts(self, vault: Vault) -> None:
        """_finalize_session should create transcript, session memory, and journal."""
        from noteweaver.cli import _finalize_session

        agent = KnowledgeAgent(vault=vault, provider=MagicMock())
        agent.messages.append({"role": "user", "content": "hello"})
        agent.messages.append({"role": "assistant", "content": "world"})

        exchanges = [{"user": "hello", "tools": [], "reply": "world"}]
        _finalize_session(vault, agent, exchanges, "chat")

        # Transcript saved
        transcript_dir = vault.meta_dir / "transcripts"
        assert transcript_dir.exists()
        transcripts = list(transcript_dir.glob("*.json"))
        assert len(transcripts) == 1

        # Session memory saved
        mem = vault.meta_dir / "session-memory.md"
        assert mem.exists()

        # Journal created
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        journal_path = vault.wiki_dir / "journals" / f"{today}.md"
        assert journal_path.exists()
        content = journal_path.read_text(encoding="utf-8")
        assert "hello" in content
        assert "Chat session" in content
