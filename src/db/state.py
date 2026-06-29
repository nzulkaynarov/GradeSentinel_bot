"""Персистентное состояние пользователя — переживает рестарт бота.

Три независимые подобласти, объединённые тем что все хранят небольшое
key→value по telegram_id или message_id:

- `user_states` — текущий шаг multi-step flow (pending_lang, pending_invite,
   broadcast и т.д.). Заменяет in-memory `register_next_step_handler`.
   Полная миграция handlers на эту схему — отдельный долг.
- `app_states.last_menu_msg_id` — id последнего отправленного menu-сообщения
   (для удаления при обновлении). Per-user, не per-state.
- `support_msg_map` — обратная связь admin-group ↔ user для поддержки
   (когда админ отвечает Reply на support-сообщение, по `message_id`
   находим оригинального пользователя).
"""
import logging
from typing import Any, Dict, Optional

from src.db.connection import get_db_connection

logger = logging.getLogger(__name__)


# ─── last_menu_msg_id ────────────────────────────────────────────────
def get_last_menu_id(user_id: int) -> Optional[int]:
    """Возвращает ID последнего сообщения меню для пользователя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT last_menu_msg_id FROM app_states WHERE user_id = %s', (user_id,))
        row = cursor.fetchone()
        return row['last_menu_msg_id'] if row else None


def update_last_menu_id(user_id: int, msg_id: Optional[int]):
    """Обновляет ID последнего сообщения меню (upsert по user_id)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO app_states (user_id, last_menu_msg_id)
            VALUES (%s, %s)
            ON CONFLICT(user_id) DO UPDATE SET last_menu_msg_id = EXCLUDED.last_menu_msg_id
        ''', (user_id, msg_id))


# ─── user_states (FSM) ───────────────────────────────────────────────
def set_user_state(user_id: int, state: str, data: str = None):
    """Сохраняет состояние пользователя в БД (upsert по user_id)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO user_states (user_id, state, data, updated_at)
            VALUES (%s, %s, %s, (now() at time zone 'utc'))
            ON CONFLICT(user_id) DO UPDATE SET
                state = EXCLUDED.state,
                data = EXCLUDED.data,
                updated_at = (now() at time zone 'utc')
        ''', (user_id, state, data))


def get_user_state(user_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает текущее состояние пользователя или None."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT state, data FROM user_states WHERE user_id = %s', (user_id,))
        row = cursor.fetchone()
        if row:
            return {'state': row['state'], 'data': row['data']}
        return None


def clear_user_state(user_id: int):
    """Удаляет состояние пользователя (по завершении flow или при сбросе)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM user_states WHERE user_id = %s', (user_id,))


# ─── support_msg_map ─────────────────────────────────────────────────
def save_support_msg_map(admin_msg_id: int, user_id: int):
    """Связь между сообщением в админ-группе и пользователем-отправителем."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO support_msg_map (admin_msg_id, user_id)
            VALUES (%s, %s)
            ON CONFLICT (admin_msg_id) DO UPDATE SET user_id = EXCLUDED.user_id
        ''', (admin_msg_id, user_id))


def get_support_user_id(admin_msg_id: int) -> Optional[int]:
    """Возвращает user_id по ID сообщения в админ-группе. Используется когда
    админ отвечает Reply на пересланное support-сообщение."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT user_id FROM support_msg_map WHERE admin_msg_id = %s',
            (admin_msg_id,),
        )
        row = cursor.fetchone()
        return row['user_id'] if row else None


__all__ = [
    "get_last_menu_id",
    "update_last_menu_id",
    "set_user_state",
    "get_user_state",
    "clear_user_state",
    "save_support_msg_map",
    "get_support_user_id",
]
