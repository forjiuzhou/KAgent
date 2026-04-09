"""Integration tests — mock LLM running complete chat/ingest/digest flows.

Each test sets up a vault, configures a KnowledgeAgent with a mocked provider
that returns scripted responses (including tool calls), and runs through a
complete interaction flow end-to-end.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from noteweaver.adapters.provider import CompletionResult, ToolCall
from noteweaver.agent import KnowledgeAgent
from noteweaver.vault import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


def _make_completion(content: str | None, tool_calls: list[dict] | None = None):
    """Create a (CompletionResult, raw_message) pair for mocking."""
    tcs = []
    raw_tcs = []
    if tool_calls:
        for tc in tool_calls:
            tcs.append(ToolCall(
                id=tc["id"],
                name=tc["name"],
                arguments=json.dumps(tc.get("arguments", {})),
            ))
            raw_tcs.append({
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": json.dumps(tc.get("arguments", {})),
                },
            })
    raw = {
        "role": "assistant",
        "content": content,
    }
    if raw_tcs:
        raw["tool_calls"] = raw_tcs
    return (CompletionResult(content=content, tool_calls=tcs), raw)


# ======================================================================
# Integration: full chat flow
# ======================================================================


class TestChatFlow:
    def test_simple_conversation(self, vault: Vault) -> None:
        """Mode 1 chat: no tool calls, just conversation."""
        provider = MagicMock()
        provider.chat_completion.return_value = _make_completion(
            "That's an interesting question about attention mechanisms!"
        )
        agent = KnowledgeAgent(vault=vault, provider=provider)

        responses = list(agent.chat("What is attention in deep learning?"))
        assert len(responses) == 1
        assert "attention" in responses[0].lower()
        assert provider.chat_completion.call_count == 1

        # Transcript should have 3 messages: system, user, assistant
        assert len(agent.messages) == 3

    def test_read_then_answer(self, vault: Vault) -> None:
        """Agent reads a page, then answers based on content."""
        provider = MagicMock()
        provider.chat_completion.side_effect = [
            _make_completion(None, [{
                "id": "tc1",
                "name": "read_page",
                "arguments": {"path": "wiki/index.md"},
            }]),
            _make_completion("The vault has an index with basic structure."),
        ]
        agent = KnowledgeAgent(vault=vault, provider=provider)

        responses = list(agent.chat("What's in my knowledge base?"))
        tool_responses = [r for r in responses if "↳" in r]
        text_responses = [r for r in responses if "↳" not in r]

        assert len(tool_responses) == 1
        assert "read_page" in tool_responses[0]
        assert len(text_responses) == 1
        assert provider.chat_completion.call_count == 2

    def test_write_page_flow(self, vault: Vault) -> None:
        """Agent checks for duplicates, then creates a new page.

        The page body must be ≥200 chars (policy: note minimum length)
        and find_existing_page must be called first (policy: dedup).
        """
        body_text = (
            "This is a comprehensive test note that covers the fundamentals "
            "of software testing including unit tests, integration tests, "
            "and end-to-end tests. Testing is crucial for maintaining "
            "code quality and preventing regressions in production systems."
        )
        page_content = (
            "---\ntitle: Test Note\ntype: note\n"
            "summary: A test note\ntags: [test]\n"
            "created: 2025-04-09\nupdated: 2025-04-09\n---\n\n"
            f"# Test Note\n\n{body_text}\n\n## Related\n"
        )
        provider = MagicMock()
        provider.chat_completion.side_effect = [
            _make_completion(None, [{
                "id": "tc1",
                "name": "find_existing_page",
                "arguments": {"title": "Test Note"},
            }]),
            _make_completion(None, [{
                "id": "tc2",
                "name": "write_page",
                "arguments": {
                    "path": "wiki/concepts/test-note.md",
                    "content": page_content,
                },
            }]),
            _make_completion("Done! Created a new note about the test topic."),
        ]
        agent = KnowledgeAgent(vault=vault, provider=provider)

        responses = list(agent.chat("Create a note about tests"))
        assert any("find_existing_page" in r for r in responses)
        assert any("write_page" in r for r in responses)

        # Verify the page was actually created
        content = vault.read_file("wiki/concepts/test-note.md")
        assert "Test Note" in content

    def test_append_section_flow(self, vault: Vault) -> None:
        """Agent reads page, then appends to it (read-before-write policy)."""
        page = (
            "---\ntitle: ML Basics\ntype: note\n"
            "summary: Machine learning basics\ntags: [ml]\n"
            "created: 2025-01-01\nupdated: 2025-01-01\n---\n\n"
            "# ML Basics\n\n## Supervised Learning\n\nLearn from labels.\n\n"
            "## Related\n\n- [[Deep Learning]]\n"
        )
        vault.write_file("wiki/concepts/ml-basics.md", page)

        provider = MagicMock()
        provider.chat_completion.side_effect = [
            _make_completion(None, [{
                "id": "tc1",
                "name": "find_existing_page",
                "arguments": {"title": "ML Basics"},
            }]),
            # Policy requires reading the page before editing it
            _make_completion(None, [{
                "id": "tc2",
                "name": "read_page",
                "arguments": {"path": "wiki/concepts/ml-basics.md"},
            }]),
            _make_completion(None, [{
                "id": "tc3",
                "name": "append_section",
                "arguments": {
                    "path": "wiki/concepts/ml-basics.md",
                    "heading": "Unsupervised Learning",
                    "content": "Learn patterns without labels.",
                },
            }]),
            _make_completion("Added a section on unsupervised learning."),
        ]
        agent = KnowledgeAgent(vault=vault, provider=provider)

        responses = list(agent.chat("Add info about unsupervised learning to ML basics"))
        content = vault.read_file("wiki/concepts/ml-basics.md")
        assert "## Unsupervised Learning" in content
        assert "Learn patterns without labels" in content
        # Original content preserved
        assert "## Supervised Learning" in content

    def test_multi_step_with_context_preserved(self, vault: Vault) -> None:
        """Multiple chat turns maintain context."""
        provider = MagicMock()
        provider.chat_completion.return_value = _make_completion("Got it!")
        agent = KnowledgeAgent(vault=vault, provider=provider)

        list(agent.chat("First message"))
        list(agent.chat("Second message"))
        list(agent.chat("Third message"))

        assert len(agent.messages) == 7  # system + 3*(user + assistant)

        # Query view should include all messages
        query = agent._build_messages_for_query()
        user_msgs = [m for m in query if m.get("role") == "user"]
        assert len(user_msgs) == 3


# ======================================================================
# Integration: session lifecycle
# ======================================================================


class TestSessionLifecycle:
    def test_full_session_lifecycle(self, vault: Vault) -> None:
        """Complete session: chat → save transcript → save memory → new session reads it."""
        provider = MagicMock()
        provider.chat_completion.side_effect = [
            _make_completion(None, [{
                "id": "tc1",
                "name": "read_page",
                "arguments": {"path": "wiki/index.md"},
            }]),
            _make_completion("Your vault is set up and ready to go."),
        ]

        # Session 1
        agent1 = KnowledgeAgent(vault=vault, provider=provider)
        list(agent1.chat("What's in my vault?"))

        transcript_path = agent1.save_transcript()
        agent1.save_session_memory()

        assert transcript_path.exists()
        assert (vault.meta_dir / "session-memory.md").exists()

        # Session 2 — fresh agent should see session memory
        provider2 = MagicMock()
        provider2.chat_completion.return_value = _make_completion(
            "Continuing from last time..."
        )
        agent2 = KnowledgeAgent(vault=vault, provider=provider2)
        list(agent2.chat("Continue"))

        # The query view should contain session memory
        query = agent2._build_messages_for_query()
        system = query[0]["content"]
        assert "Session Context" in system or "Last Session" in system

    def test_transcript_available_for_digest(self, vault: Vault) -> None:
        """Saved transcripts can be read by the read_transcript tool."""
        from noteweaver.tools.definitions import dispatch_tool

        provider = MagicMock()
        provider.chat_completion.return_value = _make_completion("Hello!")
        agent = KnowledgeAgent(vault=vault, provider=provider)
        list(agent.chat("Test message for transcript"))
        path = agent.save_transcript()

        result = dispatch_tool(vault, "read_transcript", {"filename": path.name})
        assert "Test message for transcript" in result
        assert "Hello!" in result


# ======================================================================
# Integration: dedup workflow
# ======================================================================


class TestDedupWorkflow:
    def test_find_existing_prevents_duplicate(self, vault: Vault) -> None:
        """find_existing_page finds an existing page so the agent can update it."""
        from noteweaver.tools.definitions import dispatch_tool

        page = (
            "---\ntitle: Neural Networks\ntype: canonical\n"
            "summary: Introduction to neural networks\n"
            "tags: [ai, ml]\nsources: [textbook]\n"
            "created: 2025-01-01\nupdated: 2025-01-01\n---\n\n"
            "# Neural Networks\n\nBasic building blocks of deep learning.\n\n"
            "## Related\n"
        )
        vault.write_file("wiki/concepts/neural-networks.md", page)

        # Searching for "Neural Networks" should find the existing page
        result = dispatch_tool(vault, "find_existing_page", {
            "title": "Neural Networks",
        })
        assert "wiki/concepts/neural-networks.md" in result
        assert "append_section" in result or "updating" in result.lower()

    def test_safe_to_create_when_no_match(self, vault: Vault) -> None:
        from noteweaver.tools.definitions import dispatch_tool

        result = dispatch_tool(vault, "find_existing_page", {
            "title": "Quantum Entanglement",
        })
        assert "Safe to create" in result


# ======================================================================
# Integration: tool result tiering across steps
# ======================================================================


class TestToolResultTieringIntegration:
    def test_tiering_in_multi_step_chat(self, vault: Vault) -> None:
        """Tool results from earlier turns are tiered in the query view."""
        big_content = "X" * 2000

        step_responses = []
        for i in range(4):
            step_responses.append(_make_completion(None, [{
                "id": f"tc{i}",
                "name": "read_page",
                "arguments": {"path": "wiki/index.md"},
            }]))
            step_responses.append(_make_completion(f"Answer {i}"))

        provider = MagicMock()
        provider.chat_completion.side_effect = step_responses
        agent = KnowledgeAgent(vault=vault, provider=provider)

        for i in range(4):
            list(agent.chat(f"Question {i}"))

        # All tool results should be intact in transcript
        tool_msgs = [m for m in agent.messages if isinstance(m, dict) and m.get("role") == "tool"]
        assert all(len(m["content"]) > 100 for m in tool_msgs)

        # But query view should have tiered cleanup
        query = agent._build_messages_for_query()
        query_tools = [m for m in query if isinstance(m, dict) and m.get("role") == "tool"]
        if len(query_tools) >= 4:
            # Oldest should be cleared
            assert "cleared" in query_tools[0]["content"].lower()
