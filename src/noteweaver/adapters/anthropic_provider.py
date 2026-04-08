"""Anthropic-compatible LLM provider.

Translates between the OpenAI-style tool schemas used internally by
NoteWeaver and the Anthropic Messages API format.
"""

from __future__ import annotations

import json
from typing import Any

from noteweaver.adapters.provider import LLMProvider, CompletionResult, ToolCall


def _openai_tools_to_anthropic(tools: list[dict]) -> list[dict]:
    """Convert OpenAI function-calling tool schemas to Anthropic format.

    OpenAI format:
        {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}

    Anthropic format:
        {"name": ..., "description": ..., "input_schema": ...}
    """
    result = []
    for tool in tools:
        fn = tool.get("function", {})
        result.append({
            "name": fn["name"],
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return result


def _build_anthropic_messages(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """Split messages into system prompt and conversation messages.

    Anthropic expects system as a top-level parameter, not in the messages list.
    Also converts OpenAI-style tool result messages to Anthropic format.
    """
    system = None
    conversation: list[dict] = []

    for msg in messages:
        role = msg.get("role", "")

        if role == "system":
            system = msg.get("content", "")
            continue

        if role == "assistant":
            content = _convert_assistant_message(msg)
            conversation.append({"role": "assistant", "content": content})
            continue

        if role == "tool":
            _append_tool_result(conversation, msg)
            continue

        # user messages pass through
        conversation.append({"role": "user", "content": msg.get("content", "")})

    return system, conversation


def _convert_assistant_message(msg: dict) -> list[dict]:
    """Convert an OpenAI assistant message to Anthropic content blocks."""
    blocks: list[dict] = []

    text = msg.get("content")
    if text:
        blocks.append({"type": "text", "text": text})

    tool_calls = msg.get("tool_calls", [])
    if tool_calls:
        for tc in tool_calls:
            fn = tc.get("function", {})
            try:
                input_data = json.loads(fn.get("arguments", "{}"))
            except (json.JSONDecodeError, TypeError):
                input_data = {}
            blocks.append({
                "type": "tool_use",
                "id": tc.get("id", ""),
                "name": fn.get("name", ""),
                "input": input_data,
            })

    if not blocks:
        blocks.append({"type": "text", "text": ""})

    return blocks


def _append_tool_result(conversation: list[dict], msg: dict) -> None:
    """Append a tool result to the conversation.

    Anthropic expects tool results as a user message with tool_result content blocks.
    Multiple consecutive tool results are batched into one user message.
    """
    result_block = {
        "type": "tool_result",
        "tool_use_id": msg.get("tool_call_id", ""),
        "content": msg.get("content", ""),
    }

    if conversation and conversation[-1].get("role") == "user":
        last_content = conversation[-1].get("content")
        if isinstance(last_content, list) and last_content and last_content[0].get("type") == "tool_result":
            last_content.append(result_block)
            return

    conversation.append({"role": "user", "content": [result_block]})


class AnthropicProvider(LLMProvider):
    """Provider for Anthropic and any Anthropic-compatible API endpoint."""

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        try:
            import anthropic
        except ImportError:
            raise ImportError(
                "anthropic package is required for Anthropic provider. "
                "Install it with: pip install anthropic"
            )

        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self.client = anthropic.Anthropic(**kwargs)

    def chat_completion(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict],
    ) -> tuple[CompletionResult, dict]:
        anthropic_tools = _openai_tools_to_anthropic(tools)
        system, conversation = _build_anthropic_messages(messages)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 4096,
            "messages": conversation,
            "tools": anthropic_tools,
        }
        if system:
            kwargs["system"] = system

        response = self.client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=json.dumps(block.input),
                ))

        content = "\n".join(text_parts) if text_parts else None
        result = CompletionResult(content=content, tool_calls=tool_calls)

        raw_message = self._to_openai_message(content, tool_calls)
        return result, raw_message

    @staticmethod
    def _to_openai_message(content: str | None, tool_calls: list[ToolCall]) -> dict:
        """Build an OpenAI-compatible assistant message dict for history."""
        msg: dict[str, Any] = {
            "role": "assistant",
            "content": content,
        }
        if tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments,
                    },
                }
                for tc in tool_calls
            ]
        return msg
