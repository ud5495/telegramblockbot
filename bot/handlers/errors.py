from __future__ import annotations

import logging

from telegram.error import TelegramError
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling update", exc_info=context.error)

    settings = context.application.bot_data.get("settings")
    if not settings or not settings.audit_chat_id:
        return

    error = context.error
    text = f"⚠️ Error: {type(error).__name__}: {error}"
    try:
        await context.bot.send_message(settings.audit_chat_id, text[:4000])
    except TelegramError as exc:
        logger.warning("Could not report error to audit channel: %s", exc)
