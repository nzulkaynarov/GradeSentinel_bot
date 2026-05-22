"""Unit-тесты unified Sender (notifications/sender.py)."""
from unittest.mock import MagicMock, patch

import pytest

from src.notifications import NotificationType, init_sender, get_sender
from src.notifications.quiet_hours import should_defer
import src.database_manager as dbm


@pytest.fixture
def sender_with_bot(temp_db):
    """Sender, инициализированный с MagicMock-bot. send_message успешен."""
    fake = MagicMock()
    init_sender(fake)
    return get_sender(), fake


# ─── quiet_hours policy ──────────────────────────────────────────────


def test_should_defer_grade_events():
    assert should_defer(NotificationType.GRADE_INSTANT)
    assert should_defer(NotificationType.GRADE_GROUP)
    assert should_defer(NotificationType.QUARTER_GRADE)
    assert should_defer(NotificationType.PROACTIVE_ALERT)


def test_should_NOT_defer_admin_and_summaries():
    """Admin alerts и daily summaries отправляются даже в тихие часы:
    либо сами в active window, либо требуют срочности."""
    assert not should_defer(NotificationType.SHEET_FAILURE)
    assert not should_defer(NotificationType.EVENING_SUMMARY)
    assert not should_defer(NotificationType.WEEKLY_DIGEST)
    assert not should_defer(NotificationType.BOT_ALIVE)
    assert not should_defer(NotificationType.PAYMENT_SUCCESS)


# ─── send: quiet hours queue ─────────────────────────────────────────


def test_send_queues_during_quiet_hours_for_deferrable_type(sender_with_bot, monkeypatch):
    sender, bot = sender_with_bot
    with patch('src.notifications.sender.is_quiet_hours', return_value=True):
        ok = sender.send(99999, "msg", ntype=NotificationType.GRADE_INSTANT)
    assert ok
    bot.send_message.assert_not_called()
    # Подёргиваем dbm: msg попал в notification_queue
    msgs = dbm.get_and_clear_queued_notifications(99999)
    assert "msg" in msgs


def test_send_skips_queue_for_admin_alerts_even_during_quiet(sender_with_bot):
    sender, bot = sender_with_bot
    with patch('src.notifications.sender.is_quiet_hours', return_value=True):
        ok = sender.send(99999, "alert", ntype=NotificationType.SHEET_FAILURE)
    assert ok
    bot.send_message.assert_called_once()


# ─── notify_mode = summary_only ──────────────────────────────────────


def test_send_skips_summary_only_user(sender_with_bot, temp_db):
    sender, bot = sender_with_bot
    pid = dbm.add_parent("Q", "998900112233", role='senior')
    dbm.update_parent_telegram_id("998900112233", 88888)
    dbm.set_notify_mode(88888, 'summary_only')

    with patch('src.notifications.sender.is_quiet_hours', return_value=False):
        ok = sender.send(88888, "msg", ntype=NotificationType.GRADE_INSTANT)
    assert ok  # skip = treated as success (не ошибка)
    bot.send_message.assert_not_called()


def test_send_force_overrides_summary_only(sender_with_bot, temp_db):
    sender, bot = sender_with_bot
    pid = dbm.add_parent("Q", "998900112244", role='senior')
    dbm.update_parent_telegram_id("998900112244", 77777)
    dbm.set_notify_mode(77777, 'summary_only')

    with patch('src.notifications.sender.is_quiet_hours', return_value=False):
        sender.send(77777, "msg", ntype=NotificationType.QUARTER_GRADE, force=True)
    bot.send_message.assert_called_once()


# ─── send_to_admin ──────────────────────────────────────────────────


def test_send_to_admin_uses_env_admin_id(sender_with_bot, monkeypatch):
    sender, bot = sender_with_bot
    monkeypatch.setenv("ADMIN_ID", "424242")
    sender.send_to_admin("alert", ntype=NotificationType.SHEET_FAILURE)
    bot.send_message.assert_called_once()
    assert bot.send_message.call_args.args[0] == 424242


def test_send_to_admin_skips_if_admin_id_invalid(sender_with_bot, monkeypatch):
    sender, bot = sender_with_bot
    monkeypatch.setenv("ADMIN_ID", "0")
    ok = sender.send_to_admin("alert", ntype=NotificationType.SHEET_FAILURE)
    assert not ok
    bot.send_message.assert_not_called()


# ─── send_to_group ──────────────────────────────────────────────────


def test_send_to_group_passes_thread_id(sender_with_bot):
    sender, bot = sender_with_bot
    with patch('src.notifications.sender.is_quiet_hours', return_value=False):
        sender.send_to_group(-1009999, 42, "msg", ntype=NotificationType.GRADE_GROUP)
    bot.send_message.assert_called_once()
    kwargs = bot.send_message.call_args.kwargs
    assert kwargs.get("message_thread_id") == 42


def test_send_to_group_queues_in_quiet_hours(sender_with_bot):
    sender, bot = sender_with_bot
    with patch('src.notifications.sender.is_quiet_hours', return_value=True):
        ok = sender.send_to_group(-1008888, 5, "night msg", ntype=NotificationType.GRADE_GROUP)
    assert ok
    bot.send_message.assert_not_called()
    queued = dbm.get_and_clear_queued_group_notifications(-1008888, 5)
    assert queued == ["night msg"]
