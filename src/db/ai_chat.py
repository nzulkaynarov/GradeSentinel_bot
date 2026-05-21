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


def save_chat_message(telegram_id: int, student_id: int, role: str, content: str):
    """Сохраняет одно сообщение (user или assistant). Validates role."""
    if role not in ('user', 'assistant'):
        raise ValueError(f"Invalid role: {role!r}")
    with get_db_connection() as conn:
        conn.cursor().execute(
            'INSERT INTO ai_chat_messages (telegram_id, student_id, role, content) '
            'VALUES (?, ?, ?, ?)',
            (telegram_id, student_id, role, content),
        )


def get_recent_chat_history(
    telegram_id: int, student_id: int, limit: int = MAX_HISTORY_FOR_AI
) -> List[Dict[str, Any]]:
    """Возвращает последние `limit` сообщений по chronological order (oldest first).

    Для multi-turn передаётся в Anthropic Messages API as `messages` array.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT role, content, created_at FROM ai_chat_messages '
            'WHERE telegram_id = ? AND student_id = ? '
            'ORDER BY id DESC LIMIT ?',
            (telegram_id, student_id, limit),
        )
        rows = list(reversed(cursor.fetchall()))
        return [
            {"role": r["role"], "content": r["content"], "created_at": r["created_at"]}
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
    "MAX_HISTORY_FOR_AI",
]
