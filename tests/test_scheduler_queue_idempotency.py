"""PR-F2: недеструктивное чтение очереди + per-recipient идемпотентность.

(B9) Раньше morning flush читал очередь и удалял её в ТОЙ ЖЕ транзакции, commit
     шёл ДО отправки → краш отправки терял сообщения (групповая очередь не
     реконструируется из grade_history). Теперь: читаем без удаления, удаляем
     per-message ТОЛЬКО после подтверждённой отправки.
(B10) evening/morning/weekly ставили job-маркер только после ПОЛНОГО прохода →
     частичный сбой + повторный tick в окне (minute<6, sleep 180) слал сводку
     части родителей повторно. Теперь: per-recipient чек-пойнт в settings.
"""
from unittest.mock import MagicMock

import pytest

import src.database_manager as dbm
import src.schedulers as sched


def _today():
    return sched._get_local_now().date().isoformat()


# ─── B9: недеструктивные DB-функции очереди ───────────────────────────
def test_get_queued_notifications_is_non_destructive(temp_db):
    dbm.queue_notification(111, "m1")
    dbm.queue_notification(111, "m2")

    rows = dbm.get_queued_notifications(111)
    assert [r['message'] for r in rows] == ["m1", "m2"]
    # Повторное чтение всё ещё видит обе — SELECT ничего не удалил.
    assert len(dbm.get_queued_notifications(111)) == 2

    # Удаляем по id только первое.
    dbm.delete_queued_notifications([rows[0]['id']])
    assert [r['message'] for r in dbm.get_queued_notifications(111)] == ["m2"]

    # Пустой список id — no-op.
    assert dbm.delete_queued_notifications([]) == 0


def test_get_queued_group_notifications_is_non_destructive(temp_db):
    dbm.queue_group_notification(-100, 5, "g1")
    dbm.queue_group_notification(-100, 5, "g2")
    dbm.queue_group_notification(-100, None, "no_thread")

    rows = dbm.get_queued_group_notifications(-100, 5)
    assert [r['message'] for r in rows] == ["g1", "g2"]
    # Не удалилось.
    assert len(dbm.get_queued_group_notifications(-100, 5)) == 2
    # Изоляция по thread.
    assert [r['message'] for r in dbm.get_queued_group_notifications(-100, None)] == ["no_thread"]

    dbm.delete_group_notification(rows[0]['id'])
    assert [r['message'] for r in dbm.get_queued_group_notifications(-100, 5)] == ["g2"]


# ─── B9: morning flush не теряет очередь при сбое отправки ─────────────
def test_morning_flush_send_failure_keeps_queue(temp_db):
    """Краш отправки → очередь НЕ удалена, per-recipient маркер НЕ поставлен →
    следующий tick повторит."""
    dbm.queue_notification(5555, "night msg")  # нет учеников → fallback-путь

    fake_bot = MagicMock()
    fake_bot.send_message.side_effect = Exception("telegram down")
    sched._bot = fake_bot

    sched._flush_quiet_hours_queue()

    # Сообщение осталось в очереди.
    assert len(dbm.get_queued_notifications(5555)) == 1
    # Маркер не выставлен.
    assert not sched._recipient_already_sent('morning', 5555, _today())


def test_morning_flush_success_deletes_and_no_duplicate(temp_db):
    """Успешная отправка → очередь удалена, маркер поставлен, повторный запуск
    не шлёт дубль."""
    dbm.queue_notification(5556, "night msg")

    fake_bot = MagicMock()  # send_message не кидает → доставлено
    sched._bot = fake_bot

    sched._flush_quiet_hours_queue()

    assert dbm.get_queued_notifications(5556) == []
    assert sched._recipient_already_sent('morning', 5556, _today())

    # Повторный tick: очередь пуста + маркер → ни одной отправки.
    fake_bot.reset_mock()
    sched._flush_quiet_hours_queue()
    fake_bot.send_message.assert_not_called()


def test_morning_group_flush_send_failure_keeps_queue(temp_db):
    """Групповое сообщение при сбое отправки остаётся в очереди (не теряется —
    его нельзя реконструировать из grade_history)."""
    # Нужна непустая ЛИЧНАЯ очередь, иначе flush выходит раньше группового прохода.
    dbm.queue_notification(7001, "personal")
    dbm.queue_group_notification(-1001, None, "group msg")

    fake_bot = MagicMock()
    fake_bot.send_message.side_effect = Exception("down")
    sched._bot = fake_bot

    sched._flush_quiet_hours_queue()

    # Групповое сообщение уцелело.
    assert len(dbm.get_queued_group_notifications(-1001, None)) == 1


# ─── B10: per-recipient идемпотентность evening summary ───────────────
@pytest.fixture
def evening_parent(temp_db):
    """Родитель с ребёнком и сегодняшней оценкой (чтобы сводка была непустой)."""
    head_id = dbm.add_parent("Head", "998900006666", role='senior')
    dbm.update_parent_telegram_id("998900006666", 6666)
    fam_id = dbm.add_family("F-evening")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    sid = dbm.add_student("Kid", "ss-evening", display_name="Kid")
    dbm.link_student_to_family(fam_id, sid)
    dbm.add_grade(sid, "Математика", 5.0, "5", "ref-ev",
                  grade_date=_today(), notify_pending=False)
    return {'tg_id': 6666, 'student_id': sid}


def _init_sender(bot):
    from src.notifications import init_sender
    init_sender(bot)


def test_evening_skips_already_marked_recipient(evening_parent):
    """Если per-recipient маркер уже стоит (частичный сбой прошлого прогона) —
    вечернюю сводку повторно НЕ шлём."""
    info = evening_parent
    sched._mark_recipient_sent('evening', info['tg_id'], _today())

    fake_bot = MagicMock()
    sched._bot = fake_bot
    _init_sender(fake_bot)

    sched._send_daily_evening_summary()
    fake_bot.send_message.assert_not_called()


def test_evening_marks_and_no_double_send(evening_parent):
    """Первый прогон шлёт и ставит маркер; второй прогон в том же окне — молчит."""
    info = evening_parent
    fake_bot = MagicMock()
    sched._bot = fake_bot
    _init_sender(fake_bot)

    sched._send_daily_evening_summary()
    assert fake_bot.send_message.call_count == 1
    assert sched._recipient_already_sent('evening', info['tg_id'], _today())

    fake_bot.reset_mock()
    sched._send_daily_evening_summary()
    fake_bot.send_message.assert_not_called()


# ─── B10: helper-функции маркеров ─────────────────────────────────────
def test_recipient_marker_roundtrip(temp_db):
    day = _today()
    assert not sched._recipient_already_sent('weekly_text_digest', 42, day)
    sched._mark_recipient_sent('weekly_text_digest', 42, day)
    assert sched._recipient_already_sent('weekly_text_digest', 42, day)
    # Другой день — маркер не срабатывает (значение = день).
    assert not sched._recipient_already_sent('weekly_text_digest', 42, "1999-01-01")
    # Другой job / другой адресат — независимы.
    assert not sched._recipient_already_sent('evening', 42, day)
    assert not sched._recipient_already_sent('weekly_text_digest', 99, day)
