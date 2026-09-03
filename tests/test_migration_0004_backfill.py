"""Backfill миграции 0004 (students.academic_year) на реальном PostgreSQL.

Схему тестовая БД получает одним `alembic upgrade head` по пустым таблицам, поэтому
сам backfill при обычном прогоне не исполняется ни на одной строке. Здесь мы берём
РОВНО тот SQL, что выполняет `upgrade()` (константа BACKFILL_SQL), подставляем данные
и проверяем результат — включая случай, ради которого backfill смотрит в архив.

Ключевой инвариант: у ученика, чьи оценки уехали в grade_history_archive (weekly job
переносит записи старше 180 дней), учебный год всё равно определяется по оценкам, а не
по «сегодня». Иначе его прошлогодняя таблица считалась бы актуальной и инцидент
2026-09-02 повторился бы ровно на нём.
"""
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm  # noqa: E402


def _load_migration():
    """Модуль миграции начинается с цифры — обычным import не берётся."""
    path = os.path.join(ROOT, "migrations", "versions", "0004_student_academic_year.py")
    spec = importlib.util.spec_from_file_location("migration_0004", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_migration()


def _tashkent_today():
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date()


def _current_academic_year():
    today = _tashkent_today()
    return today.year if today.month >= 9 else today.year - 1


def _insert_grade(student_id, grade_date, table="grade_history"):
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            f"INSERT INTO {table} (student_id, subject, grade_value, raw_text, "
            f"cell_reference, grade_date, date_added) "
            f"VALUES (%s, 'Математика', 5, '5', %s, %s, %s)",
            (student_id, f"X-{grade_date}", grade_date, f"{grade_date} 12:00:00"),
        )


def _run_backfill():
    """Сбрасывает academic_year и прогоняет ровно тот SQL, что делает upgrade()."""
    with dbm.get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE students SET academic_year = NULL")
        cur.execute(MIGRATION.BACKFILL_SQL)


def _year_of(student_id):
    with dbm.get_db_connection() as conn:
        row = conn.cursor().execute(
            "SELECT academic_year FROM students WHERE id = %s", (student_id,)
        ).fetchone()
        return row["academic_year"]


def test_backfill_uses_last_grade_not_today(temp_db):
    """Осенние и весенние оценки дают год НАЧАЛА учебного года."""
    autumn = dbm.add_student("Осенний", "ss-autumn")
    spring = dbm.add_student("Весенний", "ss-spring")
    _insert_grade(autumn, "2025-10-14")   # октябрь 2025 → уч. год 2025/26
    _insert_grade(spring, "2026-05-19")   # май 2026 → тот же уч. год 2025/26

    _run_backfill()

    assert _year_of(autumn) == 2025
    assert _year_of(spring) == 2025


def test_backfill_takes_the_latest_grade(temp_db):
    """Берётся максимум по датам, а не первая попавшаяся строка."""
    sid = dbm.add_student("Много оценок", "ss-many")
    for d in ("2024-09-02", "2025-03-11", "2025-09-05", "2026-04-20"):
        _insert_grade(sid, d)

    _run_backfill()

    assert _year_of(sid) == 2025  # последняя — апрель 2026 → уч. год 2025/26


def test_backfill_reads_archive_when_history_is_empty(temp_db):
    """РЕГРЕССИЯ: все оценки уехали в архив (>180 дней) → год всё равно из оценок.

    Без учёта grade_history_archive такой ученик получил бы текущий учебный год,
    его прошлогодняя таблица считалась бы актуальной, и монитор снова разослал бы
    прошлогодние оценки — ровно инцидент 2026-09-02."""
    sid = dbm.add_student("Только архив", "ss-archived")
    _insert_grade(sid, "2025-11-03", table="grade_history_archive")

    _run_backfill()

    assert _year_of(sid) == 2025
    assert _year_of(sid) != _current_academic_year() or _current_academic_year() == 2025


def test_backfill_takes_max_across_both_tables(temp_db):
    """Свежая оценка в grade_history перевешивает старую в архиве и наоборот."""
    sid = dbm.add_student("Обе таблицы", "ss-both")
    _insert_grade(sid, "2024-10-01", table="grade_history_archive")
    _insert_grade(sid, "2025-09-15", table="grade_history")

    _run_backfill()

    assert _year_of(sid) == 2025

    other = dbm.add_student("Архив свежее", "ss-both-2")
    _insert_grade(other, "2025-12-01", table="grade_history_archive")
    _insert_grade(other, "2024-09-03", table="grade_history")

    _run_backfill()

    assert _year_of(other) == 2025


def test_backfill_student_without_grades_gets_current_year(temp_db):
    """Ученик без единой оценки — текущий учебный год (единственный разумный fallback)."""
    sid = dbm.add_student("Новичок", "ss-fresh")

    _run_backfill()

    assert _year_of(sid) == _current_academic_year()


def test_backfill_does_not_touch_existing_values(temp_db):
    """WHERE academic_year IS NULL — повторный прогон не перетирает уже проставленное."""
    sid = dbm.add_student("Уже с годом", "ss-set")
    _insert_grade(sid, "2025-10-01")
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "UPDATE students SET academic_year = 2019 WHERE id = %s", (sid,)
        )

    with dbm.get_db_connection() as conn:
        conn.cursor().execute(MIGRATION.BACKFILL_SQL)  # без сброса в NULL

    assert _year_of(sid) == 2019


def test_backfill_is_idempotent(temp_db):
    """Второй прогон на тех же данных даёт тот же результат."""
    sid = dbm.add_student("Идемпотентность", "ss-idem")
    _insert_grade(sid, "2025-09-20")

    _run_backfill()
    first = _year_of(sid)
    _run_backfill()

    assert first == _year_of(sid) == 2025


def test_migration_revision_chain():
    """Ревизия встроена в цепочку после outbox-миграции."""
    assert MIGRATION.revision == "0004_student_academic_year"
    assert MIGRATION.down_revision == "0003_grade_notified_outbox"
