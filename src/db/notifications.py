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


# ─── Group notification queue (тихие часы для семейных чатов) ───────
# Появилась после инцидента 2026-05-21: группы НЕ уважали тихие часы и
# любой баг в дедупе мгновенно превращался в спам в семейный чат.
# В PR #42 — sledom: drop в тихие часы. В этом модуле — proper queue:
# пишем ночью, флэшим в 07:00 вместе с личной очередью.
#
# Ключ — (chat_id, thread_id). В одной супергруппе может быть несколько
# тем (по теме на семью). reply_markup НЕ сохраняем — после ночи кнопки
# могут устареть, проще без них.
def queue_group_notification(chat_id: int, message_thread_id: 'int | None',
                             message: str):
    """Сохраняет групповое уведомление в очередь для morning flush."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO group_notification_queue '
            '(chat_id, message_thread_id, message) VALUES (?, ?, ?)',
            (chat_id, message_thread_id, message),
        )


def get_and_clear_queued_group_notifications(
    chat_id: int, message_thread_id: 'int | None'
) -> List[str]:
    """Извлекает и удаляет все отложенные сообщения для (chat_id, thread_id).
    `IS` vs `=` для NULL: используем `IS` через двойную ветку запроса."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if message_thread_id is None:
            cursor.execute(
                'SELECT message FROM group_notification_queue '
                'WHERE chat_id = ? AND message_thread_id IS NULL '
                'ORDER BY created_at',
                (chat_id,),
            )
            messages = [row['message'] for row in cursor.fetchall()]
            cursor.execute(
                'DELETE FROM group_notification_queue '
                'WHERE chat_id = ? AND message_thread_id IS NULL',
                (chat_id,),
            )
        else:
            cursor.execute(
                'SELECT message FROM group_notification_queue '
                'WHERE chat_id = ? AND message_thread_id = ? '
                'ORDER BY created_at',
                (chat_id, message_thread_id),
            )
            messages = [row['message'] for row in cursor.fetchall()]
            cursor.execute(
                'DELETE FROM group_notification_queue '
                'WHERE chat_id = ? AND message_thread_id = ?',
                (chat_id, message_thread_id),
            )
        return messages


def get_all_queued_group_targets() -> List[dict]:
    """Уникальные (chat_id, thread_id) с отложенными сообщениями. Для morning flush."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT DISTINCT chat_id, message_thread_id FROM group_notification_queue'
        )
        return [
            {'chat_id': row['chat_id'], 'message_thread_id': row['message_thread_id']}
            for row in cursor.fetchall()
        ]


__all__ = [
    "queue_notification",
    "get_and_clear_queued_notifications",
    "get_all_queued_telegram_ids",
    "queue_group_notification",
    "get_and_clear_queued_group_notifications",
    "get_all_queued_group_targets",
]
