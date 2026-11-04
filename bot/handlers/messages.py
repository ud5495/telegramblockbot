from __future__ import annotations

import logging
import time

from telegram import Update
from telegram.constants import ChatType
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import detectors, messages, moderation
from bot.handlers.common import admin_cache_of, audit, database_of, settings_of

logger = logging.getLogger(__name__)

_AUTO_DELETE_SECONDS = 10


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _process(update, context)


async def on_edited_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _process(update, context)


async def _process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if user.is_bot:
        return
    # Anonymous admins / channel posts have no real sender user we can moderate.
    if getattr(message, "sender_chat", None) is not None:
        return

    settings = settings_of(context)
    db = database_of(context)
    admin_cache = admin_cache_of(context)

    await db.ensure_chat(
        chat.id, chat.title, settings.default_action, settings.captcha_enabled
    )
    chat_settings = await db.get_chat(chat.id)
    if not chat_settings or not chat_settings["enabled"]:
        return

    if await admin_cache.is_admin(context.bot, chat.id, user.id):
        return
    if await _is_whitelisted_user(db, chat.id, user.id, user.username):
        return

    text = message.text or message.caption or ""

    spam = context.application.bot_data["spam_tracker"].check(chat.id, user.id, text)
    if spam is not None:
        deleted = await _delete(message, context)
        if deleted:
            await db.increment_stat(chat.id, "deleted")
        if spam == "flood":
            muted = await _mute(context, db, chat, user.id, settings.mute_seconds)
            deletion_text = "removed message" if deleted else "could not remove message"
            await audit(
                context,
                f"Anti-flood: {deletion_text} from user {user.id} in chat {chat.id} "
                f"(deletion={'success' if deleted else 'failed'}, action=mute, "
                f"enforcement={'success' if muted else 'failed'})",
            )
        else:
            deletion_text = "removed" if deleted else "could not be removed"
            await audit(
                context,
                f"Duplicate message {deletion_text} from user {user.id} "
                f"in chat {chat.id}",
            )
        return

    domains = await db.list_whitelist(chat.id, "domain")
    usernames = await db.list_whitelist(chat.id, "user")
    findings = detectors.detect_message(message, domains, usernames)
    if not findings:
        return

    deleted = await _delete(message, context)
    if deleted:
        await db.increment_stat(chat.id, "deleted")

    count = await db.record_violation(
        chat.id, user.id, settings.violation_decay_seconds
    )
    action = moderation.choose_action(
        chat_settings["action"], count, settings.warn_limit
    )
    reason = ", ".join(finding.as_reason() for finding in findings[:3])

    enforcement_succeeded = deleted
    if action == "warn":
        enforcement_succeeded = await _send_warning(
            context, chat.id, user, count, message_deleted=deleted
        )
        if enforcement_succeeded:
            await db.increment_stat(chat.id, "warned")
    elif action == "mute":
        enforcement_succeeded = await _mute(
            context, db, chat, user.id, settings.mute_seconds
        )
    elif action == "ban":
        enforcement_succeeded = await _ban(context, db, chat.id, user.id)

    deletion_text = "Removed message" if deleted else "Could not remove message"
    await audit(
        context,
        f"{deletion_text} from user {user.id} in chat {chat.id} "
        f"(action={action}, deletion={'success' if deleted else 'failed'}, "
        f"enforcement={'success' if enforcement_succeeded else 'failed'}, "
        f"strike={count}, findings={reason})",
    )


async def _is_whitelisted_user(
    db, chat_id: int, user_id: int, username: str | None
) -> bool:
    if await db.is_whitelisted(chat_id, "user", str(user_id)):
        return True
    return bool(username and await db.is_whitelisted(chat_id, "user", username.lower()))


async def _delete(message, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        await message.delete()
    except TelegramError as exc:
        logger.warning("Could not delete message %s: %s", message.message_id, exc)
        return False
    return True


async def _mute(context, db, chat, user_id: int, seconds: int) -> bool:
    try:
        await moderation.mute_user(context.bot, chat.id, user_id, seconds)
    except TelegramError as exc:
        logger.warning("Could not mute user %s in chat %s: %s", user_id, chat.id, exc)
        return False
    await db.set_muted_until(chat.id, user_id, time.time() + seconds)
    await db.increment_stat(chat.id, "muted")
    return True


async def _ban(context, db, chat_id: int, user_id: int) -> bool:
    try:
        await moderation.ban_user(context.bot, chat_id, user_id)
    except TelegramError as exc:
        logger.warning("Could not ban user %s in chat %s: %s", user_id, chat_id, exc)
        return False
    await db.increment_stat(chat_id, "banned")
    return True


async def _send_warning(
    context, chat_id: int, user, count: int, message_deleted: bool = True
) -> bool:
    warning = messages.WARNING if message_deleted else messages.WARNING_NOT_DELETED
    try:
        sent = await context.bot.send_message(
            chat_id=chat_id,
            text=warning.format(name=user.mention_html(), count=count),
            parse_mode="HTML",
        )
    except TelegramError as exc:
        logger.warning("Could not send warning in chat %s: %s", chat_id, exc)
        return False
    if context.job_queue is not None:
        context.job_queue.run_once(
            _delete_later,
            when=_AUTO_DELETE_SECONDS,
            data=(sent.chat_id, sent.message_id),
        )
    return True


async def _delete_later(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id, message_id = context.job.data
    try:
        await context.bot.delete_message(chat_id, message_id)
    except TelegramError:
        pass
