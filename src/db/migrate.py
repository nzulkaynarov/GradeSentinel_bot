"""Применение миграций Alembic программно (старт бота + тест-харнес).

Заменяет старые in-code SQLite-миграции (PRAGMA table_info → ALTER). Использует
migrations/env.py, который берёт URL из окружения (DATABASE_URL / PG*).
"""
import logging
import os

logger = logging.getLogger(__name__)

# Корень репозитория: .../GradeSentinel_bot (src/db/migrate.py → ../../).
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def apply_migrations(revision: str = "head") -> None:
    """alembic upgrade <revision>. Идемпотентно; создаёт/обновляет схему."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_ROOT, "migrations"))
    logger.info("Applying Alembic migrations -> %s", revision)
    command.upgrade(cfg, revision)
