"""KnowledgeAgent — the core agent loop.

The agent uses an LLM with tool calling to operate on a Vault.
It can ONLY use the knowledge operation tools defined in tools/definitions.py.
No shell, no code execution, no arbitrary file access.

Context management architecture:

- self.messages (transcript): append-only record of the full conversation.
  Never modified by compression — the complete history is preserved.

- self._session_summary: structured summary of older conversation turns,
  generated when the transcript grows too large for the context window.

- _build_messages_for_query(): constructs the view actually sent to the LLM.
  Applies: system prompt + session memory + session summary + recent messages
  with tiered tool-result cleanup.  The transcript is never touched.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
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
| `read_transcript(filename)` | Read a saved conversation transcript (for digest). |
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

def _msg_role(m) -> str:
    """Extract role from a message (dict or object)."""
    return m.get("role", "") if isinstance(m, dict) else getattr(m, "role", "")


def _msg_content(m) -> str:
    """Extract content from a message (dict or object)."""
    if isinstance(m, dict):
        return m.get("content", "") or ""
    return getattr(m, "content", "") or ""


class KnowledgeAgent:
    """The core agent that operates on a Vault via LLM tool calling.

    Context management is split into three layers:

    1. **Transcript** (``self.messages``): complete, append-only conversation
       record.  Never mutated by compression.
    2. **Session summary** (``self._session_summary``): structured compression
       of older history, generated when the transcript outgrows the context
       window.
    3. **Query view** (``_build_messages_for_query``): the actual message list
       sent to the LLM each turn — assembled from the system prompt, session
       memory, session summary, and recent messages with tiered tool-result
       cleanup.
    """

    # Context budget
    _CHARS_PER_TOKEN = 4
    _MAX_CONTEXT_CHARS = 48000   # ~12 000 tokens — trigger summary when exceeded

    # Tool-result management
    _TOOL_RESULT_MAX = 8000      # hard cap on incoming tool results
    _TOOL_RESULT_PREVIEW = 500   # preview size for "recent-consumed" tier
    _RECENT_TURNS_FULL = 1       # completed turns whose tool results stay full
    _RECENT_TURNS_PREVIEW = 2    # additional turns that get preview treatment

    # Summary generation
    _RECENT_MESSAGES_KEEP = 6    # messages kept after summary boundary
    _SUMMARY_KEY_POINTS_MAX = 20 # max key-point lines in the summary text

    # Long-term memory
    _MEMORY_FILE_MAX_CHARS = 3000

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
        self.messages: list[dict] = [
            {"role": "system", "content": self._build_system_prompt()}
        ]
        self._session_summary: dict | None = None
        self._summary_boundary: int = 1  # messages[1:boundary] are summarised

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        """Build system prompt: static core + preferences + long-term memory.

        Schema is NOT included — the agent reads it on-demand via read_page
        when detailed conventions are needed.  This saves ~3 000 tokens/turn.
        """
        prompt = SYSTEM_PROMPT

        prefs_path = self.vault.schema_dir / "preferences.md"
        if prefs_path.is_file():
            prefs_content = prefs_path.read_text(encoding="utf-8")
            prompt += f"\n\n## User Preferences\n\n{prefs_content}"

        memory_path = self.vault.schema_dir / "memory.md"
        if memory_path.is_file():
            mem_content = memory_path.read_text(encoding="utf-8")
            if len(mem_content) <= self._MEMORY_FILE_MAX_CHARS:
                prompt += f"\n\n## Knowledge Base Memory\n\n{mem_content}"

        return prompt

    # ------------------------------------------------------------------
    # Session memory (cross-session continuity)
    # ------------------------------------------------------------------

    def _load_session_memory(self) -> str | None:
        """Load session memory from ``.meta/session-memory.md``."""
        mem_path = self.vault.meta_dir / "session-memory.md"
        if mem_path.is_file():
            content = mem_path.read_text(encoding="utf-8").strip()
            return content or None
        return None

    def save_session_memory(self) -> Path | None:
        """Extract and persist session memory for the next session.

        Scans the transcript for pages touched, tools used, and the last
        topic discussed.  Writes ``.meta/session-memory.md``.
        """
        if len(self.messages) <= 2:
            return None

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        pages: list[str] = []
        tools: list[str] = []
        last_user = ""
        last_agent = ""
        turns = 0

        for m in self.messages[1:]:
            role = _msg_role(m)
            content = _msg_content(m)
            if role == "user" and content:
                last_user = content
                turns += 1
            elif role == "assistant" and content:
                last_agent = content
            elif role == "assistant":
                tool_calls = (
                    m.get("tool_calls", [])
                    if isinstance(m, dict)
                    else getattr(m, "tool_calls", []) or []
                )
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        fn = tc.get("function", {})
                        name = fn.get("name", "")
                        args_raw = fn.get("arguments", "{}")
                    else:
                        name = getattr(tc, "name", "")
                        args_raw = getattr(tc, "arguments", "{}")
                    if name and name not in tools:
                        tools.append(name)
                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                        path = args.get("path", "")
                        if path and path not in pages:
                            pages.append(path)
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        pass

        topic_short = last_user[:200] + ("..." if len(last_user) > 200 else "")
        agent_short = last_agent[:300] + ("..." if len(last_agent) > 300 else "")

        lines = [
            f"---",
            f"updated: {now}",
            f"session_turns: {turns}",
            f"---",
            f"",
            f"## Last Session",
            f"",
            f"Topic: {topic_short}",
        ]
        if pages:
            lines.append(f"Pages touched: {', '.join(pages[:10])}")
        if agent_short:
            lines.append(f"Last response: {agent_short}")
        lines.append("")

        if tools:
            lines.append("## Tools Used")
            lines.append(f"{', '.join(tools[:15])}")
            lines.append("")

        content = "\n".join(lines) + "\n"
        mem_path = self.vault.meta_dir / "session-memory.md"
        mem_path.parent.mkdir(parents=True, exist_ok=True)
        mem_path.write_text(content, encoding="utf-8")
        return mem_path

    # ------------------------------------------------------------------
    # Transcript persistence
    # ------------------------------------------------------------------

    def get_transcript(self) -> list[dict]:
        """Return a copy of the full conversation transcript."""
        result = []
        for m in self.messages:
            if isinstance(m, dict):
                result.append(dict(m))
            else:
                result.append({
                    "role": getattr(m, "role", ""),
                    "content": getattr(m, "content", ""),
                })
        return result

    def save_transcript(self, directory: Path | None = None) -> Path:
        """Serialize the full transcript to a JSON file.

        Saves to ``.meta/transcripts/<timestamp>.json``.
        Returns the written path.
        """
        if directory is None:
            directory = self.vault.meta_dir / "transcripts"
        directory.mkdir(parents=True, exist_ok=True)

        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        path = directory / f"{ts}.json"

        serialisable = []
        for m in self.messages:
            if isinstance(m, dict):
                serialisable.append(m)
            else:
                entry: dict = {"role": getattr(m, "role", "")}
                if getattr(m, "content", None) is not None:
                    entry["content"] = m.content
                if getattr(m, "tool_calls", None):
                    entry["tool_calls"] = m.tool_calls
                serialisable.append(entry)

        path.write_text(
            json.dumps(serialisable, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    # ------------------------------------------------------------------
    # Query view builder  (C1 — the core architectural change)
    # ------------------------------------------------------------------

    def _build_messages_for_query(self) -> list[dict]:
        """Construct the message list to send to the LLM.

        This is a **read-only projection** of ``self.messages`` — the
        transcript is never modified.

        Layers applied:
        1. System prompt (with session memory injected if available)
        2. Session summary (replacing compressed history)
        3. Recent messages with tiered tool-result cleanup
        """
        # 1. System prompt — augment with session memory
        system_content = self.messages[0]["content"]
        session_memory = self._load_session_memory()
        if session_memory:
            system_content += (
                "\n\n## Session Context (from previous session)\n\n"
                + session_memory
            )

        result: list[dict] = [{"role": "system", "content": system_content}]

        # 2. Session summary (replaces messages[1:boundary])
        if self._session_summary is not None:
            result.append({
                "role": "user",
                "content": self._session_summary["text"],
            })
            result.append({
                "role": "assistant",
                "content": (
                    "I have the context from our earlier conversation. "
                    "Continuing from here."
                ),
            })

        # 3. Recent messages with tool-result tiers
        recent = self.messages[self._summary_boundary:]
        result.extend(self._apply_tool_result_tiers(recent))

        return result

    # ------------------------------------------------------------------
    # Tiered tool-result cleanup  (C3)
    # ------------------------------------------------------------------

    def _apply_tool_result_tiers(self, messages: list[dict]) -> list[dict]:
        """Return *messages* with tiered tool-result cleanup.

        The input list is **not** modified.

        Tier assignment uses a backward pass: each content-bearing assistant
        message increments a counter.  Tool results inherit the counter value
        at their position — the higher the counter, the older the result.

        ===  ====================================
        N    Treatment
        ===  ====================================
        0    Active turn — full content
        ≤ F  Recent completed — full content
        ≤ P  Older completed — preview (first ``_TOOL_RESULT_PREVIEW`` chars)
        > P  Stale — placeholder only
        ===  ====================================

        where F = ``_RECENT_TURNS_FULL`` and P = F + ``_RECENT_TURNS_PREVIEW``.
        """
        full_limit = self._RECENT_TURNS_FULL
        preview_limit = full_limit + self._RECENT_TURNS_PREVIEW

        # Backward pass: count content-bearing assistant messages after each pos
        age = [0] * len(messages)
        counter = 0
        for i in range(len(messages) - 1, -1, -1):
            age[i] = counter
            if _msg_role(messages[i]) == "assistant" and _msg_content(messages[i]):
                counter += 1

        out: list[dict] = []
        for i, m in enumerate(messages):
            if not isinstance(m, dict) or m.get("role") != "tool":
                out.append(m)
                continue

            content = m.get("content", "")
            tier = age[i]

            if tier <= full_limit:
                out.append(m)
            elif tier <= preview_limit:
                if len(content) > self._TOOL_RESULT_PREVIEW:
                    out.append({
                        **m,
                        "content": (
                            content[: self._TOOL_RESULT_PREVIEW]
                            + "\n\n... (preview — full result in transcript)"
                        ),
                    })
                else:
                    out.append(m)
            else:
                out.append({
                    **m,
                    "content": "[Tool result cleared — consumed in earlier turn]",
                })

        return out

    # ------------------------------------------------------------------
    # Session summary  (C2 — replaces old _maybe_compress_history)
    # ------------------------------------------------------------------

    def _update_session_summary(self) -> None:
        """Create or extend the session summary when the projected query
        view exceeds ``_MAX_CONTEXT_CHARS``.

        Finds a *clean* boundary (a ``user`` message) so the remaining
        recent messages form a valid conversation continuation.
        """
        system_chars = len(self.messages[0].get("content", ""))
        summary_chars = (
            len(self._session_summary["text"]) if self._session_summary else 0
        )
        recent_chars = sum(
            len(_msg_content(m)) for m in self.messages[self._summary_boundary:]
        )
        if system_chars + summary_chars + recent_chars < self._MAX_CONTEXT_CHARS:
            return

        num_recent = len(self.messages) - self._summary_boundary
        if num_recent <= self._RECENT_MESSAGES_KEEP + 1:
            return

        # Find a clean boundary (user message) near the target position
        target = len(self.messages) - self._RECENT_MESSAGES_KEEP
        new_boundary = None
        for candidate in range(target, self._summary_boundary, -1):
            if _msg_role(self.messages[candidate]) == "user":
                new_boundary = candidate
                break
        if new_boundary is None or new_boundary <= self._summary_boundary:
            return

        # Collect information from messages being compressed
        to_compress = self.messages[self._summary_boundary: new_boundary]
        key_points: list[str] = []
        tools_used: list[str] = []
        pages_touched: set[str] = set()

        for m in to_compress:
            role = _msg_role(m)
            content = _msg_content(m)

            if role == "user" and content:
                short = content[:200] + ("..." if len(content) > 200 else "")
                key_points.append(f"User: {short}")
            elif role == "assistant" and content:
                short = content[:200] + ("..." if len(content) > 200 else "")
                key_points.append(f"Agent: {short}")
            elif role == "assistant":
                tc_list = (
                    m.get("tool_calls", [])
                    if isinstance(m, dict)
                    else getattr(m, "tool_calls", []) or []
                )
                for tc in tc_list:
                    if isinstance(tc, dict):
                        fn = tc.get("function", {})
                        name = fn.get("name", "")
                        args_raw = fn.get("arguments", "{}")
                    else:
                        name = getattr(tc, "name", "")
                        args_raw = getattr(tc, "arguments", "{}")
                    if name:
                        tools_used.append(name)
                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                        p = args.get("path", "")
                        if p:
                            pages_touched.add(p)
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        pass

        # Merge with existing summary
        prev_points: list[str] = []
        prev_tools: list[str] = []
        prev_pages: set[str] = set()
        if self._session_summary:
            prev_points = self._session_summary.get("key_points", [])[-5:]
            prev_tools = self._session_summary.get("tools_used", [])
            prev_pages = set(self._session_summary.get("pages_touched", []))

        merged_points = prev_points + key_points
        merged_points = merged_points[-self._SUMMARY_KEY_POINTS_MAX:]

        all_tools = list(dict.fromkeys(prev_tools + tools_used))
        all_pages = sorted(prev_pages | pages_touched)

        # Build stable, structured summary text
        lines = [
            "[SESSION CONTEXT — Earlier conversation summary]",
            f"This represents earlier exchanges "
            f"(messages 1–{new_boundary} of the transcript).",
            "",
        ]
        if all_tools:
            lines.append(f"Tools used: {', '.join(all_tools[:15])}")
        if all_pages:
            lines.append(f"Pages touched: {', '.join(all_pages[:15])}")
        if all_tools or all_pages:
            lines.append("")
        lines.append("Conversation flow:")
        lines.extend(merged_points)

        self._session_summary = {
            "boundary": new_boundary,
            "key_points": merged_points,
            "tools_used": all_tools,
            "pages_touched": list(all_pages),
            "text": "\n".join(lines),
        }
        self._summary_boundary = new_boundary

    # ------------------------------------------------------------------
    # Backward-compatible wrappers (referenced by existing tests/callers)
    # ------------------------------------------------------------------

    def _maybe_compress_history(self) -> None:
        """Legacy name — delegates to ``_update_session_summary``."""
        self._update_session_summary()

    def _trim_old_tool_results(self) -> None:
        """No-op — tool-result cleanup now happens in the query view layer.

        Kept so external callers that relied on being able to call this
        after a turn don't break.  The actual cleanup logic lives in
        ``_apply_tool_result_tiers`` and is applied every time
        ``_build_messages_for_query`` runs.
        """

    # ------------------------------------------------------------------
    # Chat loop
    # ------------------------------------------------------------------

    def chat(self, user_message: str) -> Generator[str, None, None]:
        """Send a user message and yield agent responses.

        - All writes within a single chat turn are batched into one git commit
        - Transcript is append-only; compression only affects the query view
        - Tool results are tiered: full → preview → placeholder
        """
        self.messages.append({"role": "user", "content": user_message})
        self._update_session_summary()

        short_msg = (
            user_message[:60] + "..." if len(user_message) > 60 else user_message
        )
        self.vault._operation_depth += 1

        try:
            max_steps = 25
            for _ in range(max_steps):
                query_messages = self._build_messages_for_query()
                completion, raw_message = self.provider.chat_completion(
                    model=self.model,
                    messages=query_messages,
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

                    if len(result) > self._TOOL_RESULT_MAX:
                        result = (
                            result[: self._TOOL_RESULT_MAX]
                            + "\n\n... (truncated)"
                        )

                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    })

            yield "(reached maximum steps)"
        finally:
            self._end_operation(short_msg)

    # ------------------------------------------------------------------
    # Sizing helpers
    # ------------------------------------------------------------------

    def _estimate_chars(self) -> int:
        """Rough character count across the full transcript."""
        return sum(len(_msg_content(m)) for m in self.messages)

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
