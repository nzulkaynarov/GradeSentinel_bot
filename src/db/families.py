"""Семьи, ученики и связи parent↔family↔student.

Самый большой домен — все CRUD по семьям и ученикам, плюс несколько
композитных запросов (например, get_students_for_parent с UNION через
family_links + families.head_id для починки исторического bug'а с
неlinked-главой).

API сгруппирован:
- Создание/мутация: add_family, add_student, update_student_display_name,
  set_family_head, link_parent_to_family, link_student_to_family
- Подсчёт/чтение: get_child_count, get_all_families, get_family_members,
  get_family_members_telegram_ids, get_family_students,
  get_students_for_parent, has_children_for_grades
- Сcope per-user: get_families_for_student, get_families_for_user
- Active spreadsheets для monitor'а: get_active_spreadsheets,
  get_active_spreadsheets_with_subscription
- Удаление участников: delete_parent_from_family, delete_student_from_family
- Subscription gate: is_student_under_active_subscription
"""
import logging
from typing import Any, Dict, List, Optional

from src.db.connection import get_db_connection

logger = logging.getLogger(__name__)


# ─── Активные таблицы для polling ────────────────────────────────────
def get_active_spreadsheets() -> List[Dict[str, Any]]:
    """Список {student_id, fio, spreadsheet_id, display_name} для опроса Sheets."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id as student_id, fio, spreadsheet_id, display_name
            FROM students
            WHERE spreadsheet_id IS NOT NULL AND spreadsheet_id != ""
        ''')
        return [dict(row) for row in cursor.fetchall()]


def get_active_spreadsheets_with_subscription() -> List[Dict[str, Any]]:
    """Студенты только из семей с активной подпиской (или без ограничений)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT s.id as student_id, s.fio, s.spreadsheet_id, s.display_name
            FROM students s
            JOIN family_links fl ON s.id = fl.student_id
            JOIN families f ON fl.family_id = f.id
            WHERE s.spreadsheet_id IS NOT NULL AND s.spreadsheet_id != ''
              AND (f.subscription_end IS NULL OR f.subscription_end > datetime('now'))
        ''')
        return [dict(row) for row in cursor.fetchall()]


# ─── Lookup: семьи по студенту / пользователю ───────────────────────
def get_families_for_student(student_id: int) -> List[Dict[str, Any]]:
    """Все семьи, к которым привязан ученик."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT f.id, f.family_name, f.subscription_end
            FROM families f
            JOIN family_links fl ON fl.family_id = f.id
            WHERE fl.student_id = ?
        ''', (student_id,))
        return [dict(row) for row in cursor.fetchall()]


def get_families_for_user(telegram_id: int) -> List[Dict[str, Any]]:
    """Все семьи к которым относится пользователь.

    UNION двух источников:
    1. family_links (parent ↔ family)
    2. families.head_id (если глава не залинкован явно — исторический bug class)
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT f.id, f.family_name, f.subscription_end, f.head_id
            FROM families f
            JOIN parents p ON p.telegram_id = ?
            LEFT JOIN family_links fl ON fl.family_id = f.id AND fl.parent_id = p.id
            WHERE fl.parent_id = p.id OR f.head_id = p.id
        ''', (telegram_id,))
        return [dict(row) for row in cursor.fetchall()]


def is_student_under_active_subscription(student_id: int) -> bool:
    """True если хотя бы одна семья ученика имеет активную подписку."""
    # Lazy import — payments.py зависит от families.get_families_for_user
    # (циклическая зависимость доменов).
    from src.db.payments import is_subscription_active
    families = get_families_for_student(student_id)
    return any(is_subscription_active(f['id']) for f in families)


# ─── Создание ───────────────────────────────────────────────────────
def add_family(name: str) -> Optional[int]:
    """Создаёт новую семью, возвращает её ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO families (family_name) VALUES (?)', (name,))
        return cursor.lastrowid


def add_student(fio: str, spreadsheet_id: str, display_name: str = None) -> Optional[int]:
    """Создаёт нового студента, возвращает его ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO students (fio, spreadsheet_id, display_name) VALUES (?, ?, ?)',
            (fio, spreadsheet_id, display_name),
        )
        return cursor.lastrowid


def update_student_display_name(student_id: int, display_name: str):
    """Обновляет кэшированное display_name (из get_spreadsheet_title)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE students SET display_name = ? WHERE id = ?',
            (display_name, student_id),
        )


def update_student_spreadsheet(student_id: int, spreadsheet_id: str,
                               display_name: Optional[str] = None) -> bool:
    """Меняет ссылку (spreadsheet_id) у СУЩЕСТВУЮЩЕГО ученика, сохраняя историю.

    grade_history и quarter_grades привязаны к student_id (FK), НЕ к
    spreadsheet_id, поэтому смена ссылки in-place не теряет ни одной оценки.
    Сценарий: с началом учебного года школа выдаёт новую таблицу — родитель
    меняет ссылку, история прошлых лет остаётся, новые оценки доимпортируются
    (history_importer дедупит по содержимому).

    display_name (если передан) обновляется из заголовка новой таблицы.
    Возвращает True если ученик существовал и строка обновлена."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if display_name is not None:
            cursor.execute(
                'UPDATE students SET spreadsheet_id = ?, display_name = ? WHERE id = ?',
                (spreadsheet_id, display_name, student_id),
            )
        else:
            cursor.execute(
                'UPDATE students SET spreadsheet_id = ? WHERE id = ?',
                (spreadsheet_id, student_id),
            )
        return cursor.rowcount > 0


# ─── Связи ──────────────────────────────────────────────────────────
def set_family_head(family_id: int, parent_id: int):
    """Делает родителя главой семьи + гарантирует family_links запись.

    Атомарно: даже если кто-то вызовет set_family_head без предварительного
    link_parent_to_family, в БД останется консистентность. Иначе глава мог
    не появиться в family_links → get_students_for_parent / get_families_for_user
    не находили его «своих» детей (исторический bug).
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE families SET head_id = ? WHERE id = ?',
            (parent_id, family_id),
        )
        # SQLite UNIQUE считает NULL разными значениями, поэтому INSERT OR IGNORE
        # по UNIQUE(family_id,parent_id,student_id) НЕ дедуплицирует записи где
        # student_id IS NULL. Явный exists-check.
        cursor.execute('''
            INSERT INTO family_links (family_id, parent_id)
            SELECT ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM family_links
                WHERE family_id = ? AND parent_id = ? AND student_id IS NULL
            )
        ''', (family_id, parent_id, family_id, parent_id))


def link_parent_to_family(family_id: int, parent_id: int):
    """Привязывает родителя ко всем студентам семьи (или к самой семье если
    студентов ещё нет — placeholder запись с student_id IS NULL)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT DISTINCT student_id FROM family_links '
            'WHERE family_id = ? AND student_id IS NOT NULL',
            (family_id,),
        )
        students = cursor.fetchall()
        if students:
            for s in students:
                cursor.execute(
                    'INSERT OR IGNORE INTO family_links (family_id, parent_id, student_id) '
                    'VALUES (?, ?, ?)',
                    (family_id, parent_id, s['student_id']),
                )
        else:
            cursor.execute(
                'INSERT OR IGNORE INTO family_links (family_id, parent_id) VALUES (?, ?)',
                (family_id, parent_id),
            )


def link_student_to_family(family_id: int, student_id: int):
    """Привязывает студента ко всей семье (всем её родителям)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT DISTINCT parent_id FROM family_links WHERE family_id = ?',
            (family_id,),
        )
        parents = cursor.fetchall()
        if parents:
            for p in parents:
                cursor.execute(
                    'INSERT OR IGNORE INTO family_links (family_id, parent_id, student_id) '
                    'VALUES (?, ?, ?)',
                    (family_id, p['parent_id'], student_id),
                )
        else:
            cursor.execute(
                'INSERT OR IGNORE INTO family_links (family_id, student_id) VALUES (?, ?)',
                (family_id, student_id),
            )


# ─── Счётчики / списки ──────────────────────────────────────────────
def get_child_count(family_id: int) -> int:
    """Количество детей в семье."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT COUNT(DISTINCT student_id) as count '
            'FROM family_links WHERE family_id = ? AND student_id IS NOT NULL',
            (family_id,),
        )
        return cursor.fetchone()['count']


def get_all_families() -> List[Dict[str, Any]]:
    """Список всех семей с информацией о главе и количестве детей. Для admin /list."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT f.id, f.family_name, p.fio as head_fio,
                   (SELECT COUNT(DISTINCT student_id)
                    FROM family_links fl2
                    WHERE fl2.family_id = f.id AND fl2.student_id IS NOT NULL
                   ) as child_count
            FROM families f
            LEFT JOIN parents p ON f.head_id = p.id
            GROUP BY f.id
        ''')
        return [dict(row) for row in cursor.fetchall()]


def get_students_for_parent(telegram_id: int,
                            family_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Студенты видимые пользователю.

    UNION двух источников — для устранения исторического bug'а когда head_id
    был назначен, но family_links не содержали парную запись:
    1. Прямая связь через family_links (parent ↔ student)
    2. families.head_id — глава семьи видит её студентов даже без linked-записи

    Админ — отдельная история: ему доступны все студенты только в админ-панели,
    через /grades админ видит только детей семей где он head/parent.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if family_id:
            cursor.execute('''
                SELECT DISTINCT s.id, s.fio, s.spreadsheet_id, s.display_name
                FROM students s
                JOIN family_links fl ON s.id = fl.student_id
                JOIN parents p ON p.telegram_id = ?
                LEFT JOIN families f ON f.id = fl.family_id AND f.head_id = p.id
                WHERE fl.family_id = ?
                  AND (fl.parent_id = p.id OR f.head_id = p.id)
            ''', (telegram_id, family_id))
        else:
            cursor.execute('''
                SELECT DISTINCT s.id, s.fio, s.spreadsheet_id, s.display_name
                FROM students s
                JOIN family_links fl ON s.id = fl.student_id
                JOIN parents p ON p.telegram_id = ?
                LEFT JOIN families f ON f.id = fl.family_id AND f.head_id = p.id
                WHERE fl.parent_id = p.id OR f.head_id = p.id
            ''', (telegram_id,))
        return [dict(row) for row in cursor.fetchall()]


def has_children_for_grades(telegram_id: int) -> bool:
    """True если пользователь привязан хотя бы к одному ребёнку (для /grades)."""
    return len(get_students_for_parent(telegram_id)) > 0


def get_family_members(family_id: int) -> List[Dict[str, Any]]:
    """Список взрослых членов семьи (для admin manage_family)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT p.id, p.fio, p.role, (p.id = f.head_id) as is_head
            FROM parents p
            JOIN family_links fl ON p.id = fl.parent_id
            JOIN families f ON f.id = fl.family_id
            WHERE fl.family_id = ?
        ''', (family_id,))
        return [dict(row) for row in cursor.fetchall()]


def get_family_members_telegram_ids(family_id: int) -> List[int]:
    """Telegram_id всех членов семьи (для уведомлений об expired подписке)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT p.telegram_id
            FROM parents p
            JOIN family_links fl ON p.id = fl.parent_id
            WHERE fl.family_id = ? AND p.telegram_id IS NOT NULL
        ''', (family_id,))
        return [row['telegram_id'] for row in cursor.fetchall()]


def get_family_students(family_id: int) -> List[Dict[str, Any]]:
    """Список всех детей в семье."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT s.id, s.fio, s.spreadsheet_id
            FROM students s
            JOIN family_links fl ON s.id = fl.student_id
            WHERE fl.family_id = ?
        ''', (family_id,))
        return [dict(row) for row in cursor.fetchall()]


# ─── Удаление участников ────────────────────────────────────────────
def delete_parent_from_family(family_id: int, parent_id: int) -> bool:
    """Удаляет родителя из семьи. Главу удалить нельзя — возвращает False."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT head_id FROM families WHERE id = ?', (family_id,))
        row = cursor.fetchone()
        if row and row['head_id'] == parent_id:
            return False
        cursor.execute(
            'DELETE FROM family_links WHERE family_id = ? AND parent_id = ?',
            (family_id, parent_id),
        )
        return True


def delete_student_from_family(family_id: int, student_id: int):
    """Удаляет ребёнка из конкретной семьи. Если студент больше нигде не
    привязан — каскадно удаляет его + grade_history + quarter_grades + students."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'DELETE FROM family_links WHERE family_id = ? AND student_id = ?',
            (family_id, student_id),
        )
        cursor.execute(
            'SELECT COUNT(*) as count FROM family_links WHERE student_id = ?',
            (student_id,),
        )
        if cursor.fetchone()['count'] == 0:
            cursor.execute('DELETE FROM grade_history WHERE student_id = ?', (student_id,))
            cursor.execute('DELETE FROM quarter_grades WHERE student_id = ?', (student_id,))
            cursor.execute('DELETE FROM students WHERE id = ?', (student_id,))


__all__ = [
    "get_active_spreadsheets",
    "get_active_spreadsheets_with_subscription",
    "get_families_for_student",
    "get_families_for_user",
    "is_student_under_active_subscription",
    "add_family",
    "add_student",
    "update_student_display_name",
    "set_family_head",
    "link_parent_to_family",
    "link_student_to_family",
    "get_child_count",
    "get_all_families",
    "get_students_for_parent",
    "has_children_for_grades",
    "get_family_members",
    "get_family_members_telegram_ids",
    "get_family_students",
    "delete_parent_from_family",
    "delete_student_from_family",
]
