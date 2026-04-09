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

from noteweaver.adapters.base import BaseAdapter, IncomingMessage
from noteweaver.agent import KnowledgeAgent
from noteweaver.config import Config
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

    async def _handle_message(self, msg: IncomingMessage) -> str:
        """Route an incoming message to the Agent and return the reply.

        Uses a lock to prevent concurrent agent operations on the same vault.
        """
        async with self._lock:
            reply_parts = []
            tool_parts = []
            try:
                for chunk in self.agent.chat(msg.text):
                    if chunk.startswith("  ↳ "):
                        tool_parts.append(chunk.strip())
                    else:
                        reply_parts.append(chunk)
            except Exception as e:
                log.error("Agent error: %s", e)
                return f"Error: {e}"

            reply = "\n\n".join(reply_parts) if reply_parts else "(no response)"

            if tool_parts:
                tools_summary = "\n".join(f"• {t}" for t in tool_parts[:5])
                reply = f"_{tools_summary}_\n\n{reply}"

            return reply

    async def run(self) -> None:
        """Start all adapters and run until interrupted."""
        self._setup_adapters()

        log.info("Starting %d adapter(s)...", len(self.adapters))
        for adapter in self.adapters:
            await adapter.start()

        log.info("Gateway running. Press Ctrl+C to stop.")
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            log.info("Shutting down...")
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
