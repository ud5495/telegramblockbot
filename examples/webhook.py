"""Minimal webhook example.

Telegram pushes updates to an HTTPS endpoint instead of the bot polling. You
need a public host with a valid TLS certificate and a domain. Telegram only
delivers webhooks on public ports 443 (the default), 80, 88 or 8443.

Two common setups:

1. Reverse proxy (recommended): Caddy/nginx terminates TLS on 443 and forwards
   to this process on 127.0.0.1:8443. The bot then needs no certificate.
2. The bot terminates TLS itself: set WEBHOOK_CERT and WEBHOOK_KEY and listen
   on 0.0.0.0:8443.

Run from the repository root:

    export BOT_TOKEN="123456:your-token"
    export WEBHOOK_URL="https://bot.example.com/telegram"
    export WEBHOOK_SECRET="replace-with-32-or-more-random-characters"
    export WEBHOOK_PORT=8443
    export WEBHOOK_PATH=/telegram
    python -m examples.webhook
"""

from __future__ import annotations

import logging
import os

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from bot.config import ConfigError, load_settings

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
    env = os.environ.copy()
    env["MODE"] = "webhook"
    try:
        settings = load_settings(env)
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    logging.basicConfig(level=logging.INFO)
    application = Application.builder().token(settings.bot_token).build()
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, remove_links
        )
    )

    application.run_webhook(
        listen=settings.webhook_listen,
        port=settings.webhook_port,
        url_path=settings.webhook_path.lstrip("/"),
        webhook_url=settings.webhook_url,
        secret_token=settings.webhook_secret,
        cert=settings.webhook_cert,
        key=settings.webhook_key,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
