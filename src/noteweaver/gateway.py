"""Gateway — long-running process connecting IM platforms to the Agent.

Manages platform adapters and routes messages to the KnowledgeAgent.
All adapters share the same vault and agent instance.

Uses ``noteweaver.session`` for agent construction, session finalization,
digest prompts, and digest-date tracking — shared with ``cli.py``.

Usage:
    nw gateway              # start gateway (reads config from env vars)
    NW_TELEGRAM_TOKEN=...   # enable Telegram
    NW_FEISHU_APP_ID=...    # enable Feishu (future)
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from noteweaver.adapters.base import BaseAdapter, IncomingMessage, OutgoingMessage
from noteweaver.plan import PlanStatus
from noteweaver.session import (
    make_agent,
    finalize_session,
    build_digest_prompt,
    load_last_digest_date,
    save_last_digest_date,
)

log = logging.getLogger(__name__)


class Gateway:
    """Manages IM adapters and routes messages to the Agent."""

    def __init__(self, vault_path: Path) -> None:
        self.vault, self.agent, _cfg = make_agent(vault_path)
        self.adapters: list[BaseAdapter] = []
        self._lock = asyncio.Lock()
        self._message_count = 0
        self._SAVE_INTERVAL = 10
        self._active_chat_ids: set[str] = set()
        self._pending_notifications: list[str] = []
        self._notify_hour = int(os.environ.get("NW_NOTIFY_HOUR", "9"))
        self._pending_plan_id: str | None = None
        self._exchanges: list[dict] = []

    def _setup_adapters(self) -> None:
        """Detect which platforms are configured and create adapters."""
        telegram_token = os.environ.get("NW_TELEGRAM_TOKEN")
        telegram_users = os.environ.get("NW_TELEGRAM_ALLOWED_USERS", "")

        if telegram_token:
            from noteweaver.adapters.telegram_adapter import TelegramAdapter
            allowed = set(telegram_users.split(",")) if telegram_users else None
            adapter = TelegramAdapter(
                token=telegram_token,
                on_message=self._handle_message,
                allowed_users=allowed,
            )
            self.adapters.append(adapter)
            log.info("Telegram adapter configured")

        if not self.adapters:
            raise RuntimeError(
                "No IM adapters configured. Set NW_TELEGRAM_TOKEN to enable Telegram."
            )

    _APPROVE_KEYWORDS = frozenset({
        "好", "好的", "可以", "执行", "yes", "y", "ok", "确认",
    })

    _SKILL_COMMANDS: dict[str, tuple[str, dict]] = {
        "/import-sources": ("import_sources", {}),
        "/import_sources": ("import_sources", {}),
        "/organize":       ("organize_wiki", {}),
        "/organize-wiki":  ("organize_wiki", {}),
        "/organize_wiki":  ("organize_wiki", {}),
    }

    def _run_skill_sync(self, skill_name: str, **kwargs) -> str:
        """Run a skill synchronously and return a formatted reply string."""
        exchange: dict = {"user": f"/{skill_name}", "tools": [], "reply": ""}
        reply_parts: list[str] = []
        tool_parts: list[str] = []

        try:
            for chunk in self.agent.run_skill(skill_name, **kwargs):
                if chunk.startswith("  📋 ") or chunk.startswith("  ↳ "):
                    tool_parts.append(chunk.strip())
                    exchange["tools"].append(chunk.strip())
                elif chunk.startswith(f"[{skill_name}]"):
                    reply_parts.append(chunk)
                else:
                    reply_parts.append(chunk)
                    exchange["reply"] = chunk
        except Exception as e:
            log.error("Skill %s failed: %s", skill_name, e)
            exchange["reply"] = f"(error: {e})"
            return f"Skill {skill_name} failed: {e}"
        finally:
            try:
                self.agent.save_trace()
            except Exception as e:
                log.warning("Failed to save trace: %s", e)

        self._exchanges.append(exchange)

        reply = "\n\n".join(reply_parts) if reply_parts else f"Skill {skill_name} completed."
        if tool_parts:
            tools_summary = "\n".join(f"• {t}" for t in tool_parts[:10])
            reply = f"_{tools_summary}_\n\n{reply}"
        return reply

    async def _handle_message(self, msg: IncomingMessage) -> str:
        """Route an incoming message to the Agent and return the reply.

        Supports slash commands for skills (e.g. ``/import-sources``,
        ``/organize``) and plan approval keywords.

        Normal messages go through ``agent.chat()`` — the agent writes
        directly during chat, no plans are created.  The ``_pending_plan_id``
        path only activates when an *organize* plan is generated at session
        boundaries (e.g. cron digest calling ``generate_organize_plan``).
        """
        self._active_chat_ids.add(msg.chat_id)
        async with self._lock:
            text_stripped = msg.text.strip()
            text_lower = text_stripped.lower()

            # --- Skill slash commands ---
            cmd_word = text_lower.split()[0] if text_lower else ""
            if cmd_word in self._SKILL_COMMANDS:
                skill_name, skill_kwargs = self._SKILL_COMMANDS[cmd_word]
                parts = text_stripped.split(maxsplit=1)
                if len(parts) > 1 and skill_name == "import_sources":
                    skill_kwargs = {**skill_kwargs, "source_dir": parts[1]}
                log.info("Skill command: %s (skill=%s)", cmd_word, skill_name)
                return self._run_skill_sync(skill_name, **skill_kwargs)

            if self._pending_plan_id and text_lower in self._APPROVE_KEYWORDS:
                try:
                    plan = self.agent.plan_store.load(self._pending_plan_id)
                    if plan and plan.status == PlanStatus.PENDING:
                        self.agent.plan_store.update_status(
                            plan.id, PlanStatus.APPROVED,
                        )
                        result = self.agent.execute_plan(plan.id)
                        self._pending_plan_id = None
                        return f"✅ {result}"
                    self._pending_plan_id = None
                    return "该提案已不存在或已处理。"
                except Exception as e:
                    self._pending_plan_id = None
                    log.error("Plan execution failed: %s", e)
                    return f"执行失败: {e}"

            if self._pending_plan_id:
                plan = self.agent.plan_store.load(self._pending_plan_id)
                if plan and plan.status == PlanStatus.PENDING:
                    self.agent.plan_store.update_status(
                        plan.id, PlanStatus.REJECTED,
                    )
                self._pending_plan_id = None

            exchange: dict = {"user": msg.text, "tools": [], "reply": ""}
            reply_parts = []
            tool_parts = []
            try:
                for chunk in self.agent.chat(msg.text):
                    if chunk.startswith("  📋 ") or chunk.startswith("  ↳ "):
                        tool_parts.append(chunk.strip())
                        exchange["tools"].append(chunk.strip())
                    else:
                        reply_parts.append(chunk)
                        exchange["reply"] = chunk
            except Exception as e:
                log.error("Agent error: %s", e)
                exchange["reply"] = f"(error: {e})"
                return f"Error: {e}"
            finally:
                try:
                    self.agent.save_trace()
                except Exception as e:
                    log.warning("Failed to save trace: %s", e)
            self._exchanges.append(exchange)

            pending_plans = self.agent.plan_store.list_pending()
            for plan in pending_plans:
                self._pending_plan_id = plan.id
                summary = self.agent.format_plan(plan)
                reply_parts.append(
                    f"\n\n---\n\n📋 *整理提案* [{plan.id}]\n\n{summary}\n\n"
                    "回复「好的」执行，或发送其他消息跳过。"
                )

            self._message_count += 1
            if self._message_count % self._SAVE_INTERVAL == 0:
                try:
                    self.agent.save_transcript()
                    self.agent.save_session_memory()
                except Exception as e:
                    log.warning("Failed to save transcript/memory: %s", e)

            reply = "\n\n".join(reply_parts) if reply_parts else "(no response)"

            if tool_parts:
                tools_summary = "\n".join(f"• {t}" for t in tool_parts[:5])
                reply = f"_{tools_summary}_\n\n{reply}"

            return reply

    async def _notify_users(self, text: str) -> None:
        """Push a notification to all known chat IDs via all adapters."""
        if not self._active_chat_ids:
            log.info("No active chat IDs to notify.")
            return
        for adapter in self.adapters:
            for chat_id in self._active_chat_ids:
                try:
                    await adapter.send(OutgoingMessage(chat_id=chat_id, text=text))
                except Exception as e:
                    log.warning("Failed to notify %s: %s", chat_id, e)

    async def _run_cron(self) -> None:
        """Background cron: periodic digest, lint, and notification.

        Digest and lint run on their own intervals.  Digest results are
        queued as pending notifications.  Notifications are delivered at
        a configurable hour (NW_NOTIFY_HOUR, default 9) so users aren't
        disturbed at night.
        """
        from datetime import datetime

        digest_interval = int(os.environ.get("NW_DIGEST_INTERVAL_HOURS", "6")) * 3600
        lint_interval = int(os.environ.get("NW_LINT_INTERVAL_HOURS", "24")) * 3600

        log.info("Cron enabled: digest every %dh, lint every %dh, notify at %02d:00",
                 digest_interval // 3600, lint_interval // 3600, self._notify_hour)

        import time as _time
        last_digest = _time.time()
        last_lint = _time.time()
        last_notify_date = ""

        while True:
            await asyncio.sleep(300)  # check every 5 minutes
            import time
            now = time.time()

            # --- Digest ---
            if now - last_digest >= digest_interval:
                log.info("Cron: running digest...")
                async with self._lock:
                    try:
                        # Flush journal and transcript BEFORE digest so the
                        # agent can actually read the user's conversation
                        # history via wiki/journals/ and .meta/transcripts/.
                        if self._exchanges:
                            try:
                                finalize_session(
                                    self.vault, self.agent,
                                    self._exchanges, "chat",
                                    run_organize=False,
                                )
                                self._exchanges = []
                            except Exception as e:
                                log.warning(
                                    "Pre-digest session flush failed: %s", e,
                                )

                        self.agent.set_attended(False)
                        prompt = build_digest_prompt(self.vault, attended=False)
                        exchange: dict = {"user": "digest", "tools": [], "reply": ""}
                        for chunk in self.agent.chat(prompt):
                            if chunk.startswith("  📋 ") or chunk.startswith("  ↳ "):
                                exchange["tools"].append(chunk.strip())
                            else:
                                exchange["reply"] = chunk
                        save_last_digest_date(self.vault)
                        finalize_session(
                            self.vault, self.agent, [exchange], "digest",
                            run_organize=False,
                        )
                    except Exception as e:
                        log.error("Cron digest failed: %s", e)
                    finally:
                        self.agent.set_attended(True)
                last_digest = now

            # --- Audit + organize_wiki skill ---
            if now - last_lint >= lint_interval:
                log.info("Cron: running vault audit...")
                try:
                    report = self.vault.audit_vault()
                    self.vault.save_audit_report(report)
                    has_issues = any(report.get(k) for k in report if k != "summary")
                    if has_issues:
                        self._pending_notifications.append(
                            f"🔍 *Vault Audit*\n\n{report['summary']}"
                        )
                    log.info("Audit result: %s", report.get("summary", ""))

                    if has_issues:
                        log.info("Cron: running organize_wiki skill...")
                        async with self._lock:
                            try:
                                self.agent.set_attended(False)
                                result = self._run_skill_sync("organize_wiki")
                                log.info("organize_wiki result: %s", result[:200])
                            except Exception as e:
                                log.error("Cron organize_wiki failed: %s", e)
                            finally:
                                self.agent.set_attended(True)
                except Exception as e:
                    log.error("Cron audit failed: %s", e)
                last_lint = now

            # --- Notification delivery at configured hour ---
            current_hour = datetime.now().hour
            today = datetime.now().strftime("%Y-%m-%d")
            if (self._pending_notifications
                    and current_hour >= self._notify_hour
                    and last_notify_date != today):
                combined = "\n\n---\n\n".join(self._pending_notifications)
                log.info("Delivering %d pending notification(s)...",
                         len(self._pending_notifications))
                await self._notify_users(combined)
                self._pending_notifications.clear()
                last_notify_date = today

    async def run(self) -> None:
        """Start all adapters and background cron, run until interrupted."""
        self._setup_adapters()

        log.info("Starting %d adapter(s)...", len(self.adapters))
        for adapter in self.adapters:
            await adapter.start()

        cron_task = asyncio.create_task(self._run_cron())

        log.info("Gateway running. Press Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            cron_task.cancel()
            log.info("Shutting down...")
            try:
                finalize_session(
                    self.vault, self.agent, self._exchanges, "chat",
                    run_organize=False,
                )
                log.info("Session finalized (transcript, trace, memory, journal).")
            except Exception as e:
                log.warning("Failed to finalize session on shutdown: %s", e)
            for adapter in self.adapters:
                await adapter.stop()
            log.info("Gateway stopped.")


def run_gateway(vault_path: Path) -> None:
    """Entry point for `nw gateway` command."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    gw = Gateway(vault_path)
    asyncio.run(gw.run())
