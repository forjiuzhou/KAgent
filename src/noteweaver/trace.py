"""Structured trace collector for agent observability.

Records structured events during an agent session, enabling post-hoc
diagnosis by humans or external coding agents (Claude, Codex, etc.).

Three event layers:
1. **context_assembly** — what the LLM sees (and what was excluded)
2. **tool_call** — every tool dispatch with timing, policy verdict, result
3. **state_mutation** — vault writes, git commits, session memory changes

Design principles:
- Append-only event list (no tree, no nesting — keep it simple)
- All events are dicts serialisable to JSON
- Zero impact on control flow — trace is purely observational
- JSONL output for machine consumption, human-readable renderer for CLI
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class TraceEvent:
    """A single structured trace event."""

    timestamp: str
    kind: str
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.timestamp,
            "kind": self.kind,
            **self.data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class TraceCollector:
    """Collects structured trace events for a single chat turn.

    Usage::

        trace = TraceCollector()
        trace.record_context_assembly(...)
        trace.record_tool_call(...)
        trace.save(directory)
    """

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []
        self.session_meta: dict[str, Any] = {}
        self._turn_start: float = time.monotonic()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    # ------------------------------------------------------------------
    # Session metadata (recorded once at start)
    # ------------------------------------------------------------------

    def set_session_meta(
        self,
        *,
        model: str,
        provider: str,
        attended: bool,
        vault_path: str,
        has_session_memory: bool,
        has_long_term_memory: bool,
        has_preferences: bool,
    ) -> None:
        self.session_meta = {
            "model": model,
            "provider": provider,
            "attended": attended,
            "vault_path": vault_path,
            "has_session_memory": has_session_memory,
            "has_long_term_memory": has_long_term_memory,
            "has_preferences": has_preferences,
        }

    # ------------------------------------------------------------------
    # Layer 1: Context Assembly
    # ------------------------------------------------------------------

    def record_context_assembly(
        self,
        *,
        system_prompt_chars: int,
        session_memory_injected: bool,
        pending_proposals_injected: bool,
        summary_active: bool,
        summary_boundary: int,
        recent_message_count: int,
        total_query_messages: int,
        estimated_tokens: int,
    ) -> None:
        self.events.append(TraceEvent(
            timestamp=self._now(),
            kind="context_assembly",
            data={
                "system_prompt_chars": system_prompt_chars,
                "session_memory_injected": session_memory_injected,
                "pending_proposals_injected": pending_proposals_injected,
                "summary_active": summary_active,
                "summary_boundary": summary_boundary,
                "recent_message_count": recent_message_count,
                "total_query_messages": total_query_messages,
                "estimated_tokens": estimated_tokens,
            },
        ))

    # ------------------------------------------------------------------
    # Layer 2: Tool Calls
    # ------------------------------------------------------------------

    def record_tool_call(
        self,
        *,
        name: str,
        arguments: dict[str, Any],
        policy_allowed: bool,
        policy_warning: str | None,
        result_preview: str,
        duration_ms: float,
        error: str | None = None,
    ) -> None:
        self.events.append(TraceEvent(
            timestamp=self._now(),
            kind="tool_call",
            data={
                "name": name,
                "arguments": arguments,
                "policy_allowed": policy_allowed,
                "policy_warning": policy_warning,
                "result_preview": result_preview[:500],
                "result_chars": len(result_preview),
                "duration_ms": round(duration_ms, 1),
                "error": error,
            },
        ))

    # ------------------------------------------------------------------
    # Layer 3: State Mutations
    # ------------------------------------------------------------------

    def record_state_mutation(
        self,
        *,
        mutation_type: str,
        path: str | None = None,
        detail: str | None = None,
    ) -> None:
        data: dict[str, Any] = {"mutation_type": mutation_type}
        if path is not None:
            data["path"] = path
        if detail is not None:
            data["detail"] = detail
        self.events.append(TraceEvent(
            timestamp=self._now(),
            kind="state_mutation",
            data=data,
        ))

    # ------------------------------------------------------------------
    # Turn boundary
    # ------------------------------------------------------------------

    def record_turn_end(
        self,
        *,
        steps_taken: int,
        has_response: bool,
        hit_max_steps: bool,
    ) -> None:
        elapsed = time.monotonic() - self._turn_start
        self.events.append(TraceEvent(
            timestamp=self._now(),
            kind="turn_end",
            data={
                "steps_taken": steps_taken,
                "has_response": has_response,
                "hit_max_steps": hit_max_steps,
                "total_duration_ms": round(elapsed * 1000, 1),
            },
        ))

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, directory: Path, *, suffix: str = "") -> Path:
        """Write trace as JSONL to ``directory``.

        Returns the path of the written file.
        """
        directory.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        name = f"{ts}{suffix}.trace.jsonl"
        path = directory / name

        lines: list[str] = []
        if self.session_meta:
            header = {"ts": self._now(), "kind": "session_meta", **self.session_meta}
            lines.append(json.dumps(header, ensure_ascii=False))
        for event in self.events:
            lines.append(event.to_json())

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    @staticmethod
    def load(path: Path) -> list[dict[str, Any]]:
        """Load a trace JSONL file into a list of event dicts."""
        events: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if line:
                events.append(json.loads(line))
        return events

    @staticmethod
    def render_human(events: list[dict[str, Any]]) -> str:
        """Render trace events as a human-readable report."""
        lines: list[str] = []

        for ev in events:
            kind = ev.get("kind", "unknown")

            if kind == "session_meta":
                lines.append("═══ Session ═══")
                lines.append(f"  Model:    {ev.get('model', '?')}")
                lines.append(f"  Provider: {ev.get('provider', '?')}")
                lines.append(f"  Attended: {ev.get('attended', '?')}")
                lines.append(f"  Vault:    {ev.get('vault_path', '?')}")
                flags = []
                if ev.get("has_session_memory"):
                    flags.append("session-memory")
                if ev.get("has_long_term_memory"):
                    flags.append("long-term-memory")
                if ev.get("has_preferences"):
                    flags.append("preferences")
                if flags:
                    lines.append(f"  Loaded:   {', '.join(flags)}")
                lines.append("")

            elif kind == "context_assembly":
                lines.append(f"[{ev.get('ts', '')}] Context Assembly")
                lines.append(f"  System prompt:    {ev.get('system_prompt_chars', 0):,} chars")
                lines.append(f"  Session memory:   {'yes' if ev.get('session_memory_injected') else 'no'}")
                lines.append(f"  Proposals:        {'yes' if ev.get('pending_proposals_injected') else 'no'}")
                lines.append(f"  Summary active:   {'yes' if ev.get('summary_active') else 'no'}")
                if ev.get("summary_active"):
                    lines.append(f"  Summary boundary: msg #{ev.get('summary_boundary', '?')}")
                lines.append(f"  Recent messages:  {ev.get('recent_message_count', '?')}")
                lines.append(f"  Total → LLM:      {ev.get('total_query_messages', '?')} messages")
                lines.append(f"  Est. tokens:      ~{ev.get('estimated_tokens', 0):,}")
                lines.append("")

            elif kind == "tool_call":
                name = ev.get("name", "?")
                dur = ev.get("duration_ms", 0)
                allowed = ev.get("policy_allowed", True)
                status = "✓" if allowed else "✗ BLOCKED"
                args_str = _format_args(ev.get("arguments", {}))
                lines.append(f"[{ev.get('ts', '')}] Tool: {name}({args_str}) [{dur:.0f}ms] {status}")
                if ev.get("policy_warning"):
                    lines.append(f"  ⚠ {ev['policy_warning']}")
                if ev.get("error"):
                    lines.append(f"  ✗ Error: {ev['error']}")
                else:
                    preview = ev.get("result_preview", "")
                    total = ev.get("result_chars", 0)
                    if preview:
                        short = preview[:200].replace("\n", " ")
                        if total > 200:
                            short += f"... ({total:,} chars total)"
                        lines.append(f"  → {short}")
                lines.append("")

            elif kind == "state_mutation":
                mut_type = ev.get("mutation_type", "?")
                path = ev.get("path", "")
                detail = ev.get("detail", "")
                parts = [f"[{ev.get('ts', '')}] State: {mut_type}"]
                if path:
                    parts[0] += f"  {path}"
                if detail:
                    lines.append(parts[0])
                    lines.append(f"  {detail}")
                else:
                    lines.append(parts[0])
                lines.append("")

            elif kind == "turn_end":
                steps = ev.get("steps_taken", 0)
                total_ms = ev.get("total_duration_ms", 0)
                hit_max = ev.get("hit_max_steps", False)
                has_resp = ev.get("has_response", False)
                lines.append("═══ Turn End ═══")
                lines.append(f"  Steps:    {steps}")
                lines.append(f"  Duration: {total_ms:,.0f}ms")
                lines.append(f"  Response: {'yes' if has_resp else 'no'}")
                if hit_max:
                    lines.append("  ⚠ Hit max steps limit")
                lines.append("")

        return "\n".join(lines)


def _format_args(args: dict[str, Any]) -> str:
    """Format tool arguments for compact display."""
    parts: list[str] = []
    for k, v in args.items():
        if isinstance(v, str) and len(v) > 60:
            v = v[:57] + "..."
        parts.append(f"{k}={v!r}")
    result = ", ".join(parts)
    if len(result) > 120:
        result = result[:117] + "..."
    return result
