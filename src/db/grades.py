"""Оценки: write-path для monitor + чтения для дашборда / AI / scheduler'ов.

Структура:
- Write (monitor + history_importer): add_grade, get_existing_grade, update_grade
- Четвертные: upsert_quarter_grade, get_quarter_grades
- История за период (для дашборда / AI): get_grade_history_for_student[_all]
- Daily summaries: get_today_grades_for_student, get_overnight_grades_for_student,
  get_yesterday_grades_for_student
- Health checks (для scheduler'а bot_alive): has_today_grades_for_parent,
  has_recent_grades_for_parent
- Адресация уведомлений: get_parents_for_student

Все read-path использует `COALESCE(grade_date, date(date_added, '+5 hours'))`
как defense-in-depth для disaster recovery legacy-бэкапов (после этапа 1C
grade_date NOT NULL, но COALESCE остаётся бесплатной защитой). См.
[feedback-codebase-gotchas] пункт 12.
"""
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.db.connection import get_db_connection

logger = logging.getLogger(__name__)


# ─── Write path (monitor + history_importer) ────────────────────────
def add_grade(student_id: int, subject: str, grade_value: Optional[float],
              raw_text: str, cell_reference: str,
              grade_date: Optional[str] = None) -> bool:
    """Добавляет новую оценку в БД, если такой ещё нет.
    True — добавлена, False — дубликат по UNIQUE(student, subject, grade_date, raw_text).

    grade_date — фактическая дата оценки (YYYY-MM-DD). После этапа 1C NOT NULL.
    Если caller не передал — дефолт на сегодня по Ташкенту (зона monitor'а).
    """
    if grade_date is None:
        grade_date = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date().isoformat()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO grade_history
                  (student_id, subject, grade_value, raw_text, cell_reference, grade_date)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (student_id, subject, grade_value, raw_text, cell_reference, grade_date))
            return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            # Дубликат по UNIQUE(student, subject, grade_date, raw_text)
            # или (на legacy-БД до 1C) по старому UNIQUE(student, cell_reference).
            return False


def get_existing_grade(student_id: int, cell_reference: str) -> Optional[Dict[str, Any]]:
    """Возвращает существующую оценку по cell_reference, или None."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT grade_value, raw_text, subject
            FROM grade_history
            WHERE student_id = ? AND cell_reference = ?
        ''', (student_id, cell_reference))
        row = cursor.fetchone()
        return dict(row) if row else None


def grade_exists_by_content(student_id: int, subject: str,
                            grade_date: str, raw_text: str) -> bool:
    """True если такая оценка уже есть в БД по content-key
    (тот же ключ что и UNIQUE constraint после этапа 1C).

    Нужна когда два writer'а (monitor и history_importer) используют разные
    форматы cell_reference для одной логической оценки. Без этой проверки
    monitor шлёт уведомление каждый цикл, пока history_importer держит
    запись с «чужим» cell_reference."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 1 FROM grade_history
            WHERE student_id = ? AND subject = ? AND grade_date = ? AND raw_text = ?
            LIMIT 1
        ''', (student_id, subject, grade_date, raw_text))
        return cursor.fetchone() is not None


def update_grade(student_id: int, cell_reference: str,
                 grade_value: Optional[float], raw_text: str) -> bool:
    """Обновляет значение оценки по cell_reference. True если обновлено.
    Идентификация по cell_reference внутри monitor-домена (`Сегодня!{subject}:{date}`)
    стабильна — там не бывает коллизий."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE grade_history
            SET grade_value = ?, raw_text = ?, date_added = CURRENT_TIMESTAMP
            WHERE student_id = ? AND cell_reference = ?
        ''', (grade_value, raw_text, student_id, cell_reference))
        return cursor.rowcount > 0


# ─── Четвертные ─────────────────────────────────────────────────────
def upsert_quarter_grade(student_id: int, subject: str, quarter: int,
                         grade_value: Optional[float], raw_text: str) -> bool:
    """Вставляет или обновляет четвертную оценку. True если значение изменилось.
    Quarters per design всегда single-grade — sanitize_grade, не sanitize_cell."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT grade_value, raw_text FROM quarter_grades
            WHERE student_id = ? AND subject = ? AND quarter = ?
        ''', (student_id, subject, quarter))
        existing = cursor.fetchone()

        if existing and existing['raw_text'] == raw_text:
            return False

        cursor.execute('''
            INSERT INTO quarter_grades (student_id, subject, quarter, grade_value, raw_text)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(student_id, subject, quarter)
            DO UPDATE SET grade_value = excluded.grade_value,
                          raw_text = excluded.raw_text,
                          updated_at = CURRENT_TIMESTAMP
        ''', (student_id, subject, quarter, grade_value, raw_text))
        return True


def get_quarter_grades(student_id: int) -> List[Dict[str, Any]]:
    """Все четвертные оценки студента, sorted by subject, quarter."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT subject, quarter, grade_value, raw_text
            FROM quarter_grades
            WHERE student_id = ?
            ORDER BY subject, quarter
        ''', (student_id,))
        return [dict(row) for row in cursor.fetchall()]


# ─── История за период (дашборд / AI) ───────────────────────────────
def get_grade_history_for_student(student_id: int, days: int = 14) -> List[Dict[str, Any]]:
    """История оценок за N дней по grade_date (фактическая дата).
    COALESCE-fallback на date(date_added, '+5 hours') — defense-in-depth."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT subject, grade_value, raw_text, grade_date, date_added
            FROM grade_history
            WHERE student_id = ?
              AND COALESCE(grade_date, date(date_added, '+5 hours'))
                  >= date('now', '+5 hours', ?)
            ORDER BY COALESCE(grade_date, date(date_added, '+5 hours')),
                     date_added
        ''', (student_id, f'-{days} days'))
        return [dict(row) for row in cursor.fetchall()]


def get_grade_history_for_student_all(student_id: int, days: int = 30) -> List[Dict[str, Any]]:
    """Полная история оценок за N дней (для WebApp API). Включает cell_reference."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT subject, grade_value, raw_text, cell_reference, grade_date, date_added
            FROM grade_history
            WHERE student_id = ?
              AND COALESCE(grade_date, date(date_added, '+5 hours'))
                  >= date('now', '+5 hours', ?)
            ORDER BY COALESCE(grade_date, date(date_added, '+5 hours')) DESC,
                     date_added DESC
        ''', (student_id, f'-{days} days'))
        return [dict(row) for row in cursor.fetchall()]


# ─── Daily summaries ────────────────────────────────────────────────
def get_today_grades_for_student(student_id: int) -> List[Dict[str, Any]]:
    """Оценки студента за сегодня (по Ташкенту). Дедуп по subject — MAX(date_added)
    если оценка попала и из «Сегодня», и из «Все оценки»."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT subject, grade_value, raw_text, MAX(date_added) as date_added
            FROM grade_history
            WHERE student_id = ?
              AND COALESCE(grade_date, date(date_added, '+5 hours'))
                  = date('now', '+5 hours')
            GROUP BY subject
            ORDER BY date_added
        ''', (student_id,))
        return [dict(row) for row in cursor.fetchall()]


def get_overnight_grades_for_student(student_id: int) -> List[Dict[str, Any]]:
    """Оценки добавленные за ночь (22:00–07:00 по Ташкенту). Дедуп по subject.

    Окно: с 22:00 вчера TST до сейчас. SQL trick — datetime('now','+5 hours',
    'start of day','-2 hours','-5 hours') = (полночь TST → -2ч = 22:00
    вчера TST → -5ч = 17:00 вчера UTC).
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT subject, grade_value, raw_text, cell_reference,
                   MAX(date_added) as date_added
            FROM grade_history
            WHERE student_id = ?
              AND date_added >= datetime('now', '+5 hours', 'start of day', '-2 hours', '-5 hours')
              AND date_added <= datetime('now')
            GROUP BY subject
            ORDER BY date_added
        ''', (student_id,))
        return [dict(row) for row in cursor.fetchall()]


def get_yesterday_grades_for_student(student_id: int) -> List[Dict[str, Any]]:
    """Оценки за вчера (по Ташкенту), для evening summary сравнения."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT subject, grade_value, raw_text
            FROM grade_history
            WHERE student_id = ?
              AND COALESCE(grade_date, date(date_added, '+5 hours'))
                  = date('now', '+5 hours', '-1 day')
            ORDER BY date_added
        ''', (student_id,))
        return [dict(row) for row in cursor.fetchall()]


# ─── Health checks (scheduler bot_alive) ────────────────────────────
def has_today_grades_for_parent(telegram_id: int) -> bool:
    """Есть ли сегодня хоть одна оценка у детей родителя (по Ташкенту)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) as c FROM grade_history gh
            JOIN family_links fl ON gh.student_id = fl.student_id
            JOIN parents p ON fl.parent_id = p.id
            WHERE p.telegram_id = ?
              AND COALESCE(gh.grade_date, date(gh.date_added, '+5 hours'))
                  = date('now', '+5 hours')
        ''', (telegram_id,))
        return cursor.fetchone()['c'] > 0


def has_recent_grades_for_parent(telegram_id: int, hours: int = 48) -> bool:
    """Есть ли оценки у детей родителя за последние N часов.
    Используется bot_alive scheduler'ом: не шлём «бот работает» если оценки
    приходят регулярно (это и так доказательство)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) as c FROM grade_history gh
            JOIN family_links fl ON gh.student_id = fl.student_id
            JOIN parents p ON fl.parent_id = p.id
            WHERE p.telegram_id = ? AND gh.date_added >= datetime('now', ?)
        ''', (telegram_id, f'-{hours} hours'))
        return cursor.fetchone()['c'] > 0


# ─── Адресация уведомлений ──────────────────────────────────────────
def get_parents_for_student(student_id: int) -> List[int]:
    """Telegram_id всех родителей привязанных к ученику через семью.
    Используется monitor'ом для рассылки уведомлений."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT p.telegram_id
            FROM parents p
            JOIN family_links fl ON p.id = fl.parent_id
            WHERE fl.student_id = ? AND p.telegram_id IS NOT NULL
        ''', (student_id,))
        return [row['telegram_id'] for row in cursor.fetchall()]


__all__ = [
    "add_grade",
    "get_existing_grade",
    "grade_exists_by_content",
    "update_grade",
    "upsert_quarter_grade",
    "get_quarter_grades",
    "get_grade_history_for_student",
    "get_grade_history_for_student_all",
    "get_today_grades_for_student",
    "get_overnight_grades_for_student",
    "get_yesterday_grades_for_student",
    "has_today_grades_for_parent",
    "has_recent_grades_for_parent",
    "get_parents_for_student",
]
