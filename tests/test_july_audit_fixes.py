"""Три находки аудита 2026-07-13, дожившие до сентября.

B-H1  Групповая очередь не выливалась у семей, получающих уведомления только в
      общий чат: ранний выход из `_flush_quiet_hours_queue` при пустой личной
      очереди. Плюс сама очередь не чистилась по таймауту вообще.
B-H2/H3 Еженедельный AI-отчёт крутился ВТОРЫМ планировщиком в handlers/analytics:
      своё `while True`, серверное время вместо ташкентского и ни одного маркера —
      рестарт бота в воскресенье около 19:00 рассылал отчёт повторно.
A-H1  `get_families_for_student` без ORDER BY, а вызывающий брал `[0]`: у ребёнка
      в двух семьях история AI-чата скакала между ними.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm  # noqa: E402
import src.schedulers as sch  # noqa: E402


# ─── B-H1: групповая очередь ─────────────────────────────────────────
def _queue_group(chat_id, message, thread_id=None, age_hours=0):
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO group_notification_queue (chat_id, message_thread_id, message, "
            "created_at) VALUES (%s, %s, %s, (now() at time zone 'utc') - %s * interval '1 hour')",
            (chat_id, thread_id, message, age_hours),
        )


def _group_queue_size():
    with dbm.get_db_connection() as conn:
        return conn.cursor().execute(
            "SELECT COUNT(*) c FROM group_notification_queue").fetchone()["c"]


def test_group_queue_flushes_without_personal_queue(temp_db):
    """Семья получает уведомления только в общий чат: личная очередь пуста,
    групповая обязана вылиться. Раньше ранний выход оставлял её навсегда."""
    _queue_group(-100500, "Новая оценка")
    bot = MagicMock()

    with patch.object(sch, "_bot", bot):
        sch._flush_quiet_hours_queue()

    assert bot.send_message.called
    assert _group_queue_size() == 0


def test_group_queue_still_flushes_alongside_personal(temp_db):
    """Личная очередь непустая — групповая по-прежнему разбирается."""
    parent_id = dbm.add_parent("Head", "998900000777", role='senior')
    dbm.update_parent_telegram_id("998900000777", 777777)
    dbm.queue_notification(777777, "Личное сообщение")
    _queue_group(-100501, "Групповое сообщение")
    bot = MagicMock()

    with patch.object(sch, "_bot", bot):
        sch._flush_quiet_hours_queue()

    assert _group_queue_size() == 0


def test_group_queue_cleanup_drops_stale_rows(temp_db):
    """Сообщение в чат, куда бот больше не может писать, не висит вечно."""
    _queue_group(-100502, "Свежее", age_hours=1)
    _queue_group(-100503, "Протухшее", age_hours=100)

    removed = dbm.cleanup_old_group_notification_queue(hours=48)

    assert removed == 1
    assert _group_queue_size() == 1


# ─── B-H2/H3: еженедельный AI-отчёт ──────────────────────────────────
def test_weekly_ai_report_has_lock_and_runs_through_scheduler():
    """Джоб зарегистрирован в общем планировщике: лок есть, маркер ведётся
    там же, время — ташкентское (у планировщика единый `_get_local_now`)."""
    assert 'weekly_ai_report' in sch._job_locks

    with patch("src.handlers.analytics.send_weekly_reports") as send:
        sch._send_weekly_ai_reports()

    send.assert_called_once()


def test_analytics_no_longer_owns_a_scheduler_loop():
    """Второго планировщика больше нет — иначе дубли вернутся."""
    import src.handlers.analytics as analytics

    assert not hasattr(analytics, "_weekly_loop")
    source = open(os.path.join(ROOT, "src", "handlers", "analytics.py"),
                  encoding="utf-8").read()
    assert "while True" not in source


def test_weekly_job_marker_prevents_second_run(temp_db):
    """Повторный запуск в тот же день не рассылает второй раз."""
    sch._marker_cache.clear()
    calls = []

    sch._run_job_safe('weekly_ai_report', '2026-09-06', lambda: calls.append(1))
    sch._run_job_safe('weekly_ai_report', '2026-09-06', lambda: calls.append(1))

    assert len(calls) == 1


# ─── A-H1: детерминированная семья ───────────────────────────────────
def _family_with_student(name, phone, tg_id, student_id=None):
    parent_id = dbm.add_parent(name, phone, role='senior')
    dbm.update_parent_telegram_id(phone, tg_id)
    fam_id = dbm.add_family(name)
    dbm.set_family_head(fam_id, parent_id)
    dbm.link_parent_to_family(fam_id, parent_id)
    if student_id is not None:
        dbm.link_student_to_family(fam_id, student_id)
    return fam_id


def test_families_for_student_are_ordered(temp_db):
    """Порядок задаёт запрос, а не планировщик."""
    sid = dbm.add_student("Kid", "ss-fams")
    first = _family_with_student("A", "998900000801", 800001, sid)
    second = _family_with_student("B", "998900000802", 800002, sid)

    ids = [f["id"] for f in dbm.get_families_for_student(sid)]

    assert ids == sorted([first, second])


def test_chat_family_prefers_the_one_parent_belongs_to(temp_db):
    """История чата хранится по паре (родитель, семья): берём общую семью,
    а не просто первую у ребёнка."""
    from webapp.app import _resolve_chat_family

    sid = dbm.add_student("Kid", "ss-chat")
    _family_with_student("A", "998900000811", 810001, sid)      # чужая семья, id меньше
    mine = _family_with_student("B", "998900000812", 810002, sid)

    resolved = _resolve_chat_family(810002, sid)

    assert resolved["id"] == mine


def test_chat_family_is_stable_when_nothing_shared(temp_db):
    """Общей семьи нет — выбор всё равно детерминирован."""
    from webapp.app import _resolve_chat_family

    sid = dbm.add_student("Kid", "ss-chat2")
    first = _family_with_student("A", "998900000821", 820001, sid)
    _family_with_student("B", "998900000822", 820002, sid)

    assert _resolve_chat_family(999999, sid)["id"] == first
    assert _resolve_chat_family(999999, sid)["id"] == first
