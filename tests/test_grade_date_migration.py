"""Этапы 1A/1C RFC (Docs/rfc-grades-source-of-truth.md):
1A — миграция добавляет nullable колонку grade_date в grade_history и архив.
1C — recreate-table делает grade_date NOT NULL + UNIQUE(student, subject,
    grade_date, raw_text). Запускается из init_db только если в БД нет
    grade_date IS NULL строк (иначе отказ + WARN: запустить backfill).

Тесты в этом файле:
    - 1A: legacy-БД без grade_date получает колонку и существующая запись с
      NULL grade_date переживает повторный init_db (1C skip).
    - 1C: после полного backfill повторный init_db применяет recreate.
    - Идемпотентность.

Расширенные сценарии 1C (dedup, UNIQUE collisions, NULL guard) — в
test_stage_1c_migration.py.
"""
import os
import sqlite3
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm


def _cols(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _col_notnull(conn, table, col):
    for row in conn.execute(f"PRAGMA table_info({table})"):
        if row[1] == col:
            return bool(row[3])
    return False


def test_fresh_db_has_grade_date_not_null(temp_db):
    """Свежая БД через init_db: grade_date присутствует И NOT NULL (этап 1C)."""
    with sqlite3.connect(temp_db) as conn:
        assert 'grade_date' in _cols(conn, 'grade_history')
        assert 'grade_date' in _cols(conn, 'grade_history_archive')
        assert _col_notnull(conn, 'grade_history', 'grade_date'), \
            "После этапа 1C grade_date должно быть NOT NULL"


def test_fresh_db_rejects_insert_without_grade_date(temp_db):
    """NOT NULL constraint срабатывает на прямом INSERT без grade_date."""
    sid = dbm.add_student("Kid", "ss-mig")
    with dbm.get_db_connection() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.cursor().execute(
            "INSERT INTO grade_history (student_id, subject, raw_text, cell_reference) "
            "VALUES (?, ?, ?, ?)",
            (sid, "Алгебра", "5", "Сегодня!Алгебра:2026-05-14"),
        )


def test_legacy_db_without_grade_date_is_migrated(tmp_path, monkeypatch):
    """1A-сценарий: legacy-БД без grade_date. init_db добавляет колонку
    (1A), но 1C отказывается (NULL присутствует). Существующая запись с
    NULL grade_date — переживает миграцию без потери данных."""
    db_path = tmp_path / "legacy.db"

    # Создаём grade_history по САМОЙ СТАРОЙ схеме (без grade_date)
    with sqlite3.connect(db_path) as conn:
        conn.execute('''
            CREATE TABLE grade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                grade_value REAL,
                raw_text TEXT NOT NULL,
                cell_reference TEXT NOT NULL,
                date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, cell_reference)
            )
        ''')
        conn.execute(
            "INSERT INTO grade_history (student_id, subject, raw_text, cell_reference) "
            "VALUES (1, 'Математика', '5', 'Все оценки!IU5')"
        )
        conn.commit()
        assert 'grade_date' not in _cols(conn, 'grade_history')

    monkeypatch.setattr(dbm, 'DB_PATH', str(db_path))
    monkeypatch.setenv('ADMIN_ID', '0')
    dbm.init_db()

    with sqlite3.connect(db_path) as conn:
        assert 'grade_date' in _cols(conn, 'grade_history')
        assert 'grade_date' in _cols(conn, 'grade_history_archive')
        # 1C НЕ должен был запуститься — есть NULL grade_date в строке
        assert not _col_notnull(conn, 'grade_history', 'grade_date'), \
            "1C должен был отказаться при NULL grade_date"
        row = conn.execute(
            "SELECT raw_text, grade_date FROM grade_history WHERE id=1"
        ).fetchone()
    assert row[0] == '5'
    assert row[1] is None


def test_init_db_is_idempotent(temp_db):
    """Повторный вызов init_db на уже мигрированной БД не падает."""
    dbm.init_db()  # уже было в fixture, повторяем
    dbm.init_db()
    with sqlite3.connect(temp_db) as conn:
        assert 'grade_date' in _cols(conn, 'grade_history')
        assert _col_notnull(conn, 'grade_history', 'grade_date')
