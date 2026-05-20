"""Очередь групповых уведомлений для тихих часов.

Появилась после инцидента 2026-05-21: группы НЕ уважали тихие часы, любой
баг в дедупе превращался в спам в семейный чат. PR #42 — drop в тихие часы.
Этот модуль — proper queue: пишем ночью, флэшим в 07:00.
"""
from unittest.mock import patch, MagicMock

import pytest

import src.database_manager as dbm
import src.monitor_engine as me


# ─── Unit: db helpers ─────────────────────────────────────────────────
def test_queue_and_flush_basic(temp_db):
    """queue_group_notification → get_and_clear возвращает в порядке вставки."""
    dbm.queue_group_notification(-1001, 5, "msg1")
    dbm.queue_group_notification(-1001, 5, "msg2")

    msgs = dbm.get_and_clear_queued_group_notifications(-1001, 5)
    assert msgs == ["msg1", "msg2"]

    # После flush очередь пуста
    assert dbm.get_and_clear_queued_group_notifications(-1001, 5) == []


def test_queue_isolation_by_thread(temp_db):
    """Один chat_id с разными thread'ами — изолированные очереди."""
    dbm.queue_group_notification(-1001, 5, "thread5")
    dbm.queue_group_notification(-1001, 7, "thread7")
    dbm.queue_group_notification(-1001, None, "no_thread")

    assert dbm.get_and_clear_queued_group_notifications(-1001, 5) == ["thread5"]
    assert dbm.get_and_clear_queued_group_notifications(-1001, 7) == ["thread7"]
    assert dbm.get_and_clear_queued_group_notifications(-1001, None) == ["no_thread"]


def test_queue_targets_distinct(temp_db):
    """get_all_queued_group_targets возвращает distinct пары."""
    dbm.queue_group_notification(-1001, 5, "a")
    dbm.queue_group_notification(-1001, 5, "b")  # тот же target
    dbm.queue_group_notification(-1002, None, "c")  # другой chat

    targets = dbm.get_all_queued_group_targets()
    assert len(targets) == 2
    pairs = {(t['chat_id'], t['message_thread_id']) for t in targets}
    assert pairs == {(-1001, 5), (-1002, None)}


# ─── Integration: _send_to_groups_for_student в тихие часы ────────────
@pytest.fixture
def setup_group_for_student(temp_db):
    """Создаёт семью с групповым чатом и одним учеником."""
    head_id = dbm.add_parent("Head", "998900001234", role='senior')
    dbm.update_parent_telegram_id("998900001234", 12345)
    fam_id = dbm.add_family("F-group")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    student_id = dbm.add_student("Kid", "ss-group")
    dbm.link_student_to_family(fam_id, student_id)

    # Привязываем групповой чат
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            'INSERT INTO family_groups (family_id, chat_id, chat_title, '
            'message_thread_id, added_by) VALUES (?, ?, ?, ?, ?)',
            (fam_id, -1009999, 'Test Group', 42, head_id),
        )

    return {'student_id': student_id, 'tg_id': 12345,
            'chat_id': -1009999, 'thread_id': 42}


def test_group_message_queued_during_quiet_hours(setup_group_for_student, monkeypatch):
    """В тихие часы _send_to_groups_for_student пишет в queue, не вызывает bot.send."""
    info = setup_group_for_student

    fake_bot = MagicMock()
    me._bot = fake_bot

    with patch('src.monitor_engine.is_quiet_hours', return_value=True):
        me._send_to_groups_for_student(
            info['student_id'], "test msg", inline_markup=None,
            parent_tg_ids=[info['tg_id']],
        )

    fake_bot.send_message.assert_not_called()

    queued = dbm.get_and_clear_queued_group_notifications(info['chat_id'], info['thread_id'])
    assert queued == ["test msg"]


def test_group_message_sent_outside_quiet_hours(setup_group_for_student, monkeypatch):
    """Вне тихих часов — обычная отправка через bot.send_message, очередь не трогается."""
    info = setup_group_for_student

    fake_bot = MagicMock()
    me._bot = fake_bot

    with patch('src.monitor_engine.is_quiet_hours', return_value=False):
        me._send_to_groups_for_student(
            info['student_id'], "test msg", inline_markup=None,
            parent_tg_ids=[info['tg_id']],
        )

    assert fake_bot.send_message.called
    call = fake_bot.send_message.call_args
    assert call.args[0] == info['chat_id']
    assert call.args[1] == "test msg"
    assert call.kwargs.get('message_thread_id') == info['thread_id']

    # Очередь пуста — не должны были туда писать
    assert dbm.get_all_queued_group_targets() == []
