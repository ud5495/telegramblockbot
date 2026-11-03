from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    title TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    action TEXT NOT NULL DEFAULT 'delete',
    captcha_enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS whitelist (
    chat_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    value TEXT NOT NULL,
    added_by INTEGER,
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_id, kind, value)
);

CREATE TABLE IF NOT EXISTS violations (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    last_violation_at REAL NOT NULL DEFAULT 0,
    muted_until REAL,
    PRIMARY KEY (chat_id, user_id)
);

CREATE TABLE IF NOT EXISTS stats (
    chat_id INTEGER PRIMARY KEY,
    deleted INTEGER NOT NULL DEFAULT 0,
    warned INTEGER NOT NULL DEFAULT 0,
    muted INTEGER NOT NULL DEFAULT 0,
    banned INTEGER NOT NULL DEFAULT 0,
    captcha_passed INTEGER NOT NULL DEFAULT 0,
    captcha_failed INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pending_captcha (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    message_id INTEGER,
    expires_at REAL NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
"""

CHAT_COLUMNS = {"title", "enabled", "action", "captcha_enabled"}
WHITELIST_KINDS = {"domain", "user"}
STAT_COLUMNS = {
    "deleted",
    "warned",
    "muted",
    "banned",
    "captcha_passed",
    "captcha_failed",
}


class Database:
    """Small async wrapper around a single SQLite connection.

    All operations share one connection guarded by a lock. That is plenty for a
    single-bot workload and keeps the code simple.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.initialize() must be called first")
        return self._conn

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _fetchone(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> aiosqlite.Row | None:
        async with self._lock:
            cursor = await self.connection.execute(sql, params)
            try:
                return await cursor.fetchone()
            finally:
                await cursor.close()

    async def _fetchall(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[aiosqlite.Row]:
        async with self._lock:
            cursor = await self.connection.execute(sql, params)
            try:
                return list(await cursor.fetchall())
            finally:
                await cursor.close()

    async def _execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        async with self._lock:
            cursor = await self.connection.execute(sql, params)
            await self.connection.commit()
            rowcount = cursor.rowcount
            await cursor.close()
            return rowcount

    # ------------------------------------------------------------------ chats
    async def ensure_chat(
        self,
        chat_id: int,
        title: str | None = None,
        default_action: str = "delete",
        default_captcha: bool = False,
    ) -> None:
        await self._execute(
            "INSERT OR IGNORE INTO chats (chat_id, title, action, captcha_enabled) "
            "VALUES (?, ?, ?, ?)",
            (chat_id, title, default_action, int(default_captcha)),
        )
        if title:
            await self._execute(
                "UPDATE chats SET title = ? WHERE chat_id = ?", (title, chat_id)
            )

    async def get_chat(self, chat_id: int) -> dict[str, Any] | None:
        row = await self._fetchone("SELECT * FROM chats WHERE chat_id = ?", (chat_id,))
        return dict(row) if row else None

    async def update_chat(self, chat_id: int, **fields: Any) -> None:
        unknown = set(fields) - CHAT_COLUMNS
        if unknown:
            raise ValueError(f"Unknown chat fields: {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{name} = ?" for name in fields)
        params = tuple(
            int(value) if isinstance(value, bool) else value
            for value in fields.values()
        )
        await self._execute(
            f"UPDATE chats SET {assignments} WHERE chat_id = ?", (*params, chat_id)
        )

    # -------------------------------------------------------------- whitelist
    async def add_whitelist(
        self, chat_id: int, kind: str, value: str, added_by: int | None = None
    ) -> None:
        if kind not in WHITELIST_KINDS:
            raise ValueError(f"Unknown whitelist kind: {kind}")
        await self._execute(
            "INSERT OR IGNORE INTO whitelist (chat_id, kind, value, added_by) VALUES (?, ?, ?, ?)",
            (chat_id, kind, value, added_by),
        )

    async def remove_whitelist(self, chat_id: int, kind: str, value: str) -> bool:
        if kind not in WHITELIST_KINDS:
            raise ValueError(f"Unknown whitelist kind: {kind}")
        rowcount = await self._execute(
            "DELETE FROM whitelist WHERE chat_id = ? AND kind = ? AND value = ?",
            (chat_id, kind, value),
        )
        return rowcount > 0

    async def list_whitelist(self, chat_id: int, kind: str | None = None) -> list[str]:
        if kind is None:
            rows = await self._fetchall(
                "SELECT value FROM whitelist WHERE chat_id = ? ORDER BY value",
                (chat_id,),
            )
        else:
            if kind not in WHITELIST_KINDS:
                raise ValueError(f"Unknown whitelist kind: {kind}")
            rows = await self._fetchall(
                "SELECT value FROM whitelist WHERE chat_id = ? AND kind = ? ORDER BY value",
                (chat_id, kind),
            )
        return [row["value"] for row in rows]

    async def is_whitelisted(self, chat_id: int, kind: str, value: str) -> bool:
        if kind not in WHITELIST_KINDS:
            raise ValueError(f"Unknown whitelist kind: {kind}")
        row = await self._fetchone(
            "SELECT 1 FROM whitelist WHERE chat_id = ? AND kind = ? AND value = ? LIMIT 1",
            (chat_id, kind, value),
        )
        return row is not None

    # ------------------------------------------------------------- violations
    async def record_violation(
        self, chat_id: int, user_id: int, decay_seconds: int
    ) -> int:
        now = time.time()
        row = await self._fetchone(
            "SELECT count, last_violation_at FROM violations WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        if row is None or now - row["last_violation_at"] > decay_seconds:
            count = 1
        else:
            count = row["count"] + 1
        await self._execute(
            "INSERT INTO violations (chat_id, user_id, count, last_violation_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chat_id, user_id) DO UPDATE SET "
            "count = excluded.count, last_violation_at = excluded.last_violation_at",
            (chat_id, user_id, count, now),
        )
        return count

    async def reset_violations(self, chat_id: int, user_id: int) -> None:
        await self._execute(
            "DELETE FROM violations WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )

    async def set_muted_until(self, chat_id: int, user_id: int, until: float) -> None:
        await self._execute(
            "INSERT INTO violations (chat_id, user_id, count, last_violation_at, muted_until) "
            "VALUES (?, ?, 0, 0, ?) "
            "ON CONFLICT(chat_id, user_id) DO UPDATE SET muted_until = excluded.muted_until",
            (chat_id, user_id, until),
        )

    # ------------------------------------------------------------------ stats
    async def increment_stat(self, chat_id: int, field: str, amount: int = 1) -> None:
        if field not in STAT_COLUMNS:
            raise ValueError(f"Unknown stat field: {field}")
        await self._execute(
            "INSERT OR IGNORE INTO stats (chat_id) VALUES (?)", (chat_id,)
        )
        await self._execute(
            f"UPDATE stats SET {field} = {field} + ? WHERE chat_id = ?",
            (amount, chat_id),
        )

    async def get_stats(self, chat_id: int) -> dict[str, int]:
        await self._execute(
            "INSERT OR IGNORE INTO stats (chat_id) VALUES (?)", (chat_id,)
        )
        row = await self._fetchone("SELECT * FROM stats WHERE chat_id = ?", (chat_id,))
        return dict(row) if row else {}

    # --------------------------------------------------------------- captcha
    async def add_pending_captcha(
        self, chat_id: int, user_id: int, message_id: int | None, expires_at: float
    ) -> None:
        await self._execute(
            "INSERT OR REPLACE INTO pending_captcha (chat_id, user_id, message_id, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (chat_id, user_id, message_id, expires_at),
        )

    async def get_pending_captcha(
        self, chat_id: int, user_id: int
    ) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT * FROM pending_captcha WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        return dict(row) if row else None

    async def remove_pending_captcha(self, chat_id: int, user_id: int) -> bool:
        rowcount = await self._execute(
            "DELETE FROM pending_captcha WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        return rowcount > 0

    async def expired_captchas(self, now: float) -> list[dict[str, Any]]:
        rows = await self._fetchall(
            "SELECT * FROM pending_captcha WHERE expires_at <= ?", (now,)
        )
        return [dict(row) for row in rows]
