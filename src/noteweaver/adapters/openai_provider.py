"""OpenAI-compatible LLM provider."""

from __future__ import annotations

from openai import OpenAI

from noteweaver.adapters.provider import LLMProvider, CompletionResult, ToolCall
from noteweaver.adapters.retry import with_retry


class OpenAIProvider(LLMProvider):
    """Provider for OpenAI and any OpenAI-compatible API endpoint."""

    def __init__(self, api_key: str, base_url: str | None = None) -> None:
        self.client = OpenAI(api_key=api_key, base_url=base_url)

    def chat_completion(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict],
    ) -> tuple[CompletionResult, dict]:
        response = with_retry(
            self.client.chat.completions.create,
            model=model,
            messages=messages,
            tools=tools,
        )
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls.append(ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=tc.function.arguments,
                ))

        result = CompletionResult(
            content=message.content,
            tool_calls=tool_calls,
        )
        return result, message.model_dump()

    def simple_completion(self, model: str, messages: list[dict]) -> str | None:
        """Simple completion without tools — used for journal generation."""
        response = with_retry(
            self.client.chat.completions.create,
            model=model,
            messages=messages,
        )
        return response.choices[0].message.content
