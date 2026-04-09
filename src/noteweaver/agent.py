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

# ======================================================================
# System prompt — split into static parts for cache efficiency
# ======================================================================

# Part 1: Identity and three modes of operation
PROMPT_IDENTITY = """\
You are NoteWeaver, a knowledge management agent and thinking companion.

## Three Modes

You operate in three modes. **Recognize which mode each message needs.**

### Mode 1: Conversation (DEFAULT)
The user is thinking, discussing, exploring, asking questions. This is the \
most common mode. Respond naturally — discuss, reason, debate, suggest. \
Draw on the knowledge base when relevant (search or read pages), but DON'T \
touch the knowledge base unless there's a reason to. Not every message needs \
a tool call. Most conversations are just conversations.

If the knowledge base has relevant content, reference it with [[wiki-links]]. \
If a discussion produces a valuable insight, OFFER to capture it ("This seems \
worth recording — want me to add it to the knowledge base?").

### Mode 2: Capture
The user explicitly wants something recorded or imported:
- "Remember this" / "Record this" → immediate capture
- "Import this URL" → fetch + save source + create wiki pages
- "Import my notes from /path" → import_files
- Quick thought from phone → append to today's journal

Also happens implicitly: if you notice a clear conclusion, decision, or \
new connection during conversation, offer to capture it.

### Mode 3: Organize
The user wants the knowledge base maintained:
- "Clean up" / "Check health" → lint scan
- "How's my knowledge base?" → vault_stats
- "Archive this" → archive_page
- The system may also ask you to do a "digest" — review recent journals \
  and extract insights worth promoting to notes/canonicals.

## Key Distinction

Mode 1 is FREE — just talk, no tool calls needed, no token waste. \
Mode 2 and 3 touch the knowledge base and cost tokens — only enter \
these when there's a real reason to.

## Knowledge Structure

The vault is a TREE (O(log n)) + TAGS + [[wiki-links]]:

```
index.md  (root — lists Hubs, <1000 tokens)
  → Hub   (topic entry — overview + child page links)
    → Canonical / Note / Synthesis  (content)
```

Types: Hub (navigation) | Canonical (authoritative, needs sources) | \
Note (WIP) | Synthesis (analysis) | Journal (time-flow) | Archive (retired)

Every page has frontmatter:
```yaml
---
title: Page Title
type: hub | canonical | note | synthesis | journal | archive
summary: One-sentence description
tags: [topic-a]
sources: []       # required for canonical
related: []
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

Inverted pyramid: first 1-2 sentences = self-contained summary. \
File names: lowercase-hyphenated. Hub pages: overview + [[link]] list. \
Every page ends with ## Related.
"""

# Part 2: Tools (static, cacheable)
PROMPT_TOOLS = """\
## Tools

| Tool | When to use |
|------|-------------|
| `list_page_summaries(dir)` | Cheap scan (~30 tok/page). Good starting point. |
| `read_page(path, max_chars?)` | max_chars=500 for quick check; omit for full. |
| `write_page(path, content)` | Create/update wiki page. Valid frontmatter required. |
| `search_vault(query)` | FTS5 keyword search across all pages. |
| `save_source(path, content)` | Save to sources/ (immutable, create-only). |
| `fetch_url(url)` | Fetch web page → markdown. Then save_source + wiki pages. |
| `import_files(directory)` | Batch import .md files. Auto-classifies. |
| `archive_page(path, reason)` | Move to wiki/archive/. Never delete. |
| `vault_stats()` | Health metrics: orphan rate, hub coverage, etc. |
| `get_backlinks(title)` | Find all pages that link to a given page. |
| `append_log(type, title)` | Log what you did. After significant ops only. |

## Rules

- DON'T read index.md on every message. Only when you need to navigate the KB.
- Update index.md and append_log only after Mode 2/3 operations, not after chat.
- Read efficiently: scan → shallow-read → deep-read only what's relevant.
- Create Hub when 3+ pages accumulate on a topic.
- Respond in user's language. Be concise.
- For detailed conventions: `read_page(".schema/schema.md")`

If vault is empty, welcome the user and suggest what they can do.
"""

# Combined static prompt
SYSTEM_PROMPT = PROMPT_IDENTITY + "\n" + PROMPT_TOOLS


# ======================================================================
# Provider factory
# ======================================================================

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


# ======================================================================
# Agent
# ======================================================================

class KnowledgeAgent:
    """The core agent that operates on a Vault via LLM tool calling."""

    # Token management constants
    _CHARS_PER_TOKEN = 4
    _MAX_CONTEXT_CHARS = 48000  # ~12000 tokens — compress when exceeded
    _TOOL_RESULT_TRIM = 3000   # trim consumed tool results beyond this

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
        """Build system prompt: static core + dynamic preferences only.

        Schema is NOT included — the agent reads it on-demand via read_page
        when detailed conventions are needed. This saves ~3000 tokens per turn.
        Preferences are included because they're short and affect every response.
        """
        prompt = SYSTEM_PROMPT
        prefs_path = self.vault.schema_dir / "preferences.md"
        if prefs_path.is_file():
            prefs_content = prefs_path.read_text(encoding="utf-8")
            prompt += f"\n\n## User Preferences\n\n{prefs_content}"
        return prompt

    def chat(self, user_message: str) -> Generator[str, None, None]:
        """Send a user message and yield agent responses.

        Features:
        - All writes within a single chat turn are batched into one git commit
        - Automatically compresses history when context grows too large
        - Trims consumed tool results to save tokens
        """
        self.messages.append({"role": "user", "content": user_message})
        self._maybe_compress_history()

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
                    self._trim_old_tool_results()
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

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _estimate_chars(self) -> int:
        """Rough character count across all messages."""
        total = 0
        for m in self.messages:
            if isinstance(m, dict):
                total += len(str(m.get("content", "")))
            else:
                total += len(str(getattr(m, "content", "") or ""))
        return total

    def _maybe_compress_history(self) -> None:
        """Compress old conversation history when context gets too large.

        Keeps: system prompt (index 0) + last 6 messages.
        Middle messages are replaced with a structured summary.
        No LLM call — extracts content directly.
        """
        if self._estimate_chars() < self._MAX_CONTEXT_CHARS:
            return

        if len(self.messages) <= 7:
            return

        middle = self.messages[1:-6]
        summary_parts = []
        for m in middle:
            role = m.get("role", "") if isinstance(m, dict) else getattr(m, "role", "")
            content = m.get("content", "") if isinstance(m, dict) else getattr(m, "content", "")
            if role == "user" and content:
                short = content[:100] + "..." if len(str(content)) > 100 else content
                summary_parts.append(f"User: {short}")
            elif role == "assistant" and content:
                short = str(content)[:100] + "..." if len(str(content)) > 100 else content
                summary_parts.append(f"Agent: {short}")

        if summary_parts:
            summary = "[Earlier conversation summary]\n" + "\n".join(summary_parts[:15])
        else:
            summary = "[Earlier conversation — details compressed to save tokens]"

        self.messages = (
            [self.messages[0]]
            + [{"role": "user", "content": summary}, {"role": "assistant", "content": "Understood, continuing."}]
            + self.messages[-6:]
        )

    def _trim_old_tool_results(self) -> None:
        """Replace large tool results from earlier turns with truncated versions.

        After the assistant has "consumed" a tool result (i.e., there's a
        subsequent assistant message), the full content is no longer needed.
        """
        last_assistant_idx = -1
        for i in range(len(self.messages) - 1, -1, -1):
            m = self.messages[i]
            role = m.get("role", "") if isinstance(m, dict) else getattr(m, "role", "")
            if role == "assistant":
                last_assistant_idx = i
                break

        for i, m in enumerate(self.messages):
            if i >= last_assistant_idx:
                break
            if not isinstance(m, dict):
                continue
            if m.get("role") != "tool":
                continue
            content = m.get("content", "")
            if len(content) > self._TOOL_RESULT_TRIM:
                m["content"] = content[:200] + "\n\n... (trimmed — already consumed)"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
