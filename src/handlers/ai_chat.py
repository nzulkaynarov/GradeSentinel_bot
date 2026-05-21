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
import time
from telebot import types

from src.bot_instance import bot
from src.database_manager import (
    get_user_state, set_user_state, clear_user_state,
    get_students_for_parent, get_user_lang,
    get_grade_history_for_student_all, get_parent_role,
    is_student_under_active_subscription,
    get_recent_chat_history, save_chat_message,
    save_feedback, get_message_owner,
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

    # PR_H1: multi-turn history.
    prev_messages = get_recent_chat_history(user_id, student_id)

    # Сохраняем user message ДО вызова AI.
    save_chat_message(user_id, student_id, 'user', question)

    # PR_H4: streaming. Шлём placeholder, throttled callback edit'ит его
    # по мере накопления текста. Throttle 1.5с защищает от telegram flood
    # (edit_message_text жёсткого rate-limit'а не имеет, но flood-control
    # сработает при ~20+ edits/sec на один message). Финальный edit ставит
    # feedback markup на готовый ответ.
    try:
        placeholder = bot.send_message(user_id, t("ai_chat_thinking", lang))
        placeholder_msg_id = placeholder.message_id
    except Exception as e:
        logger.warning(f"placeholder send failed user={user_id}: {e}")
        placeholder_msg_id = None

    stream_state = {
        'last_edit_at': 0.0,
        'last_edit_text': '',
    }

    def _on_chunk(accumulated: str):
        if placeholder_msg_id is None:
            return
        now = time.monotonic()
        if now - stream_state['last_edit_at'] < 1.5:
            return
        if accumulated == stream_state['last_edit_text']:
            return
        if not accumulated.strip():
            return
        try:
            bot.edit_message_text(
                chat_id=user_id,
                message_id=placeholder_msg_id,
                text=accumulated,
            )
            stream_state['last_edit_at'] = now
            stream_state['last_edit_text'] = accumulated
        except Exception as e:
            # edit может упасть из-за "message is not modified" или
            # telegram rate limit — не фатально, просто пропускаем чанк
            logger.debug(f"stream edit_message_text failed: {e}")

    try:
        from src.analytics_engine import answer_parent_question
        answer = answer_parent_question(
            student_id=student_id,
            student_name=name,
            grades=recent_grades,
            question=question,
            lang=lang,
            prev_messages=prev_messages,
            stream_callback=_on_chunk if placeholder_msg_id else None,
        )
    except Exception as e:
        logger.warning(f"ai_chat answer failed for user={user_id} student={student_id}: {e}")
        answer = None

    if not answer:
        # Если placeholder был отправлен — заменяем на error, иначе шлём новое
        if placeholder_msg_id is not None:
            try:
                bot.edit_message_text(
                    chat_id=user_id,
                    message_id=placeholder_msg_id,
                    text=t("ai_chat_error", lang),
                )
                return
            except Exception:
                pass
        bot.send_message(user_id, t("ai_chat_error", lang))
        return

    # Сохраняем assistant ответ + получаем msg_id для feedback markup
    msg_id = save_chat_message(user_id, student_id, 'assistant', answer)

    # Финальный edit с полным текстом + 👍/👎 markup. Если placeholder
    # потерялся (send упал) — fallback на send_message.
    final_markup = _build_feedback_markup(msg_id)
    if placeholder_msg_id is not None:
        try:
            bot.edit_message_text(
                chat_id=user_id,
                message_id=placeholder_msg_id,
                text=answer,
                reply_markup=final_markup,
            )
            return
        except Exception as e:
            logger.debug(f"final edit failed, fallback to send: {e}")
    bot.send_message(user_id, answer, reply_markup=final_markup)


def _build_feedback_markup(msg_id: int, selected: int = 0) -> types.InlineKeyboardMarkup:
    """Возвращает 2-кнопочную клавиатуру 👍/👎. selected=1 → ✓ на 👍,
    selected=-1 → ✓ на 👎, 0 → без ✓ (свежий ответ)."""
    up = "👍" + (" ✓" if selected == 1 else "")
    down = "👎" + (" ✓" if selected == -1 else "")
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.row(
        types.InlineKeyboardButton(up, callback_data=f"fb:{msg_id}:u"),
        types.InlineKeyboardButton(down, callback_data=f"fb:{msg_id}:d"),
    )
    return markup


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("fb:"))
def _on_feedback(call):
    """PR_H3: обработка тапа на 👍/👎 под AI-ответом.

    Авторизация: msg_id должен принадлежать user_id вызывающего (защита
    от подделки callback_data через DevTools)."""
    user_id = call.from_user.id
    parts = call.data.split(":", 2)
    if len(parts) != 3 or parts[0] != "fb":
        bot.answer_callback_query(call.id)
        return
    try:
        msg_id = int(parts[1])
    except (ValueError, TypeError):
        bot.answer_callback_query(call.id)
        return
    rating_code = parts[2]
    if rating_code not in ('u', 'd'):
        bot.answer_callback_query(call.id)
        return

    owner = get_message_owner(msg_id)
    if owner != user_id:
        # Не палим разницу 403 vs 404 — silent fail
        bot.answer_callback_query(call.id)
        return

    rating = 1 if rating_code == 'u' else -1
    try:
        save_feedback(msg_id, user_id, rating)
    except Exception as e:
        logger.warning(f"save_feedback failed user={user_id} msg={msg_id}: {e}")
        bot.answer_callback_query(call.id)
        return

    # Подтверждение + обновляем клавиатуру (✓ на выбранном)
    lang = get_user_lang(user_id)
    bot.answer_callback_query(call.id, t("chat_feedback_thanks", lang))
    try:
        bot.edit_message_reply_markup(
            chat_id=user_id,
            message_id=call.message.message_id,
            reply_markup=_build_feedback_markup(msg_id, selected=rating),
        )
    except Exception as e:
        # Edit может упасть если "message is not modified" — не критично
        logger.debug(f"edit_message_reply_markup failed: {e}")
