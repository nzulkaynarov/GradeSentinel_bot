"""Общие фикстуры для тестов GradeSentinel."""
import os
import sys
import tempfile
import pytest

# Делаем src импортируемым
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Грузим локали один раз для всей тестовой сессии. В проде это делает main.py
# при старте; тесты которые проверяют форматированный текст без этого получают
# сырые ключи вместо переводов.
from src.i18n import load_translations as _load_translations
_load_translations()


@pytest.fixture
def temp_db(monkeypatch):
    """Создаёт временную БД и инициализирует схему. Возвращает путь к файлу."""
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)

    # Подменяем путь к БД до импорта database_manager
    import src.database_manager as dbm
    monkeypatch.setattr(dbm, 'DB_PATH', path)

    # Чтобы init_db не пытался создать админа из ENV — выставим пустой
    monkeypatch.setenv('ADMIN_ID', '0')

    dbm.init_db()
    yield path

    try:
        os.unlink(path)
    except OSError:
        pass


@pytest.fixture
def legacy_temp_db(monkeypatch):
    """Pre-1C БД: grade_date nullable + старый UNIQUE(student_id, cell_reference).

    Нужно для тестов backfill-скрипта и legacy-fallback'ов в read-path.
    После init_db делаем DROP+CREATE grade_history со старой схемой —
    это симулирует состояние прод-БД между этапами 1A и 1C.
    """
    fd, path = tempfile.mkstemp(suffix='.db')
    os.close(fd)

    import src.database_manager as dbm
    monkeypatch.setattr(dbm, 'DB_PATH', path)
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
            CREATE INDEX idx_grade_history_student_date ON grade_history(student_id, date_added);
            CREATE INDEX idx_grade_history_student_cell ON grade_history(student_id, cell_reference);
        ''')

    yield path

    try:
        os.unlink(path)
    except OSError:
        pass
