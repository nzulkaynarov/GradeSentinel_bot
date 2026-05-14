"""Инвайт-ссылки для добавления родителей в семью.

Семантика:
- Одноразовые (`is_used`, после использования становятся невалидны).
- Истекают через `expires_hours` после создания (дефолт 48ч, см. config).
- Код — URL-safe token (12 байт случайных = 16 символов base64).

Чистка истёкших — еженедельный scheduler job (см. src/db/maintenance.py).
"""
import logging
import secrets
from typing import Any, Dict, Optional

from src.db.connection import get_db_connection

logger = logging.getLogger(__name__)


def create_invite(family_id: int, created_by_parent_id: int,
                  expires_hours: int = 48) -> str:
    """Создаёт инвайт-ссылку для семьи. Возвращает invite_code."""
    code = secrets.token_urlsafe(12)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO family_invites (family_id, invite_code, created_by, expires_at)
            VALUES (?, ?, ?, datetime('now', ?))
        ''', (family_id, code, created_by_parent_id, f'+{int(expires_hours)} hours'))
    return code


def get_invite(invite_code: str) -> Optional[Dict[str, Any]]:
    """Возвращает данные инвайта, если он валиден (не использован, не истёк)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT fi.*, f.family_name
            FROM family_invites fi
            JOIN families f ON fi.family_id = f.id
            WHERE fi.invite_code = ? AND fi.is_used = 0
              AND fi.expires_at > datetime('now')
        ''', (invite_code,))
        row = cursor.fetchone()
        return dict(row) if row else None


def use_invite(invite_code: str, used_by_parent_id: int) -> bool:
    """Помечает инвайт как использованный. True если был свободный + валидный."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE family_invites SET is_used = 1, used_by = ?
            WHERE invite_code = ? AND is_used = 0
        ''', (used_by_parent_id, invite_code))
        return cursor.rowcount > 0


__all__ = ["create_invite", "get_invite", "use_invite"]
