"""Этап 2 RFC: read-path смотрит на grade_date вместо date_added.

Главный регрессионный сценарий: запись с битым date_added (старый парсер
«мая→март» оставил date_added=март, backfill выставил grade_date=май).
Бот должен показывать оценку под правильной майской датой во всех местах:
/grades, дашборд, AI-промпт.
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm
from webapp.app import compute_trend_by_day


def _seed_grade(student_id, subject, raw_text, cell_reference,
                date_added, grade_date=None, grade_value=None):
    with dbm.get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO grade_history "
            "(student_id, subject, grade_value, raw_text, cell_reference, date_added, grade_date) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (student_id, subject, grade_value, raw_text, cell_reference,
             date_added, grade_date),
        )


def test_get_history_uses_grade_date_for_period(temp_db):
    """Запись с date_added в марте, но grade_date в мае — должна попасть в
    период «за последние 14 дней» если сегодня близко к маю (которая является
    grade_date). Раньше она бы выпала фильтром по date_added."""
    sid = dbm.add_student("Kid", "ss-period")
    today = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date()
    # date_added — два месяца назад (имитация битого парсера),
    # grade_date — вчера (реальная дата оценки)
    fake_old_date_added = (today - timedelta(days=70)).isoformat() + " 12:00:00"
    real_grade_date = (today - timedelta(days=1)).isoformat()
    _seed_grade(
        sid, "Английский язык", "5", "Все оценки!IM6",
        date_added=fake_old_date_added,
        grade_date=real_grade_date,
        grade_value=5.0,
    )

    rows = dbm.get_grade_history_for_student(sid, days=7)
    assert len(rows) == 1, "Должна попасть в окно по grade_date, не по date_added"
    # psycopg возвращает date-объект — сравниваем эквивалентно через isoformat()
    assert rows[0]['grade_date'].isoformat() == real_grade_date
    assert rows[0]['raw_text'] == '5'


# Удалён test_get_history_fallback_for_null_grade_date: после этапа 1C
# grade_date NOT NULL, на свежей БД NULL невоспроизводим. COALESCE в SQL
# оставлен как defense-in-depth для disaster recovery legacy-бэкапов,
# но регулярного теста-сценария на это больше нет.


def test_today_grades_use_grade_date(temp_db):
    """Запись с grade_date=сегодня, но date_added на год назад — должна попасть
    в «оценки за сегодня»."""
    sid = dbm.add_student("Kid", "ss-today")
    today = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date()
    _seed_grade(
        sid, "История", "5", "Все оценки!XX1",
        date_added="2025-01-01 12:00:00",
        grade_date=today.isoformat(),
        grade_value=5.0,
    )
    today_grades = dbm.get_today_grades_for_student(sid)
    assert any(g['subject'] == 'История' and g['raw_text'] == '5'
               for g in today_grades)


def test_yesterday_grades_use_grade_date(temp_db):
    sid = dbm.add_student("Kid", "ss-yday")
    today = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date()
    yday = (today - timedelta(days=1)).isoformat()
    _seed_grade(
        sid, "География", "3", "Все оценки!YY1",
        date_added="2025-01-01 12:00:00",
        grade_date=yday,
        grade_value=3.0,
    )
    rows = dbm.get_yesterday_grades_for_student(sid)
    assert any(r['subject'] == 'География' and r['raw_text'] == '3' for r in rows)


def test_compute_trend_by_day_uses_grade_date():
    """Webapp группирует по grade_date если он есть, fallback на date_added."""
    grades = [
        {"grade_value": 5.0, "subject": "X", "grade_date": "2026-05-13",
         "date_added": "2026-03-05 12:00:00"},   # битый date_added, правильный grade_date
        {"grade_value": 4.0, "subject": "Y", "grade_date": "2026-05-13",
         "date_added": "2026-05-13 10:00:00"},
        {"grade_value": 3.0, "subject": "Z", "grade_date": None,
         "date_added": "2026-05-12 10:00:00"},   # legacy: fallback на date_added
    ]
    trend = compute_trend_by_day(grades, period_days=7)
    by_date = {item['date']: item for item in trend}
    # Две точки на 2026-05-13 (avg=4.5), одна на 2026-05-12 (avg=3.0)
    assert by_date['2026-05-13']['avg'] == 4.5
    assert by_date['2026-05-13']['count'] == 2
    assert by_date['2026-05-12']['avg'] == 3.0
    assert by_date['2026-05-12']['count'] == 1
    # Битая date_added=март НЕ создаёт точку в марте
    assert '2026-03-05' not in by_date
