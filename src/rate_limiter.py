"""Per-user rate limiter для Telegram bot handlers.

Thread-safe (под `threading.Lock`), с TTL-чисткой неактивных пользователей.
Никаких зависимостей от bot_instance — поэтому импортируется и в тестах,
и в main.py без побочных эффектов.

Использование:
    from src.rate_limiter import is_rate_limited
    if is_rate_limited(user_id):
        return  # тихо игнорируем
"""
import logging
import threading
import time
from collections import defaultdict

from src.config import (
    RATE_LIMIT_MAX,
    RATE_LIMIT_WINDOW,
    RATE_LIMIT_GC_INTERVAL,
)

logger = logging.getLogger(__name__)

_store: dict = defaultdict(list)
_lock = threading.Lock()
_last_gc = 0.0


def _gc(now: float) -> None:
    """Очищает записи неактивных пользователей. Вызывается под локом."""
    global _last_gc
    if now - _last_gc < RATE_LIMIT_GC_INTERVAL:
        return
    _last_gc = now
    stale = [
        uid for uid, ts_list in _store.items()
        if not ts_list or now - ts_list[-1] > RATE_LIMIT_WINDOW * 6
    ]
    for uid in stale:
        _store.pop(uid, None)
    if stale:
        logger.debug(f"Rate limit GC: removed {len(stale)} stale entries")


def is_rate_limited(user_id: int) -> bool:
    """True, если пользователь превысил лимит (RATE_LIMIT_MAX за RATE_LIMIT_WINDOW сек)."""
    now = time.time()
    with _lock:
        _gc(now)
        timestamps = _store[user_id]
        _store[user_id] = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW]
        if len(_store[user_id]) >= RATE_LIMIT_MAX:
            return True
        _store[user_id].append(now)
        return False


def reset() -> None:
    """Полный сброс — для тестов."""
    with _lock:
        _store.clear()
