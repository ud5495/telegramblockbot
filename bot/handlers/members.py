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
            attempt_id = await db.add_pending_captcha(
                chat.id,
                member.id,
                None,
                time.time() + settings.captcha_timeout,
            )
        except Exception:
            logger.exception("Could not create captcha attempt for %s", member.id)
            continue

        try:
            await moderation.mute_captcha_user(context.bot, chat.id, member.id)
        except TelegramError as exc:
            await db.remove_captcha_attempt(chat.id, member.id, attempt_id)
            logger.warning(
                "Could not mute new member %s for captcha: %s", member.id, exc
            )
            continue

        row = await db.get_pending_captcha(chat.id, member.id)
        if row is not None and row["attempt_id"] == attempt_id:
            await _send_captcha_prompt(
                context,
                row,
                member.mention_html(),
                settings.captcha_timeout,
                lease_held=True,
                ensure_muted=False,
            )


async def _send_captcha_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    row: dict,
    name: str,
    timeout: int,
    lease_held: bool = False,
    ensure_muted: bool = True,
) -> bool:
    db = database_of(context)
    chat_id = row["chat_id"]
    user_id = row["user_id"]
    attempt_id = row["attempt_id"]
    now = time.time()
    if not lease_held and not await db.claim_captcha_prompt(
        chat_id, user_id, attempt_id, now
    ):
        return False

    if ensure_muted:
        try:
            # A recovered setup row may have crashed before its first mute.
            await moderation.mute_captcha_user(context.bot, chat_id, user_id)
        except TelegramError as exc:
            await db.release_captcha_lease(chat_id, user_id, attempt_id, "pending")
            logger.warning("Could not restore captcha mute for %s: %s", user_id, exc)
            return False

    try:
        prompt = await context.bot.send_message(
            chat_id=chat_id,
            text=messages.CAPTCHA_PROMPT.format(name=name, timeout=timeout),
            parse_mode="HTML",
            reply_markup=captcha_keyboard(chat_id, user_id, attempt_id),
        )
    except TelegramError as exc:
        await db.release_captcha_lease(chat_id, user_id, attempt_id, "pending")
        logger.warning("Could not send captcha prompt to %s: %s", user_id, exc)
        return False

    try:
        # A later sweep can resend if this update cannot be saved.
        return await db.set_pending_captcha_message(
            chat_id, user_id, attempt_id, prompt.message_id
        )
    except Exception:
        await db.release_captcha_lease(chat_id, user_id, attempt_id, "pending")
        logger.exception("Could not save captcha prompt for %s", user_id)
        return False


async def _finish_captcha_pass(
    context: ContextTypes.DEFAULT_TYPE, row: dict
) -> bool:
    db = database_of(context)
    chat_id = row["chat_id"]
    user_id = row["user_id"]
    attempt_id = row["attempt_id"]

    if row["status"] == "passed":
        return await _complete_captcha_pass(db, chat_id, user_id, attempt_id)
    if row["status"] not in {"pending", "passing"}:
        return False
    if not await db.claim_captcha_pass(
        chat_id, user_id, attempt_id, time.time()
    ):
        return False

    try:
        await moderation.unmute_user(context.bot, chat_id, user_id)
    except TelegramError as exc:
        await db.release_captcha_lease(chat_id, user_id, attempt_id, "passing")
        logger.warning("Could not unmute verified user %s: %s", user_id, exc)
        return False

    if not await db.mark_captcha_passed(chat_id, user_id, attempt_id):
        return False
    return await _complete_captcha_pass(db, chat_id, user_id, attempt_id)


async def _complete_captcha_pass(
    db, chat_id: int, user_id: int, attempt_id: str
) -> bool:
    try:
        return await db.complete_captcha_pass(chat_id, user_id, attempt_id)
    except Exception:
        # The passed row prevents another unmute; only DB completion is retried.
        logger.exception("Could not finalize passed captcha for %s", user_id)
        return False


async def _enforce_captcha_timeout(
    context: ContextTypes.DEFAULT_TYPE, row: dict
) -> bool:
    db = database_of(context)
    chat_id = row["chat_id"]
    user_id = row["user_id"]
    attempt_id = row["attempt_id"]

    if row["status"] == "enforced":
        return await _complete_captcha_failure(
            context, db, chat_id, user_id, attempt_id, row
        )
    if row["status"] not in {"pending", "expired"}:
        return False
    if not await db.mark_captcha_expired(
        chat_id, user_id, attempt_id, time.time()
    ):
        return False

    kick = settings_of(context).captcha_kick_on_timeout
    try:
        if kick:
            await moderation.kick_user(context.bot, chat_id, user_id)
        else:
            # Without kick-on-timeout, expiry itself must explicitly unmute.
            await moderation.unmute_user(context.bot, chat_id, user_id)
    except TelegramError as exc:
        await db.release_captcha_lease(chat_id, user_id, attempt_id, "expired")
        action = "kick" if kick else "unmute"
        logger.warning("Could not %s unverified user %s: %s", action, user_id, exc)
        return False

    if not await db.mark_captcha_enforced(chat_id, user_id, attempt_id):
        return False
    return await _complete_captcha_failure(
        context, db, chat_id, user_id, attempt_id, row
    )


async def _complete_captcha_failure(
    context: ContextTypes.DEFAULT_TYPE,
    db,
    chat_id: int,
    user_id: int,
    attempt_id: str,
    row: dict,
) -> bool:
    try:
        completed = await db.complete_captcha_failure(chat_id, user_id, attempt_id)
    except Exception:
        # The enforced row prevents another kick/unmute on the next sweep.
        logger.exception("Could not finalize expired captcha for %s", user_id)
        return False
    if completed and row.get("message_id"):
        try:
            await context.bot.delete_message(row["chat_id"], row["message_id"])
        except TelegramError:
            pass
    return completed


async def on_captcha_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    parts = query.data.split(":")
    if (
        len(parts) not in {3, 4}
        or parts[0] != "captcha"
        or (len(parts) == 4 and not parts[3])
    ):
        await query.answer(messages.CAPTCHA_EXPIRED, show_alert=True)
        return

    try:
        chat_id = int(parts[1])
        target_user_id = int(parts[2])
    except ValueError:
        await query.answer()
        return
    attempt_id = parts[3] if len(parts) == 4 else None

    user = update.effective_user
    if user is None or user.id != target_user_id:
        await query.answer(messages.CAPTCHA_NOT_YOURS, show_alert=True)
        return

    db = database_of(context)
    pending = await db.get_pending_captcha(chat_id, user.id)
    callback_message = getattr(query, "message", None)
    if pending is None or callback_message is None:
        await query.answer(messages.CAPTCHA_EXPIRED, show_alert=True)
        return
    if attempt_id is None:
        # Three-part callbacks are accepted only for their original legacy row.
        if (
            not pending["legacy"]
            or pending["message_id"] is None
            or callback_message.message_id != pending["message_id"]
        ):
            await query.answer(messages.CAPTCHA_EXPIRED, show_alert=True)
            return
    elif (
        pending["attempt_id"] != attempt_id
        or pending["message_id"] is None
        or callback_message.message_id != pending["message_id"]
    ):
        # An old prompt must never verify a replacement or unprompted attempt.
        await query.answer(messages.CAPTCHA_EXPIRED, show_alert=True)
        return

    now = time.time()
    if pending["status"] in {"expired", "enforced"} or (
        pending["status"] == "pending" and pending["expires_at"] <= now
    ):
        completed = await _enforce_captcha_timeout(context, pending)
        await query.answer(
            messages.CAPTCHA_EXPIRED if completed else messages.CAPTCHA_RETRY,
            show_alert=True,
        )
        return

    if pending["status"] not in {"pending", "passing", "passed"}:
        await query.answer(messages.CAPTCHA_RETRY, show_alert=True)
        return

    if not await _finish_captcha_pass(context, pending):
        await query.answer(messages.CAPTCHA_RETRY, show_alert=True)
        return

    await query.answer(messages.CAPTCHA_PASSED)
    try:
        await query.edit_message_text(messages.CAPTCHA_PASSED)
    except TelegramError:
        pass


async def captcha_sweep(context: ContextTypes.DEFAULT_TYPE) -> None:
    db = database_of(context)
    settings = settings_of(context)
    actionable = await db.expired_captchas(time.time())
    for row in actionable:
        if row["status"] == "pending" and row["expires_at"] > time.time():
            await _send_captcha_prompt(
                context, row, "there", settings.captcha_timeout
            )
            continue
        if row["status"] in {"passing", "passed"}:
            if await _finish_captcha_pass(context, row) and row.get("message_id"):
                try:
                    await context.bot.edit_message_text(
                        chat_id=row["chat_id"],
                        message_id=row["message_id"],
                        text=messages.CAPTCHA_PASSED,
                    )
                except TelegramError:
                    pass
            continue
        await _enforce_captcha_timeout(context, row)


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
