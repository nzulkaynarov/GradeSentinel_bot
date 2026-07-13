"""Промокоды для скидок и подарочных подписок.

Этот модуль — первый physical extraction из database_manager.py (раньше там
лежал, остальные функции пока re-export через shim).

API:
- create_promo_code: создать новый код (с лимитом использований и TTL)
- get_promo_code: валидация (не исчерпан, не истёк) и возврат деталей
- use_promo_code: инкремент used_count
- list_promo_codes: листинг для admin panel
- delete_promo_code: удаление

Поля promo_codes (схема в database_manager.init_db):
- code (UPPERCASE), plan, discount_percent, free_months, max_uses,
  used_count, expires_at, created_at
"""
import logging
from typing import Any, Dict, List, Optional

from src.db.connection import (
    IntegrityError,
    UniqueViolation,
    conn_or_new,
    get_db_connection,
)

logger = logging.getLogger(__name__)


def create_promo_code(code: str, plan: str, discount_percent: int = 0,
                      free_months: int = 0, max_uses: int = 1,
                      expires_days: Optional[int] = None) -> bool:
    """Создаёт промокод. Возвращает True если создан.

    expires_days приводится через int() — защита от SQL-инъекции и корректный
    бинд числа в интервал (см. CLAUDE.md security note). Нечисловое значение →
    возврат False (промокод не создаётся), а не исключение.
    """
    if expires_days is not None:
        try:
            expires_days = int(expires_days)
        except (ValueError, TypeError):
            logger.error("Invalid expires_days for promo %r: %r", code, expires_days)
            return False
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            if expires_days is not None:
                cursor.execute('''
                    INSERT INTO promo_codes
                        (code, plan, discount_percent, free_months, max_uses, expires_at)
                    VALUES (%s, %s, %s, %s, %s,
                            (now() at time zone 'utc') + %s * interval '1 day')
                ''', (code.upper(), plan, discount_percent, free_months, max_uses,
                      expires_days))
            else:
                cursor.execute('''
                    INSERT INTO promo_codes
                        (code, plan, discount_percent, free_months, max_uses, expires_at)
                    VALUES (%s, %s, %s, %s, %s, NULL)
                ''', (code.upper(), plan, discount_percent, free_months, max_uses))
            return True
        except (UniqueViolation, IntegrityError) as e:
            logger.error(f"Failed to create promo code: {e}")
            return False


def get_promo_code(code: str) -> Optional[Dict[str, Any]]:
    """Возвращает промокод если он валиден (не исчерпан, не истёк)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM promo_codes
            WHERE code = %s AND used_count < max_uses
              AND (expires_at IS NULL OR expires_at > (now() at time zone 'utc'))
        ''', (code.upper(),))
        row = cursor.fetchone()
        return dict(row) if row else None


def use_promo_code(code: str, conn=None) -> bool:
    """Увеличивает счётчик использований промокода. True если был свободный слот.

    Атомарный guard `WHERE used_count < max_uses` — при max_uses=1 два
    одновременных вызова: первый вернёт True, второй (rowcount=0) вернёт False,
    поэтому начислять подписку МОЖНО только если этот вызов вернул True.

    `conn` — опционально: чтобы занять слот в той же транзакции, что и
    extend_subscription/record_payment (см. _apply_promo_to_family).
    """
    with conn_or_new(conn) as c:
        cursor = c.cursor()
        cursor.execute('''
            UPDATE promo_codes SET used_count = used_count + 1
            WHERE code = %s AND used_count < max_uses
        ''', (code.upper(),))
        return cursor.rowcount > 0


def list_promo_codes() -> List[Dict[str, Any]]:
    """Возвращает все промокоды для admin panel."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM promo_codes ORDER BY created_at DESC')
        return [dict(row) for row in cursor.fetchall()]


def delete_promo_code(code: str) -> bool:
    """Удаляет промокод. True если удалили."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM promo_codes WHERE code = %s', (code.upper(),))
        return cursor.rowcount > 0


__all__ = [
    "create_promo_code",
    "get_promo_code",
    "use_promo_code",
    "list_promo_codes",
    "delete_promo_code",
]
