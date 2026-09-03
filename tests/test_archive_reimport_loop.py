"""Цикл «архивация ↔ реимпорт» и потеря истории в дашборде (аудит 2026-09-03).

Что было на проде:
  • `archive_old_grades` отбирал по `date_added`, а importer пишет туда дату
    самой оценки — импортированная история рождалась «старой» и уезжала в архив;
  • дедуп импортёра смотрел только в `grade_history` → заархивированная оценка
    выглядела новой и импортировалась заново;
  • через неделю чистка уносила её опять. Архив: 7141 строка против 720
    уникальных, каждая оценка до 16 раз;
  • дашборд читал только `grade_history`, поэтому набор данных менялся в
    зависимости от того, когда посмотреть.

Здесь зафиксировано поведение после фикса.
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm  # noqa: E402
from src.db.maintenance import archive_old_grades  # noqa: E402
from src.history_importer import _import_from_sheet  # noqa: E402


def _tashkent_today():
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date()


def _insert_live(student_id, subject, grade_date, raw_text="4", date_added=None):
    """Оценка в grade_history. date_added по умолчанию = дате оценки (так пишет
    importer), что раньше и делало запись мгновенно «старой»."""
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO grade_history (student_id, subject, grade_value, raw_text, "
            "cell_reference, grade_date, date_added) VALUES (%s, %s, 4, %s, %s, %s, %s)",
            (student_id, subject, raw_text, f"X-{subject}-{grade_date}", grade_date,
             date_added or f"{grade_date} 12:00:00"),
        )


def _counts(student_id):
    with dbm.get_db_connection() as conn:
        cur = conn.cursor()
        live = cur.execute("SELECT COUNT(*) c FROM grade_history WHERE student_id=%s",
                           (student_id,)).fetchone()["c"]
        arch = cur.execute("SELECT COUNT(*) c FROM grade_history_archive WHERE student_id=%s",
                           (student_id,)).fetchone()["c"]
    return live, arch


# ─── Архивация отбирает по дате оценки, а не по дате записи ──────────
def test_archive_selects_by_grade_date_not_date_added(temp_db):
    """Свежезаписанная оценка за старую дату — архивная; старая запись за
    свежую дату (учитель исправил оценку вчера) — остаётся живой."""
    sid = dbm.add_student("Kid", "ss-arch")
    today = _tashkent_today()
    old_day = (today - timedelta(days=200)).isoformat()
    recent_day = (today - timedelta(days=3)).isoformat()

    # Оценка за старую дату, но записанная только что (реимпорт/правка).
    _insert_live(sid, "Алгебра", old_day, date_added=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    # Свежая оценка с искусственно старым date_added (так пишет importer).
    _insert_live(sid, "Химия", recent_day, date_added=f"{old_day} 12:00:00")

    moved = archive_old_grades(days=180)

    assert moved == 1
    live, arch = _counts(sid)
    assert (live, arch) == (1, 1)
    with dbm.get_db_connection() as conn:
        assert conn.cursor().execute(
            "SELECT subject FROM grade_history WHERE student_id=%s", (sid,)
        ).fetchone()["subject"] == "Химия"


def test_archiving_twice_does_not_duplicate(temp_db):
    """Повторная архивация той же оценки не плодит строки (UNIQUE + ON CONFLICT)."""
    sid = dbm.add_student("Kid", "ss-twice")
    old_day = (_tashkent_today() - timedelta(days=200)).isoformat()
    _insert_live(sid, "Алгебра", old_day)

    archive_old_grades(days=180)
    _insert_live(sid, "Алгебра", old_day)   # запись каким-то путём вернулась
    archive_old_grades(days=180)

    live, arch = _counts(sid)
    assert (live, arch) == (0, 1)


# ─── Импортёр не тащит обратно то, что уже в архиве ──────────────────
def _sheet(day_label, subject="Алгебра", value="4"):
    return [
        ["Все оценки", "Kid"],
        ["Оценки", day_label],
        [subject, value],
    ]


def test_importer_skips_grades_already_in_archive(temp_db):
    """Ключевая регрессия: оценка из архива больше не считается новой."""
    sid = dbm.add_student("Kid", "ss-reimport")
    old_day = date(2025, 10, 13)
    _insert_live(sid, "Алгебра", old_day.isoformat())
    archive_old_grades(days=180)
    assert _counts(sid) == (0, 1)

    result = _import_from_sheet(
        sid, "ss-reimport", "Все оценки!A1:ZZ50", "Все оценки!",
        academic_year=2025, data=_sheet("13 октября"),
    )

    assert result["imported"] == 0
    assert result["skipped"] == 1
    assert _counts(sid) == (0, 1)   # цикл разорван: обратно не вернулась


def test_importer_still_imports_genuinely_new_grades(temp_db):
    """Защита от переусердствования: незнакомая оценка по-прежнему импортируется."""
    sid = dbm.add_student("Kid", "ss-new")
    _insert_live(sid, "Алгебра", "2025-10-13")
    archive_old_grades(days=180)

    result = _import_from_sheet(
        sid, "ss-new", "Все оценки!A1:ZZ50", "Все оценки!",
        academic_year=2025, data=_sheet("14 октября", subject="Химия", value="5"),
    )

    assert result["imported"] == 1
    live, arch = _counts(sid)
    assert (live, arch) == (1, 1)


def test_importer_dedups_within_one_run(temp_db):
    """Один и тот же день продублирован в листе (наблюдалось в проде) —
    импортируется один раз, даже если в БД записи ещё не было."""
    sid = dbm.add_student("Kid", "ss-dup-cols")
    data = [
        ["Все оценки", "Kid"],
        ["Оценки", "13 октября", "13 октября"],
        ["Алгебра", "4", "4"],
    ]

    result = _import_from_sheet(
        sid, "ss-dup-cols", "Все оценки!A1:ZZ50", "Все оценки!",
        academic_year=2025, data=data,
    )

    assert result["imported"] == 1


# ─── Дашборд видит архив ─────────────────────────────────────────────
def test_dashboard_history_includes_archive(temp_db):
    """«Год» и «Итоги года» не должны терять первую половину года."""
    sid = dbm.add_student("Kid", "ss-view")
    today = _tashkent_today()
    old_day = (today - timedelta(days=200)).isoformat()
    recent_day = (today - timedelta(days=10)).isoformat()
    _insert_live(sid, "Алгебра", old_day)
    _insert_live(sid, "Химия", recent_day)
    archive_old_grades(days=180)
    assert _counts(sid) == (1, 1)

    rows = dbm.get_grade_history_for_student_all(sid, days=365)
    subjects = {r["subject"] for r in rows}

    assert subjects == {"Алгебра", "Химия"}          # архивная запись на месте
    # Порядок — свежие первыми
    assert rows[0]["subject"] == "Химия"

    short = dbm.get_grade_history_for_student_all(sid, days=30)
    assert {r["subject"] for r in short} == {"Химия"}  # период по-прежнему режет


def test_future_dated_grades_are_excluded(temp_db):
    """Запись с датой в будущем (так рождался мусор при рассинхроне учебного
    года) не должна попадать в период и висеть первой в «последних оценках»."""
    sid = dbm.add_student("Kid", "ss-future")
    today = _tashkent_today()
    _insert_live(sid, "Алгебра", (today + timedelta(days=1)).isoformat())
    _insert_live(sid, "Химия", today.isoformat())

    rows = dbm.get_grade_history_for_student_all(sid, days=7)

    assert [r["subject"] for r in rows] == ["Химия"]
