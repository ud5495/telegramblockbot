from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

ACTION_ORDER = ["delete", "warn", "mute", "ban"]


def settings_keyboard(
    enabled: bool, action: str, captcha: bool
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🟢 Disable" if enabled else "🔴 Enable",
                    callback_data="set:enabled",
                )
            ],
            [InlineKeyboardButton(f"⚙️ Action: {action}", callback_data="set:action")],
            [
                InlineKeyboardButton(
                    "🧩 Captcha: on" if captcha else "🧩 Captcha: off",
                    callback_data="set:captcha",
                )
            ],
        ]
    )


def captcha_keyboard(chat_id: int, user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ I'm human", callback_data=f"captcha:{chat_id}:{user_id}"
                )
            ]
        ]
    )


def next_action(current: str) -> str:
    try:
        index = ACTION_ORDER.index(current)
    except ValueError:
        return ACTION_ORDER[0]
    return ACTION_ORDER[(index + 1) % len(ACTION_ORDER)]
