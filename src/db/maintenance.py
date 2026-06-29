"""Обслуживание БД: архивирование, чистки, каскадные удаления.

Используется schedulers (weekly cleanup в вс 03:00) и admin handlers
(delete family). Атомарность критична — операции в одной транзакции
(psycopg сам ведёт транзакцию, коммит на выходе with-блока).
"""
import logging
from typing import Optional

from src.db.connection import get_db_connection

logger = logging.getLogger(__name__)


def archive_old_grades(days: Optional[int] = None) -> int:
    """Переносит оценки старше N дней из grade_history в grade_history_archive.

    days по умолчанию из config.GRADE_ARCHIVE_DAYS.

    Атомарность: перенос по конкретным id (не по WHERE
    date_added < cutoff) — иначе DELETE мог бы захватить запись, которой
    нет в INSERT (или удалить позднее вставленную).
    """
    if days is None:
        from src.config import GRADE_ARCHIVE_DAYS
        days = GRADE_ARCHIVE_DAYS
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM grade_history "
            "WHERE date_added < (now() at time zone 'utc') - %s * interval '1 day'",
            (int(days),),
        )
        ids = [row['id'] for row in cursor.fetchall()]
        if not ids:
            return 0

        # Чанки по 500 — лимит параметров SQLite обычно 999, держим запас
        moved = 0
        chunk_size = 500
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i:i + chunk_size]
            placeholders = ','.join(['%s'] * len(chunk))
            cursor.execute(
                f'''INSERT INTO grade_history_archive
                    (student_id, subject, grade_value, raw_text, cell_reference, grade_date, date_added)
                    SELECT student_id, subject, grade_value, raw_text, cell_reference, grade_date, date_added
                    FROM grade_history
                    WHERE id IN ({placeholders})''',
                chunk,
            )
            moved += cursor.rowcount
            cursor.execute(
                f'DELETE FROM grade_history WHERE id IN ({placeholders})',
                chunk,
            )

        logger.info(f"Archived {moved} grades older than {days} days")
        return moved


def cleanup_old_notification_queue(hours: Optional[int] = None) -> int:
    """Удаляет нерасфлушенные сообщения старше N часов (защита от утечки).
    hours по умолчанию из config.NOTIFICATION_QUEUE_TTL_HOURS."""
    if hours is None:
        from src.config import NOTIFICATION_QUEUE_TTL_HOURS
        hours = NOTIFICATION_QUEUE_TTL_HOURS
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM notification_queue
            WHERE created_at < (now() at time zone 'utc') - %s * interval '1 hour'
        ''', (int(hours),))
        if cursor.rowcount > 0:
            logger.info(f"Cleaned {cursor.rowcount} stale notifications older than {hours}h")
        return cursor.rowcount


def cleanup_expired_invites(days: Optional[int] = None) -> int:
    """Удаляет инвайты, истёкшие более N дней назад.
    days по умолчанию из config.EXPIRED_INVITE_TTL_DAYS."""
    if days is None:
        from src.config import EXPIRED_INVITE_TTL_DAYS
        days = EXPIRED_INVITE_TTL_DAYS
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM family_invites
            WHERE expires_at < (now() at time zone 'utc') - %s * interval '1 day'
        ''', (int(days),))
        if cursor.rowcount > 0:
            logger.info(f"Cleaned {cursor.rowcount} expired invites")
        return cursor.rowcount


def delete_family_cascade(family_id: int) -> bool:
    """Удаляет семью со всеми связанными данными в одной транзакции.

    Чистит: payments, family_invites, family_groups, family_links,
    осиротевших students (вместе с их grade_history и quarter_grades),
    и саму запись families.

    True если семья удалена, False если её не существовало.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT id FROM families WHERE id = %s', (family_id,))
        if not cursor.fetchone():
            return False

        # Студенты, которые останутся осиротевшими после удаления связей
        cursor.execute('''
            SELECT DISTINCT student_id FROM family_links
            WHERE family_id = %s AND student_id IS NOT NULL
        ''', (family_id,))
        student_ids = [row['student_id'] for row in cursor.fetchall()]

        cursor.execute('DELETE FROM family_links WHERE family_id = %s', (family_id,))

        # Студенты без других семей — удаляем со всеми их данными
        for s_id in student_ids:
            cursor.execute(
                'SELECT COUNT(*) as cnt FROM family_links WHERE student_id = %s',
                (s_id,),
            )
            if cursor.fetchone()['cnt'] == 0:
                cursor.execute('DELETE FROM grade_history WHERE student_id = %s', (s_id,))
                cursor.execute('DELETE FROM quarter_grades WHERE student_id = %s', (s_id,))
                cursor.execute('DELETE FROM students WHERE id = %s', (s_id,))

        cursor.execute('DELETE FROM payments WHERE family_id = %s', (family_id,))
        cursor.execute('DELETE FROM family_invites WHERE family_id = %s', (family_id,))
        cursor.execute('DELETE FROM family_groups WHERE family_id = %s', (family_id,))
        cursor.execute('DELETE FROM families WHERE id = %s', (family_id,))

        logger.info(
            f"Family {family_id} cascade-deleted (orphaned students: {len(student_ids)})"
        )
        return True


__all__ = [
    "archive_old_grades",
    "cleanup_old_notification_queue",
    "cleanup_expired_invites",
    "delete_family_cascade",
]
