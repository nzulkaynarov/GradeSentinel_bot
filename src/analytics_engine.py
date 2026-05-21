import os
import json
import logging
from datetime import datetime, timedelta, timezone
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
        f"{g.get('grade_date') or (g.get('date_added') or '')[:10]}: "
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


# ════════════════════════════════════════════════════════════
#  Year insight (итоги учебного года для end-of-year view)
# ════════════════════════════════════════════════════════════

_YEAR_INSIGHT_MAX_TOKENS = 400  # 3-5 предложений (больше чем weekly insight)


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


_YEAR_INSIGHT_PROMPTS = {
    'ru': (
        "Ты помогаешь родителю осмыслить учебный год ребёнка. На основе данных ниже "
        "напиши тёплую финальную сводку в 3-5 предложениях (без markdown, без списков, "
        "обычным текстом). Структура: 1) общая картина года, 2) главное достижение, "
        "3) что нужно подтянуть летом, 4) тёплая фраза на лето. Тон — поддерживающий, "
        "уважительный, без морализаторства. Не используй цифры из данных дословно, "
        "а интерпретируй их.\n\n"
        "Годовой средний: {year_avg}\n"
        "Всего оценок за год: {numeric_count}\n"
        "Месяцев активности: {months_active}\n"
        "Лучший месяц: {best_month_label} (средний {best_month_avg})\n"
        "Худший месяц: {worst_month_label} (средний {worst_month_avg})\n"
        "Топ-предметы: {tops}\n"
        "Проблемные предметы: {problems}\n"
        "Динамика за год (рост/падение среднего балла): {growth}\n"
        "Лучшая серия пятёрок подряд: {best_streak}"
    ),
    'uz': (
        "Ota-onaga farzandining o'quv yili haqida xulosa qilishga yordam berasan. "
        "Quyidagi ma'lumotlar asosida 3-5 jumlali yakuniy izoh yoz (markdown'siz, "
        "ro'yxat'siz, oddiy matn). Tuzilishi: 1) yilning umumiy manzarasi, 2) asosiy "
        "yutuq, 3) yozda nimani tortib qo'yish kerak, 4) yoz uchun iliq so'z. Ohang — "
        "qo'llab-quvvatlovchi, hurmatli, axloqsiz.\n\n"
        "Yillik o'rtacha: {year_avg}\n"
        "Yil davomida baholar soni: {numeric_count}\n"
        "Faol oylar: {months_active}\n"
        "Eng yaxshi oy: {best_month_label} (o'rtacha {best_month_avg})\n"
        "Eng qiyin oy: {worst_month_label} (o'rtacha {worst_month_avg})\n"
        "Top fanlar: {tops}\n"
        "Muammoli fanlar: {problems}\n"
        "Yil davomidagi dinamika: {growth}\n"
        "Eng uzun a'lo baholar ketma-ketligi: {best_streak}"
    ),
    'en': (
        "You're helping a parent reflect on their child's school year. Based on the "
        "data below, write a warm closing summary in 3-5 sentences (no markdown, no "
        "lists, plain text). Structure: 1) overall picture of the year, 2) main "
        "achievement, 3) what to work on over summer, 4) a warm note for summer. "
        "Tone — supportive, respectful, no moralizing. Don't quote numbers literally, "
        "interpret them.\n\n"
        "Year average: {year_avg}\n"
        "Total grades: {numeric_count}\n"
        "Active months: {months_active}\n"
        "Best month: {best_month_label} (avg {best_month_avg})\n"
        "Worst month: {worst_month_label} (avg {worst_month_avg})\n"
        "Top subjects: {tops}\n"
        "Problem subjects: {problems}\n"
        "Year-over-year growth: {growth}\n"
        "Longest A-streak: {best_streak}"
    ),
}


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


# ════════════════════════════════════════════════════════════
#  AI chat — родитель спрашивает про оценки ученика
# ════════════════════════════════════════════════════════════

_CHAT_MAX_TOKENS = 600
# 600 = достаточно для года (типичная нагрузка ~400-500 оценок/год для
# одного ученика). Claude Haiku 200K context съест без проблем.
_CHAT_MAX_GRADES_IN_CONTEXT = 600


def _format_grades_context(grades: list, max_count: int = _CHAT_MAX_GRADES_IN_CONTEXT) -> str:
    """Компактное представление оценок для prompt'а. По убыванию даты."""
    if not grades:
        return "(пусто — оценок в БД пока нет)"
    lines = []
    for g in grades[:max_count]:
        date_str = g.get("grade_date") or (g.get("date_added") or "")[:10]
        subj = g.get("subject", "?")
        raw = g.get("raw_text", "?")
        lines.append(f"  {date_str}  {subj}: {raw}")
    return "\n".join(lines)


_CHAT_SYSTEM_PROMPTS = {
    'ru': (
        "Ты помогаешь родителю разобраться в оценках его/её ребёнка. Отвечай коротко "
        "(2-4 предложения), на русском, обычным текстом без markdown. Тебе дана "
        "вся история оценок за учебный год И сегодняшняя дата. Опирайся ТОЛЬКО "
        "на эти данные. Если родитель использует относительные выражения "
        "(«прошлый месяц», «на этой неделе», «недавно», «летом», «в начале года») — "
        "вычисляй их сам от сегодняшней даты (например, если сегодня 21 мая, "
        "то «прошлый месяц» = апрель), и сразу отвечай по сути, не переспрашивай. "
        "Тон — поддерживающий и конкретный, без морализаторства и общих фраз. "
        "Не выдумывай оценки или предметы которых нет в данных. Если родитель "
        "спросил что-то не по теме оценок — мягко напомни что ты помощник по дневнику."
    ),
    'uz': (
        "Ota-onaga farzandining baholarini tushunishga yordam berasan. Qisqa javob "
        "ber (2-4 jumla), o'zbekcha, oddiy matn, markdown'siz. Senga butun o'quv "
        "yili davomidagi baholar tarixi VA bugungi sana berilgan. FAQAT shu "
        "ma'lumotlardan foydalan. Agar ota-ona nisbiy iboralarni ishlatsa "
        "(«oldingi oy», «bu hafta», «yaqinda», «yozda», «yil boshida») — ularni "
        "bugungi sanadan hisoblab javob ber, qayta so'rama. Ohang — "
        "qo'llab-quvvatlovchi va aniq, axloqsiz. Ma'lumotlarda bo'lmagan "
        "baho yoki fanlarni o'ylab topma. Agar savol baholar mavzusiga oid "
        "bo'lmasa — yumshoq eslatib qo'y."
    ),
    'en': (
        "You're helping a parent make sense of their child's grades. Be brief "
        "(2-4 sentences), plain text, no markdown. You have the full school-year "
        "history of grades AND today's date. Use ONLY this data. When the parent "
        "uses relative expressions («last month», «this week», «recently», «over "
        "summer», «at the start of year») — calculate them yourself from today's "
        "date and answer directly, don't ask for clarification. Tone: supportive "
        "and specific, no moralizing or generic platitudes. Don't invent grades "
        "or subjects not in the data. If the parent asks something off-topic, "
        "gently steer back to grades."
    ),
}


def _tashkent_today_str() -> str:
    """Сегодняшняя дата (Tashkent TZ, UTC+5) в ISO формате — для prompt'а."""
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date().isoformat()


def answer_parent_question(
    student_id: int,
    student_name: str,
    grades: list,
    question: str,
    lang: str = 'ru',
    prev_messages: Optional[list] = None,
) -> Optional[str]:
    """Отвечает на вопрос родителя про оценки ученика с контекстом из БД.

    Multi-turn (PR_D R6): если переданы `prev_messages` (список dict с
    role/content из ai_chat_messages), история включается в Anthropic API
    messages array — Claude видит предыдущие вопросы и свои ответы, что
    позволяет follow-up без перезаписи контекста.

    Не кэширует ответ (каждый turn уникальный). Безопасно деградирует."""
    client = _get_client()
    if not client:
        logger.warning("answer_parent_question: no anthropic client (missing ANTHROPIC_API_KEY)")
        return None

    system_prompt = _CHAT_SYSTEM_PROMPTS.get(lang, _CHAT_SYSTEM_PROMPTS['ru'])
    context = _format_grades_context(grades)

    # Для первого turn'а — даём весь контекст с оценками в user message.
    # Для follow-up — контекст уже в истории, новый вопрос лаконичнее.
    if prev_messages:
        # Multi-turn: prev_messages — это [{"role": "user"|"assistant", "content": "..."}, ...]
        # уже в правильном chronological порядке для Anthropic API.
        # Sanity: первое сообщение должно быть user (Anthropic требование).
        first_user_idx = next((i for i, m in enumerate(prev_messages) if m["role"] == "user"), None)
        if first_user_idx is None:
            # Странно — history без user сообщений; падаем в single-turn.
            prev_messages = None

    if prev_messages:
        # Контекст оценок добавляем в самый ПЕРВЫЙ user message (важно для
        # Anthropic — system + user пара). Дальше — серия turn'ов как есть.
        messages_array = []
        first_user_done = False
        for m in prev_messages:
            if m["role"] == "user" and not first_user_done:
                # Вшиваем grade-контекст в первое user-сообщение
                enriched = (
                    f"Сегодня: {_tashkent_today_str()} (Tashkent TZ)\n"
                    f"Ученик: {student_name}\n"
                    f"История оценок за учебный год (от новых к старым):\n{context}\n\n"
                    f"Вопрос родителя: {m['content']}"
                )
                messages_array.append({"role": "user", "content": enriched})
                first_user_done = True
            else:
                messages_array.append({"role": m["role"], "content": m["content"]})
        # Текущий новый вопрос — последний user turn
        messages_array.append({"role": "user", "content": question})
    else:
        # Single-turn (старое поведение)
        user_message = (
            f"Сегодня: {_tashkent_today_str()} (Tashkent TZ)\n"
            f"Ученик: {student_name}\n"
            f"История оценок за учебный год (от новых к старым):\n{context}\n\n"
            f"Вопрос родителя: {question}"
        )
        messages_array = [{"role": "user", "content": user_message}]

    try:
        message = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=_CHAT_MAX_TOKENS,
            system=system_prompt,
            messages=messages_array,
        )
        text = message.content[0].text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1].strip()
        return text or None
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
        date_str = g.get('grade_date') or (g.get('date_added') or '')[:10]
        if not date_str:
            continue
        try:
            d = datetime.fromisoformat(date_str).date()
        except (ValueError, TypeError):
            continue
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


# Промпты для proactive alert'а на 3 языках. Каждый промпт должен возвращать
# короткий заботливый текст (2-3 предложения) — не паника, конструктивный
# тон. Plain text, без markdown (notification format).
_ALERT_PROMPTS = {
    'low_grades_series': {
        'ru': (
            "Ты — заботливый помощник родителя. У ребёнка {name} за последние "
            "{days} дней появилось {count} оценок ≤3 по предметам: {subjects}. "
            "Напиши короткое (2-3 предложения, без markdown и без приветствия) "
            "уведомление родителю: упомяни факт без драматизма, предложи "
            "обсудить с ребёнком 1 конкретное действие (помощь, разговор, "
            "репетитор) — на выбор родителя. Тон — спокойный, поддерживающий."
        ),
        'uz': (
            "Sen — ota-onaning g'amxo'r yordamchisisan. {name} bolaning so'nggi "
            "{days} kun ichida {subjects} fanlaridan {count}ta ≤3 bahosi bor. "
            "Ota-onaga qisqa (2-3 jumla, markdown'siz va salomsiz) xabar yoz: "
            "faktni dramasiz aytib, bola bilan muhokama qilish uchun 1 ta "
            "aniq harakat taklif qil (yordam, suhbat, repetitor) — ota-ona "
            "tanlasin. Ohang — xotirjam, qo'llab-quvvatlovchi."
        ),
        'en': (
            "You're a caring assistant for the parent. Their child {name} has "
            "received {count} grades ≤3 in the last {days} days in subjects: "
            "{subjects}. Write a short (2-3 sentences, no markdown, no "
            "greeting) notification: mention the fact without drama, suggest "
            "1 concrete action to discuss with the child (help, talk, tutor) "
            "— the parent's choice. Tone: calm, supportive."
        ),
    },
}


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
