from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import messages
from bot.cache import AdminCache
from bot.config import Settings
from bot.db import Database

logger = logging.getLogger(__name__)


def settings_of(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def database_of(context: ContextTypes.DEFAULT_TYPE) -> Database:
    return context.application.bot_data["db"]


def admin_cache_of(context: ContextTypes.DEFAULT_TYPE) -> AdminCache:
    return context.application.bot_data["admin_cache"]


def is_group(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP))


async def require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return ``True`` when the sender is a group admin, else reply and return ``False``."""
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message
    if chat is None or user is None:
        return False
    if not is_group(update):
        if message:
            await message.reply_text(messages.GROUP_ONLY)
        return False
    if await admin_cache_of(context).is_admin(context.bot, chat.id, user.id):
        return True
    if message:
        await message.reply_text(messages.ADMIN_ONLY)
    return False


async def audit(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    settings = settings_of(context)
    if not settings.audit_chat_id:
        return
    try:
        await context.bot.send_message(settings.audit_chat_id, f"🛡️ {text}")
    except TelegramError as exc:
        logger.warning("Could not send audit message: %s", exc)
