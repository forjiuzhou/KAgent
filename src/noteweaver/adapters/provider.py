"""LLM provider abstraction — unified interface for OpenAI and Anthropic APIs.

Both providers expose the same chat-with-tools interface so the agent core
never needs to know which backend is in use.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """A single tool invocation requested by the model."""
    id: str
    name: str
    arguments: str  # JSON string


@dataclass
class CompletionResult:
    """Unified result from a chat completion call."""
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMProvider(ABC):
    """Abstract base for LLM providers that support tool calling."""

    @abstractmethod
    def chat_completion(
        self,
        model: str,
        messages: list[dict],
        tools: list[dict],
    ) -> tuple[CompletionResult, dict]:
        """Run a chat completion with tool definitions.

        Returns:
            A tuple of (CompletionResult, raw_message_dict) where
            raw_message_dict is the serialized assistant message to
            append to conversation history.
        """
        ...

    def simple_completion(self, model: str, messages: list[dict]) -> str | None:
        """Simple completion without tools — used for journal generation.

        Default implementation uses chat_completion with empty tools list
        and discards the raw message.
        """
        result, _ = self.chat_completion(model, messages, tools=[])
        return result.content
