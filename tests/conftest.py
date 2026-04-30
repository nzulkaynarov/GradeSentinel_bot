"""Общие фикстуры для тестов GradeSentinel."""
import os
import sys
import tempfile
import pytest

# Делаем src импортируемым
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


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
