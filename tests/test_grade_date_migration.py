"""Этап 1A RFC (Docs/rfc-grades-source-of-truth.md):
миграция добавляет nullable колонку grade_date в grade_history
и grade_history_archive.

После этого этапа колонка просто существует и принимает NULL — никто пока
не пишет в неё. Backfill придёт отдельным скриптом (этап 1B), NOT NULL
и новый UNIQUE — этапом 1C.
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


def test_fresh_db_has_grade_date(temp_db):
    """Свежая БД, созданная init_db — обе таблицы уже содержат grade_date."""
    with sqlite3.connect(temp_db) as conn:
        assert 'grade_date' in _cols(conn, 'grade_history')
        assert 'grade_date' in _cols(conn, 'grade_history_archive')


def test_grade_date_is_nullable(temp_db):
    """Можно вставить запись без grade_date — это намеренно: бэкфилл отдельно."""
    sid = dbm.add_student("Kid", "ss-mig")
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO grade_history (student_id, subject, raw_text, cell_reference) "
            "VALUES (?, ?, ?, ?)",
            (sid, "Алгебра", "5", "Сегодня!Алгебра:2026-05-14"),
        )
    with dbm.get_db_connection() as conn:
        row = conn.cursor().execute(
            "SELECT grade_date FROM grade_history WHERE student_id=?", (sid,)
        ).fetchone()
    assert row['grade_date'] is None


def test_legacy_db_without_grade_date_is_migrated(tmp_path, monkeypatch):
    """Симулируем старую БД (без grade_date) и проверяем что init_db добавляет
    колонку без потери данных."""
    db_path = tmp_path / "legacy.db"

    # Создаём grade_history по СТАРОЙ схеме (без grade_date)
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

    # Запускаем init_db на этой БД — миграция должна добавить grade_date
    monkeypatch.setattr(dbm, 'DB_PATH', str(db_path))
    monkeypatch.setenv('ADMIN_ID', '0')
    dbm.init_db()

    with sqlite3.connect(db_path) as conn:
        assert 'grade_date' in _cols(conn, 'grade_history')
        assert 'grade_date' in _cols(conn, 'grade_history_archive')
        # Существующая запись осталась
        row = conn.execute(
            "SELECT raw_text, grade_date FROM grade_history WHERE id=1"
        ).fetchone()
    assert row[0] == '5'
    assert row[1] is None  # backfill ещё впереди


def test_init_db_is_idempotent(temp_db):
    """Повторный вызов init_db на той же БД не падает (миграция grade_date
    проверяет наличие через PRAGMA)."""
    dbm.init_db()  # уже было в fixture, повторяем
    dbm.init_db()
    with sqlite3.connect(temp_db) as conn:
        assert 'grade_date' in _cols(conn, 'grade_history')
