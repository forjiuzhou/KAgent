"""Telegram Bot adapter.

A thin translation layer: receives Telegram messages, converts to
IncomingMessage, dispatches to the agent handler, sends back the reply.
No business logic here.

Setup: create a bot via @BotFather on Telegram, get the token.
Set environment variable: NW_TELEGRAM_TOKEN=your-bot-token
"""

from __future__ import annotations

import logging
from typing import Callable, Awaitable

from noteweaver.adapters.base import BaseAdapter, IncomingMessage, OutgoingMessage

log = logging.getLogger(__name__)


class TelegramAdapter(BaseAdapter):
    """Telegram Bot using python-telegram-bot (async)."""

    def __init__(
        self,
        token: str,
        on_message: Callable[[IncomingMessage], Awaitable[str]],
        allowed_users: set[str] | None = None,
    ) -> None:
        self._token = token
        self._on_message = on_message
        self._allowed_users = allowed_users
        self._app = None

    async def start(self) -> None:
        from telegram import Update
        from telegram.ext import (
            ApplicationBuilder,
            MessageHandler,
            CommandHandler,
            filters,
        )

        self._app = ApplicationBuilder().token(self._token).build()

        async def handle_start(update: Update, context) -> None:
            await update.message.reply_text(
                "NoteWeaver connected. Send me anything to capture it in your knowledge base."
            )

        async def handle_message(update: Update, context) -> None:
            if not update.message or not update.message.text:
                return

            user = update.effective_user
            user_id = str(user.id) if user else "unknown"
            user_name = user.full_name if user else "unknown"

            if self._allowed_users and user_id not in self._allowed_users:
                await update.message.reply_text("Unauthorized. Contact the vault owner.")
                return

            msg = IncomingMessage(
                platform="telegram",
                user_id=user_id,
                user_name=user_name,
                chat_id=str(update.effective_chat.id),
                text=update.message.text,
            )

            log.info("Telegram [%s] %s: %s", msg.chat_id, msg.user_name, msg.text[:80])

            try:
                reply = await self._on_message(msg)
                # Plain text only: LLM output often contains _, *, ` etc. Telegram Markdown
                # would treat them as formatting and fail with "Can't parse entities".
                for chunk in _split_message(reply, 4000):
                    await update.message.reply_text(chunk)
            except Exception as e:
                log.error("Error handling message: %s", e)
                await update.message.reply_text(f"Error: {e}")

        self._app.add_handler(CommandHandler("start", handle_start))
        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

        log.info("Telegram adapter starting (polling)...")
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()

    async def send(self, message: OutgoingMessage) -> None:
        if self._app:
            for chunk in _split_message(message.text, 4000):
                await self._app.bot.send_message(chat_id=message.chat_id, text=chunk)


def _split_message(text: str, max_len: int) -> list[str]:
    """Split long text into chunks that fit Telegram's message limit."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            chunks.append(text[:max_len])
            text = text[max_len:]
        else:
            # Include the newline so we do not drop it (old code used lstrip and lost \n).
            chunks.append(text[: split_at + 1])
            text = text[split_at + 1 :]
    return chunks
