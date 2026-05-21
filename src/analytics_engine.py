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
from src.ai_tools import (
    TOOL_DEFINITIONS,
    MAX_TOOL_ITERATIONS,
    dispatch_tool,
    resolve_family_id_for_student,
)

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
        "(2-4 предложения, кроме случаев когда родитель просит подробный разбор), "
        "на русском, обычным текстом без markdown. Тебе дана вся история оценок за "
        "учебный год И сегодняшняя дата. Опирайся ТОЛЬКО на эти данные. Если родитель "
        "использует относительные выражения («прошлый месяц», «на этой неделе», "
        "«недавно», «летом», «в начале года») — вычисляй их сам от сегодняшней даты "
        "(например, если сегодня 21 мая, то «прошлый месяц» = апрель), и сразу "
        "отвечай по сути, не переспрашивай. Тон — поддерживающий и конкретный, без "
        "морализаторства и общих фраз. Не выдумывай оценки или предметы которых нет "
        "в данных.\n\n"
        "Помимо оценок, ты знаешь как работает бот GradeSentinel. Если родитель "
        "спрашивает «как X», «что такое Y», «сколько Z» — отвечай коротко на "
        "основе фактов ниже, не отправляй его в поддержку.\n\n"
        "ФАКТЫ О БОТЕ:\n"
        "— Что делает: каждые 5 минут проверяет Google Таблицу с электронным "
        "дневником и присылает уведомление о новых оценках. Источник — таблица школы.\n"
        "— Тихие часы: с 22:00 до 07:00 (Ташкент) уведомления копятся и приходят "
        "одной сводкой утром.\n"
        "— Команды: /start (главное меню), /help (справка), /grades (оценки за "
        "сегодня), /ai_report (AI-анализ за 2 недели), /subscription (статус "
        "подписки и оплата), /manage_family (для главы семьи).\n"
        "— Семьи: один тариф = до 5 детей и неограниченное число родителей. "
        "«Глава семьи» создаёт семью, добавляет детей, приглашает родственников. "
        "«Родитель» получает уведомления и пользуется AI.\n"
        "— Как добавить ребёнка: только глава семьи → «⚙️ Меню» → «👶 Добавить "
        "ребёнка» → отправить URL Google Таблицы с оценками. Бот сам импортирует "
        "историю и начнёт мониторинг.\n"
        "— Инвайт-ссылки: глава семьи → «📬 Пригласить» → одноразовая ссылка, "
        "действует 48 часов. По ней родственник присоединяется к семье и тоже "
        "получает уведомления.\n"
        "— Подписка: 3 тарифа (помесячно, на квартал, на год). Без активной "
        "подписки бот авторизует, но уведомлений не присылает. Цены и оплата — "
        "в меню /subscription. Платежи через Click, Payme или Telegram Stars.\n"
        "— WebApp дашборд: кнопка «📊 Дашборд» — графики оценок по дням, разбивка "
        "по предметам, четвертные, итоги года, прямо в Telegram без браузера.\n"
        "— Языки: русский, узбекский, английский. Сменить — «⚙️ Меню» → "
        "«⚙️ Настройки».\n"
        "— Когда приходят уведомления: новая оценка — в течение 5 минут (вне "
        "тихих часов), вечерняя сводка — 19:00, утренняя сводка ночных оценок — "
        "07:00, четвертные — как учитель выставит.\n\n"
        "ЖИВЫЕ ДАННЫЕ — вызывай tools (НЕ угадывай, НЕ упоминай слово «tool»):\n"
        "• `get_subscription_status` — когда спрашивают про статус подписки, "
        "сколько осталось, когда истекает.\n"
        "• `get_family_members` — когда спрашивают кто в семье, у кого есть "
        "доступ, перечисли детей.\n"
        "• `get_family_pricing` — когда спрашивают сколько стоит, какие тарифы. "
        "ВСЕГДА вызывай этот tool, не помни цены наизусть.\n"
        "После вызова tool отвечай родителю человеческим языком, цитируя "
        "конкретные числа из результата.\n\n"
        "ЧЕГО БОТ НЕ ДЕЛАЕТ: не предсказывает будущие оценки, не пишет учителям и "
        "в школу, не редактирует дневник. Если просьба не про оценки и не про "
        "работу бота — мягко предложи открыть /support.\n\n"
        "Если родитель спросил что-то совсем не по теме (рецепты, политика и т.п.) — "
        "мягко напомни что ты помощник по дневнику."
    ),
    'uz': (
        "Ota-onaga farzandining baholarini tushunishga yordam berasan. Qisqa javob "
        "ber (2-4 jumla, agar ota-ona batafsil tahlil so'rasa — uzunroq), o'zbekcha, "
        "oddiy matn, markdown'siz. Senga butun o'quv yili davomidagi baholar tarixi "
        "VA bugungi sana berilgan. FAQAT shu ma'lumotlardan foydalan. Agar ota-ona "
        "nisbiy iboralarni ishlatsa («oldingi oy», «bu hafta», «yaqinda», «yozda», "
        "«yil boshida») — ularni bugungi sanadan hisoblab javob ber, qayta so'rama. "
        "Ohang — qo'llab-quvvatlovchi va aniq, axloqsiz. Ma'lumotlarda bo'lmagan "
        "baho yoki fanlarni o'ylab topma.\n\n"
        "Baholardan tashqari, GradeSentinel bot qanday ishlashini ham bilasan. "
        "Agar ota-ona «qanday qilib X», «Y nima», «Z qancha» deb so'rasa — quyidagi "
        "ma'lumotlar asosida qisqa javob ber, uni qo'llab-quvvatlash xizmatiga "
        "yo'naltirma.\n\n"
        "BOT HAQIDA FAKTLAR:\n"
        "— Nima qiladi: har 5 daqiqada elektron kundalikli Google Jadvalini tekshiradi "
        "va yangi baholar haqida xabar yuboradi. Manba — maktab jadvali.\n"
        "— Sokin soatlar: 22:00 dan 07:00 gacha (Toshkent) xabarlar to'planadi va "
        "ertalab yagona xulosa sifatida keladi.\n"
        "— Buyruqlar: /start (asosiy menyu), /help (yordam), /grades (bugungi "
        "baholar), /ai_report (2 hafta uchun AI-tahlil), /subscription (obuna holati "
        "va to'lov), /manage_family (oila boshlig'i uchun).\n"
        "— Oilalar: bitta tarif = 5 tagacha bola va cheksiz ota-onalar. «Oila "
        "boshlig'i» oilani yaratadi, bolalar qo'shadi, qarindoshlarini taklif qiladi. "
        "«Ota-ona» xabarlarni oladi va AI'dan foydalanadi.\n"
        "— Bolani qanday qo'shish: faqat oila boshlig'i → «⚙️ Menyu» → «👶 Bola "
        "qo'shish» → baholar bilan Google Jadval URL'ini yuborish. Bot tarixni "
        "avtomatik import qiladi va monitoringni boshlaydi.\n"
        "— Taklif havolalari: oila boshlig'i → «📬 Taklif qilish» → bir martalik "
        "havola, 48 soat ishlaydi. U orqali qarindosh oilaga qo'shiladi va u ham "
        "xabarlarni oladi.\n"
        "— Obuna: 3 tarif (oylik, choraklik, yillik). Faol obunasiz bot avtorizatsiya "
        "qiladi, lekin xabar yubormaydi. Narxlar va to'lov — /subscription "
        "menyusida. To'lovlar Click, Payme yoki Telegram Stars orqali.\n"
        "— WebApp boshqaruv paneli: «📊 Panel» tugmasi — kunlik baholar grafiklari, "
        "fanlar bo'yicha taqsimot, choraklik, yil yakuni — to'g'ridan-to'g'ri "
        "Telegram'da, brauzersiz.\n"
        "— Tillar: rus, o'zbek, ingliz. O'zgartirish — «⚙️ Menyu» → "
        "«⚙️ Sozlamalar».\n"
        "— Xabarlar qachon keladi: yangi baho — 5 daqiqa ichida (sokin soatlardan "
        "tashqari), kechki xulosa — 19:00, tungi baholar ertalabki xulosasi — 07:00, "
        "choraklik — o'qituvchi qo'yganda.\n\n"
        "JONLI MA'LUMOTLAR — tools'larni chaqir (taxmin qilma, «tool» so'zini "
        "tilga olma):\n"
        "• `get_subscription_status` — obuna holati, qancha qoldi, qachon tugaydi.\n"
        "• `get_family_members` — oilada kim bor, kimning kirishi bor, bolalarni "
        "sanab ber.\n"
        "• `get_family_pricing` — narx qancha, qanday tariflar. HAR DOIM bu "
        "tool'ni chaqir, narxlarni yodda saqlama.\n"
        "Tool chaqirgandan keyin natijadagi aniq raqamlarni iqtibos qilib, "
        "ota-onaga oddiy tilda javob ber.\n\n"
        "BOT BAJARMAYDIGAN narsalar: kelajakdagi baholarni bashorat qilmaydi, "
        "o'qituvchilarga yoki maktabga yozmaydi, kundalikni tahrirlamaydi. Agar "
        "iltimos baho yoki bot ishi haqida bo'lmasa — yumshoqlik bilan /support "
        "ochishni taklif qil.\n\n"
        "Agar ota-ona umuman mavzuga oid bo'lmagan narsani so'rasa (retseptlar, "
        "siyosat va h.k.) — yumshoq eslatib qo'y."
    ),
    'en': (
        "You're helping a parent make sense of their child's grades. Be brief "
        "(2-4 sentences, longer if the parent explicitly asks for a deep dive), "
        "plain text, no markdown. You have the full school-year history of grades "
        "AND today's date. Use ONLY this data. When the parent uses relative "
        "expressions («last month», «this week», «recently», «over summer», "
        "«at the start of year») — calculate them yourself from today's date and "
        "answer directly, don't ask for clarification. Tone: supportive and "
        "specific, no moralizing or generic platitudes. Don't invent grades or "
        "subjects not in the data.\n\n"
        "Beyond grades, you know how the GradeSentinel bot works. If the parent "
        "asks «how do I X», «what is Y», «how much Z» — answer briefly based on "
        "the facts below, don't redirect them to support.\n\n"
        "BOT FACTS:\n"
        "— What it does: every 5 minutes checks the Google Sheet with the school's "
        "electronic gradebook and sends a notification about new grades. Source is "
        "the school's spreadsheet.\n"
        "— Quiet hours: from 22:00 to 07:00 (Tashkent) notifications are batched "
        "and arrive as one morning digest.\n"
        "— Commands: /start (main menu), /help (help), /grades (today's grades), "
        "/ai_report (2-week AI analysis), /subscription (subscription status and "
        "payment), /manage_family (for the family head).\n"
        "— Families: one plan covers up to 5 children and unlimited parents. "
        "The «family head» creates the family, adds children, invites relatives. "
        "A «parent» receives notifications and uses the AI.\n"
        "— How to add a child: family head only → «⚙️ Menu» → «👶 Add child» → "
        "send the URL of the Google Sheet with grades. The bot imports history "
        "automatically and starts monitoring.\n"
        "— Invite links: family head → «📬 Invite» → one-time link, valid for 48 "
        "hours. The relative joins the family through it and also gets notifications.\n"
        "— Subscription: 3 plans (monthly, quarterly, yearly). Without an active "
        "subscription the bot authorizes you but doesn't send notifications. Prices "
        "and payment — in the /subscription menu. Payments via Click, Payme or "
        "Telegram Stars.\n"
        "— WebApp dashboard: «📊 Dashboard» button — daily grade charts, breakdown "
        "by subject, quarterly grades, year summary, right inside Telegram without "
        "a browser.\n"
        "— Languages: Russian, Uzbek, English. Change via «⚙️ Menu» → "
        "«⚙️ Settings».\n"
        "— When notifications arrive: new grade — within 5 minutes (outside quiet "
        "hours), evening digest — 19:00, morning digest of night grades — 07:00, "
        "quarterly grades — as the teacher posts them.\n\n"
        "LIVE DATA — call tools (don't guess, don't say the word «tool»):\n"
        "• `get_subscription_status` — for subscription status, days remaining, "
        "when it expires.\n"
        "• `get_family_members` — for who's in the family, who has access, list "
        "the children.\n"
        "• `get_family_pricing` — for prices and plans. ALWAYS call this tool, "
        "don't rely on memorized prices.\n"
        "After calling a tool, answer the parent in plain language, quoting the "
        "specific numbers from the result.\n\n"
        "WHAT THE BOT DOESN'T DO: doesn't predict future grades, doesn't contact "
        "teachers or the school, doesn't edit the gradebook. If the request isn't "
        "about grades or how the bot works — gently suggest opening /support.\n\n"
        "If the parent asks something completely off-topic (recipes, politics, "
        "etc.) — gently remind them you're a gradebook assistant."
    ),
}


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


def answer_parent_question(
    student_id: int,
    student_name: str,
    grades: list,
    question: str,
    lang: str = 'ru',
    prev_messages: Optional[list] = None,
    stream_callback: Optional[callable] = None,
) -> Optional[str]:
    """Отвечает на вопрос родителя про оценки ученика с контекстом из БД.

    Multi-turn (PR_D R6): если переданы `prev_messages` (список dict с
    role/content из ai_chat_messages), история включается в Anthropic API
    messages array — Claude видит предыдущие вопросы и свои ответы, что
    позволяет follow-up без перезаписи контекста.

    Streaming (PR_H4): если передан `stream_callback`, используется
    Anthropic streaming API. Callback получает накопленный текст после
    каждого delta. Возвращает полный финальный текст (как и без streaming).
    Callback должен быть быстрым (вызовы throttle'ить должен сам callback —
    например, bot handler ограничивает edit_message_text до 1 раза в 1.5с
    чтобы не упереться в telegram rate limit).

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

    # Tool-use loop. MAX_TOOL_ITERATIONS — cap чтобы Claude не зациклился
    # на serial вызовах tools. +1 для финального ответа после последней пачки tools.
    try:
        if stream_callback is not None:
            # PR_H4: streaming path. text_stream даёт только text deltas
            # (tool_use deltas игнорятся — этот PR streaming для текстовых
            # ответов, tool_use loop работает без streaming в E2).
            accumulated = ""
            with client.messages.stream(
                model="claude-haiku-4-5",
                max_tokens=_CHAT_MAX_TOKENS,
                system=system_prompt,
                messages=messages_array,
            ) as stream:
                for chunk in stream.text_stream:
                    accumulated += chunk
                    try:
                        stream_callback(accumulated)
                    except Exception as e:
                        # Callback не должен ломать stream
                        logger.debug(f"stream_callback failed: {e}")
            text = accumulated.strip()
        else:
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
