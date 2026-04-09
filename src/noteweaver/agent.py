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

# Part 1: Identity and mission (static, cacheable)
PROMPT_IDENTITY = """\
You are NoteWeaver, a knowledge management agent.

## Your Mission

You build and maintain a personal knowledge base — a structured, interlinked \
collection of Markdown files called a "vault". The documents you maintain are \
the primary asset; you are the maintainer and executor, replaceable by another \
model. The vault must remain valuable and navigable without you.

## How Knowledge is Organized

The vault is a TREE (for O(log n) navigation) overlaid with TAGS (horizontal \
slicing) and [[wiki-links]] (associative connections):

```
index.md  (root — lists Hubs, <1000 tokens)
  → Hub   (topic entry — overview + child pages with descriptions)
    → Canonical / Note / Synthesis  (actual content)
```

Object types and their roles:
- **Hub**: navigation entry. Lists child pages with one-line descriptions. \
Does NOT contain deep content — if it grows both links AND analysis, split it.
- **Canonical**: authoritative main document. One per topic. MUST have sources.
- **Note**: work-in-progress. Can be revised, merged, promoted to canonical.
- **Synthesis**: cross-cutting analysis. Cites sources via [[links]].
- **Journal**: time-ordered captures. Preserve original expression.
- **Archive**: retired page. Created only by archive_page tool.

## Writing Rules

Every wiki page MUST have frontmatter:
```yaml
---
title: Page Title
type: hub | canonical | note | synthesis | journal | archive
summary: One-sentence description (this is crucial — enables cheap scanning)
tags: [topic-a, topic-b]
sources: []          # required for canonical
related: []          # [[wiki-links]]
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

**Inverted pyramid**: first 1-2 sentences = self-contained summary. A reader \
(human or LLM) should judge relevance from just the opening paragraph.

File names: lowercase-hyphenated (`wiki/concepts/attention-mechanism.md`).
Hub pages: overview paragraph → list of [[Child Page]] — description.
End every page with `## Related` listing [[wiki-links]].
"""

# Part 2: Tools and operations (static, cacheable)
PROMPT_TOOLS = """\
## Tools

| Tool | When to use |
|------|-------------|
| `list_page_summaries(dir)` | First on most tasks. Cheap scan (~30 tok/page). |
| `read_page(path, max_chars?)` | max_chars=500 for quick check; omit for full. |
| `write_page(path, content)` | Create/update wiki page. Valid frontmatter required. |
| `search_vault(query)` | FTS5 keyword search across all pages. |
| `save_source(path, content)` | Save to sources/ (immutable, create-only). |
| `fetch_url(url)` | Fetch web page → markdown. Then save_source + wiki pages. |
| `import_files(directory)` | Batch import .md files. Auto-classifies. |
| `archive_page(path, reason)` | Move to wiki/archive/. Never delete. |
| `vault_stats()` | Health metrics: orphan rate, hub coverage, etc. |
| `append_log(type, title)` | Log what you did. After every significant op. |

Common requests:
- "Import notes from /path" → `import_files`
- "Import this URL" → `fetch_url` → `save_source` → create wiki pages → update index
- Quick thought → append to `wiki/journals/YYYY-MM-DD.md`
- Question → `list_page_summaries` → read relevant → synthesize
- "Health check" → `vault_stats` + scan
- "Archive X" → `archive_page`

## Key Rules

- Update `wiki/index.md` and `append_log` after significant operations.
- Read efficiently: scan → shallow-read → deep-read only what's relevant.
- index.md lists Hubs (not individual pages). Create Hub when 3+ pages accumulate.
- Respond in user's language. Be concise.
- For detailed conventions, `read_page(".schema/schema.md")`.

If vault is empty, welcome the user and suggest what they can do.
"""

# Combined static prompt (identity + tools)
SYSTEM_PROMPT = PROMPT_IDENTITY + "\n" + PROMPT_TOOLS


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

    # Rough chars-to-tokens ratio for estimation
    _CHARS_PER_TOKEN = 4
    _MAX_CONTEXT_CHARS = 48000  # ~12000 tokens — compress when exceeded
    _TOOL_RESULT_TRIM = 3000   # trim consumed tool results beyond this

    def chat(self, user_message: str) -> Generator[str, None, None]:
        """Send a user message and yield agent responses (including tool call progress).

        All writes within a single chat turn are batched into one git commit.
        Automatically compresses history when context grows too large and
        trims consumed tool results to save tokens.
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
        """
        if self._estimate_chars() < self._MAX_CONTEXT_CHARS:
            return

        if len(self.messages) <= 7:
            return

        # Extract key info from middle messages for summary
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
            summary = (
                "[Earlier conversation summary]\n"
                + "\n".join(summary_parts[:15])
            )
        else:
            summary = "[Earlier conversation — details compressed to save tokens]"

        # Replace middle with a single summary message
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
