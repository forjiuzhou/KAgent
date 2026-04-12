"""Session management — shared logic between CLI and Gateway.

Extracts the common operations that both ``cli.py`` and ``gateway.py``
need: agent construction, session finalization (transcript/trace/memory/
journal), substance detection, and digest-date tracking.

Before this module, CLI and Gateway duplicated these behaviors with
divergent implementations.  Now both go through the same code paths.
"""

from __future__ import annotations

import json as _json
import logging
from datetime import datetime
from pathlib import Path

from noteweaver.agent import KnowledgeAgent, create_provider
from noteweaver.config import Config
from noteweaver.vault import Vault

log = logging.getLogger(__name__)


_WRITE_TOOLS = frozenset({
    "write_page", "append_section", "update_frontmatter", "add_related_link",
    "capture", "ingest", "organize", "restructure",
})

_MIN_EXCHANGES_FOR_JOURNAL = 3


def make_agent(vault_path: Path) -> tuple[Vault, KnowledgeAgent, Config]:
    """Create a Vault and KnowledgeAgent from config.

    Returns (vault, agent, config).  Raises ``SystemExit`` if the vault
    doesn't exist or no API key is configured.

    Both CLI and Gateway should use this single constructor path so the
    agent is always built the same way.
    """
    vault = Vault(vault_path)
    if not vault.exists():
        raise RuntimeError(f"No vault at {vault_path}. Run `nw init` first.")

    cfg = Config.load(vault_path)
    if not cfg.api_key:
        if cfg.provider == "anthropic":
            raise RuntimeError(
                "Anthropic API key not set. "
                "Export ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN."
            )
        else:
            raise RuntimeError(
                "OPENAI_API_KEY not set. Export it: export OPENAI_API_KEY=sk-..."
            )

    provider = create_provider(
        cfg.provider, api_key=cfg.api_key, base_url=cfg.base_url or None,
    )

    agent = KnowledgeAgent(
        vault=vault,
        model=cfg.model,
        provider=provider,
    )
    return vault, agent, cfg


def session_has_substance(agent: KnowledgeAgent, exchanges: list[dict]) -> bool:
    """Decide whether a session is worth journaling.

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


def finalize_session(
    vault: Vault,
    agent: KnowledgeAgent,
    exchanges: list[dict],
    session_type: str = "chat",
    *,
    run_organize: bool = True,
    approve_callback=None,
) -> None:
    """Save transcript, session memory, trace, and (conditionally) journal.

    Also proposes a session organize plan if there's enough substance
    and ``run_organize`` is True.

    Parameters
    ----------
    approve_callback:
        Optional callable ``(agent, plan) -> None`` used by CLI to
        present the plan interactively.  Gateway passes ``None`` and
        handles plan approval through its own async message flow.
    """
    if (
        run_organize
        and session_type == "chat"
        and session_has_substance(agent, exchanges)
    ):
        try:
            plan_obj = agent.generate_organize_plan()
            if plan_obj and approve_callback is not None:
                approve_callback(agent, plan_obj)
        except Exception as e:
            log.warning("Session organize failed: %s", e)

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

    should_journal = session_type != "chat" or session_has_substance(agent, exchanges)

    if exchanges and should_journal:
        save_session_journal(
            vault, agent, exchanges, session_type,
            transcript_ref=str(transcript_path) if transcript_path else None,
        )


def save_session_journal(
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
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().strftime("%H:%M")
    journal_path = f"wiki/journals/{today}.md"

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
                    path = args.get("path", "") or args.get("target", "")
                    if path:
                        if name in ("write_page", "capture", "organize",
                                    "restructure", "ingest"):
                            if path not in pages_updated:
                                pages_updated.append(path)
                        elif name == "read_page":
                            if path not in pages_read:
                                pages_read.append(path)
                except (_json.JSONDecodeError, TypeError, AttributeError):
                    pass

    lines = [f"\n### {session_type.title()} session ({now})\n"]

    lines.append("#### Conversation")
    for ex in exchanges[:10]:
        user_text = ex["user"]
        user_short = user_text[:300] + "..." if len(user_text) > 300 else user_text
        lines.append(f"- **User:** {user_short}")
        if ex.get("reply"):
            reply_short = ex["reply"][:400] + "..." if len(ex["reply"]) > 400 else ex["reply"]
            lines.append(f"  **Agent:** {reply_short}")
    lines.append("")

    all_pages = sorted(set(pages_updated + pages_created))
    if all_pages or pages_read:
        lines.append("#### Pages Touched")
        for p in all_pages:
            lines.append(f"- {p} (modified)")
        for p in pages_read[:10]:
            if p not in all_pages:
                lines.append(f"- {p} (read)")
        lines.append("")

    if tools_used:
        lines.append(f"#### Tools Used")
        lines.append(f"{', '.join(tools_used)}")
        lines.append("")

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


def load_last_digest_date(vault: Vault) -> str | None:
    """Read the last-digest-date marker."""
    path = vault.meta_dir / "last-digest-date"
    if path.is_file():
        return path.read_text(encoding="utf-8").strip() or None
    return None


def save_last_digest_date(vault: Vault) -> None:
    """Write today's date as the last-digest-date marker."""
    path = vault.meta_dir / "last-digest-date"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(datetime.now().strftime("%Y-%m-%d"), encoding="utf-8")


def build_digest_prompt(vault: Vault) -> str:
    """Build the digest prompt with since-hint and transcript-hint.

    Used by both ``cmd_digest`` (CLI) and ``_run_cron`` (Gateway) so the
    agent receives identical instructions regardless of entry point.
    """
    transcript_dir = vault.meta_dir / "transcripts"
    transcript_hint = ""
    if transcript_dir.is_dir():
        transcripts = sorted(transcript_dir.glob("*.json"))
        if transcripts:
            recent = [t.name for t in transcripts[-5:]]
            transcript_hint = (
                "\n\nRecent conversation transcripts are available at "
                f".meta/transcripts/. Recent files: {', '.join(recent)}. "
                "Use read_page('.meta/transcripts/filename') to access "
                "the full conversation when a journal entry mentions "
                "something worth deeper review."
            )

    since = load_last_digest_date(vault)
    since_hint = ""
    if since:
        since_hint = f" Only review journals after {since}."

    return (
        "Review the recent journal entries in wiki/journals/. Look for:\n"
        "1. Insights, conclusions, or decisions worth promoting to a wiki page\n"
        "2. Topics mentioned repeatedly that deserve their own page\n"
        "3. Connections between conversations that aren't yet linked\n\n"
        "Workflow:\n"
        "1. Use list_pages('wiki/journals') to find recent journals.\n"
        "2. Use read_page() to read journal content.\n"
        "3. Use search() to check if a topic already has a wiki page.\n"
        "4. Use write_page() to create new wiki pages for key findings.\n"
        "5. Use add_related_link() to connect related pages."
        + since_hint + transcript_hint
    )
