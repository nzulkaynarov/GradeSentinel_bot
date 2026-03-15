import os
import logging
from typing import Optional
import anthropic

from src.database_manager import get_grade_history_for_student

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> Optional[anthropic.Anthropic]:
    """Возвращает синглтон клиента Anthropic API."""
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set. AI analytics disabled.")
        return None

    _client = anthropic.Anthropic(api_key=api_key)
    return _client


def analyze_student_grades(student_id: int, student_name: str, days: int = 14) -> Optional[str]:
    """
    Анализирует оценки студента за последние N дней через Claude API.
    Возвращает текстовый анализ или None при ошибке/отсутствии данных.
    """
    client = _get_client()
    if not client:
        return None

    grades = get_grade_history_for_student(student_id, days=days)
    if not grades:
        return None

    # Фильтруем только числовые оценки для анализа трендов
    numeric_grades = [g for g in grades if g.get('grade_value') is not None]
    if len(numeric_grades) < 2:
        return None  # Недостаточно данных для анализа

    grade_text = "\n".join(
        f"{g['date_added']}: {g['subject']} = {g['raw_text']}"
        + (f" (балл: {g['grade_value']})" if g['grade_value'] else "")
        for g in grades
    )

    try:
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": (
                    f"Проанализируй школьные оценки ученика {student_name} за последние {days} дней.\n"
                    f"Дай краткий анализ (4-6 предложений) на русском языке.\n"
                    f"Укажи:\n"
                    f"- Общий тренд (улучшение/ухудшение/стабильность)\n"
                    f"- Лучший и проблемный предмет\n"
                    f"- Серии (несколько пятёрок подряд, падение оценок)\n"
                    f"- Короткую мотивирующую рекомендацию для родителей\n\n"
                    f"Используй эмодзи для наглядности.\n"
                    f"НЕ используй заголовки и markdown. Пиши простым текстом.\n\n"
                    f"Оценки:\n{grade_text}"
                )
            }]
        )
        return message.content[0].text
    except anthropic.APIError as e:
        logger.error(f"Anthropic API error: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in AI analytics: {e}")
        return None


def generate_weekly_summary(student_id: int, student_name: str) -> Optional[str]:
    """Генерирует еженедельную сводку (вызывается по расписанию)."""
    return analyze_student_grades(student_id, student_name, days=7)
