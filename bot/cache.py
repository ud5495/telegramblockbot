from __future__ import annotations

import time
from typing import Any

from telegram import Bot
from telegram.error import TelegramError


class AdminCache:
    """TTL cache for chat administrator ids.

    Avoids calling ``get_chat_administrators`` for every message. Call
    :meth:`invalidate` when a member's status changes.
    """

    def __init__(self, ttl: int = 300) -> None:
        self.ttl = ttl
        self._cache: dict[int, tuple[float, set[int]]] = {}

    async def get_ids(self, bot: Bot, chat_id: int) -> set[int]:
        now = time.monotonic()
        cached = self._cache.get(chat_id)
        if cached is not None and cached[0] > now:
            return cached[1]

        try:
            admins: list[Any] = await bot.get_chat_administrators(chat_id)
        except TelegramError:
            cached = self._cache.get(chat_id)
            return cached[1] if cached else set()

        ids = {admin.user.id for admin in admins if getattr(admin, "user", None)}
        self._cache[chat_id] = (now + self.ttl, ids)
        return ids

    async def is_admin(self, bot: Bot, chat_id: int, user_id: int) -> bool:
        return user_id in await self.get_ids(bot, chat_id)

    def invalidate(self, chat_id: int) -> None:
        self._cache.pop(chat_id, None)

    def clear(self) -> None:
        self._cache.clear()
