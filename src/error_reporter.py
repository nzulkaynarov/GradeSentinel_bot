"""Централизованный репортинг ошибок.

Цель: одна точка для всех `except Exception as e` чтобы:
1) гарантированно логировался stack trace (`exc_info=True`),
2) можно было опционально отправить в Sentry — без условных `if sentry:` по
   всему коду,
3) сохранялась статистика по типам ошибок (для будущей дашборды).

Использование:
    from src.error_reporter import report

    try:
        ...
    except Exception as e:
        report("monitor.fetch_sheet", e, student_id=42)
        # пусть код решит — продолжать или re-raise

Sentry активируется автоматически если `SENTRY_DSN` задан в env. Без него
функция работает как обёртка `logger.exception` с extra-контекстом.
"""
import logging
from typing import Any, Optional

from src.config import SENTRY_DSN, ENVIRONMENT

logger = logging.getLogger(__name__)

_sentry_inited = False
_sentry_module = None


def _try_init_sentry() -> bool:
    """Лениво инициализирует Sentry. Возвращает True если активен."""
    global _sentry_inited, _sentry_module
    if _sentry_inited:
        return _sentry_module is not None
    _sentry_inited = True

    if not SENTRY_DSN:
        return False
    try:
        import sentry_sdk  # type: ignore

        sentry_sdk.init(
            dsn=SENTRY_DSN,
            environment=ENVIRONMENT,
            traces_sample_rate=0.0,  # не нужны traces — только ошибки
            send_default_pii=False,  # не отправляем тела request'ов
        )
        _sentry_module = sentry_sdk
        logger.info(f"Sentry initialized (env={ENVIRONMENT})")
        return True
    except ImportError:
        logger.warning("SENTRY_DSN задан, но пакет sentry_sdk не установлен")
        return False
    except Exception as e:
        logger.error(f"Sentry init failed: {e}")
        return False


def report(scope: str, exc: BaseException, **context: Any) -> None:
    """Логирует ошибку и отправляет в Sentry (если настроен).

    scope: короткий идентификатор места (например, "monitor.fetch_sheet").
    exc: само исключение.
    context: kwargs с дополнительной информацией (student_id, family_id и т.д.).
    """
    ctx_str = " ".join(f"{k}={v}" for k, v in context.items())
    logger.error(f"[{scope}] {type(exc).__name__}: {exc} {ctx_str}", exc_info=exc)

    if _try_init_sentry() and _sentry_module:
        try:
            with _sentry_module.push_scope() as s:
                s.set_tag("scope", scope)
                for k, v in context.items():
                    s.set_extra(k, v)
                _sentry_module.capture_exception(exc)
        except Exception as send_err:
            logger.error(f"Sentry capture failed: {send_err}")


def warn(scope: str, message: str, **context: Any) -> None:
    """Не-fatal предупреждение (нет exception, но что-то странное).
    Уйдёт в Sentry как message, в логи как WARNING."""
    ctx_str = " ".join(f"{k}={v}" for k, v in context.items())
    logger.warning(f"[{scope}] {message} {ctx_str}")

    if _try_init_sentry() and _sentry_module:
        try:
            with _sentry_module.push_scope() as s:
                s.set_tag("scope", scope)
                for k, v in context.items():
                    s.set_extra(k, v)
                _sentry_module.capture_message(message, level="warning")
        except Exception:
            pass
