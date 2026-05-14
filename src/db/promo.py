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

from src.db.connection import get_db_connection

logger = logging.getLogger(__name__)


def create_promo_code(code: str, plan: str, discount_percent: int = 0,
                      free_months: int = 0, max_uses: int = 1,
                      expires_days: Optional[int] = None) -> bool:
    """Создаёт промокод. Возвращает True если создан.

    expires_days приводится через int() — защита от SQL-инъекции в
    datetime('now', ?+...) modifier-строке (см. CLAUDE.md security note).
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            if expires_days is not None:
                modifier = f'+{int(expires_days)} days'
                cursor.execute('''
                    INSERT INTO promo_codes
                        (code, plan, discount_percent, free_months, max_uses, expires_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now', ?))
                ''', (code.upper(), plan, discount_percent, free_months, max_uses, modifier))
            else:
                cursor.execute('''
                    INSERT INTO promo_codes
                        (code, plan, discount_percent, free_months, max_uses, expires_at)
                    VALUES (?, ?, ?, ?, ?, NULL)
                ''', (code.upper(), plan, discount_percent, free_months, max_uses))
            return True
        except Exception as e:
            logger.error(f"Failed to create promo code: {e}")
            return False


def get_promo_code(code: str) -> Optional[Dict[str, Any]]:
    """Возвращает промокод если он валиден (не исчерпан, не истёк)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM promo_codes
            WHERE code = ? AND used_count < max_uses
              AND (expires_at IS NULL OR expires_at > datetime('now'))
        ''', (code.upper(),))
        row = cursor.fetchone()
        return dict(row) if row else None


def use_promo_code(code: str) -> bool:
    """Увеличивает счётчик использований промокода. True если был свободный слот."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE promo_codes SET used_count = used_count + 1
            WHERE code = ? AND used_count < max_uses
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
        cursor.execute('DELETE FROM promo_codes WHERE code = ?', (code.upper(),))
        return cursor.rowcount > 0


__all__ = [
    "create_promo_code",
    "get_promo_code",
    "use_promo_code",
    "list_promo_codes",
    "delete_promo_code",
]
