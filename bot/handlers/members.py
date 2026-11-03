from __future__ import annotations

import logging
import time

from telegram import ChatMember, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import messages, moderation
from bot.handlers.common import database_of, settings_of
from bot.keyboards import captcha_keyboard

logger = logging.getLogger(__name__)


async def on_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None or not message.new_chat_members:
        return

    settings = settings_of(context)
    db = database_of(context)
    await db.ensure_chat(
        chat.id, chat.title, settings.default_action, settings.captcha_enabled
    )
    chat_settings = await db.get_chat(chat.id)
    captcha_enabled = (
        chat_settings["captcha_enabled"] if chat_settings else settings.captcha_enabled
    )
    if not captcha_enabled:
        return

    for member in message.new_chat_members:
        if member.is_bot:
            continue
        try:
            await moderation.mute_user(
                context.bot, chat.id, member.id, settings.captcha_timeout
            )
        except TelegramError as exc:
            logger.warning(
                "Could not mute new member %s for captcha: %s", member.id, exc
            )
            continue

        try:
            prompt = await context.bot.send_message(
                chat_id=chat.id,
                text=messages.CAPTCHA_PROMPT.format(
                    name=member.mention_html(), timeout=settings.captcha_timeout
                ),
                parse_mode="HTML",
                reply_markup=captcha_keyboard(chat.id, member.id),
            )
        except TelegramError as exc:
            logger.warning("Could not send captcha prompt to %s: %s", member.id, exc)
            continue

        await db.add_pending_captcha(
            chat.id,
            member.id,
            prompt.message_id,
            time.time() + settings.captcha_timeout,
        )


async def on_captcha_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer()
        return

    try:
        chat_id = int(parts[1])
        target_user_id = int(parts[2])
    except ValueError:
        await query.answer()
        return

    user = update.effective_user
    if user is None or user.id != target_user_id:
        await query.answer(messages.CAPTCHA_NOT_YOURS, show_alert=True)
        return

    db = database_of(context)
    pending = await db.get_pending_captcha(chat_id, user.id)
    if pending is None or pending["expires_at"] < time.time():
        await db.remove_pending_captcha(chat_id, user.id)
        await query.answer(messages.CAPTCHA_EXPIRED, show_alert=True)
        return

    await db.remove_pending_captcha(chat_id, user.id)
    try:
        await moderation.unmute_user(context.bot, chat_id, user.id)
    except TelegramError as exc:
        logger.warning("Could not unmute verified user %s: %s", user.id, exc)

    await db.increment_stat(chat_id, "captcha_passed")
    await query.answer(messages.CAPTCHA_PASSED)
    try:
        await query.edit_message_text(messages.CAPTCHA_PASSED)
    except TelegramError:
        pass


async def captcha_sweep(context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = settings_of(context)
    db = database_of(context)
    expired = await db.expired_captchas(time.time())
    for row in expired:
        chat_id = row["chat_id"]
        user_id = row["user_id"]
        await db.remove_pending_captcha(chat_id, user_id)
        await db.increment_stat(chat_id, "captcha_failed")

        if row.get("message_id"):
            try:
                await context.bot.delete_message(chat_id, row["message_id"])
            except TelegramError:
                pass

        if settings.captcha_kick_on_timeout:
            try:
                await moderation.kick_user(context.bot, chat_id, user_id)
            except TelegramError as exc:
                logger.warning("Could not kick unverified user %s: %s", user_id, exc)


async def on_chat_member_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    chat_member = update.chat_member
    if chat_member is None:
        return
    admin_statuses = {ChatMember.ADMINISTRATOR, ChatMember.OWNER}
    statuses = {chat_member.old_chat_member.status, chat_member.new_chat_member.status}
    if statuses & admin_statuses:
        context.application.bot_data["admin_cache"].invalidate(chat_member.chat.id)
