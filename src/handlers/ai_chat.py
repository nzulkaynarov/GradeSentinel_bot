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
# 365 дней = вся история учебного года. Claude Haiku 4.5 200K context
# спокойно ест ~3K токенов для ~500 оценок. Раньше было 30 дней — это
# было необоснованное ограничение, и welcome-текст врал юзеру.
_RECENT_DAYS = 365

def start_ai_chat(user_id: int, reply_keyboard=None):
    """Точка входа из user_panel или /start.

    Решает: один ребёнок → сразу в чат, несколько → выбор; нет детей → сообщение.
    `reply_keyboard` (если задан) — постоянная reply-keyboard {Чат, Дашборд,
    Меню} которая ставится на welcome-message чтобы юзер сразу видел навигацию."""
    lang = get_user_lang(user_id)
    students = get_students_for_parent(user_id)

    # Фильтруем по активной подписке (admin обходит)
    if get_parent_role(user_id) != 'admin':
        students = [s for s in students if is_student_under_active_subscription(s['id'])]

    if not students:
        bot.send_message(user_id, t("ai_chat_no_students", lang), reply_markup=reply_keyboard)
        return

    if len(students) == 1:
        _enter_chat_mode(user_id, students[0], lang, reply_keyboard=reply_keyboard)
        return

    # Несколько детей — inline выбор. Reply-keyboard ставим здесь (а
    # _enter_chat_mode сам её больше не ставит, т.к. уже стоит).
    markup = types.InlineKeyboardMarkup(row_width=1)
    for s in students:
        name = s.get('display_name') or s['fio']
        markup.add(types.InlineKeyboardButton(
            name, callback_data=f"ai_pick:{s['id']}"
        ))
    if reply_keyboard is not None:
        # Сначала ставим reply-keyboard через отдельное сообщение, потом inline
        bot.send_message(user_id, t("ai_chat_pick_student", lang),
                          reply_markup=reply_keyboard)
        bot.send_message(user_id, "👇", reply_markup=markup)
    else:
        bot.send_message(user_id, t("ai_chat_pick_student", lang),
                          reply_markup=markup)


def start_ai_chat_with_keyboard(user_id: int, reply_keyboard):
    """Вариант start_ai_chat с обязательной reply-keyboard — для /start
    flow когда нужно сразу установить навигацию."""
    start_ai_chat(user_id, reply_keyboard=reply_keyboard)


def _enter_chat_mode(user_id: int, student: dict, lang: str, reply_keyboard=None):
    """Ставит state и отправляет лаконичное приветствие.

    PR_F: примеры вопросов вшиты в welcome-текст (3 буллета), без inline-кнопок —
    они конфликтуют с постоянной reply-keyboard {Чат, Дашборд, Меню}. Юзер
    либо копирует пример, либо пишет свой вопрос."""
    student_id = student['id']
    set_user_state(user_id, _AI_CHAT_STATE, json.dumps({
        'student_id': student_id,
    }))
    name = student.get('display_name') or student['fio']
    bot.send_message(
        user_id,
        t("ai_chat_welcome", lang, name=name),
        parse_mode='HTML',
        reply_markup=reply_keyboard,
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


@bot.message_handler(func=lambda m: _is_ai_chat_state(m.from_user.id))
def _on_chat_message(message):
    """Любой текст в ai_chat_mode → вопрос для AI.

    PR_G-hotfix: убран admin-блокировщик. Admin может legitimately быть в
    ai_chat_mode через «👨 Я родитель» (Tier 2) — там AI должен отвечать.
    Защита от «admin застрял»: /start для admin ВСЕГДА идёт в admin welcome
    (не в AI), и ai_chat_mode ставится только явным тапом «Я родитель».
    В parent-mode reply-keyboard = {💬 Чат, ⚙️ Меню} (без admin-кнопок),
    конфликта с admin handlers нет."""
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

    # PR_F: ответы AI идут БЕЗ повторяющихся suggested-кнопок.
    # Suggested были в welcome, дальше юзер пишет сам или жмёт «Меню» в
    # postoянной reply-keyboard. Это убирает визуальный шум.
    if not answer:
        bot.send_message(user_id, t("ai_chat_error", lang))
        return

    bot.send_message(user_id, answer)
