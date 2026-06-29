"""Alembic environment for GradeSentinel (PostgreSQL via psycopg v3).

URL берётся из окружения (DATABASE_URL / дискретные PG*), а не из alembic.ini —
секреты не коммитим. SQLAlchemy-драйвер принудительно psycopg v3
(postgresql+psycopg://), который уже в requirements.
"""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config


def _url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("ALEMBIC_URL")
    if not url:
        url = (
            "postgresql+psycopg://%s:%s@%s:%s/%s"
            % (
                os.environ.get("PGUSER", "gradesentinel"),
                os.environ.get("PGPASSWORD", ""),
                os.environ.get("PGHOST", "localhost"),
                os.environ.get("PGPORT", "5432"),
                os.environ.get("PGDATABASE", "gradesentinel"),
            )
        )
    # SQLAlchemy должен использовать драйвер psycopg v3.
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    elif url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    return url


if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:  # noqa: BLE001 — логирование не критично для миграций
        pass


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
