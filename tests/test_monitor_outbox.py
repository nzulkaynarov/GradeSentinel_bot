"""PR-F1: persistent outbox для уведомлений об оценках.

Проблема (до PR-F1): monitor писал оценку в grade_history, а батч уведомлений
слал ОТДЕЛЬНЫМИ проходами в конце цикла всех студентов. Exception в фазе отправки
→ оценки в БД, уведомление НЕ ушло, а на следующем цикле diff пуст (old==new) →
уведомление терялось навсегда.

Фикс: grade_history.notified_at. Отправка идёт сразу после записи по студенту;
notified_at проставляется только на доставленное; sweeper в начале цикла добивает
notified_at IS NULL.

Тесты:
  1. crash между записью и отправкой → строка с notified_at IS NULL → sweeper
     дошлёт РОВНО один раз на следующем цикле, дальше не дублирует;
  2. happy-path: отправлено → notified_at проставлен → повторный цикл без дубля;
  3. тихие часы: оценка → notification_queue, notified_at проставлен, дубля нет;
  4. групповой путь: группа получает сообщение, notified_at проставлен.
"""
import time
from unittest.mock import patch, MagicMock

import pytest

import src.database_manager as dbm
import src.monitor_engine as me


def _today_tashkent():
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date().isoformat()


@pytest.fixture(autouse=True)
def _reset_monitor_state():
    """Изолируем in-memory состояние monitor'а между тестами."""
    me._pending_grades.clear()
    me._student_failure_counts.clear()
    me._last_failure_alert.clear()
    yield
    me._pending_grades.clear()
    me._student_failure_counts.clear()
    me._last_failure_alert.clear()


@pytest.fixture
def student_with_parent(temp_db):
    """Активная семья с подпиской, один родитель (telegram_id), один ученик
    с display_name (чтобы monitor не дёргал get_spreadsheet_title)."""
    head_id = dbm.add_parent("Head", "998900000222", role='senior')
    dbm.update_parent_telegram_id("998900000222", 222222)
    fam_id = dbm.add_family("F-outbox")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    student_id = dbm.add_student("Kid FIO", "ss-outbox", display_name="Ученик")
    dbm.link_student_to_family(fam_id, student_id)

    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)).strftime('%Y-%m-%d')
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "UPDATE families SET subscription_end = %s WHERE id = %s",
            (future, fam_id),
        )
    return {'student_id': student_id, 'tg_id': 222222, 'spreadsheet_id': 'ss-outbox'}


def _seed_pending(student_id, subject, raw_text):
    """Кладём оценку сразу в «подтверждённое» состояние (обход двухфазного
    дебаунса), чтобы ближайший цикл записал её и попытался отправить."""
    me._pending_grades[(student_id, subject, _today_tashkent())] = {
        'raw_text': raw_text, 'first_seen': time.time(),
    }


def _run_cycle(grade_pairs):
    """Прогоняет один monitor-цикл с замоканным Sheets-слоем.
    grade_pairs — то, что «сегодня» в листе «Все оценки» ([(subject, raw)])."""
    with patch('src.monitor_engine.get_sheet_data', return_value=[["dummy"]]), \
         patch('src.history_importer._parse_master_sheet_for_date', return_value=grade_pairs):
        me._check_for_new_grades_impl()


def _grade_row(student_id, subject):
    with dbm.get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT raw_text, notified_at FROM grade_history "
            "WHERE student_id = %s AND subject = %s ORDER BY id DESC LIMIT 1",
            (student_id, subject),
        )
        return cur.fetchone()


# ─── 1. Crash между записью и отправкой → sweeper дошлёт ровно раз ─────
def test_crash_between_write_and_send_recovered_once(student_with_parent):
    info = student_with_parent
    sid = info['student_id']

    # Cycle 1: оценка подтверждена, но отправка КРАШИТ (exception в фазе send).
    _seed_pending(sid, "Математика", "5")
    with patch('src.monitor_engine.send_notification',
               side_effect=RuntimeError("boom")), \
         patch('src.monitor_engine._send_to_groups_for_student'):
        with pytest.raises(RuntimeError):
            _run_cycle([("Математика", "5")])

    # Оценка записана, но НЕ доставлена (в outbox).
    row = _grade_row(sid, "Математика")
    assert row['raw_text'] == "5"
    assert row['notified_at'] is None

    # Cycle 2: send теперь работает. Sweeper в начале цикла дошлёт.
    send_mock = MagicMock(return_value=True)
    with patch('src.monitor_engine.send_notification', send_mock), \
         patch('src.monitor_engine._send_to_groups_for_student'):
        _run_cycle([("Математика", "5")])

    # Ровно одна отправка (sweeper), адресат — наш родитель.
    assert send_mock.call_count == 1
    assert send_mock.call_args.args[0] == [info['tg_id']]
    # notified_at проставлен.
    assert _grade_row(sid, "Математика")['notified_at'] is not None

    # Cycle 3: outbox пуст, diff тоже пуст → НИ одной отправки.
    send_mock2 = MagicMock(return_value=True)
    with patch('src.monitor_engine.send_notification', send_mock2), \
         patch('src.monitor_engine._send_to_groups_for_student'):
        _run_cycle([("Математика", "5")])
    assert send_mock2.call_count == 0


# ─── 2. Happy-path: доставлено → notified_at → без дубля ──────────────
def test_happy_path_marks_notified_no_duplicate(student_with_parent):
    info = student_with_parent
    sid = info['student_id']

    _seed_pending(sid, "Физика", "4")
    send_mock = MagicMock(return_value=True)
    with patch('src.monitor_engine.send_notification', send_mock), \
         patch('src.monitor_engine._send_to_groups_for_student'):
        _run_cycle([("Физика", "4")])

    # Отправлено ровно раз, notified_at проставлен.
    assert send_mock.call_count == 1
    assert _grade_row(sid, "Физика")['notified_at'] is not None

    # Повторный цикл: тот же лист → old==new, outbox пуст → без отправки.
    send_mock2 = MagicMock(return_value=True)
    with patch('src.monitor_engine.send_notification', send_mock2), \
         patch('src.monitor_engine._send_to_groups_for_student'):
        _run_cycle([("Физика", "4")])
    assert send_mock2.call_count == 0


# ─── 3. Тихие часы: очередь + notified_at, без дубля ──────────────────
def test_quiet_hours_queues_and_marks_notified(student_with_parent):
    info = student_with_parent
    sid = info['student_id']

    fake_bot = MagicMock()
    me._bot = fake_bot
    from src.notifications import init_sender
    init_sender(fake_bot)

    _seed_pending(sid, "Химия", "3")
    with patch('src.notifications.sender.is_quiet_hours', return_value=True), \
         patch('src.monitor_engine._send_to_groups_for_student'):
        _run_cycle([("Химия", "3")])

    # В тихие часы личное уведомление легло в notification_queue (не bot.send).
    fake_bot.send_message.assert_not_called()
    queued = dbm.get_and_clear_queued_notifications(info['tg_id'])
    assert len(queued) == 1

    # Постановка в очередь = доставлено → notified_at проставлен.
    assert _grade_row(sid, "Химия")['notified_at'] is not None

    # Повторный цикл в тихие часы → outbox пуст, diff пуст → ничего не копится.
    with patch('src.notifications.sender.is_quiet_hours', return_value=True), \
         patch('src.monitor_engine._send_to_groups_for_student'):
        _run_cycle([("Химия", "3")])
    assert dbm.get_and_clear_queued_notifications(info['tg_id']) == []


# ─── 4. Групповой путь: группа получает + notified_at ─────────────────
@pytest.fixture
def student_with_group(student_with_parent):
    """К student_with_parent добавляем семейный групповой чат."""
    info = student_with_parent
    # Находим family_id ученика.
    with dbm.get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT family_id FROM family_links WHERE student_id = %s AND family_id IS NOT NULL LIMIT 1",
            (info['student_id'],),
        )
        fam_id = cur.fetchone()['family_id']
        cur.execute("SELECT id FROM parents WHERE telegram_id = %s", (info['tg_id'],))
        head_id = cur.fetchone()['id']
        cur.execute(
            'INSERT INTO family_groups (family_id, chat_id, chat_title, '
            'message_thread_id, added_by) VALUES (%s, %s, %s, %s, %s)',
            (fam_id, -1007777, 'Family Chat', None, head_id),
        )
    info['chat_id'] = -1007777
    return info


def test_group_path_delivers_and_marks_notified(student_with_group):
    info = student_with_group
    sid = info['student_id']

    fake_bot = MagicMock()
    me._bot = fake_bot
    from src.notifications import init_sender
    init_sender(fake_bot)

    _seed_pending(sid, "История", "5")
    # Вне тихих часов: и родитель, и группа получают сообщение через Sender.
    with patch('src.notifications.sender.is_quiet_hours', return_value=False):
        _run_cycle([("История", "5")])

    # Группа получила сообщение (bot.send_message в групповой chat_id).
    group_calls = [c for c in fake_bot.send_message.call_args_list
                   if c.args and c.args[0] == info['chat_id']]
    assert len(group_calls) == 1

    # notified_at проставлен (личная доставка подтверждена).
    assert _grade_row(sid, "История")['notified_at'] is not None
