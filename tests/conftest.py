"""Общие фикстуры для тестов GradeSentinel (PostgreSQL).

Миграция SQLite → PostgreSQL (2026-06-29): тесты гоняются против ТЕСТОВОЙ PG
(env DATABASE_URL / PG*). Схема создаётся один раз на сессию через Alembic;
изоляция между тестами — TRUNCATE всех таблиц (RESTART IDENTITY CASCADE).

Локальный прогон: docker compose -f docker-compose.test.yml run --rm tests
(postgres:17 + python:3.12). Локальный Python 3.9 не подходит — нужен Docker.
"""
import os
import sys

import pytest

# Делаем src импортируемым
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Грузим локали один раз на сессию (в проде это делает main.py при старте).
from src.i18n import load_translations as _load_translations  # noqa: E402

_load_translations()


@pytest.fixture(scope="session", autouse=True)
def _db_schema():
    """Создаёт схему один раз на сессию (Alembic upgrade head). Без сконфигурированной
    PG БД-тесты пропускаются (а не падают), чтобы не-БД тесты могли идти где угодно."""
    if not (os.environ.get("DATABASE_URL") or os.environ.get("PGHOST")):
        pytest.skip("PostgreSQL не сконфигурирован (DATABASE_URL/PGHOST)")
    os.environ.setdefault("ADMIN_ID", "0")  # не создавать админа из ENV
    import src.database_manager as dbm

    dbm.init_db()
    yield
    from src.db.pg import close_pool

    close_pool()


def _truncate_all() -> None:
    from src.db.pg import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename <> 'alembic_version'"
        )
        tables = [row[0] for row in cursor.fetchall()]
        if tables:
            cursor.execute(
                "TRUNCATE TABLE "
                + ", ".join('"%s"' % t for t in tables)
                + " RESTART IDENTITY CASCADE"
            )


@pytest.fixture
def temp_db(monkeypatch):
    """Чистая БД для теста: TRUNCATE всех таблиц до и после теста.

    Историческая совместимость: раньше фикстура возвращала путь к временному
    sqlite-файлу и подменяла DB_PATH. Теперь БД — общая тестовая PG, а изоляция
    обеспечивается truncate. Возвращает None (путь больше не нужен)."""
    monkeypatch.setenv("ADMIN_ID", "0")
    _truncate_all()
    yield None
    _truncate_all()
