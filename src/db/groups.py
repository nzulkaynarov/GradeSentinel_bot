"""Семейные групповые чаты (бот добавлен в Telegram-чат семьи).

Один chat_id → одна семья (UNIQUE constraint в схеме). Одна семья может
иметь несколько чатов (например, чат с бабушками+дедушками + отдельный
мини-чат родителей).

Для супергрупп с темами поддерживается `message_thread_id` — уведомления
падают именно в нужную тему.
"""
import logging
from typing import Any, Dict, List, Optional

from src.db.connection import get_db_connection

logger = logging.getLogger(__name__)


def link_group_to_family(family_id: int, chat_id: int, chat_title: str,
                         added_by_parent_id: int,
                         message_thread_id: Optional[int] = None) -> bool:
    """Привязывает Telegram-группу к семье. True если создана, False если
    chat_id уже привязан (к этой или другой семье — UNIQUE constraint)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO family_groups (family_id, chat_id, chat_title, message_thread_id, added_by)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        ''', (family_id, chat_id, chat_title, message_thread_id, added_by_parent_id))
        return cursor.rowcount > 0


def get_family_for_group(chat_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает {'family_id', 'family_name', 'message_thread_id'} для chat_id или None."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT fg.family_id, fg.message_thread_id, f.family_name
            FROM family_groups fg
            JOIN families f ON f.id = fg.family_id
            WHERE fg.chat_id = %s
        ''', (chat_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_groups_for_family(family_id: int) -> List[Dict[str, Any]]:
    """Список {chat_id, message_thread_id} для семьи."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT chat_id, message_thread_id FROM family_groups WHERE family_id = %s',
            (family_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_groups_for_student(student_id: int) -> List[Dict[str, Any]]:
    """Все группы привязанные к семьям этого ученика (с дедупликацией по chat_id)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT fg.chat_id, fg.message_thread_id
            FROM family_groups fg
            JOIN family_links fl ON fl.family_id = fg.family_id
            WHERE fl.student_id = %s
        ''', (student_id,))
        return [dict(row) for row in cursor.fetchall()]


def unlink_group(chat_id: int) -> bool:
    """Удаляет привязку группы. True если удалили, False если не было."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM family_groups WHERE chat_id = %s', (chat_id,))
        return cursor.rowcount > 0


def update_group_thread(chat_id: int, message_thread_id: Optional[int]) -> bool:
    """Меняет тему привязанной группы. None = писать в General."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE family_groups SET message_thread_id = %s WHERE chat_id = %s',
            (message_thread_id, chat_id),
        )
        return cursor.rowcount > 0


__all__ = [
    "link_group_to_family",
    "get_family_for_group",
    "get_groups_for_family",
    "get_groups_for_student",
    "unlink_group",
    "update_group_thread",
]
