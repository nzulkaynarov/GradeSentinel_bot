"""
Вспомогательные функции для улучшенных уведомлений родителям:
- Эмоциональное форматирование оценок (мультиязычное)
- Подсчёт серий пятёрок (streaks)
- Логика тихих часов (22:00-07:00)
"""
import logging
from datetime import datetime
from typing import Optional, Tuple

from src.i18n import t

logger = logging.getLogger(__name__)

# Часовой пояс Ташкента: UTC+5
TIMEZONE_OFFSET_HOURS = 5


def _get_local_now() -> datetime:
    from datetime import timedelta
    return datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET_HOURS)


def get_local_hour() -> int:
    return _get_local_now().hour


def get_local_date_str() -> str:
    """Возвращает дату/время по Ташкенту: '18.03.2026 14:35'."""
    return _get_local_now().strftime('%d.%m.%Y %H:%M')


def is_quiet_hours() -> bool:
    hour = get_local_hour()
    return hour >= 22 or hour < 7


def get_emotional_header(grade_value: Optional[float], clean_text: str, lang: str = 'ru') -> Tuple[str, str]:
    """
    Возвращает (заголовок, эмодзи) в зависимости от оценки.
    """
    if grade_value is not None:
        if grade_value >= 5:
            return t("notif_grade_excellent", lang), "🌟"
        elif grade_value >= 4:
            return t("notif_grade_good", lang), "👍"
        elif grade_value >= 3:
            return t("notif_grade_attention", lang), "⚠️"
        else:
            return t("notif_grade_danger", lang), "🚨"

    lower = clean_text.lower() if clean_text else ""
    if lower in ("н", "н/а"):
        return t("notif_absent", lang), "📋"
    elif lower in ("б", "болел", "болела"):
        return t("notif_sick", lang), "🏥"
    elif lower in ("осв", "ув"):
        return t("notif_excused", lang), "📋"

    return t("notif_new_entry", lang), "📝"


def get_streak_count(student_id: int) -> int:
    from src.database_manager import get_db_connection

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT grade_value FROM grade_history
            WHERE student_id = ? AND grade_value IS NOT NULL
            ORDER BY date_added DESC
            LIMIT 20
        ''', (student_id,))
        rows = cursor.fetchall()

    streak = 0
    for row in rows:
        if row['grade_value'] >= 5:
            streak += 1
        else:
            break

    return streak


def format_grade_notification(display_name: str, subject: str, clean_text: str,
                               grade_value: Optional[float], spreadsheet_id: str,
                               student_id: int, lang: str = 'ru') -> str:
    """Формирует эмоциональное уведомление об оценке с серией пятёрок."""
    header_text, emoji = get_emotional_header(grade_value, clean_text, lang)

    date_str = get_local_date_str()
    msg = (
        f"{emoji} <b>{header_text}</b>\n"
        f"🕐 {date_str}\n"
        f"{t('notif_student', lang, name=display_name)}\n"
        f"{t('notif_subject', lang, subject=subject)}\n"
        f"{t('notif_value', lang, value=clean_text)}\n\n"
        f"<a href='https://docs.google.com/spreadsheets/d/{spreadsheet_id}'>{t('grades_open_sheet', lang)}</a>"
    )

    if grade_value is not None and grade_value >= 5:
        streak = get_streak_count(student_id)
        if streak >= 2:
            msg += f"\n{t('notif_streak', lang, count=streak)}"

    return msg


def format_grade_change_notification(display_name: str, subject: str,
                                      old_text: str, new_text: str,
                                      new_grade_value: Optional[float],
                                      spreadsheet_id: str, student_id: int,
                                      lang: str = 'ru') -> str:
    """Формирует уведомление об изменении оценки преподавателем."""
    header_text, emoji = get_emotional_header(new_grade_value, new_text, lang)

    date_str = get_local_date_str()
    msg = (
        f"✏️ <b>{t('notif_grade_changed', lang)}</b>\n"
        f"🕐 {date_str}\n"
        f"{t('notif_student', lang, name=display_name)}\n"
        f"{t('notif_subject', lang, subject=subject)}\n"
        f"{t('notif_change', lang, old=old_text, new=new_text)}\n\n"
        f"<a href='https://docs.google.com/spreadsheets/d/{spreadsheet_id}'>{t('grades_open_sheet', lang)}</a>"
    )

    return msg
