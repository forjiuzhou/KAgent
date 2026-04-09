"""Tests for the LLM provider abstraction layer."""

import json
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

from noteweaver.adapters.provider import LLMProvider, CompletionResult, ToolCall
from noteweaver.adapters.openai_provider import OpenAIProvider
from noteweaver.adapters.anthropic_provider import (
    AnthropicProvider,
    _openai_tools_to_anthropic,
    _build_anthropic_messages,
)
from noteweaver.config import Config, PROVIDER_OPENAI, PROVIDER_ANTHROPIC
from noteweaver.agent import KnowledgeAgent, create_provider


# ======================================================================
# Tool schema conversion
# ======================================================================


class TestToolSchemaConversion:
    def test_openai_to_anthropic_basic(self) -> None:
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_page",
                    "description": "Read a file.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path"},
                        },
                        "required": ["path"],
                    },
                },
            }
        ]
        result = _openai_tools_to_anthropic(openai_tools)
        assert len(result) == 1
        assert result[0]["name"] == "read_page"
        assert result[0]["description"] == "Read a file."
        assert result[0]["input_schema"]["type"] == "object"
        assert "path" in result[0]["input_schema"]["properties"]

    def test_openai_to_anthropic_multiple(self) -> None:
        openai_tools = [
            {"type": "function", "function": {"name": "a", "description": "A", "parameters": {"type": "object", "properties": {}}}},
            {"type": "function", "function": {"name": "b", "description": "B", "parameters": {"type": "object", "properties": {}}}},
        ]
        result = _openai_tools_to_anthropic(openai_tools)
        assert len(result) == 2
        assert result[0]["name"] == "a"
        assert result[1]["name"] == "b"


# ======================================================================
# Message conversion
# ======================================================================


class TestMessageConversion:
    def test_system_extracted(self) -> None:
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        system, conversation = _build_anthropic_messages(messages)
        assert system == "You are helpful."
        assert len(conversation) == 1
        assert conversation[0]["role"] == "user"

    def test_user_message_passthrough(self) -> None:
        messages = [{"role": "user", "content": "Hi"}]
        system, conversation = _build_anthropic_messages(messages)
        assert system is None
        assert conversation[0]["content"] == "Hi"

    def test_assistant_with_tool_calls_converted(self) -> None:
        messages = [
            {
                "role": "assistant",
                "content": "Let me check.",
                "tool_calls": [
                    {
                        "id": "tc_1",
                        "type": "function",
                        "function": {"name": "read_page", "arguments": '{"path": "wiki/index.md"}'},
                    }
                ],
            }
        ]
        _, conversation = _build_anthropic_messages(messages)
        blocks = conversation[0]["content"]
        assert blocks[0]["type"] == "text"
        assert blocks[0]["text"] == "Let me check."
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["name"] == "read_page"
        assert blocks[1]["input"] == {"path": "wiki/index.md"}

    def test_tool_result_converted(self) -> None:
        messages = [
            {"role": "tool", "tool_call_id": "tc_1", "content": "# Wiki Index"},
        ]
        _, conversation = _build_anthropic_messages(messages)
        assert conversation[0]["role"] == "user"
        content = conversation[0]["content"]
        assert content[0]["type"] == "tool_result"
        assert content[0]["tool_use_id"] == "tc_1"
        assert content[0]["content"] == "# Wiki Index"

    def test_consecutive_tool_results_batched(self) -> None:
        messages = [
            {"role": "tool", "tool_call_id": "tc_1", "content": "result1"},
            {"role": "tool", "tool_call_id": "tc_2", "content": "result2"},
        ]
        _, conversation = _build_anthropic_messages(messages)
        assert len(conversation) == 1
        assert len(conversation[0]["content"]) == 2


# ======================================================================
# Config provider detection
# ======================================================================


class TestConfigProviderDetection:
    def test_default_is_openai(self, tmp_path: Path) -> None:
        with patch.dict("os.environ", {}, clear=True):
            cfg = Config.load(tmp_path)
        assert cfg.provider == PROVIDER_OPENAI
        assert cfg.model == "gpt-4o-mini"

    def test_anthropic_detected_from_env(self, tmp_path: Path) -> None:
        env = {"ANTHROPIC_API_KEY": "sk-ant-test"}
        with patch.dict("os.environ", env, clear=True):
            cfg = Config.load(tmp_path)
        assert cfg.provider == PROVIDER_ANTHROPIC
        assert cfg.api_key == "sk-ant-test"
        assert "claude" in cfg.model

    def test_openai_wins_when_both_keys_set(self, tmp_path: Path) -> None:
        env = {"OPENAI_API_KEY": "sk-openai", "ANTHROPIC_API_KEY": "sk-ant"}
        with patch.dict("os.environ", env, clear=True):
            cfg = Config.load(tmp_path)
        assert cfg.provider == PROVIDER_OPENAI
        assert cfg.api_key == "sk-openai"

    def test_explicit_provider_override(self, tmp_path: Path) -> None:
        env = {
            "NW_PROVIDER": "anthropic",
            "OPENAI_API_KEY": "sk-openai",
            "ANTHROPIC_API_KEY": "sk-ant",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = Config.load(tmp_path)
        assert cfg.provider == PROVIDER_ANTHROPIC
        assert cfg.api_key == "sk-ant"

    def test_anthropic_base_url(self, tmp_path: Path) -> None:
        env = {
            "NW_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "sk-ant",
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:8082",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = Config.load(tmp_path)
        assert cfg.base_url == "http://127.0.0.1:8082"

    def test_openai_api_base_alias(self, tmp_path: Path) -> None:
        env = {
            "OPENAI_API_KEY": "sk-o",
            "OPENAI_API_BASE": "https://gateway.example/v1",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = Config.load(tmp_path)
        assert cfg.base_url == "https://gateway.example/v1"

    def test_openai_base_url_precedence_over_api_base(self, tmp_path: Path) -> None:
        env = {
            "OPENAI_API_KEY": "sk-o",
            "OPENAI_BASE_URL": "https://primary.example/v1",
            "OPENAI_API_BASE": "https://fallback.example/v1",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = Config.load(tmp_path)
        assert cfg.base_url == "https://primary.example/v1"

    def test_claude_api_url_alias(self, tmp_path: Path) -> None:
        env = {
            "NW_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "sk-ant",
            "CLAUDE_API_URL": "http://127.0.0.1:9999",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = Config.load(tmp_path)
        assert cfg.base_url == "http://127.0.0.1:9999"

    def test_nw_model_overrides_default(self, tmp_path: Path) -> None:
        env = {"ANTHROPIC_API_KEY": "sk-ant", "NW_MODEL": "claude-3-haiku-20240307"}
        with patch.dict("os.environ", env, clear=True):
            cfg = Config.load(tmp_path)
        assert cfg.model == "claude-3-haiku-20240307"

    def test_auth_token_detected_as_anthropic(self, tmp_path: Path) -> None:
        env = {"ANTHROPIC_AUTH_TOKEN": "token-from-proxy"}
        with patch.dict("os.environ", env, clear=True):
            cfg = Config.load(tmp_path)
        assert cfg.provider == PROVIDER_ANTHROPIC
        assert cfg.api_key == "token-from-proxy"

    def test_auth_token_with_base_url(self, tmp_path: Path) -> None:
        env = {
            "ANTHROPIC_AUTH_TOKEN": "proxy-token",
            "ANTHROPIC_BASE_URL": "http://127.0.0.1:8082",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = Config.load(tmp_path)
        assert cfg.provider == PROVIDER_ANTHROPIC
        assert cfg.api_key == "proxy-token"
        assert cfg.base_url == "http://127.0.0.1:8082"

    def test_api_key_takes_precedence_over_auth_token(self, tmp_path: Path) -> None:
        env = {
            "ANTHROPIC_API_KEY": "the-real-key",
            "ANTHROPIC_AUTH_TOKEN": "fallback-token",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = Config.load(tmp_path)
        assert cfg.api_key == "the-real-key"

    def test_explicit_provider_with_auth_token(self, tmp_path: Path) -> None:
        env = {
            "NW_PROVIDER": "anthropic",
            "ANTHROPIC_AUTH_TOKEN": "proxy-token",
            "OPENAI_API_KEY": "sk-openai",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = Config.load(tmp_path)
        assert cfg.provider == PROVIDER_ANTHROPIC
        assert cfg.api_key == "proxy-token"


# ======================================================================
# Provider factory
# ======================================================================


class TestCreateProvider:
    def test_create_openai_provider(self) -> None:
        provider = create_provider("openai", api_key="test-key")
        assert isinstance(provider, OpenAIProvider)

    def test_create_anthropic_provider(self) -> None:
        provider = create_provider("anthropic", api_key="test-key")
        assert isinstance(provider, AnthropicProvider)

    def test_create_with_base_url(self) -> None:
        provider = create_provider(
            "anthropic", api_key="test-key", base_url="http://127.0.0.1:8082"
        )
        assert isinstance(provider, AnthropicProvider)


# ======================================================================
# Agent with provider
# ======================================================================


class TestAgentProvider:
    def test_agent_accepts_provider(self, tmp_path: Path) -> None:
        from noteweaver.vault import Vault
        v = Vault(tmp_path, auto_git=False)
        v.init()

        mock_provider = MagicMock(spec=LLMProvider)
        mock_provider.chat_completion.return_value = (
            CompletionResult(content="Hello!", tool_calls=[]),
            {"role": "assistant", "content": "Hello!"},
        )

        agent = KnowledgeAgent(vault=v, model="test-model", provider=mock_provider)
        responses = list(agent.chat("Hi"))

        assert responses == ["Hello!"]
        mock_provider.chat_completion.assert_called_once()

    def test_agent_handles_tool_calls(self, tmp_path: Path) -> None:
        from noteweaver.vault import Vault
        v = Vault(tmp_path, auto_git=False)
        v.init()

        mock_provider = MagicMock(spec=LLMProvider)
        mock_provider.chat_completion.side_effect = [
            (
                CompletionResult(
                    content=None,
                    tool_calls=[ToolCall(id="tc_1", name="read_page", arguments='{"path": "wiki/index.md"}')],
                ),
                {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": "tc_1", "type": "function", "function": {"name": "read_page", "arguments": '{"path": "wiki/index.md"}'}}],
                },
            ),
            (
                CompletionResult(content="Here's the index.", tool_calls=[]),
                {"role": "assistant", "content": "Here's the index."},
            ),
        ]

        agent = KnowledgeAgent(vault=v, model="test-model", provider=mock_provider)
        responses = list(agent.chat("Show me the index"))

        assert any("read_page" in r for r in responses)
        assert any("index" in r.lower() for r in responses)
        assert mock_provider.chat_completion.call_count == 2
