import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
import anthropic

from src.database_manager import (
    get_grade_history_for_student,
    get_quarter_grades,
)
from src.i18n import t
from src.utils import to_date_str
from src.ai_tools import (
    TOOL_DEFINITIONS,
    MAX_TOOL_ITERATIONS,
    dispatch_tool,
    resolve_family_id_for_student,
)
# PR-M1: промпты, singleton-клиент и кэш инсайтов вынесены в пакет src/ai/.
# Импортируются сюда (в namespace analytics_engine), чтобы:
#   - оркестрация ниже использовала их без префикса;
#   - обратная совместимость: `from src.analytics_engine import _get_client,
#     _CHAT_SYSTEM_PROMPTS, ...` и `monkeypatch.setattr("src.analytics_engine.
#     _get_client", ...)` продолжали работать (re-export).
from src.ai.client import _get_client, _API_TIMEOUT_SECONDS  # noqa: F401
from src.ai.insight_cache import (  # noqa: F401
    _INSIGHT_CACHE_TTL_HOURS,
    _insight_cache_key,
    _read_insight_cache,
    _write_insight_cache,
    _year_insight_cache_key,
    _read_year_insight_cache,
    _write_year_insight_cache,
)
from src.ai.prompts import (  # noqa: F401
    _YEAR_INSIGHT_PROMPTS,
    _CHAT_TODAY_LINE,
    _CHAT_TRUNCATION_NOTICE,
    _CHAT_SYSTEM_PROMPTS,
    _ALERT_PROMPTS,
)

logger = logging.getLogger(__name__)

_INSIGHT_MAX_TOKENS = 120  # 1-2 предложения, экономия токенов
_INSIGHT_TIMEOUT_SECONDS = 8.0  # короче чем у full /ai_report — не блокировать дашборд


class AIAnalyticsError(Exception):
    """Поднимается, когда Anthropic API недоступен или вернул ошибку.
    Отличается от 'оценок мало' (там просто None) — handler показывает разный текст."""


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
        f"{to_date_str(g.get('grade_date') or g.get('date_added'))}: "
        f"{g['subject']} = {g['raw_text']}"
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

# Cache-ключи + read/write вынесены в src/ai/insight_cache.py (re-export выше).


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


# ════════════════════════════════════════════════════════════
#  Year insight (итоги учебного года для end-of-year view)
# ════════════════════════════════════════════════════════════

_YEAR_INSIGHT_MAX_TOKENS = 400  # 3-5 предложений (больше чем weekly insight)

# Cache-ключи/read-write (_year_insight_cache_key, _read/_write_year_insight_cache)
# и _YEAR_INSIGHT_PROMPTS вынесены в src/ai/ (re-export наверху файла).


def compute_year_insight(student_id: int, report: dict, lang: str = 'ru') -> Optional[str]:
    """3-5 предложений итогов учебного года.

    Кэш 24h (год не меняется быстро). Безопасно деградирует."""
    if report.get("year_avg") is None or report.get("numeric_count", 0) < 5:
        return None

    cached = _read_year_insight_cache(student_id, lang)
    if cached:
        return cached

    client = _get_client()
    if not client:
        return None

    best_month = report.get("best_month") or {}
    worst_month = report.get("worst_month") or {}
    top_names = [s["name"] for s in report.get("top_subjects", [])][:3]
    problem_names = [s["name"] for s in report.get("problem_subjects", [])][:3]

    prompt_template = _YEAR_INSIGHT_PROMPTS.get(lang, _YEAR_INSIGHT_PROMPTS['ru'])
    prompt = prompt_template.format(
        year_avg=report["year_avg"],
        numeric_count=report["numeric_count"],
        months_active=report["months_active"],
        best_month_label=best_month.get("label", "—"),
        best_month_avg=best_month.get("avg", "—"),
        worst_month_label=worst_month.get("label", "—"),
        worst_month_avg=worst_month.get("avg", "—"),
        tops=", ".join(top_names) if top_names else "—",
        problems=", ".join(problem_names) if problem_names else "—",
        growth=report.get("growth") if report.get("growth") is not None else 0,
        best_streak=report.get("best_streak", 0),
    )

    try:
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=_YEAR_INSIGHT_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()
        if not _looks_like_real_insight(text):
            # Для года порог length больше — повторно проверим без 400 cap
            if len(text) < 10 or any(m.lower() in text.lower() for m in _BAD_INSIGHT_MARKERS):
                logger.warning(f"Year insight для student {student_id} отбракован")
                return None

        _write_year_insight_cache(student_id, lang, text)
        return text
    except anthropic.APITimeoutError:
        logger.warning(f"Year insight timeout for student {student_id}")
        return None
    except anthropic.APIError as e:
        logger.warning(f"Year insight API error for student {student_id}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Year insight unexpected error for student {student_id}: {e}")
        return None


_SUMMER_ACTIVITY_MAX_TOKENS = 320


def generate_summer_activity(student_name: str, subject: str,
                             lang: str = 'ru') -> Optional[str]:
    """«Летний режим»: короткая каникулярная активность для родителя под
    отстающий предмет ребёнка.

    Чистый AI с жёстким промптом (locale 'summer_activity_prompt') — он держит
    тон («помочь закрепить», НЕ «отстаёт/слабый») и формат (одна конкретная
    активность на 10-15 минут, без давления). Безопасно деградирует в None
    (нет ANTHROPIC_API_KEY / timeout / API error / мета-ответ)."""
    client = _get_client()
    if not client:
        return None

    prompt = t("summer_activity_prompt", lang, name=student_name, subject=subject)
    try:
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=_SUMMER_ACTIVITY_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()
        if not _looks_like_real_insight(text):
            logger.warning(
                f"Summer activity для {student_name}/{subject} отбракован как мета-ответ")
            return None
        return text
    except anthropic.APITimeoutError:
        logger.warning(f"Summer activity timeout for {student_name}")
        return None
    except anthropic.APIError as e:
        logger.warning(f"Summer activity API error: {e}")
        return None
    except Exception as e:
        logger.warning(f"Summer activity unexpected error: {e}")
        return None


# ════════════════════════════════════════════════════════════
#  AI chat — родитель спрашивает про оценки ученика
# ════════════════════════════════════════════════════════════

# Промпты и текстовые шаблоны чата (_CHAT_TODAY_LINE, _CHAT_TRUNCATION_NOTICE,
# _CHAT_SYSTEM_PROMPTS) вынесены в src/ai/prompts.py (re-export наверху файла).

# B20: 600 молча обрезало «подробный разбор» (stop_reason='max_tokens'), и
# обрезок сохранялся в историю. 1500 даёт место для полноценного ответа;
# Haiku output $5/1M → ~$0.0075 worst-case на ответ. Если всё равно упёрлись
# в потолок — добавляем пометку (_CHAT_TRUNCATION_NOTICE) вместо тихого обрыва.
_CHAT_MAX_TOKENS = 1500

# B21: cap на РЕБЁНКА, а не суммарный. Раньше grades[:600] резал 600 оценок
# СУММАРНО по всем детям семьи → у семьи с 4-5 детьми старые оценки части
# детей молча терялись. Теперь каждый ребёнок получает до N последних оценок,
# поэтому вопрос про конкретного ребёнка всегда имеет его данные.
# 300 ≈ покрывает активный учебный год одного ученика; для семьи из 5 детей —
# до 1500 оценок в контексте (Haiku 200K справится, prompt caching дешевит).
_CHAT_MAX_GRADES_PER_STUDENT = 300


def _format_grades_context(grades: list, max_count: int = _CHAT_MAX_GRADES_PER_STUDENT) -> str:
    """Компактное представление оценок для prompt'а. По убыванию даты.

    NAV-001 (family-scoped): если grade dict содержит поле 'student_name',
    оно добавляется в префиксе строки [Имя]. Без поля — backward-compat
    формат для single-student режима (webapp dashboard и legacy callers).

    B21: `max_count` — cap на РЕБЁНКА (по полю student_name), не суммарный.
    Ожидается что `grades` уже отсортированы newest-first — так каждый ребёнок
    получает свои самые свежие оценки. Для single-student (без student_name)
    всё падает в один bucket и cap работает как раньше."""
    if not grades:
        return "(пусто — оценок в БД пока нет)"
    lines = []
    per_student_counts: dict = {}
    for g in grades:
        student_name = g.get("student_name")
        bucket = student_name or "__single__"
        seen = per_student_counts.get(bucket, 0)
        if seen >= max_count:
            continue
        per_student_counts[bucket] = seen + 1
        date_str = to_date_str(g.get("grade_date") or g.get("date_added"))
        subj = g.get("subject", "?")
        raw = g.get("raw_text", "?")
        prefix = f"[{student_name}] " if student_name else ""
        lines.append(f"  {date_str}  {prefix}{subj}: {raw}")
    return "\n".join(lines)


def _tashkent_today_str() -> str:
    """Сегодняшняя дата (Tashkent TZ, UTC+5) в ISO формате — для prompt'а."""
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date().isoformat()


def _extract_text_from_response(response) -> Optional[str]:
    """Достаёт первый text-блок из Anthropic response. Defensive — толерантен
    к mock-объектам и нестандартным шейпам ответа."""
    content = getattr(response, 'content', None) or []
    for block in content:
        btype = getattr(block, 'type', None)
        # text может быть и в TextBlock, и в mock'е (где type не задан)
        if btype is None or btype == 'text':
            text = getattr(block, 'text', None)
            if text:
                stripped = text.strip()
                if stripped.startswith('"') and stripped.endswith('"'):
                    stripped = stripped[1:-1].strip()
                return stripped or None
    return None


def _sanitize_conversation(messages: list) -> list:
    """B15: приводит историю к валидному для Anthropic Messages API виду.

    Anthropic требует: первое сообщение = user, роли строго чередуются.
    Наша история может нарушать это по двум причинам:
      - Осиротевший user в конце: user-вопрос сохраняется ДО вызова AI; при
        падении AI assistant-ответ не сохраняется → на следующем вопросе в
        истории два user подряд.
      - Ведущий assistant: окно последних 20 сообщений может начинаться с
        assistant (обрезано посреди пары) → первое сообщение не user.

    Оба нарушают контракт → 400 → пользователь видит ошибку, а orphan остаётся
    в истории → устойчивый отказ до clear_history. Санитайзер лечит это:
      - отбрасывает ведущие assistant-сообщения;
      - схлопывает подряд идущие одинаковые роли, оставляя ПОСЛЕДНее (так
        свежий вопрос вытесняет осиротевший, а retry не даёт дубль).

    Ожидается content = str (история из БД — только текст; tool-блоки
    добавляются позже, уже после санитайза)."""
    cleaned: list = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role not in ("user", "assistant") or content is None:
            continue
        if not cleaned:
            if role != "user":
                continue  # отбрасываем ведущий assistant
            cleaned.append({"role": role, "content": content})
        elif role == cleaned[-1]["role"]:
            cleaned[-1] = {"role": role, "content": content}  # keep latest
        else:
            cleaned.append({"role": role, "content": content})
    return cleaned


def answer_parent_question(
    student_id: int,
    student_name: str,
    grades: list,
    question: str,
    lang: str = 'ru',
    prev_messages: Optional[list] = None,
    family_id: Optional[int] = None,
    stream_callback: Optional[callable] = None,
) -> Optional[str]:
    """Отвечает на вопрос родителя про оценки ученика с контекстом из БД.

    Multi-turn (PR_D R6): если переданы `prev_messages` (список dict с
    role/content из ai_chat_messages), история включается в Anthropic API
    messages array — Claude видит предыдущие вопросы и свои ответы, что
    позволяет follow-up без перезаписи контекста.

    Tool use (PR_E2): AI может вызывать get_subscription_status /
    get_family_members / get_family_pricing для live данных. family_id
    резолвится из student_id если не передан явно. Cap MAX_TOOL_ITERATIONS
    защищает от infinite loop'а.

    Streaming (PR_H4): если передан `stream_callback`, последняя итерация
    (которая возвращает text) использует Anthropic streaming API. Callback
    получает накопленный текст после каждого delta. Tool-use итерации
    идут без streaming (не имеет смысла стримить tool args). Callback должен
    throttle'ить вызовы (например, bot handler 1.5с throttle на
    edit_message_text против telegram flood-control).

    Не кэширует ответ (каждый turn уникальный). Безопасно деградирует."""
    client = _get_client()
    if not client:
        logger.warning("answer_parent_question: no anthropic client (missing ANTHROPIC_API_KEY)")
        return None

    # Резолвим family_id для tool dispatcher (один раз на вопрос)
    if family_id is None and student_id:
        family_id = resolve_family_id_for_student(student_id)

    system_prompt = _CHAT_SYSTEM_PROMPTS.get(lang, _CHAT_SYSTEM_PROMPTS['ru'])
    context = _format_grades_context(grades)

    # Высоко-заметная строка с сегодняшней датой ПЕРВЫМ system-блоком. Раньше
    # дата жила только одной строкой наверху большого grade-контекста (user-
    # блок) — при полном контексте (сотни оценок, заканчивающихся месяцы назад)
    # Haiku изредка терял её и галлюцинировал дату у конца данных («конец
    # июня» при последних оценках в мае). System-блок модель читает раньше и
    # с высшим приоритетом. Отдельный НЕкэшируемый блок (крошечный, меняется
    # ежедневно) → большой system_prompt остаётся стабильно кэшируемым.
    today_line = _CHAT_TODAY_LINE.get(lang, _CHAT_TODAY_LINE['ru']).format(
        today=_tashkent_today_str()
    )

    # B16 prompt caching: system prompt стабилен для каждого запроса (один на
    # язык) → cache_control кэширует tools + system вместе (render order —
    # tools → system → messages). Переживает смену grade-контекста (отдельный
    # tier), так что даже при новых оценках system остаётся из кэша.
    system_blocks = [
        {"type": "text", "text": today_line},
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        },
    ]

    # B15: собираем полную беседу [история..., текущий вопрос] и санитайзим одним
    # проходом — гарантия «первое=user, строгое чередование» независимо от того,
    # что лежит в истории (orphan-user в конце, ведущий assistant и т.п.).
    conversation = _sanitize_conversation(
        (prev_messages or []) + [{"role": "user", "content": question}]
    )
    # conversation[0] после санитайза всегда user (либо пусто — но мы только что
    # добавили текущий вопрос, так что минимум один user есть).

    # Grade-контекст + сегодняшняя дата вшиваются в ПЕРВОЕ user-сообщение.
    # B16: разбиваем его на два content-блока — стабильный (контекст, cache_control)
    # и волатильный (сам вопрос). Стабильный блок кэширует tools+system+контекст
    # одной точкой; переживает рост беседы (первый user на фиксированной позиции).
    first = conversation[0]
    stable_context = (
        f"Сегодня: {_tashkent_today_str()} (Tashkent TZ)\n"
        f"Ученик: {student_name}\n"
        f"История оценок за учебный год (от новых к старым):\n{context}"
    )
    first_question = first["content"]
    conversation[0] = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": stable_context,
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": f"Вопрос родителя: {first_question}"},
        ],
    }
    messages_array = conversation

    # Tool-use loop (PR_E2). Cap MAX_TOOL_ITERATIONS+1 — последняя итерация
    # должна вернуть text. Streaming (PR_H4) применяется только к итерациям
    # которые НЕ возвращают tool_use — мы заранее не знаем, поэтому каждую
    # итерацию пробуем streaming, и если stop_reason='tool_use' — игнорим
    # накопленный текст и идём в loop. Tool args через streaming доступны
    # через .get_final_message() после exit'а из stream context.
    try:
        for iteration in range(MAX_TOOL_ITERATIONS + 1):
            if stream_callback is not None:
                # PR_H4 streaming path. text_stream даёт только text deltas.
                accumulated = ""
                with client.messages.stream(
                    model="claude-haiku-4-5",
                    max_tokens=_CHAT_MAX_TOKENS,
                    system=system_blocks,
                    tools=TOOL_DEFINITIONS,
                    messages=messages_array,
                ) as stream:
                    for chunk in stream.text_stream:
                        accumulated += chunk
                        try:
                            stream_callback(accumulated)
                        except Exception as e:
                            logger.debug(f"stream_callback failed: {e}")
                    response = stream.get_final_message()
            else:
                response = client.messages.create(
                    model="claude-haiku-4-5",
                    max_tokens=_CHAT_MAX_TOKENS,
                    system=system_blocks,
                    tools=TOOL_DEFINITIONS,
                    messages=messages_array,
                )

            stop_reason = getattr(response, 'stop_reason', None)
            if stop_reason != 'tool_use':
                # Финальный ответ — извлекаем текст
                text = _extract_text_from_response(response)
                # B20: если ответ упёрся в max_tokens — он обрезан. Не отдаём
                # обрубок как полноценный ответ: добавляем пометку, чтобы юзер
                # (и сохранённая история) видели, что ответ неполный.
                if text and stop_reason == 'max_tokens':
                    notice = _CHAT_TRUNCATION_NOTICE.get(
                        lang, _CHAT_TRUNCATION_NOTICE['ru'])
                    logger.info(
                        f"answer_parent_question truncated at max_tokens "
                        f"(student={student_id}, family={family_id})"
                    )
                    text = text + notice
                return text

            # Claude хочет вызвать tools. Добавляем assistant turn с
            # tool_use блоками и user turn с tool_result.
            messages_array.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if getattr(block, 'type', None) == 'tool_use':
                    result_text = dispatch_tool(
                        tool_name=block.name,
                        tool_input=getattr(block, 'input', None) or {},
                        family_id=family_id,
                        lang=lang,
                    )
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                    })

            if not tool_results:
                # stop_reason=tool_use без блоков — странно, выходим с тем что есть
                logger.warning(
                    f"answer_parent_question: stop_reason=tool_use but no tool_use blocks "
                    f"(student={student_id})"
                )
                return _extract_text_from_response(response)

            messages_array.append({"role": "user", "content": tool_results})

        # Hit iteration cap
        logger.warning(
            f"answer_parent_question hit MAX_TOOL_ITERATIONS={MAX_TOOL_ITERATIONS} "
            f"(student={student_id}, family={family_id})"
        )
        return None
    except anthropic.APITimeoutError:
        logger.warning(f"Chat timeout for student {student_id}")
        return None
    except anthropic.APIError as e:
        logger.warning(f"Chat API error for student {student_id}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Chat unexpected error for student {student_id}: {e}")
        return None


# ════════════════════════════════════════════════════════════
#  Proactive alerts — anomaly detection + AI text generation (PR_H5)
# ════════════════════════════════════════════════════════════

# Что считаем «плохой» оценкой. Узбекская 5-балльная: 1-2 = неуд, 3 = удовл.
# Триггер на ≤3 (включая тройки), потому что внезапная серия троек у
# обычно-четвёрочника — уже сигнал для родителя.
_LOW_GRADE_THRESHOLD = 3.0

# Сколько ≤3 за окно считается «серией»
_LOW_GRADES_SERIES_MIN = 3
_LOW_GRADES_SERIES_WINDOW_DAYS = 7

# Token cap для proactive alert текста — короткий, чтобы не перегружать
# notification. 2-3 предложения.
_ALERT_MAX_TOKENS = 180


def detect_anomalies(student_id: int) -> list:
    """Возвращает список аномалий для ученика (для каждой будет alert).

    MVP: один тип — 'low_grades_series'. Future: 'sudden_drop',
    'attendance_issue' и т.д.

    Возвращает [] если всё нормально или данных мало.
    """
    grades = get_grade_history_for_student(student_id, days=_LOW_GRADES_SERIES_WINDOW_DAYS + 7)
    if not grades:
        return []

    # Tashkent today для cutoff
    today = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date()
    window_start = today - timedelta(days=_LOW_GRADES_SERIES_WINDOW_DAYS)

    low_grades = []
    for g in grades:
        if g.get('grade_value') is None:
            continue
        # psycopg возвращает DATE/TIMESTAMP как date/datetime ОБЪЕКТЫ (не строки).
        raw_date = g.get('grade_date') or g.get('date_added')
        if raw_date is None:
            continue
        if isinstance(raw_date, str):
            try:
                d = datetime.fromisoformat(raw_date).date()
            except (ValueError, TypeError):
                continue
        elif isinstance(raw_date, datetime):
            d = raw_date.date()
        else:  # date-объект (DATE-колонка grade_date)
            d = raw_date
        if d < window_start:
            continue
        if g['grade_value'] <= _LOW_GRADE_THRESHOLD:
            low_grades.append(g)

    anomalies = []
    if len(low_grades) >= _LOW_GRADES_SERIES_MIN:
        # Уникальные предметы, ограничиваем до 3 для краткости alert'а
        subjects = list(dict.fromkeys(g['subject'] for g in low_grades))[:3]
        anomalies.append({
            'type': 'low_grades_series',
            'count': len(low_grades),
            'subjects': subjects,
            'days': _LOW_GRADES_SERIES_WINDOW_DAYS,
        })

    return anomalies


def generate_proactive_alert(student_name: str, anomaly: dict,
                              lang: str = 'ru') -> Optional[str]:
    """Генерит короткий текст alert'а через Claude. Безопасно деградирует
    (None при отсутствии API key / timeout / error)."""
    client = _get_client()
    if not client:
        return None

    alert_type = anomaly.get('type')
    prompts_by_lang = _ALERT_PROMPTS.get(alert_type)
    if not prompts_by_lang:
        logger.warning(f"generate_proactive_alert: unknown type {alert_type!r}")
        return None

    prompt_template = prompts_by_lang.get(lang) or prompts_by_lang.get('ru')
    prompt = prompt_template.format(
        name=student_name,
        count=anomaly.get('count', '?'),
        days=anomaly.get('days', '?'),
        subjects=', '.join(anomaly.get('subjects', [])) or '—',
    )

    try:
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=_ALERT_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        text = message.content[0].text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()
        return text or None
    except anthropic.APITimeoutError:
        logger.warning(f"Proactive alert timeout for {student_name}")
        return None
    except anthropic.APIError as e:
        logger.warning(f"Proactive alert API error for {student_name}: {e}")
        return None
    except Exception as e:
        logger.warning(f"Proactive alert unexpected error for {student_name}: {e}")
        return None
