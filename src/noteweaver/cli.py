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

from noteweaver.constants import EXIT_KEYWORDS
from noteweaver.vault import Vault
from noteweaver.agent import KnowledgeAgent
from noteweaver.config import Config
from noteweaver.plan import Plan, PlanStatus
from noteweaver.session import (
    make_agent as _make_agent_core,
    finalize_session as _finalize_session,
    session_has_substance as _session_has_substance,
    save_last_digest_date,
    build_digest_prompt,
)

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


def _cli_finalize_session(
    vault: Vault,
    agent: KnowledgeAgent,
    exchanges: list[dict],
    session_type: str = "chat",
) -> None:
    """CLI wrapper around the shared finalize_session."""
    _finalize_session(
        vault, agent, exchanges, session_type,
        approve_callback=lambda a, p: _approve_and_execute(a, p),
    )


def _approve_and_execute(
    agent: KnowledgeAgent,
    plan: "Plan | list[dict] | None" = None,
) -> None:
    """Present a session-organize plan to the user and execute on approval.

    Called at the end of a chat session (``_finalize_session``), from
    ``cmd_organize``, and after ``cmd_ingest`` / ``cmd_lint`` / ``cmd_digest``
    when pending plans exist.  Normal interactive writes in ``chat()`` bypass
    this entirely.

    Supports both Plan objects and legacy list[dict] for backward compat.
    """
    if plan is None:
        pending = agent.plan_store.list_pending()
        if pending:
            plan = pending[0]
        else:
            legacy = agent._load_pending_plan()
            if legacy:
                plan = legacy
    if not plan:
        return

    if isinstance(plan, Plan):
        summary = agent.format_plan(plan)
        console.print(
            Panel(
                f"[bold]📋 变更提案[/bold] [{plan.id}]\n\n{summary}",
                border_style="yellow",
            )
        )

        try:
            answer = input("执行？(y/n) ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("[info]跳过。[/info]")
            agent.plan_store.update_status(plan.id, PlanStatus.REJECTED)
            return

        if not answer or answer.startswith("n"):
            agent.plan_store.update_status(plan.id, PlanStatus.REJECTED)
            console.print("[info]已跳过。[/info]")
            return

        if answer.startswith("y"):
            agent.plan_store.update_status(plan.id, PlanStatus.APPROVED)
            result = agent.execute_plan(plan.id)
            console.print(f"\n[dim]{result}[/dim]")
            return

        agent.plan_store.update_status(plan.id, PlanStatus.REJECTED)
        console.print("[info]未识别的输入，跳过。[/info]")
    else:
        summary = agent.format_organize_plan(plan)
        console.print(
            Panel(
                f"[bold]📋 变更计划[/bold]\n\n{summary}",
                border_style="yellow",
            )
        )

        try:
            answer = input("执行？(y/n/部分编号，如 '1,3') ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            console.print("[info]跳过。[/info]")
            agent._clear_pending_plan()
            return

        if not answer or answer.startswith("n"):
            agent._clear_pending_plan()
            console.print("[info]已跳过。[/info]")
            return

        if answer.startswith("y"):
            result = agent.execute_organize_plan(plan)
            console.print(f"\n[dim]{result}[/dim]")
            return

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
        console.print("[info]未识别的输入，跳过。[/info]")


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
        if user_input.lower() in EXIT_KEYWORDS:
            console.print("[info]Bye.[/info]")
            break
        if user_input.strip() == "/organize":
            try:
                plan_obj = agent.generate_organize_plan()
                if plan_obj:
                    _approve_and_execute(agent, plan_obj)
                else:
                    console.print("[info]没有需要整理的内容。[/info]")
            except Exception as e:
                console.print(f"[red]整理失败: {e}[/red]")
            continue

        exchange: dict = {"user": user_input, "tools": [], "reply": ""}
        try:
            for chunk in agent.chat(user_input):
                if chunk.startswith("  📋 "):
                    console.print(f"[tool]{chunk}[/tool]")
                    exchange["tools"].append(chunk.strip())
                elif chunk.startswith("  ↳ "):
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

    _cli_finalize_session(vault, agent, exchanges, "chat")


def _make_agent(vault_path: Path) -> tuple[Vault, KnowledgeAgent]:
    """Create a Vault and KnowledgeAgent, or exit with an error message."""
    try:
        vault, agent, _cfg = _make_agent_core(vault_path)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)
    return vault, agent


def cmd_ingest(vault_path: Path, url: str) -> None:
    """Ingest a URL into the knowledge base.

    The agent fetches the URL and writes directly during chat.
    Any pending organize plans from the session are presented for approval.
    """
    vault, agent = _make_agent(vault_path)

    console.print(f"[bold]Ingesting:[/bold] {url}")
    prompt = (
        f"Ingest this URL into the knowledge base: {url}\n\n"
        "Steps:\n"
        "1. Use fetch_url(url) to retrieve and preview the content.\n"
        "2. Use search() to check if this topic already exists in the wiki.\n"
        "3. Use write_page() to create a new wiki page, or append_section() "
        "to add to an existing page. Include proper YAML frontmatter.\n"
        "4. Use add_related_link() to connect the new page to related pages."
    ).format(url=url)
    exchange: dict = {"user": f"ingest {url}", "tools": [], "reply": ""}
    try:
        for chunk in agent.chat(prompt):
            if chunk.startswith("  📋 ") or chunk.startswith("  ↳ "):
                console.print(f"[tool]{chunk}[/tool]")
                exchange["tools"].append(chunk.strip())
            else:
                exchange["reply"] = chunk
                console.print()
                console.print(Markdown(chunk))

        for plan_obj in agent.plan_store.list_pending():
            _approve_and_execute(agent, plan_obj)
        _cli_finalize_session(vault, agent, [exchange], "ingest")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def cmd_lint(vault_path: Path) -> None:
    """Run a health check on the knowledge base.

    Phase 1: code-based audit (fast, no LLM) — always runs.
    Phase 2: if issues found and an API key is available, runs the
    ``organize_wiki`` skill which uses the agent to fix issues via
    primitive tools.
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

    for key, label in [
        ("stale_imports", "Stale imports"),
        ("hub_candidates", "Hub candidates"),
        ("orphan_pages", "Orphan pages"),
        ("missing_summaries", "Missing summaries"),
        ("broken_links", "Broken links"),
        ("missing_connections", "Missing connections"),
        ("similar_tags", "Similar tags (potential duplicates)"),
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

    cfg = Config.load(vault_path)
    has_issues = "0 issues" not in report.get("summary", "")
    if has_issues and cfg.api_key:
        console.print("[bold]Running organize_wiki skill...[/bold]\n")
        _, agent = _make_agent(vault_path)

        exchange: dict = {"user": "lint", "tools": [], "reply": ""}
        try:
            result = None
            for chunk in agent.run_skill("organize_wiki"):
                if chunk.startswith("  📋 ") or chunk.startswith("  ↳ "):
                    console.print(f"[tool]{chunk}[/tool]")
                    exchange["tools"].append(chunk.strip())
                elif chunk.startswith("[organize_wiki]"):
                    console.print(f"[info]{chunk}[/info]")
                else:
                    exchange["reply"] = chunk
                    console.print()
                    console.print(Markdown(chunk))

            for plan_obj in agent.plan_store.list_pending():
                _approve_and_execute(agent, plan_obj)
            if exchange["reply"]:
                vault.append_log("lint", "Health check completed", exchange["reply"][:500])
            _cli_finalize_session(vault, agent, [exchange], "lint")
        except Exception as e:
            console.print(f"[red]LLM analysis failed: {e}[/red]")
    elif has_issues:
        console.print("[info]Set an API key to get LLM-powered remediation plans.[/info]")
        vault.append_log("lint", "Audit completed", report["summary"])


def cmd_digest(vault_path: Path) -> None:
    """Review recent journals and extract insights worth promoting.

    The agent reads journals and writes directly during chat (e.g.
    creating wiki pages from insights).  Any pending organize plans
    are presented for approval afterwards.
    """
    vault, agent = _make_agent(vault_path)

    console.print("[bold]Reviewing recent journals for insights to extract...[/bold]\n")

    prompt = build_digest_prompt(vault)
    exchange: dict = {"user": "digest", "tools": [], "reply": ""}
    try:
        for chunk in agent.chat(prompt):
            if chunk.startswith("  📋 ") or chunk.startswith("  ↳ "):
                console.print(f"[tool]{chunk}[/tool]")
                exchange["tools"].append(chunk.strip())
            else:
                exchange["reply"] = chunk
                console.print()
                console.print(Markdown(chunk))

        for plan_obj in agent.plan_store.list_pending():
            _approve_and_execute(agent, plan_obj)

        save_last_digest_date(vault)

        _cli_finalize_session(vault, agent, [exchange], "digest")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def cmd_organize(vault_path: Path) -> None:
    """Explicitly organize recent conversation knowledge into the vault.

    Uses generate_organize_plan → approve → execute.
    """
    vault, agent = _make_agent(vault_path)

    console.print("[bold]Generating organization plan from recent sessions...[/bold]\n")

    try:
        plan_obj = agent.generate_organize_plan()
    except Exception as e:
        console.print(f"[red]Plan generation failed: {e}[/red]")
        sys.exit(1)

    if not plan_obj:
        console.print("[info]No knowledge worth capturing found in recent conversations.[/info]")
        return

    _approve_and_execute(agent, plan_obj)


def cmd_import(vault_path: Path, source_path: str) -> None:
    """Import existing markdown files into the vault (deterministic, no LLM).

    For LLM-assisted import with structuring, use ``nw import-sources``.
    """
    vault = Vault(vault_path)
    if not vault.exists():
        console.print("[red]No vault found.[/red] Run `nw init` first.")
        sys.exit(1)

    console.print(f"[bold]Importing from:[/bold] {source_path}")
    result = vault.import_directory(source_path)
    console.print(result)


def cmd_import_sources(vault_path: Path, source_dir: str = "sources") -> None:
    """LLM-assisted import: read source files and create structured wiki pages.

    Uses the ``import_sources`` skill — scans sources/ for unprocessed
    files and drives the agent to create wiki pages with proper
    frontmatter, structure, and cross-links.
    """
    vault, agent = _make_agent(vault_path)

    console.print(
        f"[bold]Running import_sources skill[/bold] "
        f"(source: {source_dir}/)\n"
    )

    exchange: dict = {"user": f"import-sources {source_dir}", "tools": [], "reply": ""}
    try:
        for chunk in agent.run_skill("import_sources", source_dir=source_dir):
            if chunk.startswith("  📋 ") or chunk.startswith("  ↳ "):
                console.print(f"[tool]{chunk}[/tool]")
                exchange["tools"].append(chunk.strip())
            elif chunk.startswith("[import_sources]"):
                console.print(f"[info]{chunk}[/info]")
            else:
                exchange["reply"] = chunk
                console.print()
                console.print(Markdown(chunk))

        for plan_obj in agent.plan_store.list_pending():
            _approve_and_execute(agent, plan_obj)
        _cli_finalize_session(vault, agent, [exchange], "ingest")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


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
        nw trace                   — list recent traces
        nw trace <file>            — render a trace (compact)
        nw trace -v <file>         — render with full debug info
        nw trace --verbose <file>  — same as -v
        nw trace --raw <file>      — output raw JSONL for machine consumption
        nw trace --last             — show the most recent trace
        nw trace --last -v          — most recent trace, verbose
    """
    from noteweaver.trace import TraceCollector

    trace_dir = vault_path / ".meta" / "traces"

    raw_mode = "--raw" in args
    verbose_mode = "--verbose" in args or "-v" in args
    last_mode = "--last" in args
    remaining = [
        a for a in args
        if a not in ("--raw", "--verbose", "-v", "--last")
    ]

    if last_mode and not remaining:
        if not trace_dir.is_dir():
            console.print("[info]No traces found.[/info]")
            return
        traces = sorted(trace_dir.glob("*.trace.jsonl"))
        if not traces:
            console.print("[info]No traces found.[/info]")
            return
        remaining = [traces[-1].name]

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
        console.print(
            f"\n[info]Run `nw trace <filename>` to view a trace.\n"
            f"     `nw trace -v <filename>` for verbose debug output.\n"
            f"     `nw trace --last` to view the most recent trace.[/info]"
        )
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
        report = TraceCollector.render_human(events, verbose=verbose_mode)
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
    elif args[0] == "organize":
        vault_path = resolve_vault_path()
        cmd_organize(vault_path)
    elif args[0] == "import":
        if len(args) < 2:
            console.print("[red]Usage: nw import <path>[/red]")
            sys.exit(1)
        vault_path = resolve_vault_path()
        cmd_import(vault_path, args[1])
    elif args[0] == "import-sources":
        vault_path = resolve_vault_path()
        source_dir = args[1] if len(args) > 1 else "sources"
        cmd_import_sources(vault_path, source_dir)
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
                "  [bold]nw import <path>[/bold]     Import existing md files (no LLM)\n"
                "  [bold]nw import-sources[/bold]    LLM-assisted import from sources/\n"
                "  [bold]nw organize[/bold]          Organize recent conversation knowledge\n"
                "  [bold]nw lint[/bold]              Health-check + auto-fix (skill: organize_wiki)\n"
                "  [bold]nw digest[/bold]            Extract insights from recent journals\n"
                "  [bold]nw rebuild-index[/bold]     Rebuild index.md and search index\n"
                "  [bold]nw status[/bold]            Show vault status\n"
                "  [bold]nw trace[/bold]             List/view agent run traces\n"
                "  [bold]nw trace -v <file>[/bold]   View trace with full debug info\n"
                "  [bold]nw trace --last[/bold]      View the most recent trace\n"
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
