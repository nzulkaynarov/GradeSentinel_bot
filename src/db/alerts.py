"""Proactive AI alerts dedup (PR_H5).

Хранит лог отправленных alert'ов чтобы не спамить родителей:
если в последние 48 часов уже отправлен alert того же типа по
этому ученику — пропускаем.

Schema (см. init_db в database_manager.py):
- proactive_alerts: id, student_id (FK→students, ON DELETE CASCADE),
  alert_type (TEXT), sent_at (TIMESTAMP)
- index по (student_id, alert_type, sent_at DESC) — для быстрого
  "was_alerted_recently" lookup.
"""
import logging
from datetime import datetime
from typing import Optional

from src.db.connection import get_db_connection

logger = logging.getLogger(__name__)

# Cooldown по умолчанию — не повторять alert того же типа чаще раза в 48ч.
# Защищает от спама при затяжной серии плохих оценок (одного alert'а
# достаточно — родитель и так в курсе).
ALERT_COOLDOWN_HOURS = 48


def save_alert(student_id: int, alert_type: str) -> int:
    """Логирует факт отправки alert'а. Возвращает row id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO proactive_alerts (student_id, alert_type) VALUES (%s, %s) RETURNING id',
            (student_id, alert_type),
        )
        return cursor.fetchone()[0]


def was_alerted_recently(student_id: int, alert_type: str,
                          hours: int = ALERT_COOLDOWN_HOURS) -> bool:
    """True если за последние `hours` часов уже отправлялся alert этого типа.

    Используется как guard перед генерацией нового alert'а:
        if not was_alerted_recently(s_id, 'low_grades_series'):
            send_alert(...)
            save_alert(...)
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM proactive_alerts "
            "WHERE student_id = %s AND alert_type = %s "
            "AND sent_at > (now() at time zone 'utc') - %s * interval '1 hour' LIMIT 1",
            (student_id, alert_type, int(hours)),
        )
        return cursor.fetchone() is not None


def get_last_alert_at(student_id: int, alert_type: str) -> Optional[datetime]:
    """Возвращает timestamp последнего alert'а указанного типа, или None.

    psycopg возвращает наивный datetime (UTC), а не строку.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT sent_at FROM proactive_alerts "
            "WHERE student_id = %s AND alert_type = %s "
            "ORDER BY sent_at DESC LIMIT 1",
            (student_id, alert_type),
        )
        row = cursor.fetchone()
        return row['sent_at'] if row else None


__all__ = [
    "save_alert",
    "was_alerted_recently",
    "get_last_alert_at",
    "ALERT_COOLDOWN_HOURS",
]
