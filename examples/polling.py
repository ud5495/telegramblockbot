"""Minimal polling example.

The bot asks Telegram for updates continuously. No public URL, TLS or domain is
required. This is the simplest way to run a bot.

Run from the repository root:

    export BOT_TOKEN="123456:your-token"
    python examples/polling.py

This is a simplified demo: it deletes link messages from *everyone*, including
admins. The full bot in ``main.py`` has admin checks, whitelists, warnings,
captcha and per-chat settings.
"""

from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

LINK_MARKERS = ("http://", "https://", "www.", "t.me/", "telegram.me/")


async def remove_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    text = (message.text or message.caption or "").lower()
    if any(marker in text for marker in LINK_MARKERS):
        try:
            await message.delete()
        except Exception:  # noqa: BLE001, S110 - demo: ignore API errors
            pass


def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("Set BOT_TOKEN, e.g. export BOT_TOKEN='123456:abc'")

    logging.basicConfig(level=logging.INFO)
    application = Application.builder().token(token).build()
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, remove_links
        )
    )

    # Long-polling: no public URL needed.
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
