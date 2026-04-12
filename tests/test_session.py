"""Tests for noteweaver.session — shared CLI/Gateway session logic."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from noteweaver.vault import Vault
from noteweaver.agent import KnowledgeAgent
from noteweaver.session import (
    make_agent,
    session_has_substance,
    finalize_session,
    save_session_journal,
    load_last_digest_date,
    save_last_digest_date,
    build_digest_prompt,
)


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    v = Vault(tmp_path, auto_git=False)
    v.init()
    return v


@pytest.fixture
def agent(vault: Vault) -> KnowledgeAgent:
    return KnowledgeAgent(vault=vault, provider=MagicMock())


class TestMakeAgent:
    def test_raises_if_no_vault(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="No vault"):
            make_agent(tmp_path / "nonexistent")

    def test_raises_if_no_api_key(self, vault: Vault, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("NW_PROVIDER", raising=False)
        with pytest.raises(RuntimeError, match="(?i)api.key"):
            make_agent(vault.root)


class TestSessionHasSubstance:
    def test_write_tool_is_substantial(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "write something"})
        agent.messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "tc1", "function": {
                "name": "write_page",
                "arguments": '{"path": "wiki/concepts/t.md", "content": "x"}',
            }}],
        })
        assert session_has_substance(agent, []) is True

    def test_read_only_not_substantial(self, agent: KnowledgeAgent) -> None:
        agent.messages.append({"role": "user", "content": "read something"})
        agent.messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "tc1", "function": {
                "name": "read_page",
                "arguments": '{"path": "wiki/concepts/t.md"}',
            }}],
        })
        assert session_has_substance(agent, [{"user": "hi"}]) is False

    def test_enough_exchanges_is_substantial(self, agent: KnowledgeAgent) -> None:
        exchanges = [{"user": f"msg {i}"} for i in range(3)]
        assert session_has_substance(agent, exchanges) is True


class TestFinalizeSession:
    def test_saves_transcript_and_memory(self, vault: Vault) -> None:
        agent = KnowledgeAgent(vault=vault, provider=MagicMock())
        agent.messages.append({"role": "user", "content": "hello"})
        agent.messages.append({"role": "assistant", "content": "hi"})

        exchanges = [{"user": "hello", "tools": [], "reply": "hi"}]
        finalize_session(vault, agent, exchanges, "chat", run_organize=False)

        assert (vault.meta_dir / "transcripts").exists()
        assert (vault.meta_dir / "session-memory.md").exists()

    def test_journals_for_system_commands(self, vault: Vault) -> None:
        from datetime import datetime

        agent = KnowledgeAgent(vault=vault, provider=MagicMock())
        agent.messages.append({"role": "user", "content": "digest"})
        agent.messages.append({"role": "assistant", "content": "done"})

        exchanges = [{"user": "digest", "tools": [], "reply": "done"}]
        finalize_session(vault, agent, exchanges, "digest", run_organize=False)

        today = datetime.now().strftime("%Y-%m-%d")
        journal_path = vault.wiki_dir / "journals" / f"{today}.md"
        assert journal_path.exists()


class TestDigestDateTracking:
    def test_roundtrip(self, vault: Vault) -> None:
        assert load_last_digest_date(vault) is None
        save_last_digest_date(vault)
        result = load_last_digest_date(vault)
        assert result is not None

        from datetime import datetime
        assert result == datetime.now().strftime("%Y-%m-%d")


class TestBuildDigestPrompt:
    def test_includes_since_hint_when_date_exists(self, vault: Vault) -> None:
        save_last_digest_date(vault)
        prompt = build_digest_prompt(vault)
        assert "Only review journals after" in prompt

    def test_no_since_hint_when_fresh(self, vault: Vault) -> None:
        prompt = build_digest_prompt(vault)
        assert "Only review journals after" not in prompt

    def test_includes_transcript_hint_when_transcripts_exist(self, vault: Vault) -> None:
        t_dir = vault.meta_dir / "transcripts"
        t_dir.mkdir(parents=True)
        (t_dir / "2026-01-01_120000.json").write_text("{}")
        prompt = build_digest_prompt(vault)
        assert "transcripts" in prompt.lower()

    def test_consistent_base_prompt(self, vault: Vault) -> None:
        prompt = build_digest_prompt(vault)
        assert "Review the recent journal entries" in prompt
        assert "write_page()" in prompt
        assert "search()" in prompt

    def test_attended_prompt_allows_write_page(self, vault: Vault) -> None:
        prompt = build_digest_prompt(vault, attended=True)
        assert "write_page()" in prompt
        assert "Do NOT use write_page()" not in prompt

    def test_unattended_prompt_forbids_write_page(self, vault: Vault) -> None:
        prompt = build_digest_prompt(vault, attended=False)
        assert "Do NOT use write_page()" in prompt
        assert "Promotion Candidates" in prompt
        assert "unattended" in prompt.lower()
