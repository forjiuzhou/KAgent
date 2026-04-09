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
    """Save transcript, session memory, and (conditionally) journal.

    Transcript and session memory are always saved.
    Journal is only written when the session has substance: a write
    operation occurred, there were enough exchanges, or it's a
    system-initiated command (ingest/lint/digest).
    """
    try:
        transcript_path = agent.save_transcript()
        log.debug("Transcript saved to %s", transcript_path)
    except Exception as e:
        log.warning("Failed to save transcript: %s", e)
        transcript_path = None

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
    """Run a health check on the knowledge base."""
    vault, agent = _make_agent(vault_path)

    console.print("[bold]Running knowledge base health check...[/bold]\n")
    prompt = (
        "Please lint/health-check the wiki. Use list_page_summaries to scan "
        "all pages, then check for:\n"
        "1. Orphan pages (no inbound [[links]] from other pages)\n"
        "2. Mentioned but missing pages (referenced via [[link]] but no page exists)\n"
        "3. Stale or contradictory information across pages\n"
        "4. Pages missing summary or tags in frontmatter\n"
        "5. Topics with 3+ pages but no Hub\n"
        "6. Suggestions for new pages or connections\n"
        "Report your findings and log them."
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
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


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
        "3. Connections between different conversations that aren't yet linked\n"
        "4. User preferences or patterns that should be noted in preferences.md\n\n"
        "For each finding, either:\n"
        "- Create the page directly if the insight is clear\n"
        "- Or report what you found and ask whether to create it\n\n"
        "This is a distillation pass — turning raw conversation logs into "
        "structured knowledge."
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
    """Rebuild index.md from actual file frontmatter."""
    vault = Vault(vault_path)
    if not vault.exists():
        console.print("[red]No vault found.[/red] Run `nw init` first.")
        sys.exit(1)

    content = vault.rebuild_index()
    console.print("[green]✓[/green] index.md rebuilt from file frontmatter.")
    s = vault.stats()
    total = s["concepts"] + s["journals"] + s["synthesis"]
    console.print(f"[info]  {total} pages indexed[/info]")


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
                "  [bold]nw rebuild-index[/bold]     Rebuild index.md from file metadata\n"
                "  [bold]nw status[/bold]            Show vault status\n"
                "  [bold]nw gateway[/bold]           Start IM gateway (Telegram/Feishu)\n"
                "  [bold]nw help[/bold]              Show this help\n\n"
                "Environment:\n"
                "  OPENAI_API_KEY         Your OpenAI API key.\n"
                "  ANTHROPIC_API_KEY      Your Anthropic API key.\n"
                "  NW_PROVIDER            Force provider: 'openai' or 'anthropic'.\n"
                "  NW_MODEL               Model name (auto-detected per provider).\n"
                "  NW_VAULT               Vault path.\n"
                "  NW_TELEGRAM_TOKEN      Telegram bot token (enables Telegram adapter).\n"
                "  NW_TELEGRAM_ALLOWED_USERS  Comma-separated Telegram user IDs.",
                border_style="blue",
            )
        )
    else:
        console.print(f"[red]Unknown command: {args[0]}[/red]")
        console.print("Run `nw help` for usage.")
        sys.exit(1)


if __name__ == "__main__":
    main()
