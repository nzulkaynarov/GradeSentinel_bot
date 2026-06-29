"""Статистика и листинг пользователей.

API:
- get_global_stats: счётчики по таблицам для admin-панели
- get_user_stats: per-user (семьи/дети/история оценок)
- get_all_telegram_ids: для broadcast'ов
- get_user_info_by_tg_id: профиль для admin /status
- get_all_parents_with_children: широкий обход для daily summary / digest
"""
import logging
from typing import Any, Dict, List, Optional

from src.db.connection import get_db_connection

logger = logging.getLogger(__name__)


def get_global_stats() -> Dict[str, Any]:
    """Глобальная статистика системы — счётчики по основным таблицам."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        return {
            'families': cursor.execute('SELECT COUNT(*) as c FROM families').fetchone()['c'],
            'parents': cursor.execute('SELECT COUNT(*) as c FROM parents').fetchone()['c'],
            'students': cursor.execute('SELECT COUNT(*) as c FROM students').fetchone()['c'],
            'history_records': cursor.execute('SELECT COUNT(*) as c FROM grade_history').fetchone()['c'],
        }


def get_user_stats(telegram_id: int) -> Dict[str, Any]:
    """Персонализированная статистика для конкретного пользователя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT id FROM parents WHERE telegram_id = %s', (telegram_id,))
        parent_row = cursor.fetchone()
        if not parent_row:
            return {'families': 0, 'students': 0, 'history_records': 0}

        parent_id = parent_row['id']
        return {
            'families': cursor.execute(
                'SELECT COUNT(DISTINCT family_id) as c FROM family_links WHERE parent_id = %s',
                (parent_id,),
            ).fetchone()['c'],
            'students': cursor.execute('''
                SELECT COUNT(DISTINCT student_id) as c
                FROM family_links
                WHERE parent_id = %s AND student_id IS NOT NULL
            ''', (parent_id,)).fetchone()['c'],
            'history_records': cursor.execute('''
                SELECT COUNT(*) as c
                FROM grade_history
                WHERE student_id IN (
                    SELECT DISTINCT student_id FROM family_links WHERE parent_id = %s
                )
            ''', (parent_id,)).fetchone()['c'],
        }


def get_all_telegram_ids() -> List[int]:
    """Список telegram_id всех зарегистрированных пользователей. Для broadcast'ов."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id FROM parents WHERE telegram_id IS NOT NULL")
        return [row['telegram_id'] for row in cursor.fetchall()]


def get_user_info_by_tg_id(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Профиль пользователя + список его семей по telegram_id. Для admin /status."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, fio, phone, role, telegram_first_name FROM parents WHERE telegram_id = %s",
            (telegram_id,),
        )
        user = cursor.fetchone()
        if not user:
            return None
        user_dict = dict(user)

        cursor.execute('''
            SELECT DISTINCT f.family_name
            FROM families f
            JOIN family_links fl ON f.id = fl.family_id
            WHERE fl.parent_id = %s
        ''', (user['id'],))
        families = [row['family_name'] for row in cursor.fetchall()]

        return {
            'id': user['id'],
            'fio': user['fio'],
            'phone': user['phone'],
            'role': user['role'],
            'telegram_first_name': user_dict.get('telegram_first_name'),
            'families': families,
        }


def get_all_parents_with_children() -> List[Dict[str, Any]]:
    """Возвращает всех родителей с их детьми (для daily summary / weekly digest)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT p.telegram_id, s.id as student_id,
                   COALESCE(s.display_name, s.fio) as display_name
            FROM parents p
            JOIN family_links fl ON p.id = fl.parent_id
            JOIN students s ON fl.student_id = s.id
            WHERE p.telegram_id IS NOT NULL AND s.spreadsheet_id IS NOT NULL
        ''')
        return [dict(row) for row in cursor.fetchall()]


__all__ = [
    "get_global_stats",
    "get_user_stats",
    "get_all_telegram_ids",
    "get_user_info_by_tg_id",
    "get_all_parents_with_children",
]
