from __future__ import annotations

import html
from urllib.parse import urlsplit

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from bot import keyboards, messages
from bot.handlers.common import (
    admin_cache_of,
    database_of,
    require_admin,
    settings_of,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, messages.HELP_TEXT)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, messages.HELP_TEXT)


async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, messages.RULES_TEXT)


async def toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat = update.effective_chat
    settings = settings_of(context)
    db = database_of(context)
    await db.ensure_chat(
        chat.id, chat.title, settings.default_action, settings.captcha_enabled
    )
    current = await db.get_chat(chat.id)
    enabled = not bool(current["enabled"])
    await db.update_chat(chat.id, enabled=enabled)
    await _reply(update, messages.ENABLED if enabled else messages.DISABLED)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat = update.effective_chat
    settings = settings_of(context)
    db = database_of(context)
    await db.ensure_chat(
        chat.id, chat.title, settings.default_action, settings.captcha_enabled
    )
    text, markup = await _build_settings(update, context)
    await _reply(update, text, markup)


async def _build_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    settings = settings_of(context)
    db = database_of(context)
    chat_settings = await db.get_chat(chat.id)
    domains = await db.list_whitelist(chat.id, "domain")
    users = await db.list_whitelist(chat.id, "user")
    text = messages.SETTINGS_TEXT.format(
        enabled="✅ on" if chat_settings["enabled"] else "❌ off",
        action=html.escape(chat_settings["action"]),
        captcha="✅ on" if chat_settings["captcha_enabled"] else "❌ off",
        warn_limit=settings.warn_limit,
        domains=html.escape(", ".join(domains)) if domains else "—",
        users=html.escape(", ".join(users)) if users else "—",
    )
    markup = keyboards.settings_keyboard(
        bool(chat_settings["enabled"]),
        chat_settings["action"],
        bool(chat_settings["captcha_enabled"]),
    )
    return text, markup


async def on_settings_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None:
        await query.answer()
        return
    if not await admin_cache_of(context).is_admin(context.bot, chat.id, user.id):
        await query.answer(messages.ADMIN_ONLY, show_alert=True)
        return

    field = query.data.split(":", 1)[1] if ":" in query.data else ""
    settings = settings_of(context)
    db = database_of(context)
    await db.ensure_chat(
        chat.id, chat.title, settings.default_action, settings.captcha_enabled
    )
    chat_settings = await db.get_chat(chat.id)

    if field == "enabled":
        await db.update_chat(chat.id, enabled=not bool(chat_settings["enabled"]))
    elif field == "captcha":
        await db.update_chat(
            chat.id, captcha_enabled=not bool(chat_settings["captcha_enabled"])
        )
    elif field == "action":
        await db.update_chat(
            chat.id, action=keyboards.next_action(chat_settings["action"])
        )
    else:
        await query.answer()
        return

    await query.answer(messages.SETTINGS_UPDATED)
    text, markup = await _build_settings(update, context)
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    except TelegramError:
        pass


async def whitelist_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat = update.effective_chat
    settings = settings_of(context)
    db = database_of(context)
    await db.ensure_chat(
        chat.id, chat.title, settings.default_action, settings.captcha_enabled
    )
    args = context.args or []

    if not args:
        await _reply(update, messages.WHITELIST_USAGE)
        return

    action = args[0].lower()
    if action == "list":
        kind = args[1].lower() if len(args) > 1 else None
        if kind is not None and kind not in {"domain", "user"}:
            await _reply(update, messages.WHITELIST_INVALID)
            return
        values = await db.list_whitelist(chat.id, kind)
        if not values:
            await _reply(update, messages.WHITELIST_EMPTY)
            return
        label = kind or "all"
        await _reply(
            update,
            f"<b>Whitelist ({html.escape(label)})</b>\n"
            + "\n".join(f"• <code>{html.escape(value)}</code>" for value in values),
        )
        return

    if action not in {"add", "remove"} or len(args) < 3:
        await _reply(update, messages.WHITELIST_USAGE)
        return

    kind = args[1].lower()
    if kind not in {"domain", "user"}:
        await _reply(update, messages.WHITELIST_INVALID)
        return

    value = _normalize_value(kind, args[2])
    if not value:
        await _reply(update, messages.WHITELIST_USAGE)
        return

    if action == "add":
        await db.add_whitelist(chat.id, kind, value, update.effective_user.id)
        await _reply(
            update, messages.WHITELIST_ADDED.format(value=html.escape(value), kind=kind)
        )
    else:
        removed = await db.remove_whitelist(chat.id, kind, value)
        if removed:
            await _reply(
                update,
                messages.WHITELIST_REMOVED.format(value=html.escape(value), kind=kind),
            )
        else:
            await _reply(
                update,
                messages.WHITELIST_NOT_FOUND.format(
                    value=html.escape(value), kind=kind
                ),
            )


def _normalize_value(kind: str, value: str) -> str:
    value = value.strip()
    if kind == "domain":
        lowered = value.lower()
        if "://" in lowered:
            lowered = urlsplit(lowered).hostname or lowered
        return lowered.removeprefix("www.").strip(".")
    return value.lstrip("@").lower()


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await require_admin(update, context):
        return
    chat = update.effective_chat
    db = database_of(context)
    stats = await db.get_stats(chat.id)
    await _reply(
        update,
        messages.STATS_TEXT.format(
            deleted=stats.get("deleted", 0),
            warned=stats.get("warned", 0),
            muted=stats.get("muted", 0),
            banned=stats.get("banned", 0),
            captcha_passed=stats.get("captcha_passed", 0),
            captcha_failed=stats.get("captcha_failed", 0),
        ),
    )


async def health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply(update, messages.HEALTH_TEXT)


async def _reply(update: Update, text: str, markup=None) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=markup,
        disable_web_page_preview=True,
    )
