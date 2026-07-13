"""Единая точка входа к соединению с БД.

Миграция SQLite → PostgreSQL (2026-06-29): соединение теперь даёт src/db/pg.py
(psycopg v3 + пул). Все модули src/db/* импортируют get_db_connection отсюда —
значит переключение бэкенда сделано в одном месте.
"""
from src.db.pg import (  # noqa: F401
    ForeignKeyViolation,
    IntegrityError,
    OperationalError,
    UniqueViolation,
    conn_or_new,
    get_db_connection,
)

__all__ = [
    "get_db_connection",
    "conn_or_new",
    "IntegrityError",
    "UniqueViolation",
    "ForeignKeyViolation",
    "OperationalError",
]
