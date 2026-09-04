"""Четвертные оценки живут внутри учебного года (Фаза 2 RFC rollover).

До миграции 0006 ключ таблицы был `(student_id, subject, quarter)`. Как только в
новой таблице школы появлялась первая четвертная, она ПЕРЕЗАПИСЫВАЛА прошлогоднюю:
карточка предмета превращалась в смесь двух учебных лет (Q1 нового рядом с Q2–Q4
старого), и «прогноз годовой» считался по этой смеси. Проявилось бы к ноябрю 2026.

Здесь закреплено: годы не смешиваются, чтение по умолчанию отдаёт год привязанной
таблицы, а сам год виден в ответе дашборда.
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm  # noqa: E402
from src.db.grades import ALL_ACADEMIC_YEARS  # noqa: E402


def _load_migration():
    path = os.path.join(ROOT, "migrations", "versions", "0006_quarter_academic_year.py")
    spec = importlib.util.spec_from_file_location("migration_0006", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_migration()


def _quarters_raw(student_id):
    with dbm.get_db_connection() as conn:
        rows = conn.cursor().execute(
            "SELECT academic_year, subject, quarter, raw_text FROM quarter_grades "
            "WHERE student_id = %s ORDER BY academic_year, subject, quarter",
            (student_id,),
        ).fetchall()
    return [(r["academic_year"], r["subject"], r["quarter"], r["raw_text"]) for r in rows]


# ─── Годы не смешиваются ─────────────────────────────────────────────
def test_new_year_does_not_overwrite_previous(temp_db):
    """Сценарий ноября: та же четверть по тому же предмету, но другой год."""
    sid = dbm.add_student("Kid", "ss-q", academic_year=2025)
    dbm.upsert_quarter_grade(sid, "Алгебра", 1, 3.0, "3", academic_year=2025)

    dbm.upsert_quarter_grade(sid, "Алгебра", 1, 5.0, "5", academic_year=2026)

    assert _quarters_raw(sid) == [
        (2025, "Алгебра", 1, "3"),
        (2026, "Алгебра", 1, "5"),
    ]


def test_same_year_still_updates_in_place(temp_db):
    """Учитель исправил четвертную — обновляем, а не плодим строку."""
    sid = dbm.add_student("Kid", "ss-q2", academic_year=2026)
    assert dbm.upsert_quarter_grade(sid, "Химия", 2, 3.0, "3", academic_year=2026) is True
    assert dbm.upsert_quarter_grade(sid, "Химия", 2, 4.0, "4", academic_year=2026) is True

    assert _quarters_raw(sid) == [(2026, "Химия", 2, "4")]


def test_unchanged_value_reports_no_change(temp_db):
    """Повторная запись того же значения не считается изменением (иначе
    монитор слал бы уведомление на каждый цикл)."""
    sid = dbm.add_student("Kid", "ss-q3", academic_year=2026)
    dbm.upsert_quarter_grade(sid, "Физика", 1, 4.0, "4", academic_year=2026)

    assert dbm.upsert_quarter_grade(sid, "Физика", 1, 4.0, "4", academic_year=2026) is False


def test_year_defaults_to_student_sheet_year(temp_db):
    """Без явного года берётся год привязанной таблицы, а не текущая дата."""
    sid = dbm.add_student("Kid", "ss-q4", academic_year=2025)

    dbm.upsert_quarter_grade(sid, "История", 3, 4.0, "4")

    assert _quarters_raw(sid) == [(2025, "История", 3, "4")]


# ─── Чтение ──────────────────────────────────────────────────────────
def test_read_returns_only_current_year(temp_db):
    """Дашборд не должен показывать прошлогодние четверти как текущие."""
    sid = dbm.add_student("Kid", "ss-q5", academic_year=2026)
    dbm.upsert_quarter_grade(sid, "Алгебра", 1, 3.0, "3", academic_year=2025)
    dbm.upsert_quarter_grade(sid, "Алгебра", 1, 5.0, "5", academic_year=2026)

    rows = dbm.get_quarter_grades(sid)

    assert [(r["academic_year"], r["raw_text"]) for r in rows] == [(2026, "5")]


def test_read_specific_and_all_years(temp_db):
    sid = dbm.add_student("Kid", "ss-q6", academic_year=2026)
    dbm.upsert_quarter_grade(sid, "Алгебра", 1, 3.0, "3", academic_year=2025)
    dbm.upsert_quarter_grade(sid, "Алгебра", 1, 5.0, "5", academic_year=2026)

    assert [r["raw_text"] for r in dbm.get_quarter_grades(sid, academic_year=2025)] == ["3"]
    everything = dbm.get_quarter_grades(sid, academic_year=ALL_ACADEMIC_YEARS)
    assert [(r["academic_year"], r["raw_text"]) for r in everything] == [(2025, "3"), (2026, "5")]
    assert dbm.get_quarter_academic_years(sid) == [2026, 2025]


def test_relinked_student_sees_new_year_quarters(temp_db):
    """После смены ссылки год ученика ещё NULL — читаем текущий, а не прошлый."""
    from src.history_importer import current_academic_year

    sid = dbm.add_student("Kid", "ss-q7", academic_year=2025)
    dbm.upsert_quarter_grade(sid, "Алгебра", 1, 3.0, "3", academic_year=2025)
    dbm.set_student_academic_year(sid, None)

    rows = dbm.get_quarter_grades(sid)
    expected_year = current_academic_year()

    assert all(r["academic_year"] == expected_year for r in rows)


# ─── Backfill миграции ───────────────────────────────────────────────
def test_backfill_takes_year_from_student(temp_db):
    """Существующие строки получают год привязанной таблицы ученика.

    Воспроизводим момент миграции: колонка уже добавлена, но ещё пуста и без
    NOT NULL — именно в этом состоянии выполняется BACKFILL_SQL."""
    sid = dbm.add_student("Kid", "ss-q8", academic_year=2025)
    dbm.upsert_quarter_grade(sid, "Алгебра", 1, 3.0, "3", academic_year=2026)

    with dbm.get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("ALTER TABLE quarter_grades ALTER COLUMN academic_year DROP NOT NULL")
        cur.execute("UPDATE quarter_grades SET academic_year = NULL WHERE student_id = %s", (sid,))
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(MIGRATION.BACKFILL_SQL)
    with dbm.get_db_connection() as conn:
        conn.cursor().execute("ALTER TABLE quarter_grades ALTER COLUMN academic_year SET NOT NULL")

    assert _quarters_raw(sid) == [(2025, "Алгебра", 1, "3")]


def test_migration_revision_chain():
    assert MIGRATION.revision == "0006_quarter_academic_year"
    assert MIGRATION.down_revision == "0005_archive_dedup"


def test_unique_constraint_covers_year(temp_db):
    """Ограничение пересобрано: дубль внутри одного года по-прежнему невозможен."""
    from psycopg import errors

    sid = dbm.add_student("Kid", "ss-q9", academic_year=2026)
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO quarter_grades (student_id, academic_year, subject, quarter, "
            "grade_value, raw_text) VALUES (%s, 2026, 'Алгебра', 1, 4, '4')", (sid,))
    with pytest.raises(errors.UniqueViolation):
        with dbm.get_db_connection() as conn:
            conn.cursor().execute(
                "INSERT INTO quarter_grades (student_id, academic_year, subject, quarter, "
                "grade_value, raw_text) VALUES (%s, 2026, 'Алгебра', 1, 5, '5')", (sid,))
