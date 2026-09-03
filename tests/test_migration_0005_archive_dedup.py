"""Чистка дублей в grade_history_archive (миграция 0005) на живом PostgreSQL.

Миграция применяется к пустым таблицам при создании тестовой схемы, поэтому сам
DELETE иначе остался бы непроверенным. Здесь мы сеем дубли ровно того вида, что
накопились на проде (7141 строка против 720 уникальных), и прогоняем РОВНО тот
SQL, что выполняет `upgrade()`.
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm  # noqa: E402


def _load_migration():
    path = os.path.join(ROOT, "migrations", "versions", "0005_archive_dedup.py")
    spec = importlib.util.spec_from_file_location("migration_0005", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_migration()


def _insert_archive(student_id, subject, grade_date, raw_text="4",
                    date_added="2025-10-13 12:00:00"):
    """Пишем в обход UNIQUE-индекса нельзя, поэтому сеем дубли там, где индекс
    не действует (grade_date IS NULL), и проверяем оба пути отдельно."""
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO grade_history_archive (student_id, subject, grade_value, "
            "raw_text, cell_reference, grade_date, date_added) "
            "VALUES (%s, %s, 4, %s, 'X', %s, %s)",
            (student_id, subject, raw_text, grade_date, date_added),
        )


def _archive_count(student_id):
    with dbm.get_db_connection() as conn:
        return conn.cursor().execute(
            "SELECT COUNT(*) c FROM grade_history_archive WHERE student_id = %s",
            (student_id,),
        ).fetchone()["c"]


def test_unique_index_blocks_new_duplicates(temp_db):
    """Барьер стоит: вторая такая же строка с непустой датой не вставится."""
    from psycopg import errors

    sid = dbm.add_student("Kid", "ss-uq")
    _insert_archive(sid, "Алгебра", "2025-10-13")
    with pytest.raises(errors.UniqueViolation):
        _insert_archive(sid, "Алгебра", "2025-10-13")


def test_dedup_removes_legacy_null_date_duplicates(temp_db):
    """Legacy-записи (grade_date IS NULL, 242 штуки на проде) частичный UNIQUE
    не покрывает — их дедуплицирует SQL миграции по дате из date_added."""
    sid = dbm.add_student("Kid", "ss-null")
    for _ in range(16):        # ровно столько копий было на проде
        _insert_archive(sid, "Алгебра", None, date_added="2025-10-13 12:00:00")
    _insert_archive(sid, "Химия", None, date_added="2025-10-14 12:00:00")
    assert _archive_count(sid) == 17

    with dbm.get_db_connection() as conn:
        conn.cursor().execute(MIGRATION.DEDUP_SQL)

    assert _archive_count(sid) == 2


def test_dedup_keeps_distinct_grades(temp_db):
    """Разные оценки одного предмета в один день (пересдача «3» и «5») —
    не дубли, обе должны остаться."""
    sid = dbm.add_student("Kid", "ss-distinct")
    _insert_archive(sid, "Алгебра", None, raw_text="3", date_added="2025-10-13 12:00:00")
    _insert_archive(sid, "Алгебра", None, raw_text="5", date_added="2025-10-13 12:00:00")

    with dbm.get_db_connection() as conn:
        conn.cursor().execute(MIGRATION.DEDUP_SQL)

    assert _archive_count(sid) == 2


def test_dedup_is_idempotent(temp_db):
    """Повторный прогон ничего больше не удаляет."""
    sid = dbm.add_student("Kid", "ss-idem")
    for _ in range(5):
        _insert_archive(sid, "Алгебра", None, date_added="2025-10-13 12:00:00")

    # Каждый прогон — своя транзакция: _archive_count открывает отдельное
    # соединение и до коммита не увидел бы изменений.
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(MIGRATION.DEDUP_SQL)
    first = _archive_count(sid)
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(MIGRATION.DEDUP_SQL)

    assert first == _archive_count(sid) == 1


def test_migration_revision_chain():
    assert MIGRATION.revision == "0005_archive_dedup"
    assert MIGRATION.down_revision == "0004_student_academic_year"
