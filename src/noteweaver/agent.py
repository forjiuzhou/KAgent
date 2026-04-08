"""KnowledgeAgent — the core agent loop.

The agent uses an LLM with tool calling to operate on a Vault.
It can ONLY use the knowledge operation tools defined in tools/definitions.py.
No shell, no code execution, no arbitrary file access.
"""

from __future__ import annotations

import json
from typing import Generator

from openai import OpenAI

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


class KnowledgeAgent:
    """The core agent that operates on a Vault via LLM tool calling."""

    def __init__(
        self,
        vault: Vault,
        model: str = "gpt-4o-mini",
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        self.vault = vault
        self.model = model
        self.client = OpenAI(api_key=api_key, base_url=base_url)
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
        """Send a user message and yield agent responses (including tool call progress)."""
        self.messages.append({"role": "user", "content": user_message})

        max_steps = 25
        for _ in range(max_steps):
            response = self.client.chat.completions.create(
                model=self.model,
                messages=self.messages,
                tools=TOOL_SCHEMAS,
            )

            choice = response.choices[0]
            message = choice.message

            # Append assistant message to history
            self.messages.append(message.model_dump())

            # If no tool calls, we're done — yield the final text
            if not message.tool_calls:
                if message.content:
                    yield message.content
                return

            # Execute tool calls
            for tool_call in message.tool_calls:
                fn_name = tool_call.function.name
                try:
                    fn_args = json.loads(tool_call.function.arguments)
                except json.JSONDecodeError:
                    fn_args = {}

                yield f"  ↳ {fn_name}({self._summarize_args(fn_args)})"

                result = dispatch_tool(self.vault, fn_name, fn_args)

                # Truncate very long results to avoid context overflow
                if len(result) > 8000:
                    result = result[:8000] + "\n\n... (truncated)"

                self.messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result,
                })

        yield "(reached maximum steps)"

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
