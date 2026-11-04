from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
from telegram.constants import ChatType
from telegram.error import TelegramError

from bot import messages
from bot.handlers.messages import on_message

CHAT_ID = 100
USER_ID = 200
AUDIT_CHAT_ID = 500


def _case(*, action="delete", spam=None, violation_count=1):
    message = SimpleNamespace(
        message_id=10,
        text="bad message",
        caption=None,
        sender_chat=None,
        delete=AsyncMock(),
    )
    chat = SimpleNamespace(id=CHAT_ID, title="Test chat", type=ChatType.GROUP)
    user = SimpleNamespace(
        id=USER_ID,
        is_bot=False,
        username="tester",
        mention_html=Mock(return_value="Tester"),
    )
    db = SimpleNamespace(
        ensure_chat=AsyncMock(),
        get_chat=AsyncMock(return_value={"enabled": True, "action": action}),
        is_whitelisted=AsyncMock(return_value=False),
        list_whitelist=AsyncMock(return_value=[]),
        record_violation=AsyncMock(return_value=violation_count),
        set_muted_until=AsyncMock(),
        increment_stat=AsyncMock(),
    )
    bot = SimpleNamespace(
        send_message=AsyncMock(
            return_value=SimpleNamespace(chat_id=CHAT_ID, message_id=999)
        ),
        restrict_chat_member=AsyncMock(),
        ban_chat_member=AsyncMock(),
    )
    settings = SimpleNamespace(
        default_action=action,
        captcha_enabled=False,
        violation_decay_seconds=3600,
        warn_limit=3,
        mute_seconds=60,
        audit_chat_id=AUDIT_CHAT_ID,
    )
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "settings": settings,
                "db": db,
                "admin_cache": SimpleNamespace(is_admin=AsyncMock(return_value=False)),
                "spam_tracker": SimpleNamespace(check=Mock(return_value=spam)),
            }
        ),
        bot=bot,
        job_queue=None,
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=chat,
        effective_user=user,
    )
    return SimpleNamespace(update=update, context=context, message=message, db=db, bot=bot)


async def _run(case):
    finding = SimpleNamespace(as_reason=Mock(return_value="url"))
    with patch(
        "bot.handlers.messages.detectors.detect_message", return_value=[finding]
    ):
        await on_message(case.update, case.context)


def _audit_text(case):
    for audit_call in case.bot.send_message.await_args_list:
        if audit_call.args and audit_call.args[0] == AUDIT_CHAT_ID:
            return audit_call.args[1]
    raise AssertionError("No audit message was sent")


def _warning_text(case):
    for message_call in case.bot.send_message.await_args_list:
        if message_call.kwargs.get("parse_mode") == "HTML":
            return message_call.kwargs["text"]
    raise AssertionError("No warning message was sent")


@pytest.mark.asyncio
async def test_duplicate_deletion_failure_is_audited_without_deleted_stat():
    case = _case(spam="duplicate")
    case.message.delete.side_effect = TelegramError("delete denied")

    await _run(case)

    case.db.record_violation.assert_not_awaited()
    case.db.increment_stat.assert_not_awaited()
    case.bot.restrict_chat_member.assert_not_awaited()
    assert "Duplicate message could not be removed" in _audit_text(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["delete", "warn", "mute", "ban"])
async def test_deletion_failure_still_records_violation_and_enforces(action):
    case = _case(action=action)
    case.message.delete.side_effect = TelegramError("delete denied")

    await _run(case)

    case.db.record_violation.assert_awaited_once_with(CHAT_ID, USER_ID, 3600)
    expected_stats = {
        "delete": [],
        "warn": [call(CHAT_ID, "warned")],
        "mute": [call(CHAT_ID, "muted")],
        "ban": [call(CHAT_ID, "banned")],
    }
    assert case.db.increment_stat.await_args_list == expected_stats[action]
    assert case.bot.restrict_chat_member.await_count == int(action == "mute")
    assert case.bot.ban_chat_member.await_count == int(action == "ban")
    assert case.db.set_muted_until.await_count == int(action == "mute")

    if action == "warn":
        warning_text = _warning_text(case)
        assert warning_text == messages.WARNING_NOT_DELETED.format(
            name="Tester", count=1
        )
        assert "removed" not in warning_text

    audit_text = _audit_text(case)
    assert "Could not remove message" in audit_text
    assert "deletion=failed" in audit_text
    expected_enforcement = "failed" if action == "delete" else "success"
    assert f"enforcement={expected_enforcement}" in audit_text


@pytest.mark.asyncio
async def test_duplicate_counts_one_deletion_without_a_violation():
    case = _case(spam="duplicate")

    await _run(case)

    assert case.db.increment_stat.await_args_list == [call(CHAT_ID, "deleted")]
    case.db.record_violation.assert_not_awaited()
    case.bot.restrict_chat_member.assert_not_awaited()
    assert "Duplicate message removed" in _audit_text(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_fails", [False, True])
@pytest.mark.parametrize("mute_fails", [False, True])
async def test_flood_audits_deletion_and_mute_outcomes_independently(
    delete_fails, mute_fails
):
    case = _case(spam="flood")
    if delete_fails:
        case.message.delete.side_effect = TelegramError("delete denied")
    if mute_fails:
        case.bot.restrict_chat_member.side_effect = TelegramError("mute denied")

    await _run(case)

    expected_stats = []
    if not delete_fails:
        expected_stats.append(call(CHAT_ID, "deleted"))
    if not mute_fails:
        expected_stats.append(call(CHAT_ID, "muted"))
    assert case.db.increment_stat.await_args_list == expected_stats
    case.bot.restrict_chat_member.assert_awaited_once()
    assert case.db.set_muted_until.await_count == int(not mute_fails)
    case.db.record_violation.assert_not_awaited()
    audit_text = _audit_text(case)
    deletion_text = "removed message" if not delete_fails else "could not remove message"
    assert f"Anti-flood: {deletion_text}" in audit_text
    assert f"deletion={'failed' if delete_fails else 'success'}" in audit_text
    assert f"enforcement={'failed' if mute_fails else 'success'}" in audit_text


@pytest.mark.asyncio
async def test_delete_action_counts_one_deletion():
    case = _case(action="delete")

    await _run(case)

    assert case.db.increment_stat.await_args_list == [call(CHAT_ID, "deleted")]
    case.db.record_violation.assert_awaited_once_with(CHAT_ID, USER_ID, 3600)
    assert "enforcement=success" in _audit_text(case)


@pytest.mark.asyncio
@pytest.mark.parametrize("warning_fails", [False, True])
async def test_warning_stat_and_audit_follow_delivery(warning_fails):
    case = _case(action="warn")

    async def deliver_message(*args, **kwargs):
        if kwargs.get("parse_mode") == "HTML":
            assert call(CHAT_ID, "warned") not in case.db.increment_stat.await_args_list
            if warning_fails:
                raise TelegramError("warning denied")
        return SimpleNamespace(chat_id=CHAT_ID, message_id=999)

    case.bot.send_message.side_effect = deliver_message

    await _run(case)

    expected_stats = [call(CHAT_ID, "deleted")]
    if not warning_fails:
        expected_stats.append(call(CHAT_ID, "warned"))
    assert case.db.increment_stat.await_args_list == expected_stats
    audit_text = _audit_text(case)
    assert "Removed message" in audit_text
    assert f"enforcement={'failed' if warning_fails else 'success'}" in audit_text


@pytest.mark.asyncio
@pytest.mark.parametrize("mute_fails", [False, True])
async def test_mute_stat_and_audit_follow_api_outcome(mute_fails):
    case = _case(action="mute")
    if mute_fails:
        case.bot.restrict_chat_member.side_effect = TelegramError("mute denied")

    await _run(case)

    expected_stats = [call(CHAT_ID, "deleted")]
    if not mute_fails:
        expected_stats.append(call(CHAT_ID, "muted"))
    assert case.db.increment_stat.await_args_list == expected_stats
    assert case.db.set_muted_until.await_count == int(not mute_fails)
    audit_text = _audit_text(case)
    assert "Removed message" in audit_text
    assert f"enforcement={'failed' if mute_fails else 'success'}" in audit_text


@pytest.mark.asyncio
@pytest.mark.parametrize("ban_fails", [False, True])
async def test_ban_stat_and_audit_follow_api_outcome(ban_fails):
    case = _case(action="ban")
    if ban_fails:
        case.bot.ban_chat_member.side_effect = TelegramError("ban denied")

    await _run(case)

    expected_stats = [call(CHAT_ID, "deleted")]
    if not ban_fails:
        expected_stats.append(call(CHAT_ID, "banned"))
    assert case.db.increment_stat.await_args_list == expected_stats
    audit_text = _audit_text(case)
    assert "Removed message" in audit_text
    assert f"enforcement={'failed' if ban_fails else 'success'}" in audit_text
