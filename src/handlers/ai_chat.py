"""AI-чат с контекстом ученика напрямую в Telegram.

UX: пользователь жмёт «💬 Спросить AI» в user_panel → выбирает ребёнка
(если несколько) → попадает в state `ai_chat_mode` с data=student_id.
Все его текстовые сообщения в этом режиме идут в Claude как вопросы.
Выход — кнопка «❌ Выйти» которая чистит state.

Это primary entry point для AI после реструктуризации (PR_A: AI-first
navigation). До этого AI был спрятан в WebApp в самом низу дашборда.
"""
import logging
import json
from telebot import types

from src.bot_instance import bot
from src.database_manager import (
    get_user_state, set_user_state, clear_user_state,
    get_students_for_parent, get_user_lang,
    get_grade_history_for_student_all, get_parent_role,
    is_student_under_active_subscription,
)
from src.i18n import t

logger = logging.getLogger(__name__)

_AI_CHAT_STATE = 'ai_chat_mode'
_RECENT_DAYS = 30  # сколько дней оценок отдаём AI как контекст

# Suggested prompts по умолчанию — снижают порог входа (большинство юзеров
# не знает что спрашивать). Текст кнопок локализуется через t().
_SUGGESTED_PROMPT_KEYS = [
    "ai_suggested_summer",   # «Что подтянуть летом?»
    "ai_suggested_compare",  # «Сравни с прошлым месяцем»
    "ai_suggested_concern",  # «Где есть поводы для беспокойства?»
]


def _build_keyboard(lang: str, with_exit: bool = True) -> types.InlineKeyboardMarkup:
    """Inline-keyboard с suggested prompts + кнопкой выхода."""
    markup = types.InlineKeyboardMarkup(row_width=1)
    for key in _SUGGESTED_PROMPT_KEYS:
        markup.add(types.InlineKeyboardButton(
            t(key, lang), callback_data=f"ai_prompt:{key}"
        ))
    if with_exit:
        markup.add(types.InlineKeyboardButton(
            t("ai_chat_exit", lang), callback_data="ai_chat_exit"
        ))
    return markup


def start_ai_chat(user_id: int):
    """Точка входа из user_panel. Решает: один ребёнок → сразу в чат,
    несколько → выбор; нет детей → сообщение."""
    lang = get_user_lang(user_id)
    students = get_students_for_parent(user_id)

    # Фильтруем по активной подписке (admin обходит)
    if get_parent_role(user_id) != 'admin':
        students = [s for s in students if is_student_under_active_subscription(s['id'])]

    if not students:
        bot.send_message(user_id, t("ai_chat_no_students", lang))
        return

    if len(students) == 1:
        _enter_chat_mode(user_id, students[0], lang)
        return

    # Несколько детей — попросить выбрать
    markup = types.InlineKeyboardMarkup(row_width=1)
    for s in students:
        name = s.get('display_name') or s['fio']
        markup.add(types.InlineKeyboardButton(
            name, callback_data=f"ai_pick:{s['id']}"
        ))
    markup.add(types.InlineKeyboardButton(
        t("btn_back", lang), callback_data="up_back"
    ))
    bot.send_message(user_id, t("ai_chat_pick_student", lang), reply_markup=markup)


def _enter_chat_mode(user_id: int, student: dict, lang: str):
    """Ставит state, отправляет приветствие с suggested prompts."""
    student_id = student['id']
    set_user_state(user_id, _AI_CHAT_STATE, json.dumps({
        'student_id': student_id,
    }))
    name = student.get('display_name') or student['fio']
    bot.send_message(
        user_id,
        t("ai_chat_welcome", lang, name=name),
        reply_markup=_build_keyboard(lang),
        parse_mode='HTML',
    )


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("ai_pick:"))
def _on_pick_student(call):
    """Выбор ребёнка из меню (когда детей несколько)."""
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    try:
        student_id = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        return

    # Проверяем что ребёнок реально принадлежит этому родителю
    students = get_students_for_parent(user_id)
    student = next((s for s in students if s['id'] == student_id), None)
    if not student:
        bot.send_message(user_id, t("ai_chat_no_students", lang))
        return
    if get_parent_role(user_id) != 'admin' and not is_student_under_active_subscription(student_id):
        bot.send_message(user_id, t("subscription_inactive", lang))
        return

    _enter_chat_mode(user_id, student, lang)


@bot.callback_query_handler(func=lambda c: c.data == "ai_chat_exit")
def _on_chat_exit(call):
    """Выход из ai_chat_mode."""
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    clear_user_state(user_id)
    bot.send_message(user_id, t("ai_chat_bye", lang))
    # Возвращаем в user panel через делегирование
    from src.main import _show_user_panel
    _show_user_panel(user_id)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("ai_prompt:"))
def _on_suggested_prompt(call):
    """Юзер тапнул suggested prompt — это как-будто он сам ввёл этот вопрос."""
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    state = get_user_state(user_id)
    if not state or state.get('state') != _AI_CHAT_STATE:
        # Сессия истекла (бот перезапустился? user nuked state?) — стартуем заново
        start_ai_chat(user_id)
        return

    try:
        prompt_key = call.data.split(":", 1)[1]
    except IndexError:
        return
    # Берём локализованный текст suggested prompt'а — отправляем его в AI
    # как-будто это был user input.
    question_text = t(prompt_key, lang)
    _ask_ai(user_id, question_text, lang, state)


@bot.message_handler(func=lambda m: _is_ai_chat_state(m.from_user.id))
def _on_chat_message(message):
    """Любой текст в ai_chat_mode → вопрос для AI.

    ВАЖНО: этот handler регистрируется через @bot.message_handler с func=.
    pyTelegramBotAPI обходит handler'ы в порядке регистрации; main.py
    регистрирует state_flows и ai_chat ДО generic catch-all handler'а,
    чтобы они срабатывали первыми."""
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    state = get_user_state(user_id)
    if not state:
        return
    question = (message.text or "").strip()
    if not question:
        return
    if len(question) > 500:
        bot.send_message(user_id, t("ai_chat_too_long", lang))
        return
    _ask_ai(user_id, question, lang, state)


def _is_ai_chat_state(user_id: int) -> bool:
    state = get_user_state(user_id)
    return bool(state and state.get('state') == _AI_CHAT_STATE)


def _ask_ai(user_id: int, question: str, lang: str, state: dict):
    """Берёт контекст ученика, спрашивает Claude, отвечает родителю."""
    try:
        data = json.loads(state.get('data') or '{}')
        student_id = data.get('student_id')
    except (json.JSONDecodeError, ValueError):
        student_id = None
    if not student_id:
        bot.send_message(user_id, t("ai_chat_session_lost", lang))
        clear_user_state(user_id)
        return

    students = get_students_for_parent(user_id)
    student = next((s for s in students if s['id'] == student_id), None)
    if not student:
        bot.send_message(user_id, t("ai_chat_session_lost", lang))
        clear_user_state(user_id)
        return

    name = student.get('display_name') or student['fio']
    # Индикатор «AI думает» (Telegram chat action — показывает «typing»)
    try:
        bot.send_chat_action(user_id, 'typing')
    except Exception:
        pass

    recent_grades = get_grade_history_for_student_all(student_id, days=_RECENT_DAYS)

    try:
        from src.analytics_engine import answer_parent_question
        answer = answer_parent_question(
            student_id=student_id,
            student_name=name,
            grades=recent_grades,
            question=question,
            lang=lang,
        )
    except Exception as e:
        logger.warning(f"ai_chat answer failed for user={user_id} student={student_id}: {e}")
        answer = None

    if not answer:
        bot.send_message(user_id, t("ai_chat_error", lang),
                          reply_markup=_build_keyboard(lang))
        return

    bot.send_message(user_id, answer, reply_markup=_build_keyboard(lang))
