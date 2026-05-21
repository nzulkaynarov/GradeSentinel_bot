"""Conversation history для AI-чата (PR_D R6).

Хранит сообщения родитель↔AI per (telegram_id, student_id) — каждый ребёнок
имеет отдельную ветку. Используется для multi-turn чата: follow-up вопросы
с памятью прошлых ответов («а что насчёт следующей четверти?» после
вопроса про прошлый месяц).

Limit: 20 сообщений в контексте AI (10 пар user+assistant). Старше — не
шлём в Anthropic API (экономия токенов + latency).
"""
import logging
from typing import List, Dict, Any

from src.db.connection import get_db_connection

logger = logging.getLogger(__name__)

# Максимум сообщений в context window. 20 = 10 пар user+assistant
# (~3000 tokens средняя глубина beседы). Старые молча отбрасываются —
# для AI они невидимы, но в БД остаются для UI history view.
MAX_HISTORY_FOR_AI = 20


def save_chat_message(telegram_id: int, student_id: int, role: str, content: str) -> int:
    """Сохраняет одно сообщение (user или assistant) и возвращает row id.

    PR_H3: id используется для привязки feedback к конкретному ответу AI.
    Backward compatible — callers которые игнорят return value не ломаются."""
    if role not in ('user', 'assistant'):
        raise ValueError(f"Invalid role: {role!r}")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO ai_chat_messages (telegram_id, student_id, role, content) '
            'VALUES (?, ?, ?, ?)',
            (telegram_id, student_id, role, content),
        )
        return cursor.lastrowid


def save_feedback(message_id: int, telegram_id: int, rating: int,
                  comment: str = None) -> None:
    """Сохраняет 👍/👎 на assistant-сообщение. UPSERT по message_id —
    если юзер передумал (👍 → 👎), заменяем rating и обновляем timestamp.

    rating: 1 (positive) или -1 (negative). Любое другое значение → ValueError."""
    if rating not in (1, -1):
        raise ValueError(f"Invalid rating: {rating!r}, expected 1 or -1")
    with get_db_connection() as conn:
        conn.cursor().execute(
            'INSERT INTO ai_chat_feedback (message_id, telegram_id, rating, comment) '
            'VALUES (?, ?, ?, ?) '
            'ON CONFLICT(message_id) DO UPDATE SET '
            '  rating=excluded.rating, '
            '  comment=excluded.comment, '
            '  created_at=CURRENT_TIMESTAMP',
            (message_id, telegram_id, rating, comment),
        )


def get_feedback_for_message(message_id: int):
    """Возвращает feedback dict {rating, comment, created_at} или None."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT rating, comment, created_at FROM ai_chat_feedback WHERE message_id = ?',
            (message_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return dict(row)


def get_message_owner(message_id: int):
    """Возвращает telegram_id владельца сообщения, или None если такого нет.
    Используется для авторизации feedback'а — нельзя оценивать чужие чаты."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT telegram_id FROM ai_chat_messages WHERE id = ?',
            (message_id,),
        )
        row = cursor.fetchone()
        return row['telegram_id'] if row else None


def get_recent_chat_history(
    telegram_id: int, student_id: int, limit: int = MAX_HISTORY_FOR_AI
) -> List[Dict[str, Any]]:
    """Возвращает последние `limit` сообщений по chronological order (oldest first).

    Для multi-turn передаётся в Anthropic Messages API as `messages` array.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, role, content, created_at FROM ai_chat_messages '
            'WHERE telegram_id = ? AND student_id = ? '
            'ORDER BY id DESC LIMIT ?',
            (telegram_id, student_id, limit),
        )
        rows = list(reversed(cursor.fetchall()))
        # PR_H3: id нужен webapp UI для привязки feedback-кнопок к assistant
        # сообщениям. answer_parent_question его игнорит — там только role/content.
        return [
            {"id": r["id"], "role": r["role"], "content": r["content"],
             "created_at": r["created_at"]}
            for r in rows
        ]


def clear_chat_history(telegram_id: int, student_id: int):
    """Очищает историю чата (например, юзер нажал «начать заново»)."""
    with get_db_connection() as conn:
        conn.cursor().execute(
            'DELETE FROM ai_chat_messages WHERE telegram_id = ? AND student_id = ?',
            (telegram_id, student_id),
        )


__all__ = [
    "save_chat_message",
    "get_recent_chat_history",
    "clear_chat_history",
    "save_feedback",
    "get_feedback_for_message",
    "get_message_owner",
    "MAX_HISTORY_FOR_AI",
]
