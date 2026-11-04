from __future__ import annotations

import time
from types import SimpleNamespace

import aiosqlite
import pytest
import pytest_asyncio
from telegram import ChatPermissions
from telegram.error import TelegramError

from bot import messages, moderation
from bot.config import Settings
from bot.db import Database
from bot.handlers import members
from bot.keyboards import captcha_keyboard

CHAT_ID = -1001234567890
USER_ID = 123456789


class FakeBot:
    def __init__(self, permissions: ChatPermissions | None = None) -> None:
        self.chat_permissions = permissions
        self.get_chat_failures = 0
        self.restrict_failures = 0
        self.unban_failures = 0
        self.send_failures = 0
        self.restrict_calls: list[dict] = []
        self.ban_calls: list[dict] = []
        self.unban_calls: list[dict] = []
        self.sent_messages: list[dict] = []
        self.deleted_messages: list[tuple[int, int]] = []
        self.edited_messages: list[dict] = []

    async def get_chat(self, chat_id: int) -> SimpleNamespace:
        if self.get_chat_failures:
            self.get_chat_failures -= 1
            raise TelegramError("temporary get chat failure")
        return SimpleNamespace(permissions=self.chat_permissions)

    async def restrict_chat_member(self, **kwargs) -> None:
        self.restrict_calls.append(kwargs)
        if self.restrict_failures:
            self.restrict_failures -= 1
            raise TelegramError("temporary restrict failure")

    async def ban_chat_member(self, **kwargs) -> None:
        self.ban_calls.append(kwargs)

    async def unban_chat_member(self, **kwargs) -> None:
        self.unban_calls.append(kwargs)
        if self.unban_failures:
            self.unban_failures -= 1
            raise TelegramError("temporary unban failure")

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        self.deleted_messages.append((chat_id, message_id))

    async def send_message(self, **kwargs) -> SimpleNamespace:
        self.sent_messages.append(kwargs)
        if self.send_failures:
            self.send_failures -= 1
            raise TelegramError("temporary send failure")
        return SimpleNamespace(message_id=100 + len(self.sent_messages))

    async def edit_message_text(self, **kwargs) -> None:
        self.edited_messages.append(kwargs)


class FakeQuery:
    def __init__(self, data: str, message_id: int) -> None:
        self.data = data
        self.message = SimpleNamespace(message_id=message_id)
        self.answers: list[tuple[str | None, bool]] = []
        self.edits: list[str] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text: str) -> None:
        self.edits.append(text)


def make_context(db: Database, bot: FakeBot, kick: bool = True) -> SimpleNamespace:
    settings = Settings(
        bot_token="123:abc",
        captcha_enabled=True,
        captcha_kick_on_timeout=kick,
    )
    return SimpleNamespace(
        bot=bot,
        application=SimpleNamespace(
            bot_data={"db": db, "settings": settings}
        ),
    )


def make_update(
    attempt_id: str | None, message_id: int
) -> tuple[SimpleNamespace, FakeQuery]:
    suffix = f":{attempt_id}" if attempt_id is not None else ""
    query = FakeQuery(
        f"captcha:{CHAT_ID}:{USER_ID}{suffix}", message_id=message_id
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_user=SimpleNamespace(id=USER_ID),
    )
    return update, query


class FakeMember:
    id = USER_ID
    is_bot = False

    def mention_html(self) -> str:
        return "Test User"


def make_join_update() -> SimpleNamespace:
    return SimpleNamespace(
        effective_message=SimpleNamespace(new_chat_members=[FakeMember()]),
        effective_chat=SimpleNamespace(id=CHAT_ID, title="Test Chat"),
    )


async def add_attempt(
    db: Database, *, expires_at: float, message_id: int = 77
) -> str:
    return await db.add_pending_captcha(
        CHAT_ID, USER_ID, message_id, expires_at
    )


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(tmp_path / "captcha.db")
    await database.initialize()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_expired_callback_enforces_timeout_and_retries_kick_after_restart(db):
    attempt_id = await add_attempt(db, expires_at=time.time() - 1)
    bot = FakeBot()
    bot.unban_failures = 1
    context = make_context(db, bot, kick=True)
    update, query = make_update(attempt_id, message_id=77)

    await members.on_captcha_callback(update, context)

    assert len(bot.ban_calls) == 1
    assert len(bot.unban_calls) == 1
    assert query.answers == [(messages.CAPTCHA_RETRY, True)]
    retained = await db.get_pending_captcha(CHAT_ID, USER_ID)
    assert retained is not None
    assert retained["status"] == "expired"
    assert (await db.get_stats(CHAT_ID))["captcha_failed"] == 0
    assert bot.deleted_messages == []

    path = db.path
    await db.close()
    restarted_db = Database(path)
    await restarted_db.initialize()
    context.application.bot_data["db"] = restarted_db
    try:
        await members.captcha_sweep(context)
        assert await restarted_db.get_pending_captcha(CHAT_ID, USER_ID) is None
        assert (await restarted_db.get_stats(CHAT_ID))["captcha_failed"] == 1

        await members.captcha_sweep(context)
        assert (await restarted_db.get_stats(CHAT_ID))["captcha_failed"] == 1
        assert len(bot.ban_calls) == 2
        assert bot.deleted_messages == [(CHAT_ID, 77)]
    finally:
        await restarted_db.close()


@pytest.mark.asyncio
async def test_unmute_failure_is_not_success_and_retries_after_restart(db):
    attempt_id = await add_attempt(db, expires_at=time.time() + 60)
    permissions = ChatPermissions(can_send_messages=True, can_send_photos=True)
    bot = FakeBot(permissions)
    bot.restrict_failures = 1
    context = make_context(db, bot)
    update, query = make_update(attempt_id, message_id=77)

    await members.on_captcha_callback(update, context)

    assert query.answers == [(messages.CAPTCHA_RETRY, True)]
    assert query.edits == []
    retained = await db.get_pending_captcha(CHAT_ID, USER_ID)
    assert retained is not None
    assert retained["status"] == "passing"
    assert (await db.get_stats(CHAT_ID))["captcha_passed"] == 0

    path = db.path
    await db.close()
    restarted_db = Database(path)
    await restarted_db.initialize()
    context.application.bot_data["db"] = restarted_db
    try:
        await members.captcha_sweep(context)
        assert await restarted_db.get_pending_captcha(CHAT_ID, USER_ID) is None
        assert (await restarted_db.get_stats(CHAT_ID))["captcha_passed"] == 1
        assert bot.edited_messages == [
            {
                "chat_id": CHAT_ID,
                "message_id": 77,
                "text": messages.CAPTCHA_PASSED,
            }
        ]

        await members.captcha_sweep(context)
        assert (await restarted_db.get_stats(CHAT_ID))["captcha_passed"] == 1
        assert len(bot.restrict_calls) == 2
    finally:
        await restarted_db.close()


@pytest.mark.asyncio
async def test_successful_callback_is_only_counted_once(db):
    attempt_id = await add_attempt(db, expires_at=time.time() + 60)
    bot = FakeBot(ChatPermissions(can_send_messages=True))
    context = make_context(db, bot)
    update, query = make_update(attempt_id, message_id=77)

    await members.on_captcha_callback(update, context)

    assert query.answers == [(messages.CAPTCHA_PASSED, False)]
    assert query.edits == [messages.CAPTCHA_PASSED]
    assert await db.get_pending_captcha(CHAT_ID, USER_ID) is None
    assert (await db.get_stats(CHAT_ID))["captcha_passed"] == 1

    await members.on_captcha_callback(update, context)
    assert query.answers[-1] == (messages.CAPTCHA_EXPIRED, True)
    assert (await db.get_stats(CHAT_ID))["captcha_passed"] == 1
    assert len(bot.restrict_calls) == 1


@pytest.mark.asyncio
async def test_old_attempt_or_message_cannot_pass_new_attempt(db):
    old_attempt = await add_attempt(
        db, expires_at=time.time() + 60, message_id=10
    )
    new_attempt = await add_attempt(
        db, expires_at=time.time() + 60, message_id=20
    )
    bot = FakeBot(ChatPermissions(can_send_messages=True))
    context = make_context(db, bot)

    old_update, old_query = make_update(old_attempt, message_id=10)
    await members.on_captcha_callback(old_update, context)
    assert old_query.answers == [(messages.CAPTCHA_EXPIRED, True)]

    wrong_message_update, wrong_message_query = make_update(
        new_attempt, message_id=10
    )
    await members.on_captcha_callback(wrong_message_update, context)
    assert wrong_message_query.answers == [(messages.CAPTCHA_EXPIRED, True)]

    current = await db.get_pending_captcha(CHAT_ID, USER_ID)
    assert current is not None
    assert current["attempt_id"] == new_attempt
    assert current["status"] == "pending"
    assert bot.restrict_calls == []
    assert (await db.get_stats(CHAT_ID))["captcha_passed"] == 0


@pytest.mark.asyncio
async def test_no_kick_expiry_unmute_failure_is_retained_and_retried(db):
    await add_attempt(db, expires_at=time.time() - 1)
    permissions = ChatPermissions(can_send_messages=True, can_send_videos=True)
    bot = FakeBot(permissions)
    bot.restrict_failures = 1
    context = make_context(db, bot, kick=False)

    await members.captcha_sweep(context)

    retained = await db.get_pending_captcha(CHAT_ID, USER_ID)
    assert retained is not None
    assert retained["status"] == "expired"
    assert (await db.get_stats(CHAT_ID))["captcha_failed"] == 0
    assert bot.ban_calls == []

    await members.captcha_sweep(context)
    assert await db.get_pending_captcha(CHAT_ID, USER_ID) is None
    assert (await db.get_stats(CHAT_ID))["captcha_failed"] == 1
    assert len(bot.restrict_calls) == 2
    assert all(
        call["permissions"] is permissions for call in bot.restrict_calls
    )

    await members.captcha_sweep(context)
    assert (await db.get_stats(CHAT_ID))["captcha_failed"] == 1


@pytest.mark.asyncio
async def test_unmute_uses_chat_defaults_or_send_only_fallback():
    defaults = ChatPermissions(
        can_send_messages=True, can_send_photos=False, can_invite_users=True
    )
    bot = FakeBot(defaults)
    await moderation.unmute_user(bot, CHAT_ID, USER_ID)
    assert bot.restrict_calls[0]["permissions"] is defaults

    bot.chat_permissions = None
    await moderation.unmute_user(bot, CHAT_ID, USER_ID)
    fallback = bot.restrict_calls[1]["permissions"]
    assert fallback.to_dict() == {"can_send_messages": True}


@pytest.mark.asyncio
async def test_captcha_restriction_does_not_auto_expire():
    bot = FakeBot()
    await moderation.mute_captcha_user(bot, CHAT_ID, USER_ID)
    call = bot.restrict_calls[0]
    assert call["permissions"].can_send_messages is False
    assert "until_date" not in call


def test_captcha_callback_data_fits_telegram_limit():
    keyboard = captcha_keyboard(CHAT_ID, USER_ID, "0123456789abcdef")
    data = keyboard.inline_keyboard[0][0].callback_data
    assert len(data.encode("utf-8")) <= 64


@pytest.mark.asyncio
async def test_existing_pending_captcha_table_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    connection = await aiosqlite.connect(path)
    await connection.executescript(
        """
        CREATE TABLE pending_captcha (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            message_id INTEGER,
            expires_at REAL NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        );
        """
    )
    await connection.execute(
        "INSERT INTO pending_captcha (chat_id, user_id, message_id, expires_at) "
        "VALUES (?, ?, ?, ?)",
        (CHAT_ID, USER_ID, 77, time.time() + 60),
    )
    await connection.commit()
    await connection.close()

    database = Database(path)
    await database.initialize()
    try:
        row = await database.get_pending_captcha(CHAT_ID, USER_ID)
        assert row is not None
        assert len(row["attempt_id"]) == 16
        assert row["status"] == "pending"
        assert row["legacy"] == 1

        bot = FakeBot(ChatPermissions(can_send_messages=True))
        context = make_context(database, bot)
        update, query = make_update(None, message_id=77)
        await members.on_captcha_callback(update, context)
        assert query.answers == [(messages.CAPTCHA_PASSED, False)]
        assert (await database.get_stats(CHAT_ID))["captcha_passed"] == 1

        replacement = await add_attempt(
            database, expires_at=time.time() + 60, message_id=77
        )
        assert replacement != row["attempt_id"]
        await members.on_captcha_callback(update, context)
        assert query.answers[-1] == (messages.CAPTCHA_EXPIRED, True)
        assert len(bot.restrict_calls) == 1
        assert (await database.get_stats(CHAT_ID))["captcha_passed"] == 1
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_join_db_failure_happens_before_mute(db, monkeypatch):
    async def fail_add(*args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(db, "add_pending_captcha", fail_add)
    bot = FakeBot()
    await members.on_new_members(make_join_update(), make_context(db, bot))
    assert bot.restrict_calls == []
    assert bot.sent_messages == []


@pytest.mark.asyncio
async def test_mute_failure_cleans_up_only_its_attempt(db):
    bot = FakeBot()
    bot.restrict_failures = 1
    await members.on_new_members(make_join_update(), make_context(db, bot))
    assert len(bot.restrict_calls) == 1
    assert bot.sent_messages == []
    assert await db.get_pending_captcha(CHAT_ID, USER_ID) is None


@pytest.mark.asyncio
async def test_prompt_send_failure_is_retried_by_sweeper(db):
    bot = FakeBot(ChatPermissions(can_send_messages=True))
    bot.send_failures = 1
    context = make_context(db, bot)
    await members.on_new_members(make_join_update(), context)

    row = await db.get_pending_captcha(CHAT_ID, USER_ID)
    assert row is not None
    assert row["status"] == "pending"
    assert row["message_id"] is None
    assert len(bot.sent_messages) == 1

    await members.captcha_sweep(context)
    row = await db.get_pending_captcha(CHAT_ID, USER_ID)
    assert row is not None
    assert row["message_id"] is not None
    assert len(bot.sent_messages) == 2


@pytest.mark.asyncio
async def test_prompt_message_id_save_failure_is_retried(db, monkeypatch):
    original_set_message = db.set_pending_captcha_message
    failed = False

    async def fail_once(*args):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("temporary database failure")
        return await original_set_message(*args)

    monkeypatch.setattr(db, "set_pending_captcha_message", fail_once)
    bot = FakeBot()
    context = make_context(db, bot)
    await members.on_new_members(make_join_update(), context)
    assert len(bot.sent_messages) == 1
    assert (await db.get_pending_captcha(CHAT_ID, USER_ID))["message_id"] is None

    await members.captcha_sweep(context)
    assert len(bot.sent_messages) == 2
    row = await db.get_pending_captcha(CHAT_ID, USER_ID)
    assert row is not None
    assert row["message_id"] is not None


@pytest.mark.asyncio
async def test_active_lease_blocks_duplicate_and_stale_lease_retries_after_restart(db):
    attempt_id = await add_attempt(db, expires_at=time.time() + 60)
    now = time.time()
    assert await db.claim_captcha_pass(
        CHAT_ID, USER_ID, attempt_id, now, lease_until=now + 60
    )
    bot = FakeBot(ChatPermissions(can_send_messages=True))
    context = make_context(db, bot)

    await members.captcha_sweep(context)
    assert bot.restrict_calls == []
    assert (await db.get_pending_captcha(CHAT_ID, USER_ID))["status"] == "passing"

    await db._execute(
        "UPDATE pending_captcha SET lease_until = ? "
        "WHERE chat_id = ? AND user_id = ?",
        (time.time() - 1, CHAT_ID, USER_ID),
    )
    path = db.path
    await db.close()
    restarted_db = Database(path)
    await restarted_db.initialize()
    context.application.bot_data["db"] = restarted_db
    try:
        await members.captcha_sweep(context)
        assert len(bot.restrict_calls) == 1
        assert (await restarted_db.get_stats(CHAT_ID))["captcha_passed"] == 1
    finally:
        await restarted_db.close()


@pytest.mark.asyncio
async def test_api_success_finalization_failure_does_not_repeat_unmute(
    db, monkeypatch
):
    attempt_id = await add_attempt(db, expires_at=time.time() + 60)
    bot = FakeBot(ChatPermissions(can_send_messages=True))
    context = make_context(db, bot)
    update, query = make_update(attempt_id, message_id=77)

    async def fail_completion(*args):
        raise RuntimeError("temporary database failure")

    monkeypatch.setattr(db, "complete_captcha_pass", fail_completion)
    await members.on_captcha_callback(update, context)
    assert query.answers == [(messages.CAPTCHA_RETRY, True)]
    row = await db.get_pending_captcha(CHAT_ID, USER_ID)
    assert row is not None
    assert row["status"] == "passed"
    assert (await db.get_stats(CHAT_ID))["captcha_passed"] == 0
    assert len(bot.restrict_calls) == 1

    monkeypatch.undo()
    await members.captcha_sweep(context)
    assert await db.get_pending_captcha(CHAT_ID, USER_ID) is None
    assert (await db.get_stats(CHAT_ID))["captcha_passed"] == 1
    assert len(bot.restrict_calls) == 1


@pytest.mark.asyncio
async def test_api_success_finalization_failure_does_not_repeat_kick(
    db, monkeypatch
):
    await add_attempt(db, expires_at=time.time() - 1)
    bot = FakeBot()
    context = make_context(db, bot)

    async def fail_completion(*args):
        raise RuntimeError("temporary database failure")

    monkeypatch.setattr(db, "complete_captcha_failure", fail_completion)
    await members.captcha_sweep(context)
    row = await db.get_pending_captcha(CHAT_ID, USER_ID)
    assert row is not None
    assert row["status"] == "enforced"
    assert len(bot.ban_calls) == 1
    assert len(bot.unban_calls) == 1
    assert (await db.get_stats(CHAT_ID))["captcha_failed"] == 0

    monkeypatch.undo()
    await members.captcha_sweep(context)
    assert await db.get_pending_captcha(CHAT_ID, USER_ID) is None
    assert (await db.get_stats(CHAT_ID))["captcha_failed"] == 1
    assert len(bot.ban_calls) == 1
    assert len(bot.unban_calls) == 1


@pytest.mark.asyncio
async def test_get_chat_failure_is_retained_and_retried(db):
    attempt_id = await add_attempt(db, expires_at=time.time() + 60)
    bot = FakeBot(ChatPermissions(can_send_messages=True))
    bot.get_chat_failures = 1
    context = make_context(db, bot)
    update, query = make_update(attempt_id, message_id=77)

    await members.on_captcha_callback(update, context)
    assert query.answers == [(messages.CAPTCHA_RETRY, True)]
    assert bot.restrict_calls == []
    assert (await db.get_pending_captcha(CHAT_ID, USER_ID))["status"] == "passing"
    assert (await db.get_stats(CHAT_ID))["captcha_passed"] == 0

    await members.captcha_sweep(context)
    assert len(bot.restrict_calls) == 1
    assert (await db.get_stats(CHAT_ID))["captcha_passed"] == 1
