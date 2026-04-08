"""CLI entry point — interactive dialog with the knowledge agent."""

from __future__ import annotations

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

    if exchanges:
        _save_session_journal(vault, exchanges, "chat")


def _save_session_journal(
    vault: Vault,
    exchanges: list[dict],
    session_type: str = "chat",
) -> None:
    """Append a session record to today's journal.

    Each exchange dict has: user (str), tools (list[str]), reply (str).
    Records both user input AND agent responses for full traceability.
    """
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    journal_path = f"wiki/journals/{today}.md"

    lines = [f"\n### {session_type.title()} session ({now})\n"]
    for ex in exchanges[:15]:
        user_short = ex["user"][:120] + "..." if len(ex["user"]) > 120 else ex["user"]
        lines.append(f"**User:** {user_short}")
        if ex.get("tools"):
            tools_str = ", ".join(t.lstrip("↳ ").split("(")[0].strip() for t in ex["tools"][:5])
            lines.append(f"*Tools:* {tools_str}")
        if ex.get("reply"):
            reply_short = ex["reply"][:200] + "..." if len(ex["reply"]) > 200 else ex["reply"]
            lines.append(f"**Agent:** {reply_short}")
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
        _save_session_journal(vault, [exchange], "ingest")
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
        _save_session_journal(vault, [exchange], "lint")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def cmd_import(vault_path: Path, source_path: str) -> None:
    """Import existing markdown files into the vault.

    Scans the source directory, classifies files by frontmatter,
    copies them into appropriate vault locations, and rebuilds the index.
    """
    from noteweaver.frontmatter import extract_frontmatter

    vault = Vault(vault_path)
    if not vault.exists():
        console.print("[red]No vault found.[/red] Run `nw init` first.")
        sys.exit(1)

    src = Path(source_path).resolve()
    if not src.is_dir():
        console.print(f"[red]Not a directory: {source_path}[/red]")
        sys.exit(1)

    md_files = sorted(src.rglob("*.md"))
    if not md_files:
        console.print(f"[info]No .md files found in {source_path}[/info]")
        return

    console.print(f"[bold]Scanning:[/bold] {src}")
    console.print(f"[info]Found {len(md_files)} markdown files[/info]\n")

    plan: list[dict] = []
    for f in md_files:
        content = f.read_text(encoding="utf-8", errors="replace")
        fm = extract_frontmatter(content)
        rel_name = f.name

        if fm and fm.get("type") in ("hub", "canonical", "note", "synthesis"):
            dest = f"wiki/concepts/{rel_name}"
            obj_type = fm["type"]
        elif fm and fm.get("type") == "journal":
            dest = f"wiki/journals/{rel_name}"
            obj_type = "journal"
        else:
            dest = f"wiki/concepts/{rel_name}"
            obj_type = "note (no frontmatter — will be added)"

        plan.append({
            "source": str(f),
            "dest": dest,
            "type": obj_type,
            "has_frontmatter": fm is not None,
            "content": content,
        })

    console.print("[bold]Migration plan:[/bold]\n")
    for p in plan:
        fm_status = "[green]✓[/green]" if p["has_frontmatter"] else "[yellow]![/yellow]"
        console.print(f"  {fm_status} {Path(p['source']).name} → {p['dest']} ({p['type']})")

    console.print(f"\n[bold]{len(plan)} files to import.[/bold]")
    console.print("[info]Importing...[/info]\n")

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    imported = 0

    for p in plan:
        content = p["content"]
        if not p["has_frontmatter"]:
            title = Path(p["source"]).stem.replace("-", " ").replace("_", " ").title()
            header = (
                f"---\ntitle: {title}\ntype: note\n"
                f"summary: Imported from {Path(p['source']).name}\n"
                f"tags: [imported]\ncreated: {today}\nupdated: {today}\n---\n\n"
            )
            content = header + content

        try:
            vault.write_file(p["dest"], content)
            imported += 1
        except Exception as e:
            console.print(f"[red]  Error importing {p['source']}: {e}[/red]")

    vault.rebuild_index()
    vault.append_log("import", f"Imported {imported} files from {source_path}")
    console.print(f"\n[green]✓[/green] Imported {imported}/{len(plan)} files. Index rebuilt.")


def cmd_rebuild_index(vault_path: Path) -> None:
    """Rebuild index.md from actual file frontmatter."""
    vault = Vault(vault_path)
    if not vault.exists():
        console.print("[red]No vault found.[/red] Run `nw init` first.")
        sys.exit(1)

    content = vault.rebuild_index()
    console.print("[green]✓[/green] index.md rebuilt from file frontmatter.")
    s = vault.stats()
    total = s["concepts"] + s["entities"] + s["journals"] + s["synthesis"]
    console.print(f"[info]  {total} pages indexed[/info]")


def cmd_status(vault_path: Path) -> None:
    """Show vault status — page counts, recent activity, health."""
    vault = Vault(vault_path)
    if not vault.exists():
        console.print("[red]No vault found.[/red] Run `nw init` first.")
        sys.exit(1)

    s = vault.stats()
    total = s["concepts"] + s["entities"] + s["journals"] + s["synthesis"]

    console.print(
        Panel(
            f"[bold]Vault Status[/bold]  {vault_path}\n\n"
            f"  Wiki pages:    [bold]{total}[/bold]\n"
            f"    concepts/    {s['concepts']}\n"
            f"    entities/    {s['entities']}\n"
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
                "  [bold]nw rebuild-index[/bold]     Rebuild index.md from file metadata\n"
                "  [bold]nw status[/bold]            Show vault status\n"
                "  [bold]nw help[/bold]              Show this help\n\n"
                "Environment:\n"
                "  OPENAI_API_KEY       Your OpenAI API key.\n"
                "  ANTHROPIC_API_KEY    Your Anthropic API key.\n"
                "  ANTHROPIC_AUTH_TOKEN Alternative to ANTHROPIC_API_KEY (for proxies).\n"
                "  OPENAI_BASE_URL      Custom OpenAI-compatible endpoint.\n"
                "  ANTHROPIC_BASE_URL   Custom Anthropic-compatible endpoint.\n"
                "  NW_PROVIDER          Force provider: 'openai' or 'anthropic'.\n"
                "  NW_MODEL             Model name (auto-detected per provider).\n"
                "  NW_VAULT             Vault path.",
                border_style="blue",
            )
        )
    else:
        console.print(f"[red]Unknown command: {args[0]}[/red]")
        console.print("Run `nw help` for usage.")
        sys.exit(1)


if __name__ == "__main__":
    main()
