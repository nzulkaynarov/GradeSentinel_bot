"""Key-value store: `settings` таблица.

Хранит как простые пары (например, `scheduler_last_*` маркеры от планировщика),
так и сериализованные структуры — текущие тарифы подписки в `plans` (JSON).

API:
- get_setting / set_setting: универсальный k-v
- get_plans_from_db / save_plans_to_db: типизированный wrapper над ключом 'plans'
"""
import json
import logging
from typing import Any, Dict, Optional

from src.db.connection import get_db_connection

logger = logging.getLogger(__name__)


def get_setting(key: str, default: str = None) -> Optional[str]:
    """Возвращает значение настройки по ключу."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
        row = cursor.fetchone()
        return row['value'] if row else default


def set_setting(key: str, value: str):
    """Устанавливает настройку (upsert по key)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        ''', (key, value))


def get_plans_from_db() -> Optional[Dict[str, Any]]:
    """Возвращает тарифы из БД или None если не заданы / невалидный JSON."""
    raw = get_setting('plans')
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def save_plans_to_db(plans: Dict[str, Any]):
    """Сохраняет тарифы в БД (JSON)."""
    set_setting('plans', json.dumps(plans, ensure_ascii=False))


__all__ = [
    "get_setting",
    "set_setting",
    "get_plans_from_db",
    "save_plans_to_db",
]
