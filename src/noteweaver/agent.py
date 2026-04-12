"""KnowledgeAgent — the core agent loop.

V2 architecture: continuous conversation flow with primitive tools.

Key changes from V1:
- No Plan mode — agent reads, proposes in natural language, writes after
  user approval, all in one continuous conversation
- All tools (read + write) available during chat
- Schema summary injected into system prompt by default
- Policy layer enforces safety gates (read-before-write, etc.)

Context management:
- self.messages (transcript): append-only record of the full conversation.
  Never modified by compression.
- self._session_summary: structured summary of older conversation turns.
- _build_messages_for_query(): constructs the view actually sent to the LLM.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from noteweaver.adapters.provider import LLMProvider
from noteweaver.vault import Vault
from noteweaver.tools.definitions import (
    TOOL_SCHEMAS, dispatch_tool,
)
from noteweaver.tools.policy import (
    PolicyContext, check_pre_dispatch,
)
from noteweaver.trace import TraceCollector

# Plan machinery — used by session-organize and gateway approval flows.
from noteweaver.plan import (
    Plan, PlanStatus, PlanStore, generate_plan_id,
)
from noteweaver.tools.policy import classify_change_type

# ======================================================================
# System prompt
# ======================================================================

PROMPT_IDENTITY = """\
You are NoteWeaver, a knowledge management agent and thinking companion.

## How You Work

You have two modes:

### 1. Conversation (default)
Respond naturally — discuss, reason, debate, suggest. Draw on the \
knowledge base when relevant (search or read pages). Reference existing \
content with [[wiki-links]]. Most interactions are just conversations.

### 2. Knowledge Capture
When the user asks you to record, remember, organize, or import something, \
or when you notice something worth capturing, follow the protocols \
defined in .schema/protocols.md (injected below). Key principle: \
read first, propose in natural language, write after user approval.

**Bias towards action.** When the user gives an explicit instruction to \
execute (e.g. "整理完", "继续", "全部做", "go ahead", "do it"), treat it \
as blanket approval for the entire scope — do NOT split the work into \
batches that each require separate confirmation. Process ALL items in \
scope using tool calls, then report results once at the end. Do not \
stop midway to ask "should I continue?" or present a plan for the \
next batch. Long plans with no tool calls waste the user's time.

The wiki is a tree overlaid with a graph:

```
index.md  (root — lists Hubs, <1000 tokens)
  → Hub   (topic entry — overview + child page links)
    → Canonical / Note / Synthesis  (content)
```
"""

PROMPT_TOOLS = """\
## Tools

### Read Tools (use freely)
| Tool | Purpose |
|------|---------|
| `read_page(path, section?, max_chars?)` | Read a page or specific section. |
| `search(query, scope?)` | Full-text search. scope: wiki/sources/all. |
| `get_backlinks(title)` | Pages linking to a title. |
| `list_pages(directory?)` | List pages with structured page cards. |
| `fetch_url(url)` | Preview a URL's content. |

### Write Tools
| Tool | Purpose |
|------|---------|
| `write_page(path, content)` | Create or overwrite a full page. |
| `append_section(path, heading, content)` | Add a section to an existing page. |
| `update_frontmatter(path, fields)` | Update metadata fields on a page. |
| `add_related_link(path, link_to)` | Add a [[wiki-link]] to Related section. |

### Reading Strategy (progressive disclosure)

1. **World summary** (always visible above) — understand wiki shape first.
2. **Page cards**: `list_pages` returns structured cards (title, type, \
summary, tags, updated) — judge relevance without reading full pages.
3. **Quick scan**: `read_page(path, max_chars=500)` for a relevance check.
4. **Deep read**: `read_page(path)` or `read_page(path, section='...')`.
5. **Search**: `search(query)` for keyword lookup across wiki and sources.

Use the user's language for content. \
If vault is empty, welcome the user and suggest what they can do.
"""

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

    V2 architecture: continuous conversation flow.
    - All tools (read + write) available during chat
    - No Plan mode — agent proposes in natural language, writes after approval
    - Schema summary always in system prompt

    Context management is split into three layers:

    1. **Transcript** (``self.messages``): complete, append-only conversation
       record.  Never mutated by compression.
    2. **Session summary** (``self._session_summary``): structured compression
       of older history.
    3. **Query view** (``_build_messages_for_query``): the actual message list
       sent to the LLM each turn.
    """

    # Context budget
    _CHARS_PER_TOKEN = 4
    _MAX_CONTEXT_CHARS = 48000

    # Tool-result management
    _TOOL_RESULT_MAX = 8000
    _TOOL_RESULT_PREVIEW = 500
    _RECENT_TURNS_FULL = 1
    _RECENT_TURNS_PREVIEW = 2

    # Summary generation
    _RECENT_MESSAGES_KEEP = 6
    _SUMMARY_KEY_POINTS_MAX = 20

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
        self._summary_boundary: int = 1
        self._policy_ctx = PolicyContext()
        self._trace = TraceCollector()
        self._provider_name = provider_name if provider is None else type(provider).__name__
        self.plan_store = PlanStore(vault.meta_dir)

    def set_attended(self, attended: bool) -> None:
        """Mark whether the user is present for this session."""
        self._policy_ctx.attended = attended

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def _build_system_prompt(self) -> str:
        """Build system prompt from static core + .schema/ files.

        Injection order:
        1. SYSTEM_PROMPT (identity + tools) — hardcoded
        2. .schema/schema.md — wiki structure definition
        3. .schema/protocols.md — behavioral constraints
        4. .schema/preferences.md — user preferences
        5. .schema/memory.md — long-term knowledge base memory
        """
        prompt = SYSTEM_PROMPT

        schema_path = self.vault.schema_dir / "schema.md"
        if schema_path.is_file():
            schema_content = schema_path.read_text(encoding="utf-8")
            prompt += f"\n\n{schema_content}"

        protocols_path = self.vault.schema_dir / "protocols.md"
        if protocols_path.is_file():
            proto_content = protocols_path.read_text(encoding="utf-8")
            prompt += f"\n\n{proto_content}"

        prefs_path = self.vault.schema_dir / "preferences.md"
        if prefs_path.is_file():
            prefs_content = prefs_path.read_text(encoding="utf-8")
            prompt += f"\n\n{prefs_content}"

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
        """Extract and persist session memory for the next session."""
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

        active_tags: dict[str, int] = {}
        for p in pages:
            try:
                content_raw = self.vault.read_file(p)
                from noteweaver.frontmatter import extract_frontmatter
                fm = extract_frontmatter(content_raw)
                if fm and fm.get("tags"):
                    for tag in fm["tags"]:
                        active_tags[tag] = active_tags.get(tag, 0) + 1
            except (FileNotFoundError, PermissionError):
                pass

        prev_mem = self._load_session_memory()
        prev_topics: list[str] = []
        if prev_mem:
            for line in prev_mem.split("\n"):
                if line.startswith("Recent topics:"):
                    prev_topics = [
                        t.strip() for t in line.split(":", 1)[1].split(",")
                        if t.strip()
                    ]

        ranked_tags = sorted(active_tags, key=active_tags.get, reverse=True)
        all_topics = list(dict.fromkeys(ranked_tags + prev_topics))[:10]

        lines = [
            "---",
            f"updated: {now}",
            f"session_turns: {turns}",
            "---",
            "",
            "## Last Session",
            "",
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

        if all_topics or pages:
            lines.append("## Active Workset")
            if all_topics:
                lines.append(f"Recent topics: {', '.join(all_topics)}")
            wiki_pages = [p for p in pages if p.startswith("wiki/") and "/archive/" not in p]
            if wiki_pages:
                lines.append(f"Active pages: {', '.join(wiki_pages[:8])}")
            lines.append("")

        prev_open = self._extract_open_items(prev_mem) if prev_mem else []
        new_open = self._extract_open_items_from_transcript()
        merged_open = list(dict.fromkeys(new_open + prev_open))[:8]
        if merged_open:
            lines.append("## Open Items")
            for item in merged_open:
                lines.append(f"- {item}")
            lines.append("")

        result = "\n".join(lines) + "\n"
        mem_path = self.vault.meta_dir / "session-memory.md"
        mem_path.parent.mkdir(parents=True, exist_ok=True)
        mem_path.write_text(result, encoding="utf-8")
        return mem_path

    def _scan_pending_proposals(self) -> str:
        """Scan recent journals for Promotion Candidates sections."""
        journals_dir = self.vault.wiki_dir / "journals"
        if not journals_dir.is_dir():
            return ""

        journal_files = sorted(journals_dir.glob("*.md"), reverse=True)[:3]
        candidates: list[str] = []
        for jf in journal_files:
            try:
                content = jf.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            marker = "#### Promotion Candidates"
            idx = content.find(marker)
            if idx == -1:
                continue
            block = content[idx:]
            for end_marker in ("\n### ", "\n---"):
                end_idx = block.find(end_marker, len(marker))
                if end_idx != -1:
                    block = block[:end_idx]
                    break
            block = block.strip()
            if block and len(block) > len(marker) + 5:
                rel = str(jf.relative_to(self.vault.root))
                candidates.append(f"*From {rel}:*\n{block}")

        return "\n\n".join(candidates)

    @staticmethod
    def _extract_open_items(memory_text: str | None) -> list[str]:
        """Extract open question / follow-up items from session memory text."""
        if not memory_text:
            return []
        items: list[str] = []
        in_section = False
        for line in memory_text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("## Open Items"):
                in_section = True
                continue
            if in_section and stripped.startswith("##"):
                break
            if in_section and stripped.startswith("- "):
                items.append(stripped[2:].strip())
        return items

    def _extract_open_items_from_transcript(self) -> list[str]:
        """Scan transcript for question marks in user messages (heuristic)."""
        items: list[str] = []
        for m in self.messages[1:]:
            role = _msg_role(m)
            content = _msg_content(m)
            if role == "user" and content and "?" in content:
                short = content.split("?")[0].strip()
                if len(short) > 10:
                    items.append(short[:120] + "?")
        return items[-5:]

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
        """Serialize the full transcript to a JSON file."""
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

    def save_trace(self, directory: Path | None = None) -> Path | None:
        """Save the current trace to a JSONL file."""
        if not self._trace.events:
            return None
        if directory is None:
            directory = self.vault.meta_dir / "traces"
        return self._trace.save(directory)

    # ------------------------------------------------------------------
    # Query view builder
    # ------------------------------------------------------------------

    def _build_messages_for_query(self) -> list[dict]:
        """Construct the message list to send to the LLM.

        This is a **read-only projection** of ``self.messages``.

        Layers applied:
        1. System prompt (with session memory injected if available)
        2. Session summary (replacing compressed history)
        3. Recent messages with tiered tool-result cleanup
        """
        system_content = self.messages[0]["content"]
        session_memory = self._load_session_memory()
        session_memory_injected = False
        if session_memory:
            system_content += (
                "\n\n## Session Context (from previous session)\n\n"
                + session_memory
            )
            session_memory_injected = True

        pending_proposals_injected = False
        if self._policy_ctx.attended:
            proposals = self._scan_pending_proposals()
            if proposals:
                system_content += (
                    "\n\n## Pending Promotion Candidates\n\n"
                    "The following insights were identified by a previous digest "
                    "pass and are waiting for your review. Offer to promote them "
                    "when relevant, e.g. 'I found some insights from recent "
                    "sessions — want me to turn them into wiki pages?'\n\n"
                    + proposals
                )
                pending_proposals_injected = True

        try:
            vault_ctx = self.vault.scan_vault_context()
            if vault_ctx and "Total: 0 pages" not in vault_ctx:
                system_content += (
                    "\n\n## Current Vault Contents\n\n" + vault_ctx
                )
            else:
                system_content += (
                    "\n\n## Current Vault Contents\n\n"
                    "The vault is empty — no pages yet. Welcome the user "
                    "and suggest what they can do (import notes, start "
                    "capturing knowledge from conversations, etc.)."
                )
        except Exception:
            pass

        audit_path = self.vault.meta_dir / "audit-report.json"
        if audit_path.is_file():
            try:
                audit_report = json.loads(audit_path.read_text(encoding="utf-8"))
                audit_summary = audit_report.get("summary", "")
                if audit_summary and "0 issues" not in audit_summary:
                    system_content += (
                        f"\n\n## Vault Health\n\n{audit_summary}\n"
                        "Mention this to the user when relevant."
                    )
            except (json.JSONDecodeError, OSError):
                pass

        result: list[dict] = [{"role": "system", "content": system_content}]

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

        recent = self.messages[self._summary_boundary:]
        result.extend(self._apply_tool_result_tiers(recent))

        total_chars = sum(len(_msg_content(m)) for m in result)
        self._trace.record_context_assembly(
            system_prompt_chars=len(system_content),
            session_memory_injected=session_memory_injected,
            pending_proposals_injected=pending_proposals_injected,
            summary_active=self._session_summary is not None,
            summary_boundary=self._summary_boundary,
            recent_message_count=len(recent),
            total_query_messages=len(result),
            estimated_tokens=total_chars // self._CHARS_PER_TOKEN,
        )

        return result

    # ------------------------------------------------------------------
    # Tiered tool-result cleanup
    # ------------------------------------------------------------------

    def _apply_tool_result_tiers(self, messages: list[dict]) -> list[dict]:
        """Return *messages* with tiered tool-result cleanup."""
        full_limit = self._RECENT_TURNS_FULL
        preview_limit = full_limit + self._RECENT_TURNS_PREVIEW

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
    # Session summary
    # ------------------------------------------------------------------

    def _update_session_summary(self) -> None:
        """Create or extend the session summary when the projected query
        view exceeds ``_MAX_CONTEXT_CHARS``."""
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

        target = len(self.messages) - self._RECENT_MESSAGES_KEEP
        new_boundary = None
        for candidate in range(target, self._summary_boundary, -1):
            if _msg_role(self.messages[candidate]) == "user":
                new_boundary = candidate
                break
        if new_boundary is None or new_boundary <= self._summary_boundary:
            return

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
    # Backward-compatible wrappers
    # ------------------------------------------------------------------

    def _maybe_compress_history(self) -> None:
        """Legacy name — delegates to ``_update_session_summary``."""
        self._update_session_summary()

    def _trim_old_tool_results(self) -> None:
        """No-op — tool-result cleanup now happens in the query view layer."""

    # ------------------------------------------------------------------
    # Chat loop (V2: all tools available, no Plan mode)
    # ------------------------------------------------------------------

    def chat(self, user_message: str) -> Generator[str, None, None]:
        """Send a user message and yield agent responses.

        V2: All tools (read + write) are available during chat.
        The agent proposes changes in natural language and writes after
        user approval — all in the same conversation flow.
        """
        self._trace = TraceCollector()
        self._trace.set_session_meta(
            model=self.model,
            provider=self._provider_name,
            attended=self._policy_ctx.attended,
            vault_path=str(self.vault.root),
            has_session_memory=(self.vault.meta_dir / "session-memory.md").is_file(),
            has_long_term_memory=(self.vault.schema_dir / "memory.md").is_file(),
            has_preferences=(self.vault.schema_dir / "preferences.md").is_file(),
        )

        self.messages.append({"role": "user", "content": user_message})
        self._update_session_summary()

        self._trace.record_user_message(message=user_message)

        short_msg = (
            user_message[:60] + "..." if len(user_message) > 60 else user_message
        )
        self.vault._operation_depth += 1

        steps_taken = 0
        has_response = False
        hit_max = False

        try:
            max_steps = 25
            for _ in range(max_steps):
                steps_taken += 1
                query_messages = self._build_messages_for_query()

                prompt_chars = sum(len(_msg_content(m)) for m in query_messages)
                self._trace.record_llm_request(
                    step=steps_taken,
                    model=self.model,
                    message_count=len(query_messages),
                    tool_count=len(TOOL_SCHEMAS),
                    estimated_prompt_chars=prompt_chars,
                )

                t_llm = time.monotonic()
                llm_error: str | None = None
                try:
                    completion, raw_message = self.provider.chat_completion(
                        model=self.model,
                        messages=query_messages,
                        tools=TOOL_SCHEMAS,
                    )
                except Exception as exc:
                    llm_error = f"{type(exc).__name__}: {exc}"
                    self._trace.record_llm_response(
                        step=steps_taken,
                        has_content=False,
                        content_preview="",
                        tool_calls=None,
                        duration_ms=(time.monotonic() - t_llm) * 1000,
                        error=llm_error,
                    )
                    self._trace.record_error(
                        error_type=type(exc).__name__,
                        message=str(exc),
                        traceback_str=self._trace.capture_traceback(),
                        context={"step": steps_taken, "phase": "llm_call"},
                    )
                    raise

                llm_duration = (time.monotonic() - t_llm) * 1000

                tc_summaries: list[dict] | None = None
                if completion.tool_calls:
                    tc_summaries = []
                    for tc in completion.tool_calls:
                        try:
                            tc_args = json.loads(tc.arguments)
                        except json.JSONDecodeError:
                            tc_args = {}
                        tc_summaries.append({
                            "name": tc.name,
                            "arguments": tc_args,
                        })

                self._trace.record_llm_response(
                    step=steps_taken,
                    has_content=bool(completion.content),
                    content_preview=completion.content or "",
                    tool_calls=tc_summaries,
                    duration_ms=llm_duration,
                )

                self.messages.append(raw_message)

                if not completion.tool_calls:
                    if completion.content:
                        has_response = True
                        self._trace.record_agent_reply(
                            content=completion.content,
                        )
                        yield completion.content
                    return

                for tool_call in completion.tool_calls:
                    try:
                        fn_args = json.loads(tool_call.arguments)
                    except json.JSONDecodeError:
                        fn_args = {}

                    yield f"  ↳ {tool_call.name}({self._summarize_args(fn_args)})"

                    verdict = check_pre_dispatch(
                        tool_call.name, fn_args, self._policy_ctx,
                    )

                    t0 = time.monotonic()
                    error_msg: str | None = None
                    if not verdict.allowed:
                        result = f"Policy blocked: {verdict.warning}"
                    else:
                        try:
                            result = dispatch_tool(
                                self.vault, tool_call.name, fn_args
                            )
                        except Exception as exc:
                            error_msg = f"{type(exc).__name__}: {exc}"
                            result = (
                                f"Error executing {tool_call.name}: {error_msg}"
                            )
                            self._trace.record_error(
                                error_type=type(exc).__name__,
                                message=str(exc),
                                traceback_str=self._trace.capture_traceback(),
                                context={
                                    "step": steps_taken,
                                    "tool": tool_call.name,
                                    "arguments": fn_args,
                                },
                            )

                    duration_ms = (time.monotonic() - t0) * 1000

                    self._trace.record_tool_call(
                        name=tool_call.name,
                        arguments=fn_args,
                        policy_allowed=verdict.allowed,
                        policy_warning=verdict.warning,
                        result_preview=result,
                        duration_ms=duration_ms,
                        error=error_msg,
                    )

                    self._policy_ctx.record_tool_call(
                        tool_call.name, fn_args,
                    )

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

            hit_max = True
            yield "(reached maximum steps)"
        finally:
            self._trace.record_turn_end(
                steps_taken=steps_taken,
                has_response=has_response,
                hit_max_steps=hit_max,
            )
            self._end_operation(short_msg)

    # ------------------------------------------------------------------
    # Journal generation (LLM-assisted)
    # ------------------------------------------------------------------

    def generate_journal_summary(self) -> dict:
        """Use one LLM call to generate structured journal slots."""
        if len(self.messages) <= 2:
            return {"insights": [], "decisions": [], "open_questions": [], "follow_ups": []}

        digest_parts: list[str] = []
        for m in self.messages[1:]:
            role = _msg_role(m)
            content = _msg_content(m)
            if role == "user" and content:
                digest_parts.append(f"User: {content[:500]}")
            elif role == "assistant" and content:
                digest_parts.append(f"Agent: {content[:500]}")
        conversation_text = "\n".join(digest_parts[-30:])

        prompt_messages = [
            {"role": "system", "content": (
                "You are a concise note-taking assistant. Given a conversation, "
                "extract structured information. Respond ONLY in the exact format below, "
                "with one item per line. Use the user's language. Be brief (one sentence per item).\n\n"
                "INSIGHTS:\n- (key takeaways or conclusions from the conversation)\n\n"
                "DECISIONS:\n- (any decisions made during the conversation)\n\n"
                "OPEN_QUESTIONS:\n- (unresolved questions or topics to explore further)\n\n"
                "FOLLOW_UPS:\n- (concrete next actions or things to do)\n\n"
                "If a section has nothing, write - (none)\n"
            )},
            {"role": "user", "content": f"Extract from this conversation:\n\n{conversation_text}"},
        ]

        try:
            raw = self.provider.simple_completion(self.model, prompt_messages)
            if not raw:
                return {"insights": [], "decisions": [], "open_questions": [], "follow_ups": []}
            return self._parse_journal_sections(raw)
        except Exception:
            return {"insights": [], "decisions": [], "open_questions": [], "follow_ups": []}

    @staticmethod
    def _parse_journal_sections(text: str) -> dict:
        """Parse LLM output into structured journal slots."""
        sections: dict[str, list[str]] = {
            "insights": [], "decisions": [], "open_questions": [], "follow_ups": [],
        }
        current_key: str | None = None
        key_map = {
            "INSIGHTS": "insights",
            "DECISIONS": "decisions",
            "OPEN_QUESTIONS": "open_questions",
            "FOLLOW_UPS": "follow_ups",
            "FOLLOW-UPS": "follow_ups",
        }

        for line in text.split("\n"):
            stripped = line.strip()
            upper = stripped.rstrip(":").upper()
            if upper in key_map:
                current_key = key_map[upper]
                continue
            if current_key and stripped.startswith("- "):
                item = stripped[2:].strip()
                if item and item.lower() != "(none)":
                    sections[current_key].append(item)

        return sections

    # ------------------------------------------------------------------
    # Plan execution (organize-only — used by generate_organize_plan /
    # CLI _finalize_session / gateway plan approval)
    # ------------------------------------------------------------------

    EXECUTE_PLAN_PROMPT = """\
You are NoteWeaver's execution engine. An approved change plan is provided below. \
Your job is to implement it precisely using the available tools.

## Rules

1. **Implement the approved plan faithfully.** Do not re-evaluate whether \
the plan is a good idea — it has already been approved by the user.
2. **Read before writing.** Always read_page() a target before modifying it.
3. **Minimal changes.** Make the smallest changes that fulfill the plan.
4. **If the plan conflicts with current vault state** (e.g. target page \
was deleted, content already exists), STOP and report the conflict — \
do not silently modify the plan.
5. **Maintain knowledge structure.** Every new page must be reachable. \
Add related links. Use proper frontmatter.
6. Use the user's language for content.

## Approved Plan

{plan_summary}

## Rationale

{plan_rationale}

## Intent

{plan_intent}
"""

    def _handle_submit_plan(self, args: dict) -> Plan:
        """Create a Plan from submit_plan tool arguments.

        Used by ``generate_organize_plan()`` to persist session-organize
        proposals.  Not called during normal ``chat()`` — V2 chat writes
        directly via primitive tools.
        """
        targets = args.get("targets") or []
        target_mtimes: dict[str, float] = {}
        for t in targets:
            resolved = self.vault._resolve(t)
            if resolved.is_file():
                target_mtimes[str(resolved)] = resolved.stat().st_mtime

        model_change_type = args.get("change_type", "structural")
        intent = args.get("intent", "create")
        verified_change_type = classify_change_type(
            intent=intent,
            targets=targets,
            model_suggestion=model_change_type,
            vault=self.vault,
        )

        plan = Plan(
            id=generate_plan_id(),
            status=PlanStatus.PENDING,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            summary=args.get("summary", ""),
            targets=targets,
            rationale=args.get("rationale", ""),
            intent=intent,
            change_type=verified_change_type,
            open_questions=args.get("open_questions") or [],
            target_mtimes=target_mtimes,
        )

        self.plan_store.save(plan)
        return plan

    def _format_plan_submission_result(self, plan: Plan) -> str:
        status_msg = {
            "incremental": (
                f"Proposal {plan.id} created (incremental — will execute "
                "immediately after this response). "
            ),
            "structural": (
                f"Proposal {plan.id} created (structural — awaiting user "
                "approval before execution). "
            ),
        }
        base = status_msg.get(plan.change_type, f"Proposal {plan.id} created. ")
        if plan.open_questions:
            base += "Open questions: " + "; ".join(plan.open_questions)
        return base

    def execute_plan(self, plan_id: str) -> str:
        """Execute an approved plan via a dedicated LLM call.

        This is the *organize-only* execution path.  It runs after the
        user approves a plan generated by ``generate_organize_plan()``,
        either interactively (CLI ``_approve_and_execute``) or via the
        gateway approval flow.  Normal interactive writes happen directly
        inside ``chat()`` and never go through this method.
        """
        plan = self.plan_store.load(plan_id)
        if plan is None:
            return f"Plan {plan_id} not found."
        if plan.status not in (PlanStatus.APPROVED, PlanStatus.PENDING):
            return f"Plan {plan_id} is {plan.status.value}, cannot execute."

        stale = self.plan_store.check_staleness(plan)
        if stale:
            stale_paths = ", ".join(stale)
            return (
                f"⚠ Plan {plan_id} targets have changed since creation: "
                f"{stale_paths}. Consider regenerating the plan."
            )

        vault_ctx = self.vault.scan_vault_context()
        exec_prompt = self.EXECUTE_PLAN_PROMPT.format(
            plan_summary=plan.summary,
            plan_rationale=plan.rationale,
            plan_intent=plan.intent,
        )
        if plan.open_questions:
            exec_prompt += (
                "\n\n## Open Questions (already resolved by user approval)\n\n"
                + "\n".join(f"- {q}" for q in plan.open_questions)
            )
        exec_prompt += f"\n\n## Current Vault Structure\n\n{vault_ctx}"

        messages = [
            {"role": "system", "content": exec_prompt},
            {"role": "user", "content": "Execute this approved plan now."},
        ]

        results: list[str] = []
        executed_tool_calls: list[dict] = []
        with self.vault.operation("Execute plan"):
            max_steps = 25
            for _ in range(max_steps):
                try:
                    completion, _ = self.provider.chat_completion(
                        model=self.model,
                        messages=messages,
                        tools=TOOL_SCHEMAS,
                    )
                except Exception as exc:
                    results.append(f"✗ LLM call failed: {exc}")
                    break

                if not completion.tool_calls:
                    if completion.content:
                        results.append(f"Agent: {completion.content[:200]}")
                    break

                for tc in completion.tool_calls:
                    try:
                        fn_args = json.loads(tc.arguments)
                    except json.JSONDecodeError:
                        fn_args = {}

                    verdict = check_pre_dispatch(
                        tc.name, fn_args, self._policy_ctx,
                    )
                    if not verdict.allowed:
                        tool_result = f"Policy blocked: {verdict.warning}"
                        results.append(f"✗ {tc.name}: blocked — {verdict.warning}")
                    else:
                        try:
                            tool_result = dispatch_tool(
                                self.vault, tc.name, fn_args,
                            )
                            self._policy_ctx.record_tool_call(tc.name, fn_args)
                            is_error = tool_result.startswith("Error")
                            results.append(
                                f"{'✗' if is_error else '✓'} {tc.name}: "
                                f"{tool_result[:120]}"
                            )
                            if not is_error:
                                executed_tool_calls.append({
                                    "name": tc.name,
                                    "arguments": fn_args,
                                })
                        except Exception as exc:
                            tool_result = f"Error: {exc}"
                            results.append(f"✗ {tc.name}: {exc}")

                    messages.append({
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": tc.arguments,
                            },
                        }],
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_result,
                    })

            disclosure_report = self._ensure_progressive_disclosure(
                executed_tool_calls,
            )
            if disclosure_report:
                results.extend(disclosure_report)

        success = sum(1 for r in results if r.startswith("✓"))
        try:
            self.vault.append_log(
                "plan",
                f"Executed plan {plan.id} ({success}/{len(results)} succeeded)",
                "\n".join(results[:10]),
            )
        except Exception:
            pass

        report = (
            f"执行了 {len(results)} 项操作（{success} 成功）：\n"
            + "\n".join(results)
        )
        self.plan_store.update_status(
            plan_id, PlanStatus.EXECUTED, execution_report=report,
        )

        return report

    # ------------------------------------------------------------------
    # Session organize (end-of-session knowledge extraction)
    # ------------------------------------------------------------------

    _ORGANIZE_CHAR_THRESHOLD = 3000
    _ORGANIZE_DIGEST_MAX = 8000
    _last_organize_boundary: int = 1

    ORGANIZE_SESSION_PROMPT = (
        "You are a knowledge management assistant. Given a conversation digest "
        "and the current vault structure, propose what knowledge should be captured "
        "or updated in the vault.\n\n"
        "Use submit_plan to describe your proposed changes. Focus on WHAT and WHY.\n\n"
        "Guidelines:\n"
        "- Only capture insights, decisions, conclusions, and new knowledge — "
        "not every conversational exchange.\n"
        "- Describe whether content should be appended to an existing page "
        "or captured as a new note.\n"
        "- Use the user's language for content descriptions.\n"
        "- If nothing is worth capturing, respond with a text message saying so "
        "(do not call submit_plan).\n"
        "- Keep descriptions concise and well-structured."
    )

    def _build_conversation_digest(self, since_boundary: int | None = None) -> str:
        """Build a compact digest of recent conversation for organize planning."""
        boundary = since_boundary if since_boundary is not None else self._last_organize_boundary
        recent = self.messages[boundary:]

        parts: list[str] = []
        budget = self._ORGANIZE_DIGEST_MAX

        for m in recent:
            if budget <= 0:
                break
            role = _msg_role(m)
            content = _msg_content(m)

            if role == "user" and content:
                entry = f"User: {content[:500]}"
                parts.append(entry)
                budget -= len(entry)
            elif role == "assistant" and content:
                entry = f"Assistant: {content[:300]}"
                parts.append(entry)
                budget -= len(entry)
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
                    try:
                        args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    path = args.get("path", args.get("title", ""))
                    entry = f"Tool: {name}({path})" if path else f"Tool: {name}()"
                    parts.append(entry)
                    budget -= len(entry)
            elif role == "tool" and content:
                entry = f"Result: {content[:200]}"
                parts.append(entry)
                budget -= len(entry)

        return "\n".join(parts)

    def should_organize(self) -> bool:
        """Check if enough new conversation content has accumulated."""
        recent_chars = sum(
            len(_msg_content(m))
            for m in self.messages[self._last_organize_boundary:]
            if _msg_role(m) in ("user", "assistant") and _msg_content(m)
        )
        return recent_chars >= self._ORGANIZE_CHAR_THRESHOLD

    def generate_organize_plan(self) -> Plan | None:
        """Use one LLM call to generate an organize plan as a Plan object."""
        if len(self.messages) <= 2:
            return None

        digest = self._build_conversation_digest()
        vault_ctx = self.vault.scan_vault_context()

        from noteweaver.tools.definitions import SUBMIT_PLAN_SCHEMA

        messages = [
            {"role": "system", "content": self.ORGANIZE_SESSION_PROMPT},
            {"role": "user", "content": (
                f"## Conversation Digest\n\n{digest}\n\n"
                f"---\n\n## Current Vault Structure\n\n{vault_ctx}"
            )},
        ]

        try:
            completion, _ = self.provider.chat_completion(
                model=self.model,
                messages=messages,
                tools=[SUBMIT_PLAN_SCHEMA],
            )
        except Exception:
            return None

        if not completion.tool_calls:
            return None

        for tc in completion.tool_calls:
            if tc.name == "submit_plan":
                try:
                    args = json.loads(tc.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                return self._handle_submit_plan(args)

        return None

    def format_plan(self, plan: Plan) -> str:
        """Format a Plan as a human-readable summary for display."""
        intent_labels = {
            "append": "追加内容",
            "create": "新建页面",
            "organize": "整理",
            "restructure": "重构",
        }
        type_labels = {
            "incremental": "增量变更（自动执行）",
            "structural": "结构性变更（需审批）",
        }
        lines = [
            f"**{intent_labels.get(plan.intent, plan.intent)}** "
            f"[{type_labels.get(plan.change_type, plan.change_type)}]",
            "",
            plan.summary,
        ]
        if plan.targets:
            lines.append("")
            lines.append("涉及页面:")
            for t in plan.targets:
                lines.append(f"  - {t}")
        if plan.rationale:
            lines.append("")
            lines.append(f"理由: {plan.rationale}")
        if plan.open_questions:
            lines.append("")
            lines.append("待确认:")
            for q in plan.open_questions:
                lines.append(f"  - {q}")
        return "\n".join(lines)

    def format_organize_plan(self, plan_or_actions: "Plan | list[dict]") -> str:
        """Format a plan for display. Accepts Plan or legacy list[dict]."""
        if isinstance(plan_or_actions, Plan):
            return self.format_plan(plan_or_actions)
        if isinstance(plan_or_actions, list):
            parts = []
            for i, action in enumerate(plan_or_actions, 1):
                name = action.get("name", "?")
                args = action.get("arguments", {})
                summary_parts = [f"{k}={str(v)[:40]}" for k, v in args.items()]
                parts.append(f"{i}. {name}({', '.join(summary_parts[:3])})")
            return "\n".join(parts)
        return str(plan_or_actions)

    def execute_organize_plan(
        self, plan_or_actions: "Plan | list[dict] | None" = None,
    ) -> str:
        """Execute a plan — supports both Plan objects and legacy list[dict]."""
        if plan_or_actions is None:
            pending = self.plan_store.list_pending()
            if pending:
                plan_obj = pending[0]
                self.plan_store.update_status(plan_obj.id, PlanStatus.APPROVED)
                return self.execute_plan(plan_obj.id)
            legacy = self._load_pending_plan()
            if legacy:
                return self._execute_legacy_plan(legacy)
            return "没有待执行的整理计划。"

        if isinstance(plan_or_actions, Plan):
            if plan_or_actions.status == PlanStatus.PENDING:
                self.plan_store.update_status(
                    plan_or_actions.id, PlanStatus.APPROVED,
                )
            return self.execute_plan(plan_or_actions.id)

        if isinstance(plan_or_actions, list):
            return self._execute_legacy_plan(plan_or_actions)

        return "没有待执行的整理计划。"

    def _execute_legacy_plan(self, actions: list[dict]) -> str:
        """Execute a legacy-format plan (list of {name, arguments} dicts)."""
        if not actions:
            return "没有待执行的整理计划。"

        results: list[str] = []
        with self.vault.operation("Knowledge update"):
            for action in actions:
                name = action.get("name", "")
                args = action.get("arguments", {})

                verdict = check_pre_dispatch(name, args, self._policy_ctx)
                if not verdict.allowed:
                    results.append(f"✗ {name}: blocked by policy — {verdict.warning}")
                    continue

                try:
                    result = dispatch_tool(self.vault, name, args)
                    self._policy_ctx.record_tool_call(name, args)
                    is_error = result.startswith("Error")
                    results.append(f"{'✗' if is_error else '✓'} {name}: {result[:120]}")
                    if verdict.warning:
                        results.append(f"  ⚠ {verdict.warning}")
                except Exception as e:
                    results.append(f"✗ {name}: {e}")

            disclosure_report = self._ensure_progressive_disclosure(actions)
            if disclosure_report:
                results.extend(disclosure_report)

        success = sum(1 for r in results if r.startswith("✓"))
        try:
            self.vault.append_log(
                "plan",
                f"Executed plan ({success}/{len(results)} succeeded)",
                "\n".join(results[:10]),
            )
        except Exception:
            pass

        self._clear_pending_plan()
        self._last_organize_boundary = len(self.messages)

        return (
            f"执行了 {len(results)} 项操作（{success} 成功）：\n"
            + "\n".join(results)
        )

    def _ensure_progressive_disclosure(self, plan: list[dict]) -> list[str]:
        """After executing writes, ensure new/modified pages are reachable."""
        from noteweaver.frontmatter import extract_frontmatter

        written_paths = set()
        for action in plan:
            name = action.get("name", "")
            args = action.get("arguments", {})
            path = args.get("path", "")
            if name == "write_page" and path:
                written_paths.add(path)
            elif name == "capture":
                target = args.get("target", "")
                if target:
                    written_paths.add(target)
                else:
                    title = args.get("title", "")
                    slug = str(title).lower().replace(" ", "-").replace("/", "-")
                    import re as _re
                    slug = _re.sub(r"[^a-z0-9\u4e00-\u9fff-]", "", slug)[:60]
                    slug = _re.sub(r"-{2,}", "-", slug).strip("-")
                    ptype = args.get("type", "note")
                    if ptype == "synthesis":
                        written_paths.add(f"wiki/synthesis/{slug}.md")
                    else:
                        written_paths.add(f"wiki/concepts/{slug}.md")

        if not written_paths:
            return []

        report: list[str] = []
        all_frontmatters = self.vault.read_frontmatters("wiki")
        hubs = {p["title"]: p for p in all_frontmatters if p["type"] == "hub"}
        tag_pages: dict[str, list[str]] = {}
        for p in all_frontmatters:
            for t in (p.get("tags") or []):
                tag_pages.setdefault(t, []).append(p["path"])

        for wpath in written_paths:
            try:
                content = self.vault.read_file(wpath)
            except FileNotFoundError:
                continue
            fm = extract_frontmatter(content)
            if not fm:
                continue

            title = fm.get("title", "")
            ptype = fm.get("type", "")
            tags = fm.get("tags") or []

            if ptype in ("hub", "journal", "archive"):
                continue
            if not title:
                continue

            ref_count = self.vault.backlinks.reference_count(title)
            if ref_count > 0:
                continue

            linked = False
            for tag in tags:
                for hub_title, hub_info in hubs.items():
                    hub_tags = hub_info.get("tags") or []
                    if tag in hub_tags:
                        try:
                            result = dispatch_tool(self.vault, "organize", {
                                "target": hub_info["path"],
                                "action": "link",
                                "link_to": title,
                            })
                            if result.startswith("OK"):
                                report.append(
                                    f"✓ 链接: {title} → hub「{hub_title}」"
                                )
                                linked = True
                        except Exception:
                            pass
                        break
                if linked:
                    break

            if not linked:
                for tag in tags:
                    hub_exists = any(
                        tag in (h.get("tags") or [])
                        for h in hubs.values()
                    )
                    if not hub_exists:
                        hub_slug = str(tag).lower().replace(" ", "-")
                        import re as _re
                        hub_slug = _re.sub(r"[^a-z0-9-]", "", hub_slug)
                        hub_slug = _re.sub(r"-{2,}", "-", hub_slug).strip("-")[:60]
                        hub_path = f"wiki/concepts/{hub_slug}.md"

                        from datetime import datetime, timezone
                        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        page_titles = []
                        for pp in all_frontmatters:
                            if tag in (pp.get("tags") or []) and pp.get("title"):
                                page_titles.append(pp["title"])
                        if title not in page_titles:
                            page_titles.append(title)

                        links_block = "\n".join(f"- [[{pt}]]" for pt in page_titles[:10])
                        tag_str = str(tag)
                        hub_content = (
                            f"---\ntitle: {tag_str.title()}\ntype: hub\n"
                            f"summary: Hub for {tag_str} topics\n"
                            f"tags: [{tag_str}]\n"
                            f"created: {today}\nupdated: {today}\n---\n\n"
                            f"# {tag_str.title()}\n\n"
                            f"## Pages\n\n{links_block}\n\n"
                            f"## Related\n"
                        )
                        try:
                            self.vault.write_file(hub_path, hub_content)
                            report.append(
                                f"✓ 新建 hub「{tag_str.title()}」（{len(page_titles)} 页面）"
                            )
                            linked = True
                        except Exception:
                            pass
                        break

        needs_index_rebuild = any("hub" in r.lower() for r in report)
        if needs_index_rebuild:
            try:
                self.vault.rebuild_index()
                report.append("✓ 重建 index.md")
            except Exception:
                pass

        return report

    def _save_pending_plan(self, plan: list[dict]) -> Path:
        path = self.vault.meta_dir / "pending-organize.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(plan, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def _load_pending_plan(self) -> list[dict] | None:
        path = self.vault.meta_dir / "pending-organize.json"
        if not path.is_file():
            return None
        try:
            plan = json.loads(path.read_text(encoding="utf-8"))
            return plan if isinstance(plan, list) else None
        except (json.JSONDecodeError, OSError):
            return None

    def _clear_pending_plan(self) -> None:
        path = self.vault.meta_dir / "pending-organize.json"
        if path.is_file():
            path.unlink()

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
