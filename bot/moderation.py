from __future__ import annotations

import time
from collections import defaultdict, deque

from telegram import Bot, ChatPermissions
from telegram.error import TelegramError


def choose_action(base_action: str, violation_count: int, warn_limit: int) -> str:
    """Return the action to apply for a violation.

    ``warn`` escalates to ``mute`` once the user reaches ``warn_limit`` strikes.
    Other actions are used as-is.
    """
    if base_action == "delete":
        return "delete"
    if base_action == "warn":
        return "mute" if violation_count >= warn_limit else "warn"
    if base_action in {"mute", "ban"}:
        return base_action
    return "delete"


class SpamTracker:
    """In-memory flood/duplicate detector keyed by ``(chat_id, user_id)``."""

    def __init__(
        self,
        max_messages: int = 5,
        window_seconds: int = 10,
        duplicate_threshold: int = 3,
    ) -> None:
        self.max_messages = max_messages
        self.window_seconds = window_seconds
        self.duplicate_threshold = duplicate_threshold
        self._events: dict[tuple[int, int], deque[float]] = defaultdict(deque)
        self._recent_text: dict[tuple[int, int], deque[tuple[float, str]]] = (
            defaultdict(deque)
        )

    def check(self, chat_id: int, user_id: int, text: str) -> str | None:
        """Return ``"flood"``, ``"duplicate"`` or ``None``."""
        now = time.monotonic()
        key = (chat_id, user_id)

        events = self._events[key]
        while events and now - events[0] > self.window_seconds:
            events.popleft()
        events.append(now)
        if len(events) > self.max_messages:
            return "flood"

        normalized = (text or "").strip().lower()
        if normalized:
            recent = self._recent_text[key]
            while recent and now - recent[0][0] > self.window_seconds:
                recent.popleft()
            recent.append((now, normalized))
            if (
                sum(1 for _, item in recent if item == normalized)
                >= self.duplicate_threshold
            ):
                return "duplicate"

        return None

    def reset(self, chat_id: int, user_id: int) -> None:
        key = (chat_id, user_id)
        self._events.pop(key, None)
        self._recent_text.pop(key, None)


async def mute_user(bot: Bot, chat_id: int, user_id: int, seconds: int) -> None:
    until_date = int(time.time()) + max(seconds, 1)
    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=ChatPermissions(can_send_messages=False),
        until_date=until_date,
    )


async def unmute_user(bot: Bot, chat_id: int, user_id: int) -> None:
    await bot.restrict_chat_member(
        chat_id=chat_id,
        user_id=user_id,
        permissions=ChatPermissions.all_permissions(),
    )


async def ban_user(bot: Bot, chat_id: int, user_id: int) -> None:
    await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)


async def kick_user(bot: Bot, chat_id: int, user_id: int) -> None:
    """Kick a user (ban followed by an immediate unban)."""
    try:
        await bot.ban_chat_member(
            chat_id=chat_id, user_id=user_id, revoke_messages=False
        )
    finally:
        try:
            await bot.unban_chat_member(
                chat_id=chat_id, user_id=user_id, only_if_banned=True
            )
        except TelegramError:
            pass
