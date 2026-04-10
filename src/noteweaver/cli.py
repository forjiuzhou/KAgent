"""CLI entry point — interactive dialog with the knowledge agent."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.theme import Theme
from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

from noteweaver.vault import Vault
from noteweaver.agent import KnowledgeAgent
from noteweaver.config import Config

log = logging.getLogger(__name__)

THEME = Theme({
    "tool": "dim cyan",
    "info": "dim",
})
console = Console(theme=THEME)


def resolve_vault_path() -> Path:
    """Find or decide the vault path."""
    env = os.environ.get("NW_VAULT")
    if env:
        return Path(env)
    cwd = Path.cwd()
    if (cwd / ".schema" / "schema.md").is_file():
        return cwd
    return cwd / "vault"


def cmd_init(vault_path: Path) -> None:
    """Initialize a new vault."""
    vault = Vault(vault_path)
    if vault.exists():
        console.print(f"[info]Vault already exists at {vault_path}[/info]")
        return
    vault.init()
    console.print(f"[green]✓[/green] Vault initialized at [bold]{vault_path}[/bold]")
    console.print("[info]  sources/   — drop your raw materials here[/info]")
    console.print("[info]  wiki/      — agent-maintained knowledge[/info]")
    console.print("[info]  .schema/   — vault conventions[/info]")


_WRITE_TOOLS = frozenset({
    "write_page", "append_section", "append_to_section",
    "update_frontmatter", "add_related_link", "save_source",
    "archive_page", "import_files",
})

_MIN_EXCHANGES_FOR_JOURNAL = 3


def _session_has_substance(agent: KnowledgeAgent, exchanges: list[dict]) -> bool:
    """Decide whether this session is worth journaling.

    A session has substance if ANY of the following are true:
    - A write tool was invoked (vault was modified)
    - There were enough exchanges to constitute a real conversation
    - The session type is a system-initiated operation (ingest/lint/digest)
    """
    for m in agent.messages[1:]:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                if fn.get("name", "") in _WRITE_TOOLS:
                    return True
    return len(exchanges) >= _MIN_EXCHANGES_FOR_JOURNAL


def _finalize_session(
    vault: Vault,
    agent: KnowledgeAgent,
    exchanges: list[dict],
    session_type: str = "chat",
) -> None:
    """Save transcript, session memory, trace, and (conditionally) journal.

    Also proposes a session organize plan if there's enough substance.
    Transcript, trace, and session memory are always saved.
    Journal is only written when the session has substance: a write
    operation occurred, there were enough exchanges, or it's a
    system-initiated command (ingest/lint/digest).
    """
    # Session organize: propose knowledge extraction before saving
    if session_type == "chat" and _session_has_substance(agent, exchanges):
        _propose_session_organize(agent)

    try:
        transcript_path = agent.save_transcript()
        log.debug("Transcript saved to %s", transcript_path)
    except Exception as e:
        log.warning("Failed to save transcript: %s", e)
        transcript_path = None

    try:
        trace_path = agent.save_trace()
        if trace_path:
            log.debug("Trace saved to %s", trace_path)
    except Exception as e:
        log.warning("Failed to save trace: %s", e)

    try:
        agent.save_session_memory()
    except Exception as e:
        log.warning("Failed to save session memory: %s", e)

    # System-initiated commands (ingest, lint, digest) always journal
    should_journal = session_type != "chat" or _session_has_substance(agent, exchanges)

    if exchanges and should_journal:
        _save_session_journal(
            vault, agent, exchanges, session_type,
            transcript_ref=str(transcript_path) if transcript_path else None,
        )


def _propose_session_organize(agent: KnowledgeAgent) -> None:
    """Generate an organize plan and interactively ask user for approval."""
    try:
        plan = agent.generate_organize_plan()
    except Exception as e:
        log.warning("Session organize plan generation failed: %s", e)
        return

    if not plan:
        return

    summary = agent.format_organize_plan(plan)
    console.print(
        Panel(
            f"[bold]💡 整理建议[/bold]\n\n{summary}",
            border_style="yellow",
        )
    )

    try:
        answer = input("执行这些整理操作？(y/n/部分编号，如 '1,3') ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print("[info]跳过整理。[/info]")
        return

    if not answer or answer in ("n", "no", "否"):
        agent._clear_pending_plan()
        console.print("[info]已跳过。[/info]")
        return

    if answer in ("y", "yes", "是", "好", "好的"):
        result = agent.execute_organize_plan(plan)
        console.print(f"\n[dim]{result}[/dim]")
        return

    # Partial selection: parse comma-separated indices
    try:
        indices = {int(x.strip()) for x in answer.split(",") if x.strip().isdigit()}
        if indices:
            selected = [plan[i - 1] for i in sorted(indices) if 1 <= i <= len(plan)]
            if selected:
                result = agent.execute_organize_plan(selected)
                console.print(f"\n[dim]{result}[/dim]")
                return
    except (ValueError, IndexError):
        pass

    agent._clear_pending_plan()
    console.print("[info]未识别的输入，跳过整理。[/info]")


def cmd_chat(vault_path: Path) -> None:
    """Interactive chat with the knowledge agent."""
    vault, agent = _make_agent(vault_path)
    cfg = Config.load(vault_path)

    console.print(
        Panel(
            f"[bold]NoteWeaver[/bold] — Knowledge Agent\n"
            f"[info]Vault: {vault_path}\n"
            f"Provider: {cfg.provider} | Model: {cfg.model}[/info]\n"
            f"Type your message. Ctrl+D or 'exit' to quit.",
            border_style="blue",
        )
    )

    history_file = vault_path / ".meta" / "chat_history"
    history_file.parent.mkdir(parents=True, exist_ok=True)
    session: PromptSession = PromptSession(history=FileHistory(str(history_file)))

    exchanges: list[dict] = []

    while True:
        try:
            user_input = session.prompt("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[info]Bye.[/info]")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "/exit", "/quit"):
            console.print("[info]Bye.[/info]")
            break

        exchange: dict = {"user": user_input, "tools": [], "reply": ""}
        try:
            for chunk in agent.chat(user_input):
                if chunk.startswith("  ↳ "):
                    console.print(f"[tool]{chunk}[/tool]")
                    exchange["tools"].append(chunk.strip())
                else:
                    exchange["reply"] = chunk
                    console.print()
                    console.print(Markdown(chunk))
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")
            exchange["reply"] = f"(error: {e})"
        exchanges.append(exchange)

        # Periodic organize proposal during long conversations
        if agent.should_organize():
            _propose_session_organize(agent)

    _finalize_session(vault, agent, exchanges, "chat")


def _save_session_journal(
    vault: Vault,
    agent: KnowledgeAgent,
    exchanges: list[dict],
    session_type: str = "chat",
    *,
    transcript_ref: str | None = None,
) -> None:
    """Append a structured session record to today's journal.

    Uses fixed slots (Insights, Decisions, Open Questions, Pages Touched,
    Follow-ups) for reliable downstream processing by ``nw digest``.
    """
    from datetime import datetime
    import json as _json

    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%H:%M")
    journal_path = f"wiki/journals/{today}.md"

    # Extract structured information from the agent transcript
    pages_created: list[str] = []
    pages_updated: list[str] = []
    pages_read: list[str] = []
    tools_used: list[str] = []
    user_topics: list[str] = []
    agent_conclusions: list[str] = []

    for m in agent.messages[1:]:
        if not isinstance(m, dict):
            continue
        role = m.get("role", "")
        content = m.get("content", "") or ""

        if role == "user" and content:
            short = content[:200] + ("..." if len(content) > 200 else "")
            user_topics.append(short)

        elif role == "assistant" and content:
            short = content[:300] + ("..." if len(content) > 300 else "")
            agent_conclusions.append(short)

        elif role == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                name = fn.get("name", "")
                if name and name not in tools_used:
                    tools_used.append(name)
                try:
                    args = _json.loads(fn.get("arguments", "{}"))
                    path = args.get("path", "")
                    if path:
                        if name in ("write_page", "append_section", "append_to_section",
                                    "update_frontmatter", "add_related_link"):
                            if path not in pages_updated:
                                pages_updated.append(path)
                        elif name == "read_page":
                            if path not in pages_read:
                                pages_read.append(path)
                except (_json.JSONDecodeError, TypeError, AttributeError):
                    pass

    lines = [f"\n### {session_type.title()} session ({now})\n"]

    # Conversation summary slot
    lines.append("#### Conversation")
    for ex in exchanges[:10]:
        user_text = ex["user"]
        user_short = user_text[:300] + "..." if len(user_text) > 300 else user_text
        lines.append(f"- **User:** {user_short}")
        if ex.get("reply"):
            reply_short = ex["reply"][:400] + "..." if len(ex["reply"]) > 400 else ex["reply"]
            lines.append(f"  **Agent:** {reply_short}")
    lines.append("")

    # Pages touched slot
    all_pages = sorted(set(pages_updated + pages_created))
    if all_pages or pages_read:
        lines.append("#### Pages Touched")
        for p in all_pages:
            lines.append(f"- {p} (modified)")
        for p in pages_read[:10]:
            if p not in all_pages:
                lines.append(f"- {p} (read)")
        lines.append("")

    # Tools slot
    if tools_used:
        lines.append(f"#### Tools Used")
        lines.append(f"{', '.join(tools_used)}")
        lines.append("")

    # LLM-generated structured slots
    try:
        journal_data = agent.generate_journal_summary()
    except Exception:
        journal_data = {"insights": [], "decisions": [], "open_questions": [], "follow_ups": []}

    if journal_data.get("insights"):
        lines.append("#### Insights")
        for item in journal_data["insights"]:
            lines.append(f"- {item}")
        lines.append("")

    if journal_data.get("decisions"):
        lines.append("#### Decisions")
        for item in journal_data["decisions"]:
            lines.append(f"- {item}")
        lines.append("")

    if journal_data.get("open_questions"):
        lines.append("#### Open Questions")
        for item in journal_data["open_questions"]:
            lines.append(f"- {item}")
        lines.append("")

    if journal_data.get("follow_ups"):
        lines.append("#### Follow-ups")
        for item in journal_data["follow_ups"]:
            lines.append(f"- {item}")
        lines.append("")
    else:
        lines.append("#### Follow-ups")
        lines.append("*(none identified)*")
        lines.append("")

    if transcript_ref:
        lines.append(f"*Transcript:* `{transcript_ref}`")
        lines.append("")

    entry = "\n".join(lines)

    try:
        existing = vault.read_file(journal_path)
        vault.write_file(journal_path, existing + entry)
    except FileNotFoundError:
        header = (
            f"---\ntitle: Journal {today}\ntype: journal\n"
            f"summary: Daily journal for {today}\ntags: [journal]\n"
            f"created: {today}\nupdated: {today}\n---\n\n"
            f"# {today}\n"
        )
        vault.write_file(journal_path, header + entry)

    vault.append_log(
        "session",
        f"{session_type.title()} session ({len(exchanges)} exchanges)",
        f"Journal: {journal_path}",
    )


def _make_agent(vault_path: Path) -> tuple[Vault, KnowledgeAgent]:
    """Create a Vault and KnowledgeAgent, or exit with an error message."""
    vault = Vault(vault_path)
    if not vault.exists():
        console.print("[red]No vault found.[/red] Run `nw init` first.")
        sys.exit(1)

    cfg = Config.load(vault_path)
    if not cfg.api_key:
        if cfg.provider == "anthropic":
            console.print(
                "[red]Anthropic API key not set.[/red]\n"
                "Export one of:\n"
                "  export ANTHROPIC_API_KEY=sk-ant-...\n"
                "  export ANTHROPIC_AUTH_TOKEN=sk-ant-..."
            )
        else:
            console.print(
                "[red]OPENAI_API_KEY not set.[/red]\n"
                "Export it: export OPENAI_API_KEY=sk-..."
            )
        sys.exit(1)

    agent = KnowledgeAgent(
        vault=vault,
        model=cfg.model,
        api_key=cfg.api_key,
        base_url=cfg.base_url or None,
        provider_name=cfg.provider,
    )
    return vault, agent


def cmd_ingest(vault_path: Path, url: str) -> None:
    """Ingest a URL into the knowledge base (one-shot, no interactive chat)."""
    vault, agent = _make_agent(vault_path)

    console.print(f"[bold]Ingesting:[/bold] {url}")
    prompt = (
        f"Please ingest this URL into the knowledge base: {url}\n"
        "Fetch the content, create appropriate wiki pages, update the index and log."
    )
    exchange: dict = {"user": f"ingest {url}", "tools": [], "reply": ""}
    try:
        for chunk in agent.chat(prompt):
            if chunk.startswith("  ↳ "):
                console.print(f"[tool]{chunk}[/tool]")
                exchange["tools"].append(chunk.strip())
            else:
                exchange["reply"] = chunk
                console.print()
                console.print(Markdown(chunk))
        _finalize_session(vault, agent, [exchange], "ingest")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def cmd_lint(vault_path: Path) -> None:
    """Run a health check on the knowledge base.

    Phase 1: code-based audit (fast, no LLM).
    Phase 2: if issues found and an API key is available, hand the
    structured report to the agent for deeper analysis.
    """
    import json as _json

    vault = Vault(vault_path)
    if not vault.exists():
        console.print("[red]No vault found.[/red] Run `nw init` first.")
        sys.exit(1)

    console.print("[bold]Running vault audit...[/bold]\n")

    report = vault.audit_vault()
    vault.save_audit_report(report)
    console.print(f"[info]{report['summary']}[/info]\n")

    # Display findings
    for key, label in [
        ("stale_imports", "Stale imports"),
        ("hub_candidates", "Hub candidates"),
        ("orphan_pages", "Orphan pages"),
        ("missing_summaries", "Missing summaries"),
        ("broken_links", "Broken links"),
        ("missing_connections", "Missing connections"),
    ]:
        items = report.get(key, [])
        if not items:
            continue
        console.print(f"[bold]{label}[/bold] ({len(items)}):")
        for item in items[:10]:
            if isinstance(item, str):
                console.print(f"  - {item}")
            elif isinstance(item, dict):
                console.print(f"  - {_json.dumps(item, ensure_ascii=False)}")
        if len(items) > 10:
            console.print(f"  ... and {len(items) - 10} more")
        console.print()

    # Phase 2: LLM analysis if issues found and API key available
    cfg = Config.load(vault_path)
    has_issues = report.get("summary", "").startswith("0") is False and "0 issues" not in report.get("summary", "")
    if has_issues and cfg.api_key:
        console.print("[bold]Running LLM analysis...[/bold]\n")
        _, agent = _make_agent(vault_path)

        prompt = (
            f"Vault audit found these issues:\n\n"
            f"{_json.dumps(report, indent=2, ensure_ascii=False)}\n\n"
            "Please analyze the findings above. For each category with issues, "
            "suggest concrete actions to fix them. Prioritize by impact. "
            "Be concise — the audit already found the problems, you just need "
            "to recommend next steps. Log your analysis."
        )
        exchange: dict = {"user": "lint", "tools": [], "reply": ""}
        try:
            for chunk in agent.chat(prompt):
                if chunk.startswith("  ↳ "):
                    console.print(f"[tool]{chunk}[/tool]")
                    exchange["tools"].append(chunk.strip())
                else:
                    exchange["reply"] = chunk
                    console.print()
                    console.print(Markdown(chunk))

            if exchange["reply"]:
                vault.append_log("lint", "Health check completed", exchange["reply"][:500])
            _finalize_session(vault, agent, [exchange], "lint")
        except Exception as e:
            console.print(f"[red]LLM analysis failed: {e}[/red]")
    elif has_issues:
        console.print("[info]Set an API key to get LLM-powered analysis of these findings.[/info]")
        vault.append_log("lint", "Audit completed", report["summary"])


def cmd_digest(vault_path: Path) -> None:
    """Review recent journals and extract insights worth promoting.

    This is the 'background distillation' step — the Agent looks at
    recent conversation logs and journals, identifies valuable content
    that hasn't been captured as proper wiki pages, and offers to create
    notes/canonicals from them.
    """
    vault, agent = _make_agent(vault_path)

    console.print("[bold]Reviewing recent journals for insights to extract...[/bold]\n")

    # Check for available transcripts
    transcript_dir = vault.meta_dir / "transcripts"
    transcript_hint = ""
    if transcript_dir.is_dir():
        transcripts = sorted(transcript_dir.glob("*.json"))
        if transcripts:
            recent = [t.name for t in transcripts[-5:]]
            transcript_hint = (
                "\n\nRecent conversation transcripts are available at "
                f".meta/transcripts/. Recent files: {', '.join(recent)}. "
                "Use read_transcript(path) to access the full conversation "
                "when a journal entry mentions something worth deeper review."
            )

    prompt = (
        "Please review the recent journal entries in wiki/journals/. "
        "For each journal, look for:\n"
        "1. Insights, conclusions, or decisions worth promoting to a Note or Canonical\n"
        "2. Topics mentioned repeatedly that deserve their own page\n"
        "3. Connections between different conversations that aren't yet linked\n\n"
        "For each finding, write a structured proposal as a "
        "'#### Promotion Candidates' section in today's journal. Each candidate "
        "should include: proposed title, target type (note/canonical/synthesis), "
        "a summary of the insight, and which journal entries it came from.\n\n"
        "Do NOT create wiki pages directly — only write proposals to the journal. "
        "The user will review and confirm promotions in their next session.\n\n"
        "After writing proposals, report what you found."
        + transcript_hint
    )
    exchange: dict = {"user": "digest", "tools": [], "reply": ""}
    try:
        for chunk in agent.chat(prompt):
            if chunk.startswith("  ↳ "):
                console.print(f"[tool]{chunk}[/tool]")
                exchange["tools"].append(chunk.strip())
            else:
                exchange["reply"] = chunk
                console.print()
                console.print(Markdown(chunk))

        _finalize_session(vault, agent, [exchange], "digest")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def cmd_import(vault_path: Path, source_path: str) -> None:
    """Import existing markdown files into the vault."""
    vault = Vault(vault_path)
    if not vault.exists():
        console.print("[red]No vault found.[/red] Run `nw init` first.")
        sys.exit(1)

    console.print(f"[bold]Importing from:[/bold] {source_path}")
    result = vault.import_directory(source_path)
    console.print(result)


def cmd_rebuild_index(vault_path: Path) -> None:
    """Rebuild index.md and the FTS search index from file frontmatter."""
    vault = Vault(vault_path)
    if not vault.exists():
        console.print("[red]No vault found.[/red] Run `nw init` first.")
        sys.exit(1)

    vault.rebuild_index()
    console.print("[green]✓[/green] index.md rebuilt from file frontmatter.")

    count = vault.rebuild_search_index()
    console.print(f"[green]✓[/green] Search index rebuilt ({count} pages indexed).")


def cmd_status(vault_path: Path) -> None:
    """Show vault status — page counts, recent activity, health."""
    vault = Vault(vault_path)
    if not vault.exists():
        console.print("[red]No vault found.[/red] Run `nw init` first.")
        sys.exit(1)

    s = vault.stats()
    total = s["concepts"] + s["journals"] + s["synthesis"]

    console.print(
        Panel(
            f"[bold]Vault Status[/bold]  {vault_path}\n\n"
            f"  Wiki pages:    [bold]{total}[/bold]\n"
            f"    concepts/    {s['concepts']}\n"
            f"    journals/    {s['journals']}\n"
            f"    synthesis/   {s['synthesis']}\n"
            f"  Source files:   {s['sources']}",
            border_style="blue",
        )
    )

    # Health metrics
    metrics = vault.health_metrics()
    if metrics["total_pages"] > 0:
        console.print("\n[bold]Health metrics:[/bold]")
        console.print(f"  Hubs:                    {metrics['hubs']}")
        console.print(f"  Canonicals:              {metrics['canonicals']}")
        console.print(f"  Canonical source ratio:  {metrics['canonical_source_ratio']}")
        console.print(f"  Orphan pages:            {metrics['orphan_rate']}")
        console.print(f"  Missing summary:         {metrics['pages_without_summary']}")

    # Show last 5 log entries
    try:
        log_content = vault.read_file("wiki/log.md")
        entries = [
            line for line in log_content.split("\n")
            if line.startswith("## [")
        ]
        if entries:
            console.print("\n[bold]Recent activity:[/bold]")
            for entry in entries[-5:]:
                console.print(f"  {entry.lstrip('# ')}")
    except FileNotFoundError:
        pass

    # Session memory status
    mem_path = vault.meta_dir / "session-memory.md"
    if mem_path.is_file():
        console.print("\n[info]Session memory: active[/info]")

    # Transcript count
    transcript_dir = vault.meta_dir / "transcripts"
    if transcript_dir.is_dir():
        count = len(list(transcript_dir.glob("*.json")))
        if count:
            console.print(f"[info]Transcripts:    {count} saved[/info]")


def cmd_trace(vault_path: Path, args: list[str]) -> None:
    """Show or list agent traces for debugging.

    Usage:
        nw trace              — list recent traces
        nw trace <file>       — render a specific trace (human-readable)
        nw trace --raw <file> — output raw JSONL for machine consumption
    """
    from noteweaver.trace import TraceCollector

    trace_dir = vault_path / ".meta" / "traces"

    raw_mode = "--raw" in args
    remaining = [a for a in args if a != "--raw"]

    if not remaining:
        if not trace_dir.is_dir():
            console.print("[info]No traces found.[/info]")
            return
        traces = sorted(trace_dir.glob("*.trace.jsonl"))
        if not traces:
            console.print("[info]No traces found.[/info]")
            return
        console.print(f"[bold]Traces[/bold] ({len(traces)} total)\n")
        for t in traces[-20:]:
            size = t.stat().st_size
            console.print(f"  {t.name}  ({size:,} bytes)")
        console.print(f"\n[info]Run `nw trace <filename>` to view a trace.[/info]")
        return

    target = remaining[0]
    path = trace_dir / target if not Path(target).is_absolute() else Path(target)

    if not path.exists():
        if not path.suffix:
            path = trace_dir / (target + ".trace.jsonl")
        if not path.exists():
            console.print(f"[red]Trace not found: {target}[/red]")
            return

    if raw_mode:
        sys.stdout.write(path.read_text(encoding="utf-8"))
    else:
        events = TraceCollector.load(path)
        report = TraceCollector.render_human(events)
        console.print(report)


def main() -> None:
    """Main CLI entry point."""
    args = sys.argv[1:]

    if not args or args[0] == "chat":
        vault_path = resolve_vault_path()
        cmd_chat(vault_path)
    elif args[0] == "init":
        vault_path = resolve_vault_path()
        cmd_init(vault_path)
    elif args[0] == "ingest":
        if len(args) < 2:
            console.print("[red]Usage: nw ingest <url>[/red]")
            sys.exit(1)
        vault_path = resolve_vault_path()
        cmd_ingest(vault_path, args[1])
    elif args[0] == "lint":
        vault_path = resolve_vault_path()
        cmd_lint(vault_path)
    elif args[0] == "digest":
        vault_path = resolve_vault_path()
        cmd_digest(vault_path)
    elif args[0] == "import":
        if len(args) < 2:
            console.print("[red]Usage: nw import <path>[/red]")
            sys.exit(1)
        vault_path = resolve_vault_path()
        cmd_import(vault_path, args[1])
    elif args[0] in ("rebuild-index", "rebuild"):
        vault_path = resolve_vault_path()
        cmd_rebuild_index(vault_path)
    elif args[0] == "status":
        vault_path = resolve_vault_path()
        cmd_status(vault_path)
    elif args[0] == "trace":
        vault_path = resolve_vault_path()
        cmd_trace(vault_path, args[1:])
    elif args[0] == "gateway":
        vault_path = resolve_vault_path()
        from noteweaver.gateway import run_gateway
        run_gateway(vault_path)
    elif args[0] == "help" or args[0] in ("-h", "--help"):
        console.print(
            Panel(
                "[bold]NoteWeaver[/bold] — AI Knowledge Management Agent\n\n"
                "Commands:\n"
                "  [bold]nw init[/bold]              Initialize a new vault\n"
                "  [bold]nw chat[/bold]              Chat with the agent (default)\n"
                "  [bold]nw ingest <url>[/bold]      Import a web article\n"
                "  [bold]nw import <path>[/bold]     Import existing md files\n"
                "  [bold]nw lint[/bold]              Health-check the knowledge base\n"
                "  [bold]nw digest[/bold]            Extract insights from recent journals\n"
                "  [bold]nw rebuild-index[/bold]     Rebuild index.md and search index\n"
                "  [bold]nw status[/bold]            Show vault status\n"
                "  [bold]nw trace[/bold]             List/view agent run traces\n"
                "  [bold]nw gateway[/bold]           Start IM gateway (Telegram/Feishu)\n"
                "  [bold]nw help[/bold]              Show this help\n\n"
                "Environment:\n"
                "  OPENAI_API_KEY         Your OpenAI API key.\n"
                "  OPENAI_BASE_URL        OpenAI-compatible API base URL (optional).\n"
                "  OPENAI_API_BASE        Alias for OPENAI_BASE_URL.\n"
                "  ANTHROPIC_API_KEY      Your Anthropic API key.\n"
                "  ANTHROPIC_AUTH_TOKEN   Anthropic proxy token (alt to API key).\n"
                "  ANTHROPIC_BASE_URL     Custom Anthropic / Claude API URL (optional).\n"
                "  ANTHROPIC_API_URL      Alias for ANTHROPIC_BASE_URL.\n"
                "  CLAUDE_API_URL         Alias for ANTHROPIC_BASE_URL.\n"
                "  NW_PROVIDER            Force provider: 'openai' or 'anthropic'.\n"
                "  NW_MODEL               Model name (auto-detected per provider).\n"
                "  NW_VAULT               Vault path.\n"
                "  NW_TELEGRAM_TOKEN      Telegram bot token (enables Telegram adapter).\n"
                "  NW_TELEGRAM_ALLOWED_USERS  Comma-separated Telegram user IDs.\n"
                "  NW_DIGEST_INTERVAL_HOURS   Auto-digest interval in gateway (default: 6).\n"
                "  NW_LINT_INTERVAL_HOURS     Auto-lint interval in gateway (default: 24).\n"
                "  NW_NOTIFY_HOUR         Hour to send batched notifications (default: 9).",
                border_style="blue",
            )
        )
    else:
        console.print(f"[red]Unknown command: {args[0]}[/red]")
        console.print("Run `nw help` for usage.")
        sys.exit(1)


if __name__ == "__main__":
    main()
