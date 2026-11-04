from __future__ import annotations

import asyncio
import time
import uuid
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
    attempt_id TEXT NOT NULL DEFAULT '',
    message_id INTEGER,
    expires_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    lease_until REAL NOT NULL DEFAULT 0,
    legacy INTEGER NOT NULL DEFAULT 0,
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
        await self._ensure_captcha_schema()
        await self._conn.commit()

    async def _ensure_captcha_schema(self) -> None:
        """Add captcha recovery fields to databases created by older versions."""
        cursor = await self.connection.execute("PRAGMA table_info(pending_captcha)")
        try:
            columns = {row["name"] for row in await cursor.fetchall()}
        finally:
            await cursor.close()

        added_columns = {
            "attempt_id": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "lease_until": "REAL NOT NULL DEFAULT 0",
            "legacy": "INTEGER NOT NULL DEFAULT 0",
        }
        for name, definition in added_columns.items():
            if name not in columns:
                await self.connection.execute(
                    f"ALTER TABLE pending_captcha ADD COLUMN {name} {definition}"
                )

        await self.connection.execute(
            "UPDATE pending_captcha SET status = 'pending' "
            "WHERE status IS NULL OR status = ''"
        )
        cursor = await self.connection.execute(
            "SELECT chat_id, user_id FROM pending_captcha "
            "WHERE attempt_id IS NULL OR attempt_id = ''"
        )
        try:
            rows = list(await cursor.fetchall())
        finally:
            await cursor.close()
        for row in rows:
            await self.connection.execute(
                "UPDATE pending_captcha SET attempt_id = ?, legacy = 1 "
                "WHERE chat_id = ? AND user_id = ?",
                (uuid.uuid4().hex[:16], row["chat_id"], row["user_id"]),
            )

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
    ) -> str:
        attempt_id = uuid.uuid4().hex[:16]
        lease_until = time.time() + 60 if message_id is None else 0
        await self._execute(
            "INSERT OR REPLACE INTO pending_captcha "
            "(chat_id, user_id, attempt_id, message_id, expires_at, status, "
            "lease_until, legacy) VALUES (?, ?, ?, ?, ?, 'pending', ?, 0)",
            (
                chat_id,
                user_id,
                attempt_id,
                message_id,
                expires_at,
                lease_until,
            ),
        )
        return attempt_id

    async def set_pending_captcha_message(
        self, chat_id: int, user_id: int, attempt_id: str, message_id: int
    ) -> bool:
        rowcount = await self._execute(
            "UPDATE pending_captcha SET message_id = ?, lease_until = 0 "
            "WHERE chat_id = ? AND user_id = ? AND attempt_id = ? "
            "AND status = 'pending'",
            (message_id, chat_id, user_id, attempt_id),
        )
        return rowcount > 0

    async def get_pending_captcha(
        self, chat_id: int, user_id: int
    ) -> dict[str, Any] | None:
        row = await self._fetchone(
            "SELECT * FROM pending_captcha WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        return dict(row) if row else None

    async def claim_captcha_prompt(
        self,
        chat_id: int,
        user_id: int,
        attempt_id: str,
        now: float,
        lease_until: float | None = None,
    ) -> bool:
        lease_until = now + 60 if lease_until is None else lease_until
        rowcount = await self._execute(
            "UPDATE pending_captcha SET lease_until = ? "
            "WHERE chat_id = ? AND user_id = ? AND attempt_id = ? "
            "AND status = 'pending' AND message_id IS NULL AND expires_at > ? "
            "AND lease_until <= ?",
            (lease_until, chat_id, user_id, attempt_id, now, now),
        )
        return rowcount > 0

    async def claim_captcha_pass(
        self,
        chat_id: int,
        user_id: int,
        attempt_id: str,
        now: float,
        lease_until: float | None = None,
    ) -> bool:
        """Atomically lease a new pass or a stale passing retry."""
        lease_until = now + 60 if lease_until is None else lease_until
        rowcount = await self._execute(
            "UPDATE pending_captcha SET status = 'passing', lease_until = ? "
            "WHERE chat_id = ? AND user_id = ? AND attempt_id = ? "
            "AND (status = 'passing' "
            "OR (status = 'pending' AND message_id IS NOT NULL AND expires_at > ?)) "
            "AND lease_until <= ?",
            (lease_until, chat_id, user_id, attempt_id, now, now),
        )
        return rowcount > 0

    async def mark_captcha_expired(
        self,
        chat_id: int,
        user_id: int,
        attempt_id: str,
        now: float,
        lease_until: float | None = None,
    ) -> bool:
        """Atomically lease new timeout enforcement or a stale expired retry."""
        lease_until = now + 60 if lease_until is None else lease_until
        rowcount = await self._execute(
            "UPDATE pending_captcha SET status = 'expired', lease_until = ? "
            "WHERE chat_id = ? AND user_id = ? AND attempt_id = ? "
            "AND (status = 'expired' OR (status = 'pending' AND expires_at <= ?)) "
            "AND lease_until <= ?",
            (lease_until, chat_id, user_id, attempt_id, now, now),
        )
        return rowcount > 0

    async def release_captcha_lease(
        self, chat_id: int, user_id: int, attempt_id: str, status: str
    ) -> bool:
        rowcount = await self._execute(
            "UPDATE pending_captcha SET lease_until = 0 "
            "WHERE chat_id = ? AND user_id = ? AND attempt_id = ? AND status = ?",
            (chat_id, user_id, attempt_id, status),
        )
        return rowcount > 0

    async def mark_captcha_passed(
        self, chat_id: int, user_id: int, attempt_id: str
    ) -> bool:
        rowcount = await self._execute(
            "UPDATE pending_captcha SET status = 'passed', lease_until = 0 "
            "WHERE chat_id = ? AND user_id = ? AND attempt_id = ? "
            "AND status = 'passing'",
            (chat_id, user_id, attempt_id),
        )
        return rowcount > 0

    async def mark_captcha_enforced(
        self, chat_id: int, user_id: int, attempt_id: str
    ) -> bool:
        rowcount = await self._execute(
            "UPDATE pending_captcha SET status = 'enforced', lease_until = 0 "
            "WHERE chat_id = ? AND user_id = ? AND attempt_id = ? "
            "AND status = 'expired'",
            (chat_id, user_id, attempt_id),
        )
        return rowcount > 0

    async def complete_captcha_pass(
        self, chat_id: int, user_id: int, attempt_id: str
    ) -> bool:
        return await self._complete_captcha(
            chat_id, user_id, attempt_id, "passed", "captcha_passed"
        )

    async def complete_captcha_failure(
        self, chat_id: int, user_id: int, attempt_id: str
    ) -> bool:
        return await self._complete_captcha(
            chat_id, user_id, attempt_id, "enforced", "captcha_failed"
        )

    async def _complete_captcha(
        self,
        chat_id: int,
        user_id: int,
        attempt_id: str,
        status: str,
        stat_field: str,
    ) -> bool:
        """Remove a completed attempt and count it in the same transaction."""
        if stat_field not in STAT_COLUMNS:
            raise ValueError(f"Unknown stat field: {stat_field}")
        async with self._lock:
            try:
                cursor = await self.connection.execute(
                    "DELETE FROM pending_captcha "
                    "WHERE chat_id = ? AND user_id = ? AND attempt_id = ? "
                    "AND status = ?",
                    (chat_id, user_id, attempt_id, status),
                )
                completed = cursor.rowcount > 0
                await cursor.close()
                if completed:
                    cursor = await self.connection.execute(
                        f"INSERT INTO stats (chat_id, {stat_field}) VALUES (?, 1) "
                        f"ON CONFLICT(chat_id) DO UPDATE SET "
                        f"{stat_field} = {stat_field} + 1",
                        (chat_id,),
                    )
                    await cursor.close()
                await self.connection.commit()
                return completed
            except Exception:
                await self.connection.rollback()
                raise

    async def remove_captcha_attempt(
        self, chat_id: int, user_id: int, attempt_id: str
    ) -> bool:
        rowcount = await self._execute(
            "DELETE FROM pending_captcha "
            "WHERE chat_id = ? AND user_id = ? AND attempt_id = ?",
            (chat_id, user_id, attempt_id),
        )
        return rowcount > 0

    async def remove_pending_captcha(self, chat_id: int, user_id: int) -> bool:
        rowcount = await self._execute(
            "DELETE FROM pending_captcha WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        return rowcount > 0

    async def expired_captchas(self, now: float) -> list[dict[str, Any]]:
        """Return attempts needing prompts, API actions, or DB finalization."""
        rows = await self._fetchall(
            "SELECT * FROM pending_captcha "
            "WHERE status IN ('passing', 'passed', 'expired', 'enforced') "
            "OR (status = 'pending' AND (expires_at <= ? OR message_id IS NULL)) "
            "ORDER BY expires_at",
            (now,),
        )
        return [dict(row) for row in rows]
