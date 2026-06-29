"""Слой подключения к PostgreSQL (psycopg v3 + пул соединений).

Замена SQLite-подключения (`sqlite3`). Миграция SQLite → PostgreSQL, 2026-06-29
(см. Docs/migration-sqlite-to-postgres-estimate-2026-06-29.md).

Контракт `get_db_connection()` намеренно совпадает со старым sqlite-вариантом,
чтобы вызывающий код менялся минимально:

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("... WHERE x = %s", (val,))   # плейсхолдеры: %s (psycopg), не ?
    # commit на чистом выходе, rollback при исключении, соединение возвращается в пул

Строки (`Row`) ведут себя как `sqlite3.Row`: работает и `row['col']`, и `row[0]`,
и `dict(row)`, и `len(row)` — чтобы не переписывать сотни вызовов доступа к полям.

Решения миграции (подтверждены владельцем 2026-06-29):
  • даты — `timestamp` (наивный UTC); арифметика +5ч в SQL нормализует через UTC;
  • пул `psycopg_pool` с проверкой живости/реконнектом (БД по сети через WireGuard);
  • при недоступности БД бот деградирует явно (см. вызывающий код / хендлеры).
"""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Optional

import psycopg
from psycopg import errors as _pg_errors
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

# ── Исключения, переэкспортируемые для dialect-agnostic catch'ей ──────────────
# Старый код ловил sqlite3.IntegrityError; теперь call-сайты ловят это.
IntegrityError = _pg_errors.IntegrityError          # базовый класс нарушений целостности
UniqueViolation = _pg_errors.UniqueViolation        # подкласс IntegrityError (дубль UNIQUE)
ForeignKeyViolation = _pg_errors.ForeignKeyViolation
OperationalError = _pg_errors.OperationalError      # сетевые/доступность БД


def _dsn() -> str:
    """DSN из окружения. Прод: DATABASE_URL=postgresql://user:pw@10.0.0.2:5432/db?sslmode=require.
    Fallback на дискретные PG*-переменные (удобно для docker-compose/тестов)."""
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        return dsn
    host = os.environ.get("PGHOST", "localhost")
    port = os.environ.get("PGPORT", "5432")
    user = os.environ.get("PGUSER", "gradesentinel")
    pwd = os.environ.get("PGPASSWORD", "")
    db = os.environ.get("PGDATABASE", "gradesentinel")
    return f"host={host} port={port} user={user} password={pwd} dbname={db}"


class Row(Mapping):
    """sqlite3.Row-совместимая строка: поддерживает row['name'] И row[0]/срезы,
    len(row), итерацию по ключам и dict(row)."""

    __slots__ = ("_cols", "_vals", "_idx")

    def __init__(self, cols, vals):
        self._cols = cols
        self._vals = vals
        self._idx = None  # ленивый {name: position}

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._vals[key]
        if self._idx is None:
            self._idx = {col: pos for pos, col in enumerate(self._cols)}
        return self._vals[self._idx[key]]

    def __iter__(self):
        # Mapping-протокол: итерация по ключам → dict(row) даёт {col: val}.
        return iter(self._cols)

    def __len__(self):
        return len(self._vals)

    def keys(self):
        return list(self._cols)

    def __repr__(self):
        return f"Row({dict(self)!r})"


def _row_factory(cursor):
    """psycopg row_factory: на каждый запрос строит maker, отдающий Row."""
    description = cursor.description
    cols = [c.name for c in description] if description else []

    def make(values):
        return Row(cols, values)

    return make


def _configure(conn: "psycopg.Connection") -> None:
    """Применяется пулом к каждому новому соединению."""
    conn.row_factory = _row_factory
    conn.autocommit = False  # нужны транзакции (commit-on-exit + SAVEPOINT-логика)


_pool: Optional[ConnectionPool] = None


def _get_pool() -> ConnectionPool:
    """Ленивая инициализация пула (один на процесс)."""
    global _pool
    if _pool is None:
        pool = ConnectionPool(
            conninfo=_dsn(),
            min_size=int(os.environ.get("DB_POOL_MIN", "1")),
            max_size=int(os.environ.get("DB_POOL_MAX", "5")),
            timeout=float(os.environ.get("DB_POOL_TIMEOUT", "20")),
            max_lifetime=float(os.environ.get("DB_POOL_MAX_LIFETIME", "1800")),
            # Проверка живости на checkout + реконнект — устойчивость к морганиям
            # WireGuard/перезапуску PG (бот не должен падать на мёртвом соединении).
            check=ConnectionPool.check_connection,
            configure=_configure,
            kwargs={"connect_timeout": int(os.environ.get("DB_CONNECT_TIMEOUT", "20"))},
            open=False,
        )
        pool.open(wait=True, timeout=float(os.environ.get("DB_POOL_OPEN_TIMEOUT", "20")))
        _pool = pool
        logger.info("PostgreSQL connection pool opened (max_size=%s).", pool.max_size)
    return _pool


def close_pool() -> None:
    """Корректно закрыть пул (graceful shutdown бота / teardown тестов)."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def get_db_connection():
    """Заимствует соединение из пула. Commit на чистом выходе, rollback при
    исключении, затем соединение возвращается в пул (psycopg_pool делает это сам).

    Зеркалит старый sqlite-контракт: явный conn.commit() не нужен.
    """
    pool = _get_pool()
    with pool.connection() as conn:
        yield conn
