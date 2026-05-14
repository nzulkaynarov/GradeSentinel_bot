"""Этап 1C RFC (Docs/rfc-grades-source-of-truth.md):
grade_history.grade_date становится NOT NULL, UNIQUE по содержимому
(student_id, subject, grade_date, raw_text) вместо UNIQUE(student_id,
cell_reference).

Тесты ниже проверяют:
- Свежая БД через init_db уже в новом виде (NOT NULL + новый UNIQUE).
- Legacy-БД с заполненным backfill'ом grade_date → 1C применяется при
  следующем init_db; legacy с NULL'ами — 1C тихо skip + WARN.
- INSERT OR IGNORE dedup при recreate-table: коллизии по содержимому
  схлопываются, выживает запись с наименьшим id (самая ранняя).
- Новый UNIQUE реально срабатывает на INSERT'е дубля.
- Идемпотентность.
"""
import os
import sqlite3
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm


def _col_notnull(conn, table, col):
    for row in conn.execute(f"PRAGMA table_info({table})"):
        if row[1] == col:
            return bool(row[3])
    return False


def _indexes(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA index_list({table})")]


def _has_unique_on(conn, table, expected_cols: list[str]) -> bool:
    """True если есть UNIQUE-индекс ровно по этим колонкам (в этом порядке)."""
    for idx in conn.execute(f"PRAGMA index_list({table})"):
        idx_name, unique = idx[1], idx[2]
        if not unique:
            continue
        cols = [r[2] for r in conn.execute(f"PRAGMA index_info({idx_name})")]
        if cols == expected_cols:
            return True
    return False


def test_fresh_db_has_new_unique(temp_db):
    """init_db на чистой БД создаёт grade_history сразу с новым UNIQUE по
    содержимому, без старого UNIQUE(student_id, cell_reference)."""
    with sqlite3.connect(temp_db) as conn:
        assert _has_unique_on(
            conn, 'grade_history',
            ['student_id', 'subject', 'grade_date', 'raw_text'],
        )
        assert not _has_unique_on(
            conn, 'grade_history', ['student_id', 'cell_reference']
        )


def test_unique_collision_on_duplicate_content(temp_db):
    """Два INSERT'а одного и того же (student, subject, grade_date, raw_text)
    с РАЗНЫМ cell_reference — второй должен фейлиться через add_grade -> False
    и raise через raw INSERT."""
    sid = dbm.add_student("Kid", "ss-uc")
    ok1 = dbm.add_grade(sid, "Алгебра", 5.0, "5",
                        "Сегодня!Алгебра:2026-05-14", grade_date="2026-05-14")
    assert ok1 is True
    # Другой cell_ref (имитация импорта из «Все оценки!»), идентичное содержимое
    ok2 = dbm.add_grade(sid, "Алгебра", 5.0, "5",
                        "Все оценки!H7", grade_date="2026-05-14")
    assert ok2 is False, "Повторный контент должен быть отброшен новым UNIQUE"


def test_different_grades_same_day_coexist(temp_db):
    """Две разные оценки за один день (например, два двойных балла 3 и 5
    по одному предмету) — должны мирно ужиться. Это легитимный сценарий."""
    sid = dbm.add_student("Kid", "ss-coexist")
    ok1 = dbm.add_grade(sid, "Алгебра", 5.0, "5",
                        "Сегодня!Алгебра:2026-05-14#1", grade_date="2026-05-14")
    ok2 = dbm.add_grade(sid, "Алгебра", 3.0, "3",
                        "Сегодня!Алгебра:2026-05-14#2", grade_date="2026-05-14")
    assert ok1 is True and ok2 is True


def test_1c_dedups_legacy_collisions(tmp_path, monkeypatch):
    """Legacy-БД с двумя строками одинакового содержимого (Сегодня! + Все оценки!).
    После полного backfill grade_date оба ряда стали target'ом нового UNIQUE.
    Recreate-миграция через INSERT OR IGNORE должна сохранить РАННЮЮ строку
    (с меньшим id) и отбросить позднюю."""
    db_path = tmp_path / "legacy.db"

    # Сначала init_db на этом пути — создаст в современной схеме
    monkeypatch.setattr(dbm, 'DB_PATH', str(db_path))
    monkeypatch.setenv('ADMIN_ID', '0')
    dbm.init_db()

    # Демотируем grade_history к pre-1C виду
    with dbm.get_db_connection() as conn:
        conn.cursor().executescript('''
            DROP TABLE grade_history;
            CREATE TABLE grade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                grade_value REAL,
                raw_text TEXT NOT NULL,
                cell_reference TEXT NOT NULL,
                grade_date DATE,
                date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(student_id) REFERENCES students(id),
                UNIQUE(student_id, cell_reference)
            );
        ''')

    # Сидим 2 коллизионные строки (monitor + import одной и той же оценки)
    sid = dbm.add_student("Kid", "ss-collision")
    with dbm.get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO grade_history "
            "(student_id, subject, grade_value, raw_text, cell_reference, grade_date) "
            "VALUES (?, 'Алгебра', 5.0, '5', 'Сегодня!Алгебра:2026-05-08', '2026-05-08')",
            (sid,),
        )
        early_id = cur.lastrowid
        cur.execute(
            "INSERT INTO grade_history "
            "(student_id, subject, grade_value, raw_text, cell_reference, grade_date) "
            "VALUES (?, 'Алгебра', 5.0, '5', 'Все оценки!H7', '2026-05-08')",
            (sid,),
        )
        late_id = cur.lastrowid
        assert late_id > early_id

    # Запускаем init_db — должна сработать 1C-миграция (нет NULL'ов)
    dbm.init_db()

    with sqlite3.connect(str(db_path)) as conn:
        # grade_date теперь NOT NULL
        assert _col_notnull(conn, 'grade_history', 'grade_date')
        # Новый UNIQUE на месте
        assert _has_unique_on(
            conn, 'grade_history',
            ['student_id', 'subject', 'grade_date', 'raw_text'],
        )
        # Выжил один — с меньшим id (Сегодня! строка)
        rows = list(conn.execute(
            "SELECT id, cell_reference FROM grade_history WHERE student_id=?",
            (sid,),
        ))
    assert len(rows) == 1
    assert rows[0][0] == early_id
    assert rows[0][1].startswith("Сегодня!")


def test_1c_skipped_when_nulls_present(tmp_path, monkeypatch, caplog):
    """Если есть строки с grade_date IS NULL — 1C отказывается и просит
    запустить backfill. grade_date остаётся nullable."""
    db_path = tmp_path / "legacy_with_nulls.db"

    monkeypatch.setattr(dbm, 'DB_PATH', str(db_path))
    monkeypatch.setenv('ADMIN_ID', '0')
    dbm.init_db()

    with dbm.get_db_connection() as conn:
        conn.cursor().executescript('''
            DROP TABLE grade_history;
            CREATE TABLE grade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                grade_value REAL,
                raw_text TEXT NOT NULL,
                cell_reference TEXT NOT NULL,
                grade_date DATE,
                date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(student_id) REFERENCES students(id),
                UNIQUE(student_id, cell_reference)
            );
        ''')

    sid = dbm.add_student("Kid", "ss-null")
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO grade_history "
            "(student_id, subject, raw_text, cell_reference, grade_date) "
            "VALUES (?, 'Алгебра', '5', 'Все оценки!H7', NULL)",
            (sid,),
        )

    import logging
    with caplog.at_level(logging.WARNING):
        dbm.init_db()

    with sqlite3.connect(str(db_path)) as conn:
        # 1C не применился — grade_date всё ещё nullable
        assert not _col_notnull(conn, 'grade_history', 'grade_date'), \
            "1C должен был отказаться при NULL grade_date"

    assert any(
        "Skip stage 1C" in rec.message and "backfill_grade_date" in rec.message
        for rec in caplog.records
    ), "Должно быть WARN с подсказкой про backfill"


def test_1c_applies_after_backfill(tmp_path, monkeypatch):
    """1C skipped → backfill заполнил NULL'ы → следующий init_db применяет 1C."""
    db_path = tmp_path / "legacy_pending.db"

    monkeypatch.setattr(dbm, 'DB_PATH', str(db_path))
    monkeypatch.setenv('ADMIN_ID', '0')
    dbm.init_db()

    with dbm.get_db_connection() as conn:
        conn.cursor().executescript('''
            DROP TABLE grade_history;
            CREATE TABLE grade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                grade_value REAL,
                raw_text TEXT NOT NULL,
                cell_reference TEXT NOT NULL,
                grade_date DATE,
                date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(student_id) REFERENCES students(id),
                UNIQUE(student_id, cell_reference)
            );
        ''')

    sid = dbm.add_student("Kid", "ss-pending")
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO grade_history "
            "(student_id, subject, raw_text, cell_reference, grade_date) "
            "VALUES (?, 'Алгебра', '5', 'Все оценки!H7', NULL)",
            (sid,),
        )

    # init_db №1 — skip
    dbm.init_db()
    with sqlite3.connect(str(db_path)) as conn:
        assert not _col_notnull(conn, 'grade_history', 'grade_date')

    # Имитируем backfill: проставляем grade_date вручную
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "UPDATE grade_history SET grade_date = '2026-05-08' "
            "WHERE student_id = ?", (sid,),
        )

    # init_db №2 — теперь должен применить 1C
    dbm.init_db()
    with sqlite3.connect(str(db_path)) as conn:
        assert _col_notnull(conn, 'grade_history', 'grade_date')
        assert _has_unique_on(
            conn, 'grade_history',
            ['student_id', 'subject', 'grade_date', 'raw_text'],
        )
        # Данные на месте
        row = conn.execute(
            "SELECT raw_text, grade_date FROM grade_history WHERE student_id=?",
            (sid,),
        ).fetchone()
    assert row == ('5', '2026-05-08')


def test_1c_idempotent(temp_db):
    """Повторный init_db на уже мигрированной БД не падает и не теряет данных."""
    sid = dbm.add_student("Kid", "ss-idem")
    dbm.add_grade(sid, "Алгебра", 5.0, "5", "Сегодня!Алгебра:2026-05-14",
                  grade_date="2026-05-14")

    dbm.init_db()
    dbm.init_db()

    with sqlite3.connect(temp_db) as conn:
        assert _col_notnull(conn, 'grade_history', 'grade_date')
        assert _has_unique_on(
            conn, 'grade_history',
            ['student_id', 'subject', 'grade_date', 'raw_text'],
        )
        rows = list(conn.execute(
            "SELECT raw_text FROM grade_history WHERE student_id=?", (sid,),
        ))
    assert len(rows) == 1 and rows[0][0] == '5'


def test_1c_has_supporting_index_on_grade_date(temp_db):
    """Покрывающий индекс idx_grade_history_student_grade_date — нужен для
    быстрых WHERE grade_date >= ? (read-path этап 2)."""
    with sqlite3.connect(temp_db) as conn:
        idx_names = _indexes(conn, 'grade_history')
    assert 'idx_grade_history_student_grade_date' in idx_names
