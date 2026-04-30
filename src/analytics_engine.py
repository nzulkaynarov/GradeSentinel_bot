import os
import logging
from typing import Optional
import anthropic

from src.database_manager import get_grade_history_for_student, get_quarter_grades
from src.i18n import t

logger = logging.getLogger(__name__)

_client = None

# Короткий таймаут: пользователь не должен ждать 10 минут (SDK дефолт), если
# Anthropic тормозит или сеть на Pi моргает. 30 сек хватает для max_tokens=800.
_API_TIMEOUT_SECONDS = 30.0


class AIAnalyticsError(Exception):
    """Поднимается, когда Anthropic API недоступен или вернул ошибку.
    Отличается от 'оценок мало' (там просто None) — handler показывает разный текст."""


def _get_client() -> Optional[anthropic.Anthropic]:
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set. AI analytics disabled.")
        return None

    _client = anthropic.Anthropic(api_key=api_key, timeout=_API_TIMEOUT_SECONDS)
    return _client


def analyze_student_grades(student_id: int, student_name: str, days: int = 14, lang: str = 'ru') -> Optional[str]:
    """
    Анализирует оценки студента за последние N дней через Claude API.

    Возвращает текст анализа, или None если данных недостаточно.
    Поднимает AIAnalyticsError при ошибке API/сети — чтобы handler мог
    показать пользователю осмысленное сообщение, а не "недостаточно данных".
    """
    client = _get_client()
    if not client:
        return None

    grades = get_grade_history_for_student(student_id, days=days)
    if not grades:
        return None

    numeric_grades = [g for g in grades if g.get('grade_value') is not None]
    if len(numeric_grades) < 2:
        return None

    grade_text = "\n".join(
        f"{g['date_added']}: {g['subject']} = {g['raw_text']}"
        + (f" (балл: {g['grade_value']})" if g['grade_value'] else "")
        for g in grades
    )

    # Добавляем четвертные оценки в контекст AI
    quarter_grades = get_quarter_grades(student_id)
    quarter_names = {1: "1ч", 2: "2ч", 3: "3ч", 4: "4ч", 5: "Год"}
    if quarter_grades:
        quarter_text = "\n".join(
            f"{q['subject']}: {quarter_names.get(q['quarter'], '?')} = {q['raw_text']}"
            for q in quarter_grades
        )
        grade_text += f"\n\nЧетвертные оценки:\n{quarter_text}"

    prompt = t("ai_prompt", lang, name=student_name, days=days, grades=grade_text)

    try:
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=800,
            messages=[{
                "role": "user",
                "content": prompt,
            }],
        )
        return message.content[0].text
    except anthropic.APITimeoutError as e:
        logger.error(f"Anthropic API timeout after {_API_TIMEOUT_SECONDS}s", exc_info=e)
        raise AIAnalyticsError("timeout") from e
    except anthropic.APIError as e:
        logger.error(f"Anthropic API error: {e}", exc_info=e)
        raise AIAnalyticsError(str(e)) from e
    except Exception as e:
        logger.error("Unexpected error in AI analytics", exc_info=e)
        raise AIAnalyticsError(str(e)) from e


def generate_weekly_summary(student_id: int, student_name: str, lang: str = 'ru') -> Optional[str]:
    """Используется планировщиком воскресной рассылки — глотаем API-ошибки,
    чтобы один зависший Anthropic не валил всю рассылку."""
    try:
        return analyze_student_grades(student_id, student_name, days=7, lang=lang)
    except AIAnalyticsError:
        return None
