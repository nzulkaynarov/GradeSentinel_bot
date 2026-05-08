import os
import json
import logging
from datetime import datetime, timedelta
from typing import Optional
import anthropic

from src.database_manager import (
    get_grade_history_for_student,
    get_quarter_grades,
    get_setting,
    set_setting,
)
from src.i18n import t

logger = logging.getLogger(__name__)

_client = None

# Короткий таймаут: пользователь не должен ждать 10 минут (SDK дефолт), если
# Anthropic тормозит или сеть моргает. 30 сек хватает для max_tokens=800.
_API_TIMEOUT_SECONDS = 30.0

# Дашборд-инсайт — короткий, для hero-области. Кэш на 6 часов чтобы не палить
# токены при каждом открытии дашборда. Кэш живёт в settings таблице,
# переживает рестарт (ключ — student_id+lang+days).
_INSIGHT_CACHE_TTL_HOURS = 6
_INSIGHT_MAX_TOKENS = 120  # 1-2 предложения, экономия токенов
_INSIGHT_TIMEOUT_SECONDS = 8.0  # короче чем у full /ai_report — не блокировать дашборд


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


# ════════════════════════════════════════════════════════════
#  Dashboard insight (короткая фраза для hero дашборда)
# ════════════════════════════════════════════════════════════

def _insight_cache_key(student_id: int, days: int, lang: str) -> str:
    # v2: bump префикса инвалидирует старые кэши с плохими ответами
    # (ранее t("insight_prompt") возвращал сам ключ → Claude генерил мета-описание).
    return f"insight_v2:{student_id}:{days}:{lang}"


# Маркеры мета-ответа Claude (когда он не понял задачу и описывает себя).
# Если ответ содержит такие фразы — считаем его невалидным и не кэшируем.
_BAD_INSIGHT_MARKERS = (
    "# ", "## ", "**",          # markdown structure (наш промпт запрещал markdown)
    "Insight Prompt",
    "I'm ready to help",
    "I can assist",
    "Here's how I can",
    "What I Can Do",
    "Готов помочь",
    "Я могу помочь",
    "Чем я могу",
)


def _looks_like_real_insight(text: str) -> bool:
    """Проверяет что Claude вернул реальный совет, а не мета-описание себя."""
    if not text or len(text) < 10:
        return False
    if len(text) > 400:
        # Промпт ограничивал 200 chars; если намного больше — Claude явно
        # ушёл в structured-ответ вместо короткого совета
        return False
    lower = text.lower()
    for marker in _BAD_INSIGHT_MARKERS:
        if marker.lower() in lower:
            return False
    return True


def _read_insight_cache(student_id: int, days: int, lang: str) -> Optional[str]:
    """Возвращает кэшированный insight если он не протух (TTL 6h)."""
    raw = get_setting(_insight_cache_key(student_id, days, lang))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        generated_at = datetime.fromisoformat(data["generated_at"])
        age = datetime.now() - generated_at
        if age < timedelta(hours=_INSIGHT_CACHE_TTL_HOURS):
            return data["text"]
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        logger.debug(f"Insight cache read failed: {e}")
    return None


def _write_insight_cache(student_id: int, days: int, lang: str, text: str):
    """Сохраняет insight с timestamp."""
    payload = json.dumps({
        "text": text,
        "generated_at": datetime.now().isoformat(),
    })
    set_setting(_insight_cache_key(student_id, days, lang), payload)


def compute_dashboard_insight(
    student_id: int,
    summary: dict,
    lang: str = 'ru',
    days: int = 7,
) -> Optional[str]:
    """
    Возвращает 1-2 предложения совета для родителя, основанные на summary.

    Кэшируется 6 часов в settings table — чтобы не палить токены при каждом
    открытии дашборда. Безопасно деградирует:
      - нет ANTHROPIC_API_KEY → None
      - нет данных (current_avg=None) → None
      - timeout/API error → None (не блокирует дашборд)

    summary должен содержать: current_avg, delta, trend, status,
    problem_subjects (list of {name, avg}), top_subjects (list of {name, avg}).
    """
    if summary.get("current_avg") is None:
        return None

    # Проверяем кэш
    cached = _read_insight_cache(student_id, days, lang)
    if cached:
        return cached

    client = _get_client()
    if not client:
        return None

    # Собираем компактные данные для prompt'а
    problem_names = [s["name"] for s in summary.get("problem_subjects", [])][:3]
    top_names = [s["name"] for s in summary.get("top_subjects", [])][:3]

    # Локализованный prompt — у нас есть единый ключ "insight_prompt" с
    # плейсхолдерами. Сам insight приходит от Claude уже на нужном языке.
    prompt = t(
        "insight_prompt",
        lang,
        avg=summary["current_avg"],
        delta=summary.get("delta") if summary.get("delta") is not None else 0,
        trend=summary.get("trend", "stable"),
        problems=", ".join(problem_names) if problem_names else "—",
        tops=", ".join(top_names) if top_names else "—",
    )

    try:
        # Локальный override таймаута через client config — нельзя per-call,
        # но дефолт 30s, мы создаём client с тем дефолтом. Для insight'а проще
        # принять 30s и не мутировать singleton.
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=_INSIGHT_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        # Чистим типичный мусор в ответе
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()

        # Sanity check: если Claude вернул мета-описание вместо совета —
        # не кэшируем, возвращаем None (дашборд покажет hero без инсайта).
        if not _looks_like_real_insight(text):
            logger.warning(
                f"Insight для student {student_id} отбракован как мета-ответ: "
                f"{text[:80]}..."
            )
            return None

        _write_insight_cache(student_id, days, lang, text)
        return text
    except anthropic.APITimeoutError:
        logger.warning(f"Insight timeout for student {student_id}")
        return None
    except anthropic.APIError as e:
        logger.warning(f"Insight API error for student {student_id}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Insight unexpected error for student {student_id}: {e}")
        return None
