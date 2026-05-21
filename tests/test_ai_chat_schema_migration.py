"""Регрессия NAV-001 hotfix: миграция ai_chat_messages для пре-NAV БД.

Сценарий прод-багa 21.05.2026:
- До NAV-001: ai_chat_messages.student_id INTEGER NOT NULL
- Деплой NAV-001 сделал ALTER TABLE ADD COLUMN family_id (nullable)
- НО: ALTER не поменял student_id constraint — он остался NOT NULL
- save_family_chat_message пишет с student_id=NULL → IntegrityError → бот crash

Hotfix: init_db детектит legacy schema (student_id NOT NULL) и пересоздаёт
таблицу с правильной schema (student_id nullable). Этот тест симулирует
сценарий и проверяет что после init_db миграция прошла + новые INSERT
с student_id=NULL работают.
"""
import os
import sys
import sqlite3
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "12345:test")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")


@pytest.fixture
def legacy_ai_chat_db(monkeypatch):
    """Создаёт БД с legacy ai_chat_messages schema (student_id NOT NULL,
    без family_id). Имитирует prod state ДО hotfix."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)

    import src.database_manager as dbm
    monkeypatch.setattr(dbm, 'DB_PATH', path)
    monkeypatch.setenv('ADMIN_ID', '0')

    # Создаём минимальный набор таблиц нужных для backfill JOIN
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    cursor.executescript('''
        CREATE TABLE parents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fio TEXT NOT NULL,
            phone TEXT UNIQUE NOT NULL,
            telegram_id INTEGER UNIQUE,
            role TEXT DEFAULT 'senior'
        );
        CREATE TABLE families (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            family_name TEXT NOT NULL,
            subscription_end TIMESTAMP
        );
        CREATE TABLE students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fio TEXT NOT NULL,
            spreadsheet_id TEXT NOT NULL
        );
        CREATE TABLE family_links (
            family_id INTEGER,
            parent_id INTEGER,
            student_id INTEGER
        );
        -- Legacy schema: student_id NOT NULL, нет family_id
        CREATE TABLE ai_chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO parents (id, fio, phone) VALUES (1, 'Parent', '+998000');
        INSERT INTO families (id, family_name) VALUES (10, 'Test');
        INSERT INTO students (id, fio, spreadsheet_id) VALUES (5, 'Kid', 'ss');
        INSERT INTO family_links (family_id, parent_id, student_id) VALUES (10, 1, 5);
        INSERT INTO ai_chat_messages (telegram_id, student_id, role, content)
        VALUES (111, 5, 'user', 'старый вопрос');
        INSERT INTO ai_chat_messages (telegram_id, student_id, role, content)
        VALUES (111, 5, 'assistant', 'старый ответ');
    ''')
    conn.commit()
    conn.close()

    yield path

    try:
        os.unlink(path)
    except OSError:
        pass


def _student_id_notnull(path):
    """Возвращает True если в текущей схеме student_id всё ещё NOT NULL."""
    conn = sqlite3.connect(path)
    try:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(ai_chat_messages)")
        for row in cursor.fetchall():
            # PRAGMA cols: cid, name, type, notnull, dflt, pk
            if row[1] == 'student_id':
                return bool(row[3])
        return False
    finally:
        conn.close()


def test_legacy_schema_starts_with_notnull(legacy_ai_chat_db):
    """Sanity: fixture создаёт legacy schema корректно."""
    assert _student_id_notnull(legacy_ai_chat_db) is True


def test_init_db_rebuilds_legacy_schema(legacy_ai_chat_db):
    """Hotfix: init_db должен пересоздать таблицу со student_id nullable."""
    import src.database_manager as dbm
    dbm.init_db()

    assert _student_id_notnull(legacy_ai_chat_db) is False


def test_legacy_rows_preserved_with_family_id(legacy_ai_chat_db):
    """После rebuild старые строки сохранены и backfill family_id сработал."""
    import src.database_manager as dbm
    dbm.init_db()

    with dbm.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, telegram_id, student_id, family_id, role, content '
            'FROM ai_chat_messages ORDER BY id'
        )
        rows = [dict(r) for r in cursor.fetchall()]

    assert len(rows) == 2
    assert rows[0]['content'] == 'старый вопрос'
    assert rows[0]['student_id'] == 5
    assert rows[0]['family_id'] == 10  # backfilled
    assert rows[1]['content'] == 'старый ответ'
    assert rows[1]['family_id'] == 10


def test_new_insert_with_null_student_id_works(legacy_ai_chat_db):
    """После rebuild новые INSERT с student_id=NULL не падают (фикс crash'а)."""
    import src.database_manager as dbm
    dbm.init_db()

    # Это и есть то что save_family_chat_message делает — без student_id
    msg_id = dbm.save_family_chat_message(111, 10, 'user', 'новый family-scoped вопрос')
    assert isinstance(msg_id, int)

    history = dbm.get_recent_family_chat_history(111, 10)
    # 2 legacy + 1 новое = 3
    assert len(history) == 3
    assert history[-1]['content'] == 'новый family-scoped вопрос'


def test_idempotent_no_op_on_already_migrated(legacy_ai_chat_db):
    """Повторный init_db на уже мигрированной БД не падает и не рушит данные."""
    import src.database_manager as dbm
    dbm.init_db()
    dbm.init_db()  # вторая прогонка — должна быть no-op для миграции

    with dbm.get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as c FROM ai_chat_messages')
        assert cursor.fetchone()['c'] == 2  # legacy строки на месте
