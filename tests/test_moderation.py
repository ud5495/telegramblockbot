from bot.moderation import SpamTracker, choose_action


def test_delete_action_stays_delete():
    assert choose_action("delete", 1, 3) == "delete"
    assert choose_action("delete", 99, 3) == "delete"


def test_warn_escalates_to_mute():
    assert choose_action("warn", 1, 3) == "warn"
    assert choose_action("warn", 2, 3) == "warn"
    assert choose_action("warn", 3, 3) == "mute"
    assert choose_action("warn", 4, 3) == "mute"


def test_mute_and_ban_are_used_as_is():
    assert choose_action("mute", 1, 3) == "mute"
    assert choose_action("ban", 1, 3) == "ban"


def test_unknown_action_falls_back_to_delete():
    assert choose_action("nuke", 1, 3) == "delete"


def test_flood_detection():
    tracker = SpamTracker(max_messages=2, window_seconds=60)
    assert tracker.check(1, 1, "a") is None
    assert tracker.check(1, 1, "b") is None
    assert tracker.check(1, 1, "c") == "flood"


def test_duplicate_detection():
    tracker = SpamTracker(max_messages=100, window_seconds=60, duplicate_threshold=3)
    assert tracker.check(1, 1, "same") is None
    assert tracker.check(1, 1, "same") is None
    assert tracker.check(1, 1, "same") == "duplicate"


def test_different_users_are_tracked_separately():
    tracker = SpamTracker(max_messages=1, window_seconds=60)
    assert tracker.check(1, 1, "a") is None
    assert tracker.check(1, 2, "a") is None


def test_reset_clears_state():
    tracker = SpamTracker(max_messages=1, window_seconds=60)
    assert tracker.check(1, 1, "a") is None
    tracker.reset(1, 1)
    assert tracker.check(1, 1, "a") is None
