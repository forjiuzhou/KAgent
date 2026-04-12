"""Gateway — long-running process connecting IM platforms to the Agent.

Manages platform adapters and routes messages to the KnowledgeAgent.
All adapters share the same vault and agent instance.

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
from noteweaver.agent import KnowledgeAgent
from noteweaver.config import Config
from noteweaver.plan import PlanStatus
from noteweaver.vault import Vault

log = logging.getLogger(__name__)


class Gateway:
    """Manages IM adapters and routes messages to the Agent."""

    def __init__(self, vault_path: Path) -> None:
        self.vault = Vault(vault_path)
        if not self.vault.exists():
            raise RuntimeError(f"No vault at {vault_path}. Run `nw init` first.")

        cfg = Config.load(vault_path)
        if not cfg.api_key:
            raise RuntimeError("No LLM API key configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY.")

        from noteweaver.agent import create_provider
        provider = create_provider(
            cfg.provider, api_key=cfg.api_key, base_url=cfg.base_url
        )

        self.agent = KnowledgeAgent(
            vault=self.vault,
            model=cfg.model,
            provider=provider,
        )
        self.adapters: list[BaseAdapter] = []
        self._lock = asyncio.Lock()
        self._message_count = 0
        self._SAVE_INTERVAL = 10  # save transcript every N messages
        self._active_chat_ids: set[str] = set()
        self._pending_notifications: list[str] = []
        self._notify_hour = int(os.environ.get("NW_NOTIFY_HOUR", "9"))
        self._pending_plan_id: str | None = None

    def _load_last_digest_date(self) -> str | None:
        path = self.vault.meta_dir / "last-digest-date"
        if path.is_file():
            return path.read_text(encoding="utf-8").strip() or None
        return None

    def _save_last_digest_date(self) -> None:
        from datetime import datetime
        path = self.vault.meta_dir / "last-digest-date"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(datetime.now().strftime("%Y-%m-%d"), encoding="utf-8")

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

    async def _handle_message(self, msg: IncomingMessage) -> str:
        """Route an incoming message to the Agent and return the reply.

        V2: the agent writes directly during chat() — no plans are created
        from normal conversation.  The ``_pending_plan_id`` path only
        activates when an *organize* plan is generated at session boundaries
        (e.g. cron digest calling ``generate_organize_plan``).
        """
        self._active_chat_ids.add(msg.chat_id)
        async with self._lock:
            if self._pending_plan_id and msg.text.strip().lower() in self._APPROVE_KEYWORDS:
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

            reply_parts = []
            tool_parts = []
            try:
                for chunk in self.agent.chat(msg.text):
                    if chunk.startswith("  📋 ") or chunk.startswith("  ↳ "):
                        tool_parts.append(chunk.strip())
                    else:
                        reply_parts.append(chunk)
            except Exception as e:
                log.error("Agent error: %s", e)
                return f"Error: {e}"

            # Handle organize plans (the only source of pending plans in V2).
            # Normal chat() writes directly and never creates plans.
            # Plans only appear here from generate_organize_plan() / cron.
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

        last_digest = 0.0
        last_lint = 0.0
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
                        self.agent.set_attended(False)
                        since = self._load_last_digest_date()
                        since_hint = f" Only review journals after {since}." if since else ""
                        for chunk in self.agent.chat(
                            "Review recent journal entries and promote any insights "
                            "worth capturing as wiki pages. Write findings as "
                            "'#### Promotion Candidates' sections in today's "
                            "journal — the user will review them interactively."
                            + since_hint
                        ):
                            pass
                        self._save_last_digest_date()
                    except Exception as e:
                        log.error("Cron digest failed: %s", e)
                    finally:
                        self.agent.set_attended(True)
                last_digest = now

            # --- Audit (pure code, no LLM, no lock needed) ---
            if now - last_lint >= lint_interval:
                log.info("Cron: running vault audit...")
                try:
                    report = self.vault.audit_vault()
                    self.vault.save_audit_report(report)
                    if any(report.get(k) for k in report if k != "summary"):
                        self._pending_notifications.append(
                            f"🔍 *Vault Audit*\n\n{report['summary']}"
                        )
                    log.info("Audit result: %s", report.get("summary", ""))
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
                self.agent.save_transcript()
                self.agent.save_session_memory()
                log.info("Transcript and session memory saved.")
            except Exception as e:
                log.warning("Failed to save on shutdown: %s", e)
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
