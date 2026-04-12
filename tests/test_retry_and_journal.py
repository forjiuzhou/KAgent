"""Tests for retry/backoff, error recovery, and LLM-generated journal."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from noteweaver.adapters.retry import with_retry, _is_retryable
from noteweaver.adapters.provider import LLMProvider, CompletionResult, ToolCall
from noteweaver.agent import KnowledgeAgent
from noteweaver.vault import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


# ======================================================================
# Retry infrastructure
# ======================================================================


class TestRetryable:
    def test_rate_limit_error_is_retryable(self) -> None:
        exc = type("RateLimitError", (Exception,), {})()
        assert _is_retryable(exc)

    def test_timeout_error_is_retryable(self) -> None:
        exc = type("APITimeoutError", (Exception,), {})()
        assert _is_retryable(exc)

    def test_connection_error_is_retryable(self) -> None:
        exc = type("APIConnectionError", (Exception,), {})()
        assert _is_retryable(exc)

    def test_status_429_is_retryable(self) -> None:
        exc = Exception("rate limited")
        exc.status_code = 429  # type: ignore[attr-defined]
        assert _is_retryable(exc)

    def test_status_529_is_retryable(self) -> None:
        exc = Exception("overloaded")
        exc.status_code = 529  # type: ignore[attr-defined]
        assert _is_retryable(exc)

    def test_status_500_is_retryable(self) -> None:
        exc = Exception("server error")
        exc.status_code = 500  # type: ignore[attr-defined]
        assert _is_retryable(exc)

    def test_status_401_is_not_retryable(self) -> None:
        exc = Exception("unauthorized")
        exc.status_code = 401  # type: ignore[attr-defined]
        assert not _is_retryable(exc)

    def test_generic_value_error_not_retryable(self) -> None:
        assert not _is_retryable(ValueError("bad input"))


class TestWithRetry:
    def test_succeeds_on_first_try(self) -> None:
        fn = MagicMock(return_value="ok")
        result = with_retry(fn, max_retries=3, initial_backoff=0.01)
        assert result == "ok"
        assert fn.call_count == 1

    def test_retries_on_transient_failure(self) -> None:
        rate_limit = type("RateLimitError", (Exception,), {})
        fn = MagicMock(side_effect=[rate_limit(), rate_limit(), "ok"])
        result = with_retry(fn, max_retries=3, initial_backoff=0.01)
        assert result == "ok"
        assert fn.call_count == 3

    def test_raises_non_retryable_immediately(self) -> None:
        fn = MagicMock(side_effect=ValueError("bad"))
        with pytest.raises(ValueError):
            with_retry(fn, max_retries=3, initial_backoff=0.01)
        assert fn.call_count == 1

    def test_exhausts_retries(self) -> None:
        rate_limit = type("RateLimitError", (Exception,), {})
        fn = MagicMock(side_effect=rate_limit())
        with pytest.raises(rate_limit):
            with_retry(fn, max_retries=2, initial_backoff=0.01)
        assert fn.call_count == 3  # initial + 2 retries

    def test_backoff_increases(self) -> None:
        rate_limit = type("RateLimitError", (Exception,), {})
        fn = MagicMock(side_effect=[rate_limit(), rate_limit(), "ok"])
        start = time.monotonic()
        with_retry(fn, max_retries=3, initial_backoff=0.05)
        elapsed = time.monotonic() - start
        # Should have slept at least 0.05 + 0.10 = 0.15s
        assert elapsed >= 0.12


# ======================================================================
# Chat loop error recovery
# ======================================================================


class TestChatErrorRecovery:
    def test_tool_exception_captured_as_result(self, vault: Vault) -> None:
        """If dispatch_tool raises, the error is fed back to the model."""
        provider = MagicMock()
        provider.chat_completion.side_effect = [
            (
                CompletionResult(content=None, tool_calls=[
                    ToolCall(id="tc1", name="read_page", arguments='{"path": "wiki/nonexistent.md"}'),
                ]),
                {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "tc1", "type": "function",
                                    "function": {"name": "read_page", "arguments": '{"path": "wiki/nonexistent.md"}'}}],
                },
            ),
            (
                CompletionResult(content="Sorry, I couldn't find that file."),
                {"role": "assistant", "content": "Sorry, I couldn't find that file."},
            ),
        ]
        agent = KnowledgeAgent(vault=vault, provider=provider)
        responses = list(agent.chat("Read a nonexistent file"))

        # The agent should still produce a response (model handles the error)
        text_responses = [r for r in responses if "↳" not in r]
        assert len(text_responses) == 1
        # Provider was called twice: tool call + final response
        assert provider.chat_completion.call_count == 2

    def test_tool_crash_does_not_kill_session(self, vault: Vault) -> None:
        """Even if a tool handler crashes with an unexpected exception,
        the error is captured and the model can continue."""

        provider = MagicMock()
        provider.chat_completion.side_effect = [
            (
                CompletionResult(content=None, tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="survey_topic",
                        arguments='{"topic": "stats"}',
                    ),
                ]),
                {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "tc1", "type": "function",
                                    "function": {
                                        "name": "survey_topic",
                                        "arguments": '{"topic": "stats"}',
                                    }}],
                },
            ),
            (
                CompletionResult(content="There was an issue surveying the topic."),
                {"role": "assistant", "content": "There was an issue surveying the topic."},
            ),
        ]

        agent = KnowledgeAgent(vault=vault, provider=provider)

        # Monkey-patch vault.read_frontmatters to crash (used by survey_topic)
        original = vault.read_frontmatters
        vault.read_frontmatters = MagicMock(side_effect=RuntimeError("db corruption"))

        try:
            responses = list(agent.chat("Survey the vault"))
            text_responses = [r for r in responses if "↳" not in r]
            assert len(text_responses) == 1

            # The error should be recorded in transcript as a tool result
            tool_msgs = [m for m in agent.messages if isinstance(m, dict) and m.get("role") == "tool"]
            assert len(tool_msgs) == 1
            assert "Error executing survey_topic" in tool_msgs[0]["content"]
            assert "RuntimeError" in tool_msgs[0]["content"]
        finally:
            vault.read_frontmatters = original


# ======================================================================
# LLM-generated journal
# ======================================================================


class TestJournalGeneration:
    def test_generate_journal_summary(self, vault: Vault) -> None:
        provider = MagicMock()
        provider.simple_completion.return_value = (
            "INSIGHTS:\n"
            "- Attention mechanism is a differentiable retrieval\n"
            "- Multi-head allows different subspace focus\n\n"
            "DECISIONS:\n"
            "- Use canonical format for attention page\n\n"
            "OPEN_QUESTIONS:\n"
            "- How does flash attention change complexity?\n\n"
            "FOLLOW_UPS:\n"
            "- Write detailed multi-head attention derivation\n"
        )
        agent = KnowledgeAgent(vault=vault, provider=provider)
        agent.messages.append({"role": "user", "content": "Explain attention"})
        agent.messages.append({"role": "assistant", "content": "Attention is..."})

        result = agent.generate_journal_summary()

        assert len(result["insights"]) == 2
        assert "differentiable retrieval" in result["insights"][0]
        assert len(result["decisions"]) == 1
        assert len(result["open_questions"]) == 1
        assert len(result["follow_ups"]) == 1
        provider.simple_completion.assert_called_once()

    def test_generate_journal_summary_empty_session(self, vault: Vault) -> None:
        provider = MagicMock()
        agent = KnowledgeAgent(vault=vault, provider=provider)
        result = agent.generate_journal_summary()
        assert result == {"insights": [], "decisions": [], "open_questions": [], "follow_ups": []}
        provider.simple_completion.assert_not_called()

    def test_generate_journal_summary_llm_failure(self, vault: Vault) -> None:
        """If LLM call fails, return empty slots instead of crashing."""
        provider = MagicMock()
        provider.simple_completion.side_effect = RuntimeError("API down")
        agent = KnowledgeAgent(vault=vault, provider=provider)
        agent.messages.append({"role": "user", "content": "test"})
        agent.messages.append({"role": "assistant", "content": "reply"})

        result = agent.generate_journal_summary()
        assert result == {"insights": [], "decisions": [], "open_questions": [], "follow_ups": []}

    def test_parse_journal_sections_handles_none_items(self, vault: Vault) -> None:
        text = "INSIGHTS:\n- (none)\n\nDECISIONS:\n- Choose React\n"
        result = KnowledgeAgent._parse_journal_sections(text)
        assert result["insights"] == []
        assert result["decisions"] == ["Choose React"]

    def test_parse_journal_sections_robust_to_formatting(self, vault: Vault) -> None:
        text = (
            "INSIGHTS:\n"
            "- First insight\n"
            "- Second insight\n"
            "\n"
            "DECISIONS\n"  # no colon
            "- A decision\n"
            "\n"
            "FOLLOW-UPS:\n"  # hyphenated variant
            "- Do something\n"
        )
        result = KnowledgeAgent._parse_journal_sections(text)
        assert len(result["insights"]) == 2
        assert len(result["decisions"]) == 1
        assert len(result["follow_ups"]) == 1


class TestFinalizationWithJournal:
    def test_finalize_includes_llm_journal(self, vault: Vault) -> None:
        """finalize_session calls generate_journal_summary and writes slots."""
        from noteweaver.session import finalize_session

        provider = MagicMock()
        provider.simple_completion.return_value = (
            "INSIGHTS:\n- Key insight from test\n\n"
            "DECISIONS:\n- (none)\n\n"
            "OPEN_QUESTIONS:\n- What about X?\n\n"
            "FOLLOW_UPS:\n- Check X next time\n"
        )
        agent = KnowledgeAgent(vault=vault, provider=provider)
        # Include a write tool call so the session is considered substantial
        agent.messages.append({"role": "user", "content": "create a note"})
        agent.messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "tc1", "function": {
                "name": "write_page",
                "arguments": '{"path": "wiki/concepts/t.md", "content": "x"}',
            }}],
        })
        agent.messages.append({"role": "tool", "tool_call_id": "tc1", "content": "OK"})
        agent.messages.append({"role": "assistant", "content": "world"})

        exchanges = [{"user": "create a note", "tools": ["write_page"], "reply": "world"}]
        finalize_session(vault, agent, exchanges, "chat", run_organize=False)

        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        journal = vault.read_file(f"wiki/journals/{today}.md")
        assert "#### Insights" in journal
        assert "Key insight from test" in journal
        assert "#### Open Questions" in journal
        assert "What about X?" in journal
        assert "#### Follow-ups" in journal
        assert "Check X next time" in journal
