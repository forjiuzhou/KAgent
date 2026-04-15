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
from noteweaver.constants import GATEWAY_SAVE_INTERVAL, GATEWAY_CRON_POLL_SECONDS
from noteweaver.plan import PlanStatus
from noteweaver.session import (
    make_agent,
    finalize_session,
    build_digest_prompt,
    load_last_digest_date,
    save_last_digest_date,
)
from noteweaver.job import (
    get_next_ready_job,
    get_running_job,
    update_contract_status,
    build_worker_prompt,
    parse_progress,
    extract_audit_criteria,
    check_audit_criteria,
    detect_stall,
)

log = logging.getLogger(__name__)


class Gateway:
    """Manages IM adapters and routes messages to the Agent."""

    def __init__(self, vault_path: Path) -> None:
        self.vault, self.agent, _cfg = make_agent(vault_path)
        self.adapters: list[BaseAdapter] = []
        self._lock = asyncio.Lock()
        self._message_count = 0
        self._SAVE_INTERVAL = GATEWAY_SAVE_INTERVAL
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

    def _classify_plan_response(self, user_text: str) -> str:
        """Use the LLM to classify whether the user approves or rejects a plan.

        Returns "approve", "reject", or "unclear".
        """
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a tiny intent classifier. The user was shown a "
                    "proposed change plan and asked whether to execute it. "
                    "Based on their reply, respond with EXACTLY one word:\n"
                    "  approve  — if the user agrees/confirms\n"
                    "  reject   — if the user declines/refuses\n"
                    "  unclear  — if you genuinely cannot tell\n"
                    "No other output."
                ),
            },
            {"role": "user", "content": user_text},
        ]
        try:
            raw = self.agent.provider.simple_completion(self.agent.model, messages)
            if raw:
                word = raw.strip().lower().split()[0]
                if word in ("approve", "reject", "unclear"):
                    return word
        except Exception as e:
            log.warning("Plan intent classification failed, treating as reject: %s", e)
        return "reject"

    async def _handle_message(self, msg: IncomingMessage) -> str:
        """Route an incoming message to the Agent and return the reply.

        All messages go through ``agent.chat()``.  The LLM knows about
        skills via the system prompt and triggers them with markers like
        ``<<skill:import_sources>>``.  The chat loop detects and executes
        these automatically — no slash commands needed.

        The ``_pending_plan_id`` path only activates when an *organize*
        plan is generated at session boundaries (e.g. cron digest calling
        ``generate_organize_plan``).
        """
        self._active_chat_ids.add(msg.chat_id)
        async with self._lock:
            if self._pending_plan_id:
                intent = self._classify_plan_response(msg.text)
                if intent == "approve":
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
                else:
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
                    "要执行吗？回复确认或拒绝即可。"
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

    def _run_job_iteration(self, job: dict) -> dict:
        """Execute one iteration of a job. Returns result dict.

        This is the harness that:
        1. Runs audit_vault() for backpressure
        2. Gets git diff from last iteration
        3. Reads contract + progress
        4. Assembles prompt and spawns fresh worker agent
        5. Worker does its work via chat()
        6. Git commit
        7. Runs audit again and checks hard criteria
        8. Determines if job is complete/failed/stalled
        """
        from noteweaver.agent import KnowledgeAgent
        from noteweaver.vault.audit import audit_vault
        from noteweaver.tools.handlers_read import handle_audit_vault

        job_dir = job["dir"]
        job_id = job["id"]
        iteration = job["iteration_count"] + 1
        max_iters = job["max_iterations"]

        log.info("Job [%s] starting iteration %d/%d", job_id, iteration, max_iters)

        update_contract_status(job_dir, "running")

        pre_audit = audit_vault(self.vault)
        audit_text = handle_audit_vault(self.vault)

        diff_files: list[str] = []
        try:
            if hasattr(self.vault, '_repo') and self.vault._repo is not None:
                diff_output = self.vault._repo.git.diff("--name-only", "HEAD~1")
                diff_files = [f for f in diff_output.split("\n") if f.strip()]
        except Exception:
            pass

        contract_content = job["contract_content"]
        progress_content = job["progress_content"]

        prompt = build_worker_prompt(
            contract_content=contract_content,
            progress_content=progress_content,
            diff_files=diff_files,
            audit_summary=audit_text,
            iteration=iteration,
        )
        prompt = prompt.replace("{job_id}", job_id)

        worker = KnowledgeAgent(
            vault=self.vault,
            model=self.agent.model,
            provider=self.agent.provider,
        )
        worker.messages = [
            {"role": "system", "content": worker._build_job_system_prompt()}
        ]
        worker.set_attended(True)

        tool_count = 0
        reply_parts: list[str] = []
        try:
            for chunk in worker.chat(prompt):
                if chunk.startswith("  ↳ "):
                    tool_count += 1
                elif not chunk.startswith("  📋 "):
                    reply_parts.append(chunk)
        except Exception as e:
            log.error("Job [%s] worker error in iteration %d: %s", job_id, iteration, e)
            return {
                "iteration": iteration,
                "success": False,
                "error": str(e),
                "completed": False,
                "failed": False,
            }

        log.info("Job [%s] iteration %d: %d tool calls", job_id, iteration, tool_count)

        post_audit = audit_vault(self.vault)

        progress_path = job_dir / "progress.md"
        if progress_path.is_file():
            progress_content = progress_path.read_text(encoding="utf-8")
        progress_data = parse_progress(progress_content)
        declares_complete = progress_data["declares_complete"]

        audit_criteria = extract_audit_criteria(job["criteria"])
        criteria_results = check_audit_criteria(audit_criteria, post_audit)
        all_audit_pass = all(cr["passed"] for cr in criteria_results) if criteria_results else True

        completed = declares_complete and all_audit_pass
        failed = iteration >= max_iters and not completed

        if completed:
            update_contract_status(job_dir, "completed")
            log.info("Job [%s] COMPLETED at iteration %d", job_id, iteration)
        elif failed:
            update_contract_status(job_dir, "failed")
            log.info("Job [%s] FAILED at max iterations %d", job_id, iteration)
        else:
            pass

        return {
            "iteration": iteration,
            "success": True,
            "tool_count": tool_count,
            "completed": completed,
            "failed": failed,
            "declares_complete": declares_complete,
            "all_audit_pass": all_audit_pass,
            "criteria_results": criteria_results,
            "reply": "\n".join(reply_parts)[:500],
        }

    async def _run_cron(self) -> None:
        """Background cron: periodic digest, lint, job loop, and notification.

        Digest and lint run on their own intervals.  Digest results are
        queued as pending notifications.  Notifications are delivered at
        a configurable hour (NW_NOTIFY_HOUR, default 9) so users aren't
        disturbed at night.

        Job loop runs on every cron poll when the main agent is idle.
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
        recent_job_diffs: dict[str, list[list[str]]] = {}

        while True:
            await asyncio.sleep(GATEWAY_CRON_POLL_SECONDS)
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
                                for chunk in self.agent.run_skill("organize_wiki"):
                                    pass  # consume generator; cron has no UI
                            except Exception as e:
                                log.error("Cron organize_wiki failed: %s", e)
                            finally:
                                self.agent.set_attended(True)
                except Exception as e:
                    log.error("Cron audit failed: %s", e)
                last_lint = now

            # --- Job loop (only when main agent is idle) ---
            if not self._lock.locked():
                async with self._lock:
                    try:
                        job = get_running_job(self.vault) or get_next_ready_job(self.vault)
                        if job:
                            job_id = job["id"]
                            result = self._run_job_iteration(job)

                            diff_files: list[str] = []
                            try:
                                if hasattr(self.vault, '_repo') and self.vault._repo is not None:
                                    diff_output = self.vault._repo.git.diff(
                                        "--name-only", "HEAD~1",
                                    )
                                    diff_files = [
                                        f for f in diff_output.split("\n") if f.strip()
                                    ]
                            except Exception:
                                pass
                            recent_job_diffs.setdefault(job_id, []).insert(0, diff_files)
                            recent_job_diffs[job_id] = recent_job_diffs[job_id][:5]

                            if result.get("completed"):
                                self._pending_notifications.append(
                                    f"✅ Job [{job_id}] completed at iteration "
                                    f"{result['iteration']}."
                                )
                            elif result.get("failed"):
                                self._pending_notifications.append(
                                    f"❌ Job [{job_id}] reached max iterations "
                                    f"({result['iteration']}). Review progress."
                                )
                            elif detect_stall(job, recent_job_diffs.get(job_id, [])):
                                self._pending_notifications.append(
                                    f"⚠️ Job [{job_id}] appears stalled "
                                    f"(no changes for last iterations). "
                                    "Consider reviewing the contract."
                                )
                    except Exception as e:
                        log.error("Job loop failed: %s", e)

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
