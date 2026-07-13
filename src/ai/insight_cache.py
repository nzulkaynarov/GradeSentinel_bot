"""Кэш AI-инсайтов в таблице settings (dashboard insight + year insight).

Выделено из `src/analytics_engine.py` (PR-M1). Кэш живёт в settings (переживает
рестарт). Ключи версионируются префиксом (`insight_v2:`, `year_insight_v1:`) —
bump инвалидирует старые кэши. Валидация ответа (`_looks_like_real_insight`)
остаётся в analytics_engine — это проверка ответа модели, не кэш.
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from src.database_manager import get_setting, set_setting

logger = logging.getLogger(__name__)

# Дашборд-инсайт кэшируется на 6 часов чтобы не палить токены при каждом
# открытии дашборда. Кэш живёт в settings таблице, переживает рестарт
# (ключ — student_id+lang+days).
_INSIGHT_CACHE_TTL_HOURS = 6


def _insight_cache_key(student_id: int, days: int, lang: str) -> str:
    # v2: bump префикса инвалидирует старые кэши с плохими ответами
    # (ранее t("insight_prompt") возвращал сам ключ → Claude генерил мета-описание).
    return f"insight_v2:{student_id}:{days}:{lang}"


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


def _year_insight_cache_key(student_id: int, lang: str) -> str:
    return f"year_insight_v1:{student_id}:{lang}"


def _read_year_insight_cache(student_id: int, lang: str) -> Optional[str]:
    raw = get_setting(_year_insight_cache_key(student_id, lang))
    if not raw:
        return None
    try:
        data = json.loads(raw)
        generated_at = datetime.fromisoformat(data["generated_at"])
        # Year insight кэшируем дольше — данные за весь год меняются медленно.
        if datetime.now() - generated_at < timedelta(hours=24):
            return data["text"]
    except (json.JSONDecodeError, KeyError, ValueError):
        pass
    return None


def _write_year_insight_cache(student_id: int, lang: str, text: str):
    set_setting(_year_insight_cache_key(student_id, lang), json.dumps({
        "text": text,
        "generated_at": datetime.now().isoformat(),
    }))
