"""Утилиты для безопасной работы с Telegram Bot API.

send_with_retry — обёртка вокруг любого вызова Telegram API, которая корректно
обрабатывает 429 RetryAfter (FloodControl), сетевые таймауты и другие транзиентные
ошибки. Используется в broadcast и в массовых уведомлениях.
"""
import logging
import time
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# Транзиентные коды Telegram API: 429 (rate limit), 502/503/504 (gateway issues)
_TRANSIENT_HTTP = {429, 500, 502, 503, 504}
# Безусловно «навсегда» — нет смысла ретраить
_TERMINAL_HTTP = {400, 403}


def _extract_retry_after(exception: Exception) -> Optional[int]:
    """Парсит retry_after из ApiTelegramException pyTelegramBotAPI."""
    try:
        result = getattr(exception, 'result_json', None) or getattr(exception, 'result', None)
        if isinstance(result, dict):
            params = result.get('parameters') or {}
            ra = params.get('retry_after')
            if ra is not None:
                return int(ra)
    except Exception:
        return None
    return None


def _http_code(exception: Exception) -> Optional[int]:
    """Возвращает HTTP-код ошибки если он есть, иначе None."""
    code = getattr(exception, 'error_code', None)
    if code is not None:
        try:
            return int(code)
        except (TypeError, ValueError):
            return None
    return None


def send_with_retry(
    func: Callable,
    *args,
    max_attempts: int = 3,
    base_delay: float = 0.05,
    max_retry_after: int = 60,
    **kwargs,
) -> Tuple[bool, Optional[Exception]]:
    """Выполняет Telegram API вызов с обработкой 429 RetryAfter.

    Возвращает (success, last_exception).
    - success=True если вызов прошёл (возможно после ретраев).
    - При 403 (бот заблокирован пользователем) — success=False, попытки не повторяются.
    - При retry_after > max_retry_after — пропускаем (не блокируем broadcast надолго).
    """
    last_exc: Optional[Exception] = None
    attempt = 0
    while attempt < max_attempts:
        try:
            func(*args, **kwargs)
            return True, None
        except Exception as e:
            last_exc = e
            code = _http_code(e)

            # Терминальные — нет смысла ретраить
            if code in _TERMINAL_HTTP:
                return False, e

            # 429 → ждём retry_after
            if code == 429:
                ra = _extract_retry_after(e) or 5
                if ra > max_retry_after:
                    logger.warning(f"Telegram retry_after={ra}s > max={max_retry_after}s, skipping")
                    return False, e
                logger.info(f"Telegram flood control, sleeping {ra}s (attempt {attempt+1}/{max_attempts})")
                time.sleep(ra)
                attempt += 1
                continue

            # Транзиентные сетевые/серверные — exp backoff
            if code in _TRANSIENT_HTTP or code is None:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Telegram API transient error {code}: {e}; retry in {delay:.2f}s")
                time.sleep(delay)
                attempt += 1
                continue

            # Иначе — неизвестная ошибка, не ретраим
            return False, e

    return False, last_exc
