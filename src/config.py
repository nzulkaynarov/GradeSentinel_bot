"""Централизованные константы конфигурации.

Все «магические числа» собраны здесь, чтобы тюнить без поиска по 20 файлам.
Где это разумно — значения читаются из переменных окружения (для прода/теста).

Импорт:
    from src.config import POLLING_INTERVAL, RATE_LIMIT_MAX
"""
import os


def _env_int(name: str, default: int) -> int:
    """Читает int из ENV с fallback на default. Невалидное значение → default + warning в log."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        import logging
        logging.getLogger(__name__).warning(
            f"Invalid env {name}={raw!r}, using default={default}"
        )
        return default


# ────────────────────────────────────────────────────────────
# Polling / Monitor
# ────────────────────────────────────────────────────────────

# Интервал между циклами проверки Google Sheets (секунды)
POLLING_INTERVAL = _env_int("POLLING_INTERVAL", 300)

# Кол-во worker'ов для параллельного fetch'а Sheets
FETCH_WORKERS = _env_int("FETCH_WORKERS", 8)

# Сколько consecutive failures по одному ученику → алерт
SHEET_FAILURE_THRESHOLD = _env_int("SHEET_FAILURE_THRESHOLD", 5)

# Cooldown между алертами по одному «зависшему» ученику (часы)
SHEET_FAILURE_ALERT_COOLDOWN_HOURS = _env_int("SHEET_FAILURE_ALERT_COOLDOWN_HOURS", 24)


# ────────────────────────────────────────────────────────────
# Rate limiting (per-user)
# ────────────────────────────────────────────────────────────

RATE_LIMIT_MAX = _env_int("RATE_LIMIT_MAX", 5)         # запросов
RATE_LIMIT_WINDOW = _env_int("RATE_LIMIT_WINDOW", 10)  # за N секунд
RATE_LIMIT_GC_INTERVAL = _env_int("RATE_LIMIT_GC_INTERVAL", 600)  # как часто чистить stale


# ────────────────────────────────────────────────────────────
# Кэш user panel (TTL)
# ────────────────────────────────────────────────────────────

PANEL_CACHE_TTL = _env_int("PANEL_CACHE_TTL", 30)  # секунды


# ────────────────────────────────────────────────────────────
# Тихие часы (Asia/Tashkent)
# ────────────────────────────────────────────────────────────

TIMEZONE_OFFSET_HOURS = _env_int("TIMEZONE_OFFSET_HOURS", 5)
QUIET_HOURS_START = _env_int("QUIET_HOURS_START", 22)
QUIET_HOURS_END = _env_int("QUIET_HOURS_END", 7)


# ────────────────────────────────────────────────────────────
# Архивирование БД
# ────────────────────────────────────────────────────────────

GRADE_ARCHIVE_DAYS = _env_int("GRADE_ARCHIVE_DAYS", 180)
NOTIFICATION_QUEUE_TTL_HOURS = _env_int("NOTIFICATION_QUEUE_TTL_HOURS", 48)
EXPIRED_INVITE_TTL_DAYS = _env_int("EXPIRED_INVITE_TTL_DAYS", 30)


# ────────────────────────────────────────────────────────────
# Telegram broadcast / send_with_retry
# ────────────────────────────────────────────────────────────

BROADCAST_DELAY_SECONDS = float(os.environ.get("BROADCAST_DELAY_SECONDS", "0.04"))
BROADCAST_MAX_RETRY_AFTER = _env_int("BROADCAST_MAX_RETRY_AFTER", 30)


# ────────────────────────────────────────────────────────────
# Heartbeat
# ────────────────────────────────────────────────────────────

HEARTBEAT_INTERVAL = _env_int("HEARTBEAT_INTERVAL", 30)


# ────────────────────────────────────────────────────────────
# Лимиты доменной логики
# ────────────────────────────────────────────────────────────

MAX_CHILDREN_PER_FAMILY = _env_int("MAX_CHILDREN_PER_FAMILY", 5)
INVITE_EXPIRES_HOURS = _env_int("INVITE_EXPIRES_HOURS", 48)


# ────────────────────────────────────────────────────────────
# Sentry / observability (опционально)
# ────────────────────────────────────────────────────────────

SENTRY_DSN = os.environ.get("SENTRY_DSN", "")  # пусто = выключен
ENVIRONMENT = os.environ.get("ENVIRONMENT", "production")
