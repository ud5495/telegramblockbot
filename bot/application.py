from __future__ import annotations

import logging

from telegram import BotCommand, Update
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from bot.cache import AdminCache
from bot.config import Settings
from bot.db import Database
from bot.handlers import commands, members
from bot.handlers import messages as message_handlers
from bot.handlers.errors import error_handler
from bot.moderation import SpamTracker

logger = logging.getLogger(__name__)

NEW_MESSAGE_FILTER = (
    filters.UpdateType.MESSAGE & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND
)
EDITED_MESSAGE_FILTER = filters.UpdateType.EDITED_MESSAGE & (
    filters.TEXT | filters.CAPTION
)

BOT_COMMANDS = [
    BotCommand("start", "Show help"),
    BotCommand("help", "Show help"),
    BotCommand("settings", "Show or change settings"),
    BotCommand("toggle", "Enable or disable moderation"),
    BotCommand("whitelist", "Manage whitelisted domains/users"),
    BotCommand("stats", "Show moderation stats"),
    BotCommand("rules", "Show chat rules"),
    BotCommand("health", "Health check"),
]


def build_application(settings: Settings) -> Application:
    db = Database(settings.db_path)
    admin_cache = AdminCache(settings.admin_cache_ttl)
    spam_tracker = SpamTracker(
        settings.flood_max_messages,
        settings.flood_window_seconds,
    )

    async def post_init(application: Application) -> None:
        await db.initialize()
        try:
            await application.bot.set_my_commands(BOT_COMMANDS)
        except TelegramError as exc:
            logger.warning("Could not register bot commands: %s", exc)
        if application.job_queue is not None:
            application.job_queue.run_repeating(
                members.captcha_sweep, interval=30, first=30
            )

    async def post_shutdown(application: Application) -> None:
        await db.close()

    application = (
        Application.builder()
        .token(settings.bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.bot_data["settings"] = settings
    application.bot_data["db"] = db
    application.bot_data["admin_cache"] = admin_cache
    application.bot_data["spam_tracker"] = spam_tracker

    _register_handlers(application)
    application.add_error_handler(error_handler)
    return application


def _register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", commands.start))
    application.add_handler(CommandHandler("help", commands.help_command))
    application.add_handler(CommandHandler("rules", commands.rules))
    application.add_handler(CommandHandler("settings", commands.settings_command))
    application.add_handler(CommandHandler("toggle", commands.toggle))
    application.add_handler(CommandHandler("whitelist", commands.whitelist_command))
    application.add_handler(CommandHandler("stats", commands.stats_command))
    application.add_handler(CommandHandler("health", commands.health))

    application.add_handler(
        MessageHandler(NEW_MESSAGE_FILTER, message_handlers.on_message)
    )
    application.add_handler(
        MessageHandler(EDITED_MESSAGE_FILTER, message_handlers.on_edited_message)
    )
    application.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, members.on_new_members)
    )
    application.add_handler(
        ChatMemberHandler(members.on_chat_member_update, ChatMemberHandler.CHAT_MEMBER)
    )
    application.add_handler(
        CallbackQueryHandler(members.on_captcha_callback, pattern=r"^captcha:")
    )
    application.add_handler(
        CallbackQueryHandler(commands.on_settings_callback, pattern=r"^set:")
    )


ALLOWED_UPDATES = [
    Update.MESSAGE,
    Update.EDITED_MESSAGE,
    Update.CHAT_MEMBER,
    Update.CALLBACK_QUERY,
]
