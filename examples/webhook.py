"""Minimal webhook example.

Telegram pushes updates to an HTTPS endpoint instead of the bot polling. You
need a public host with a valid TLS certificate and a domain. Telegram only
delivers webhooks on ports 443, 80, 88 or 8443.

Two common setups:

1. Reverse proxy (recommended): Caddy/nginx terminates TLS on 443 and forwards
   to this process on 127.0.0.1:8443. The bot then needs no certificate.
2. The bot terminates TLS itself: set WEBHOOK_CERT and WEBHOOK_KEY and listen
   on 0.0.0.0:8443.

Run from the repository root:

    export BOT_TOKEN="123456:your-token"
    export WEBHOOK_URL="https://bot.example.com/telegram"
    export WEBHOOK_SECRET="a-long-random-string"
    export WEBHOOK_PORT=8443
    export WEBHOOK_PATH=/telegram
    python examples/webhook.py
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
    webhook_url = os.environ.get("WEBHOOK_URL")
    if not token:
        raise SystemExit("Set BOT_TOKEN")
    if not webhook_url or not webhook_url.startswith("https://"):
        raise SystemExit("Set WEBHOOK_URL to a public https:// URL")

    listen = os.environ.get("WEBHOOK_LISTEN", "0.0.0.0")
    port = int(os.environ.get("WEBHOOK_PORT", "8443"))
    url_path = os.environ.get("WEBHOOK_PATH", "/telegram").lstrip("/")
    secret_token = os.environ.get("WEBHOOK_SECRET") or None
    cert = os.environ.get("WEBHOOK_CERT") or None
    key = os.environ.get("WEBHOOK_KEY") or None

    logging.basicConfig(level=logging.INFO)
    application = Application.builder().token(token).build()
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, remove_links
        )
    )

    application.run_webhook(
        listen=listen,
        port=port,
        url_path=url_path,
        webhook_url=webhook_url,
        secret_token=secret_token,
        cert=cert,
        key=key,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
