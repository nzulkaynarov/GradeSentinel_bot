"""
Вспомогательные функции для улучшенных уведомлений родителям:
- Эмоциональное форматирование оценок
- Подсчёт серий пятёрок (streaks)
- Логика тихих часов (22:00-07:00)
"""
import logging
from datetime import datetime
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Часовой пояс Ташкента: UTC+5
TIMEZONE_OFFSET_HOURS = 5


def get_local_hour() -> int:
    """Возвращает текущий час по местному времени (UTC+5)."""
    from datetime import timedelta
    now_utc = datetime.utcnow()
    local = now_utc + timedelta(hours=TIMEZONE_OFFSET_HOURS)
    return local.hour


def is_quiet_hours() -> bool:
    """Проверяет, находимся ли мы в тихих часах (22:00 - 07:00 по местному времени)."""
    hour = get_local_hour()
    return hour >= 22 or hour < 7


def get_emotional_header(grade_value: Optional[float], clean_text: str) -> Tuple[str, str]:
    """
    Возвращает (заголовок, эмодзи) в зависимости от оценки.
    Для числовых оценок - по уровню, для текстовых (н, б) - нейтральный формат.
    """
    if grade_value is not None:
        if grade_value >= 5:
            return "Отличная оценка!", "🌟"
        elif grade_value >= 4:
            return "Хорошая оценка", "👍"
        elif grade_value >= 3:
            return "Обратите внимание", "⚠️"
        else:
            return "Требуется внимание!", "🚨"

    # Текстовые отметки (н, б, болел, осв и т.д.)
    lower = clean_text.lower() if clean_text else ""
    if lower in ("н", "н/а"):
        return "Отсутствие на уроке", "📋"
    elif lower in ("б", "болел", "болела"):
        return "Отсутствие по болезни", "🏥"
    elif lower in ("осв", "ув"):
        return "Освобождение", "📋"

    return "Новая запись в дневнике", "📝"


def get_streak_count(student_id: int) -> int:
    """
    Подсчитывает серию последних подряд идущих пятёрок у ученика.
    Считает с конца: если последняя оценка 5, потом 5, потом 4 — streak = 2.
    """
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
                               student_id: int) -> str:
    """Формирует эмоциональное уведомление об оценке с серией пятёрок."""
    header_text, emoji = get_emotional_header(grade_value, clean_text)

    msg = (
        f"{emoji} <b>{header_text}</b>\n"
        f"👨‍🎓 Ученик: {display_name}\n"
        f"📚 Предмет: {subject}\n"
        f"📝 Значение: <b>{clean_text}</b>\n\n"
        f"<a href='https://docs.google.com/spreadsheets/d/{spreadsheet_id}'>🔗 Открыть таблицу</a>"
    )

    # Добавляем streak если есть серия пятёрок (>= 2)
    if grade_value is not None and grade_value >= 5:
        streak = get_streak_count(student_id)
        if streak >= 2:
            msg += f"\n🔥 Это уже {streak}-я пятёрка подряд!"

    return msg
