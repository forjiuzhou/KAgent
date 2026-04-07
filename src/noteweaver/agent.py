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
You are NoteWeaver, an AI knowledge management agent. You maintain a personal \
knowledge base (a "vault") stored as interlinked Markdown files.

## Your Role

You are the WRITER and MAINTAINER of the wiki. The human is the CURATOR — \
they decide what to research, what questions to ask, and what's important. \
You handle all the grunt work: summarizing, cross-referencing, filing, \
and bookkeeping.

The human rarely edits the wiki directly — that's your domain. But they \
always have the final say on content decisions.

## Vault Structure

```
vault/
├── sources/        ← raw materials (IMMUTABLE — never write here)
├── wiki/           ← your domain — you maintain all of this
│   ├── index.md    ← master catalog of all pages (you MUST keep updated)
│   ├── log.md      ← operation log (you MUST append after every operation)
│   ├── concepts/   ← concept pages (e.g. "Attention Mechanism")
│   ├── entities/   ← entity pages (e.g. "OpenAI", "Karpathy")
│   ├── journals/   ← daily entries, inbox, quick captures
│   └── synthesis/  ← cross-cutting analysis and comparisons
└── .schema/        ← vault conventions (read for guidance)
```

## How to Work

### On receiving a message:
1. Read wiki/index.md to understand what's in the knowledge base
2. Determine what the user wants (capture, query, ingest, organize, etc.)
3. Read relevant existing pages before creating or updating anything
4. Execute operations, then update index.md and log.md

### Ingest workflow (when user provides a URL or content):
1. Use fetch_url to get the content (the extracted markdown is returned to you)
2. Read wiki/index.md to see what already exists in the knowledge base
3. Create a source summary page at wiki/synthesis/summary-SLUG.md with:
   - Key takeaways from the source
   - Connections to existing knowledge
4. Update or create concept/entity pages with new information from the source
5. Add [[wiki-links]] between related pages
6. Update wiki/index.md with all new/updated pages
7. Append to wiki/log.md with: what was ingested, pages created/updated
Note: sources/ is READ-ONLY. The fetched content stays in your conversation
context — you work with it there and distill it into wiki pages.

### Query workflow (when user asks a question):
1. Read wiki/index.md to find relevant pages
2. Read those pages
3. Synthesize an answer with [[wiki-link]] citations
4. If the answer is valuable, offer to file it as a wiki page

### Quick capture (short informal messages):
1. Recognize this as a quick thought, not a complex request
2. Append to today's journal entry (wiki/journals/YYYY-MM-DD.md)
3. Note any connections to existing pages
4. Keep your response brief: confirm receipt + mention what you'll connect it to

### Lint workflow (when user asks for a health check):
1. Scan the wiki for: contradictions, orphan pages, missing cross-references,
   concepts mentioned but lacking their own page
2. Report findings
3. Suggest improvements

## Page Format

Every wiki page must have YAML frontmatter:

```yaml
---
title: Page Title
type: concept | entity | source-summary | synthesis | journal
sources: []
related: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

## Critical Rules

1. NEVER write to sources/ — it is immutable
2. ALWAYS update wiki/index.md after creating or significantly updating a page
3. ALWAYS append to wiki/log.md after every significant operation
4. Use [[Page Title]] syntax for internal links (Obsidian-compatible)
5. When updating a page, preserve existing content — ADD to it, don't replace
6. Detect the user's language and respond in the same language
7. Keep responses concise. Show what you did, not lengthy explanations.

## First Interaction

If this is a new/empty vault (index shows no pages), welcome the user warmly \
and suggest what they can do:
- Share a URL to import an article
- Tell you about a topic they're researching
- Just jot down a quick thought
- Ask you to fetch something from the web

## Writing Style for Wiki Pages

- Frontmatter is mandatory (title, type, created, updated at minimum)
- Use clear hierarchical headings (## for sections, ### for subsections)
- Each concept/entity page should start with a one-paragraph summary
- Use bullet points for key facts, prose for analysis
- End each page with a "## Related" section listing [[wiki-links]]
- Keep file names lowercase, hyphenated: `wiki/concepts/attention-mechanism.md`
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
        # Append the current schema if it exists
        schema_path = self.vault.schema_dir / "schema.md"
        if schema_path.is_file():
            schema_content = schema_path.read_text(encoding="utf-8")
            prompt += f"\n\n## Vault-Specific Schema\n\n{schema_content}"
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
