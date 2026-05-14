"""Этап 3 RFC: write-path пишет grade_date явно.

monitor_engine берёт дату из cell_reference «Сегодня!subject:YYYY-MM-DD»
(она же tashkent_today в цикле). history_importer берёт дату из заголовка
столбца Sheets (`rec['date']`).

После этого этапа новые записи всегда имеют grade_date NOT NULL.
Legacy-записи (до 1B бэкфилла) ещё могут быть с NULL — но в проде backfill
прошёл, так что эта ситуация осталась только в тестовых сценариях.
"""
import os
import sys
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm
import src.monitor_engine as me
from src.history_importer import import_history_for_student


def _tashkent_today():
    return (datetime.utcnow() + timedelta(hours=5)).date()


def _seed_active_student(temp_db, sid_label='ss-w'):
    head_id = dbm.add_parent("Head", "998900007777", role='senior')
    dbm.update_parent_telegram_id("998900007777", 777777)
    fam_id = dbm.add_family("F-write")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    sid = dbm.add_student("Kid", sid_label)
    dbm.update_student_display_name(sid, "Kid")
    dbm.link_student_to_family(fam_id, sid)
    future = (datetime.utcnow() + timedelta(days=30)).strftime('%Y-%m-%d')
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "UPDATE families SET subscription_end = ? WHERE id = ?",
            (future, fam_id),
        )
    return sid


@pytest.fixture(autouse=True)
def _reset_pending():
    me._pending_grades.clear()
    yield
    me._pending_grades.clear()


def _make_sheet(grades):
    rows = [["Сегодня", "Kid"], ["Оценки", "13 мая"]]
    for subj, val in grades.items():
        rows.append([subj, val])
    return rows


def _run_cycle(sheet_data):
    with patch('src.monitor_engine.get_sheet_data', return_value=sheet_data), \
         patch('src.monitor_engine.get_spreadsheet_title', return_value="Kid"), \
         patch('src.monitor_engine.send_notification'), \
         patch('src.monitor_engine._send_to_groups_for_student'):
        me._check_for_new_grades_impl()


def test_monitor_writes_grade_date_on_insert(temp_db):
    """monitor подтверждает новую оценку → INSERT с grade_date = tashkent_today."""
    sid = _seed_active_student(temp_db)
    _run_cycle(_make_sheet({"Алгебра": "5"}))     # pending
    _run_cycle(_make_sheet({"Алгебра": "5"}))     # confirm

    with dbm.get_db_connection() as conn:
        row = conn.cursor().execute(
            "SELECT grade_date, raw_text FROM grade_history "
            "WHERE student_id=? AND subject=?", (sid, "Алгебра")
        ).fetchone()
    assert row is not None, "monitor должен был вставить запись после подтверждения"
    assert row['raw_text'] == '5'
    assert row['grade_date'] == _tashkent_today().isoformat()


def test_history_importer_writes_grade_date(temp_db):
    """import_history_for_student пишет grade_date из заголовка столбца Sheets."""
    sid = _seed_active_student(temp_db, sid_label='ss-hi')
    yday = (_tashkent_today() - timedelta(days=2))
    months_ru = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
        5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
        9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря',
    }
    label = f"{yday.day} {months_ru[yday.month]}"

    sheet = [
        ["Все оценки", "Kid"],
        ["Оценки", label],
        ["Физика", "4"],
    ]
    with patch('src.history_importer.get_sheet_data', return_value=sheet):
        import_history_for_student(sid, "ss-hi")

    with dbm.get_db_connection() as conn:
        row = conn.cursor().execute(
            "SELECT grade_date, raw_text FROM grade_history "
            "WHERE student_id=? AND subject=?", (sid, "Физика")
        ).fetchone()
    assert row is not None
    assert row['raw_text'] == '4'
    assert row['grade_date'] == yday.isoformat()


def test_add_grade_defaults_to_today_when_grade_date_omitted(temp_db):
    """После 1C grade_date NOT NULL. add_grade без kwarg должен дефолтить на
    сегодняшнюю дату по Ташкенту — единственный безопасный fallback для
    legacy-вызовов (это домен monitor'а)."""
    from datetime import datetime, timedelta
    expected = (datetime.utcnow() + timedelta(hours=5)).date().isoformat()
    sid = dbm.add_student("Kid", "ss-compat")
    ok = dbm.add_grade(sid, "X", 5.0, "5", "Сегодня!X:2026-05-14")
    assert ok is True
    with dbm.get_db_connection() as conn:
        row = conn.cursor().execute(
            "SELECT grade_date FROM grade_history WHERE student_id=?", (sid,)
        ).fetchone()
    assert row['grade_date'] == expected


def test_add_grade_with_grade_date_explicit(temp_db):
    sid = dbm.add_student("Kid", "ss-explicit")
    ok = dbm.add_grade(sid, "Y", 4.0, "4", "Сегодня!Y:2026-05-14",
                       grade_date="2026-05-14")
    assert ok is True
    with dbm.get_db_connection() as conn:
        row = conn.cursor().execute(
            "SELECT grade_date FROM grade_history WHERE student_id=?", (sid,)
        ).fetchone()
    assert row['grade_date'] == "2026-05-14"
