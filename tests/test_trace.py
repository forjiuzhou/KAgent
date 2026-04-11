"""Tests for the trace / observability system."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from noteweaver.trace import TraceCollector
from noteweaver.agent import KnowledgeAgent
from noteweaver.vault import Vault


# ======================================================================
# TraceCollector unit tests
# ======================================================================


class TestTraceCollector:
    def test_empty_trace_has_no_events(self) -> None:
        tc = TraceCollector()
        assert tc.events == []

    def test_set_session_meta(self) -> None:
        tc = TraceCollector()
        tc.set_session_meta(
            model="gpt-4o",
            provider="openai",
            attended=True,
            vault_path="/tmp/vault",
            has_session_memory=False,
            has_long_term_memory=True,
            has_preferences=False,
        )
        assert tc.session_meta["model"] == "gpt-4o"
        assert tc.session_meta["attended"] is True

    def test_record_context_assembly(self) -> None:
        tc = TraceCollector()
        tc.record_context_assembly(
            system_prompt_chars=5000,
            session_memory_injected=True,
            pending_proposals_injected=False,
            summary_active=False,
            summary_boundary=1,
            recent_message_count=4,
            total_query_messages=5,
            estimated_tokens=1250,
        )
        assert len(tc.events) == 1
        ev = tc.events[0]
        assert ev.kind == "context_assembly"
        assert ev.data["system_prompt_chars"] == 5000
        assert ev.data["session_memory_injected"] is True
        assert ev.data["estimated_tokens"] == 1250

    def test_record_tool_call(self) -> None:
        tc = TraceCollector()
        tc.record_tool_call(
            name="read_page",
            arguments={"path": "wiki/index.md"},
            policy_allowed=True,
            policy_warning=None,
            result_preview="---\ntitle: Index...",
            duration_ms=12.5,
        )
        assert len(tc.events) == 1
        ev = tc.events[0]
        assert ev.kind == "tool_call"
        assert ev.data["name"] == "read_page"
        assert ev.data["policy_allowed"] is True
        assert ev.data["duration_ms"] == 12.5

    def test_record_tool_call_blocked(self) -> None:
        tc = TraceCollector()
        tc.record_tool_call(
            name="write_page",
            arguments={"path": "wiki/concepts/test.md", "content": "..."},
            policy_allowed=False,
            policy_warning="Policy: read first",
            result_preview="Policy: read first",
            duration_ms=0.1,
        )
        ev = tc.events[0]
        assert ev.data["policy_allowed"] is False
        assert ev.data["policy_warning"] == "Policy: read first"

    def test_record_tool_call_with_error(self) -> None:
        tc = TraceCollector()
        tc.record_tool_call(
            name="fetch_url",
            arguments={"url": "https://bad.example.com"},
            policy_allowed=True,
            policy_warning=None,
            result_preview="Error: ConnectionError",
            duration_ms=5000.0,
            error="ConnectionError: failed",
        )
        ev = tc.events[0]
        assert ev.data["error"] == "ConnectionError: failed"

    def test_result_preview_truncated_to_500(self) -> None:
        tc = TraceCollector()
        long_result = "x" * 1000
        tc.record_tool_call(
            name="read_page",
            arguments={"path": "test.md"},
            policy_allowed=True,
            policy_warning=None,
            result_preview=long_result,
            duration_ms=1.0,
        )
        assert len(tc.events[0].data["result_preview"]) == 500
        assert tc.events[0].data["result_chars"] == 1000

    def test_record_state_mutation(self) -> None:
        tc = TraceCollector()
        tc.record_state_mutation(
            mutation_type="file_write",
            path="wiki/concepts/test.md",
            detail="Created new page",
        )
        ev = tc.events[0]
        assert ev.kind == "state_mutation"
        assert ev.data["mutation_type"] == "file_write"
        assert ev.data["path"] == "wiki/concepts/test.md"

    def test_record_turn_end(self) -> None:
        tc = TraceCollector()
        tc.record_turn_end(
            steps_taken=3,
            has_response=True,
            hit_max_steps=False,
        )
        ev = tc.events[0]
        assert ev.kind == "turn_end"
        assert ev.data["steps_taken"] == 3
        assert ev.data["has_response"] is True
        assert ev.data["hit_max_steps"] is False
        assert "total_duration_ms" in ev.data


# ======================================================================
# Persistence (save / load)
# ======================================================================


class TestTracePersistence:
    def test_save_creates_jsonl(self, tmp_path: Path) -> None:
        tc = TraceCollector()
        tc.set_session_meta(
            model="gpt-4o",
            provider="openai",
            attended=True,
            vault_path="/tmp/vault",
            has_session_memory=False,
            has_long_term_memory=False,
            has_preferences=False,
        )
        tc.record_tool_call(
            name="search",
            arguments={"query": "test"},
            policy_allowed=True,
            policy_warning=None,
            result_preview="Found 2 results",
            duration_ms=10.0,
        )
        path = tc.save(tmp_path)
        assert path.suffix == ".jsonl"
        assert path.exists()

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2  # session_meta + tool_call

        meta = json.loads(lines[0])
        assert meta["kind"] == "session_meta"
        assert meta["model"] == "gpt-4o"

        tool = json.loads(lines[1])
        assert tool["kind"] == "tool_call"
        assert tool["name"] == "search"

    def test_save_empty_trace_still_writes_meta(self, tmp_path: Path) -> None:
        tc = TraceCollector()
        tc.set_session_meta(
            model="gpt-4o",
            provider="openai",
            attended=True,
            vault_path="/tmp/vault",
            has_session_memory=False,
            has_long_term_memory=False,
            has_preferences=False,
        )
        path = tc.save(tmp_path)
        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        assert json.loads(lines[0])["kind"] == "session_meta"

    def test_load_roundtrip(self, tmp_path: Path) -> None:
        tc = TraceCollector()
        tc.set_session_meta(
            model="test-model",
            provider="openai",
            attended=False,
            vault_path="/tmp/v",
            has_session_memory=True,
            has_long_term_memory=False,
            has_preferences=True,
        )
        tc.record_context_assembly(
            system_prompt_chars=3000,
            session_memory_injected=True,
            pending_proposals_injected=False,
            summary_active=False,
            summary_boundary=1,
            recent_message_count=2,
            total_query_messages=3,
            estimated_tokens=750,
        )
        tc.record_tool_call(
            name="list_pages",
            arguments={"directory": "wiki/concepts"},
            policy_allowed=True,
            policy_warning=None,
            result_preview="3 pages found",
            duration_ms=5.0,
        )
        tc.record_turn_end(
            steps_taken=2,
            has_response=True,
            hit_max_steps=False,
        )

        path = tc.save(tmp_path)
        events = TraceCollector.load(path)

        assert len(events) == 4  # meta + assembly + tool + turn_end
        kinds = [e["kind"] for e in events]
        assert kinds == ["session_meta", "context_assembly", "tool_call", "turn_end"]


# ======================================================================
# Human-readable rendering
# ======================================================================


class TestTraceRendering:
    def test_render_includes_all_sections(self, tmp_path: Path) -> None:
        tc = TraceCollector()
        tc.set_session_meta(
            model="claude-3.5-sonnet",
            provider="anthropic",
            attended=True,
            vault_path="/tmp/vault",
            has_session_memory=True,
            has_long_term_memory=False,
            has_preferences=True,
        )
        tc.record_context_assembly(
            system_prompt_chars=4000,
            session_memory_injected=True,
            pending_proposals_injected=False,
            summary_active=False,
            summary_boundary=1,
            recent_message_count=3,
            total_query_messages=4,
            estimated_tokens=1000,
        )
        tc.record_tool_call(
            name="read_page",
            arguments={"path": "wiki/index.md"},
            policy_allowed=True,
            policy_warning=None,
            result_preview="# Index\n...",
            duration_ms=8.0,
        )
        tc.record_tool_call(
            name="write_page",
            arguments={"path": "wiki/concepts/new.md"},
            policy_allowed=False,
            policy_warning="Policy: read first",
            result_preview="Policy: read first",
            duration_ms=0.1,
        )
        tc.record_state_mutation(
            mutation_type="git_commit",
            detail="Committed 2 files",
        )
        tc.record_turn_end(
            steps_taken=3,
            has_response=True,
            hit_max_steps=False,
        )

        path = tc.save(tmp_path)
        events = TraceCollector.load(path)
        report = TraceCollector.render_human(events)

        assert "Session" in report
        assert "claude-3.5-sonnet" in report
        assert "anthropic" in report
        assert "Context Assembly" in report
        assert "session-memory" in report
        assert "read_page" in report
        assert "BLOCKED" in report
        assert "Policy: read first" in report
        assert "git_commit" in report
        assert "Turn End" in report

    def test_render_empty_trace(self) -> None:
        report = TraceCollector.render_human([])
        assert report == ""


# ======================================================================
# Agent integration
# ======================================================================


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


@pytest.fixture
def agent(vault: Vault) -> KnowledgeAgent:
    mock_provider = MagicMock()
    return KnowledgeAgent(vault=vault, provider=mock_provider)


class TestAgentTraceIntegration:
    def test_agent_has_trace_collector(self, agent: KnowledgeAgent) -> None:
        assert hasattr(agent, "_trace")
        assert isinstance(agent._trace, TraceCollector)

    def test_save_trace_empty_returns_none(self, agent: KnowledgeAgent) -> None:
        result = agent.save_trace()
        assert result is None

    def test_save_trace_with_events(self, agent: KnowledgeAgent) -> None:
        agent._trace.record_context_assembly(
            system_prompt_chars=1000,
            session_memory_injected=False,
            pending_proposals_injected=False,
            summary_active=False,
            summary_boundary=1,
            recent_message_count=1,
            total_query_messages=2,
            estimated_tokens=250,
        )
        path = agent.save_trace()
        assert path is not None
        assert path.exists()
        assert path.suffix == ".jsonl"
        assert path.parent.name == "traces"

    def test_build_messages_records_context_assembly(
        self, agent: KnowledgeAgent
    ) -> None:
        agent.messages.append({"role": "user", "content": "hello"})
        agent._build_messages_for_query()

        assert len(agent._trace.events) >= 1
        ev = agent._trace.events[-1]
        assert ev.kind == "context_assembly"
        assert ev.data["system_prompt_chars"] > 0
        assert ev.data["total_query_messages"] >= 2
