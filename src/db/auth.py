"""Авторизация и профиль пользователя.

API:
- Создание/lookup родителей: add_parent, get_parent_by_phone,
  get_parent_by_telegram, get_parent_id_by_telegram, update_parent_telegram_id
- Профильные поля: get_parent_role, get_user_lang/set_user_lang,
  get_notify_mode/set_notify_mode
- Авторизационные предикаты: is_head_of_any_family, is_head_of_family,
  is_member_of_family, can_manage_family, get_families_for_head

Авторизационные предикаты используются ВСЕМИ callback handler'ами с
family_id для предотвращения IDOR (см. CLAUDE.md security note).
`can_manage_family` — единственный source of truth для admin OR head.
"""
import logging
from typing import Any, Dict, List, Optional

from src.db.connection import get_db_connection

logger = logging.getLogger(__name__)


# ─── Lookup по телефону / telegram_id ────────────────────────────────
def _normalize_phone(phone: str) -> str:
    """Удаляет ведущий + для согласованного matching'а в БД."""
    return phone.replace("+", "")


def get_parent_by_phone(phone: str) -> Optional[Dict[str, Any]]:
    """Находит родителя по номеру телефона. Match с/без ведущего +."""
    phone = _normalize_phone(phone)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM parents WHERE phone = ? OR phone = ?',
            (phone, "+" + phone),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def get_parent_by_telegram(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает полную запись родителя по telegram_id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM parents WHERE telegram_id = ?', (telegram_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_parent_id_by_telegram(telegram_id: int) -> Optional[int]:
    """Возвращает internal parent ID по telegram_id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM parents WHERE telegram_id = ?', (telegram_id,))
        row = cursor.fetchone()
        return row['id'] if row else None


def update_parent_telegram_id(phone: str, telegram_id: int):
    """Привязывает telegram_id к родителю по номеру телефона."""
    phone = _normalize_phone(phone)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE parents SET telegram_id = ? WHERE phone = ? OR phone = ?',
            (telegram_id, phone, "+" + phone),
        )


# ─── Создание родителя ──────────────────────────────────────────────
def add_parent(fio: str, phone: str, role: str = 'senior') -> Optional[int]:
    """Создаёт нового родителя и возвращает его ID. INSERT OR IGNORE: при
    конфликте по UNIQUE(phone) возвращает существующий id."""
    phone = _normalize_phone(phone)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR IGNORE INTO parents (fio, phone, role) VALUES (?, ?, ?)',
            (fio, phone, role),
        )
        if cursor.rowcount == 0:
            cursor.execute('SELECT id FROM parents WHERE phone = ?', (phone,))
            return cursor.fetchone()['id']
        return cursor.lastrowid


# ─── Профиль ────────────────────────────────────────────────────────
def get_parent_role(telegram_id: int) -> Optional[str]:
    """Возвращает роль ('admin' / 'senior' / 'head') или None."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT role FROM parents WHERE telegram_id = ?', (telegram_id,))
        row = cursor.fetchone()
        return row['role'] if row else None


def get_user_lang(telegram_id: int) -> str:
    """Возвращает язык пользователя (ru/uz/en). По умолчанию 'ru'."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT lang FROM parents WHERE telegram_id = ?', (telegram_id,))
        row = cursor.fetchone()
        return row['lang'] if row and row['lang'] else 'ru'


def set_user_lang(telegram_id: int, lang: str):
    """Устанавливает язык пользователя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE parents SET lang = ? WHERE telegram_id = ?',
            (lang, telegram_id),
        )


def get_notify_mode(telegram_id: int) -> str:
    """Режим уведомлений: 'instant' (по умолчанию) или 'summary_only'."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT notify_mode FROM parents WHERE telegram_id = ?',
            (telegram_id,),
        )
        row = cursor.fetchone()
        return row['notify_mode'] if row and row['notify_mode'] else 'instant'


def set_notify_mode(telegram_id: int, mode: str):
    """Устанавливает режим уведомлений ('instant' или 'summary_only')."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE parents SET notify_mode = ? WHERE telegram_id = ?',
            (mode, telegram_id),
        )


# ─── Авторизационные предикаты ──────────────────────────────────────
def get_families_for_head(head_telegram_id: int) -> List[Dict[str, Any]]:
    """Семьи где пользователь — head (по families.head_id, не через family_links)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT f.id, f.family_name
            FROM families f
            JOIN parents p ON f.head_id = p.id
            WHERE p.telegram_id = ?
        ''', (head_telegram_id,))
        return [dict(row) for row in cursor.fetchall()]


def is_head_of_any_family(telegram_id: int) -> bool:
    """Является ли пользователь head'ом хотя бы одной семьи."""
    return len(get_families_for_head(telegram_id)) > 0


def is_head_of_family(telegram_id: int, family_id: int) -> bool:
    """Является ли пользователь head'ом конкретной семьи."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 1 FROM families f
            JOIN parents p ON f.head_id = p.id
            WHERE p.telegram_id = ? AND f.id = ?
        ''', (telegram_id, family_id))
        return cursor.fetchone() is not None


def is_member_of_family(telegram_id: int, family_id: int) -> bool:
    """Член семьи (через family_links)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 1 FROM family_links fl
            JOIN parents p ON fl.parent_id = p.id
            WHERE p.telegram_id = ? AND fl.family_id = ?
            LIMIT 1
        ''', (telegram_id, family_id))
        return cursor.fetchone() is not None


def can_manage_family(telegram_id: int, family_id: int) -> bool:
    """Может ли пользователь управлять семьёй (admin OR head этой семьи).
    Единственный source of truth для деструктивных действий над семьёй."""
    if get_parent_role(telegram_id) == 'admin':
        return True
    return is_head_of_family(telegram_id, family_id)


# ─── Backward-compat re-exports для существующих импортов ───────────
# Эти функции семантически НЕ auth (families/subscription домены), но
# `src.db.auth` исторически их re-export'ил. Сохраняем backward compat —
# новый код должен импортировать из соответствующих модулей напрямую.
from src.database_manager import (  # noqa: E402, F401
    get_families_for_student,
    is_student_under_active_subscription,
    is_subscription_active,
)


__all__ = [
    "get_parent_by_phone",
    "get_parent_by_telegram",
    "get_parent_id_by_telegram",
    "update_parent_telegram_id",
    "add_parent",
    "get_parent_role",
    "get_user_lang",
    "set_user_lang",
    "get_notify_mode",
    "set_notify_mode",
    "get_families_for_head",
    "is_head_of_any_family",
    "is_head_of_family",
    "is_member_of_family",
    "can_manage_family",
    # Re-exports из database_manager (см. ниже)
    "get_families_for_student",
    "is_student_under_active_subscription",
    "is_subscription_active",
]
