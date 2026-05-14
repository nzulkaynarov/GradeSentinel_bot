"""Очередь отложенных уведомлений (тихие часы 22:00–07:00 Tashkent).

Когда `is_quiet_hours()` True — monitor НЕ шлёт сразу, а пишет в очередь.
Утром в 07:00 scheduler job `_flush_quiet_hours_queue` забирает всё и
шлёт агрегированной сводкой.
"""
import logging
from typing import List

from src.db.connection import get_db_connection

logger = logging.getLogger(__name__)


def queue_notification(telegram_id: int, message: str):
    """Сохраняет уведомление в очередь для отложенной отправки."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO notification_queue (telegram_id, message) VALUES (?, ?)',
            (telegram_id, message),
        )


def get_and_clear_queued_notifications(telegram_id: int) -> List[str]:
    """Извлекает и удаляет все отложенные уведомления для пользователя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT message FROM notification_queue WHERE telegram_id = ? ORDER BY created_at',
            (telegram_id,),
        )
        messages = [row['message'] for row in cursor.fetchall()]
        cursor.execute(
            'DELETE FROM notification_queue WHERE telegram_id = ?',
            (telegram_id,),
        )
        return messages


def get_all_queued_telegram_ids() -> List[int]:
    """Уникальные telegram_id с отложенными уведомлениями. Для morning flush."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT telegram_id FROM notification_queue')
        return [row['telegram_id'] for row in cursor.fetchall()]


__all__ = [
    "queue_notification",
    "get_and_clear_queued_notifications",
    "get_all_queued_telegram_ids",
]
