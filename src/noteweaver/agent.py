"""KnowledgeAgent — the core agent loop.

The agent uses an LLM with tool calling to operate on a Vault.
It can ONLY use the knowledge operation tools defined in tools/definitions.py.
No shell, no code execution, no arbitrary file access.
"""

from __future__ import annotations

import json
from typing import Generator

from noteweaver.adapters.provider import LLMProvider
from noteweaver.vault import Vault
from noteweaver.tools.definitions import TOOL_SCHEMAS, dispatch_tool

SYSTEM_PROMPT = """\
You are NoteWeaver, a knowledge management agent. You maintain a vault of \
interlinked Markdown files.

You are the WRITER. The human is the CURATOR. Read .schema/schema.md for \
the full operating manual — it defines the principles, object types, \
workflows, and constraints you must follow.

Key reminders (details in schema):
- Navigate: index.md → Hub → Page. Use list_page_summaries to scan cheaply.
- Read efficiently: scan first (list_page_summaries), shallow-read if needed \
  (read_page with max_chars=500), deep-read only what's relevant.
- Write: every page needs frontmatter (title, type, summary, tags).
- Update index.md and log.md after every significant operation.
- Respond in the user's language. Be concise.

If the vault is empty, welcome the user and suggest:
- Share a URL to import an article
- Describe a topic they're researching
- Jot down a quick thought
"""


def create_provider(
    provider_name: str,
    api_key: str,
    base_url: str | None = None,
) -> LLMProvider:
    """Factory: create the appropriate LLM provider."""
    if provider_name == "anthropic":
        from noteweaver.adapters.anthropic_provider import AnthropicProvider
        return AnthropicProvider(api_key=api_key, base_url=base_url)
    else:
        from noteweaver.adapters.openai_provider import OpenAIProvider
        return OpenAIProvider(api_key=api_key, base_url=base_url)


class KnowledgeAgent:
    """The core agent that operates on a Vault via LLM tool calling."""

    def __init__(
        self,
        vault: Vault,
        model: str = "gpt-4o-mini",
        provider: LLMProvider | None = None,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        provider_name: str = "openai",
    ) -> None:
        self.vault = vault
        self.model = model
        if provider is not None:
            self.provider = provider
        else:
            self.provider = create_provider(
                provider_name, api_key=api_key or "", base_url=base_url
            )
        self.messages: list[dict] = [{"role": "system", "content": self._build_system_prompt()}]

    def _build_system_prompt(self) -> str:
        prompt = SYSTEM_PROMPT
        schema_path = self.vault.schema_dir / "schema.md"
        if schema_path.is_file():
            schema_content = schema_path.read_text(encoding="utf-8")
            prompt += f"\n\n## Vault-Specific Schema\n\n{schema_content}"
        prefs_path = self.vault.schema_dir / "preferences.md"
        if prefs_path.is_file():
            prefs_content = prefs_path.read_text(encoding="utf-8")
            prompt += f"\n\n## User Preferences\n\n{prefs_content}"
        return prompt

    def chat(self, user_message: str) -> Generator[str, None, None]:
        """Send a user message and yield agent responses (including tool call progress).

        All writes within a single chat turn are batched into one git commit.
        """
        self.messages.append({"role": "user", "content": user_message})

        short_msg = user_message[:60] + "..." if len(user_message) > 60 else user_message
        self.vault._operation_depth += 1

        try:
            max_steps = 25
            for _ in range(max_steps):
                completion, raw_message = self.provider.chat_completion(
                    model=self.model,
                    messages=self.messages,
                    tools=TOOL_SCHEMAS,
                )

                self.messages.append(raw_message)

                if not completion.tool_calls:
                    if completion.content:
                        yield completion.content
                    return

                for tool_call in completion.tool_calls:
                    try:
                        fn_args = json.loads(tool_call.arguments)
                    except json.JSONDecodeError:
                        fn_args = {}

                    yield f"  ↳ {tool_call.name}({self._summarize_args(fn_args)})"

                    result = dispatch_tool(self.vault, tool_call.name, fn_args)

                    if len(result) > 8000:
                        result = result[:8000] + "\n\n... (truncated)"

                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    })

            yield "(reached maximum steps)"
        finally:
            self._end_operation(short_msg)

    def _end_operation(self, message: str) -> None:
        """Finalize the operation — commit all batched writes."""
        self.vault._operation_depth = max(0, self.vault._operation_depth - 1)
        if self.vault._operation_depth == 0 and self.vault._operation_dirty:
            self.vault._git_commit(message)
            self.vault._operation_dirty = False

    @staticmethod
    def _summarize_args(args: dict) -> str:
        """Short summary of tool arguments for display."""
        parts = []
        for k, v in args.items():
            s = str(v)
            if len(s) > 60:
                s = s[:57] + "..."
            parts.append(f"{k}={s!r}")
        return ", ".join(parts)
