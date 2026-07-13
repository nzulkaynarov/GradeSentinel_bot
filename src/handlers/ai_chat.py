"""AI-чат с контекстом семьи напрямую в Telegram.

UX (post NAV-001): пользователь жмёт «💬 Чат» → если у него одна семья,
сразу попадает в чат с контекстом ВСЕХ детей; если несколько семей —
сначала выбор семьи. AI видит оценки всех детей и умеет их сравнивать.

State: `ai_chat_mode` с data={'family_id': N}. Раньше было student_id;
после NAV-001 — family_id (см. memory project_ai_roadmap_2026 +
project_navigation_architecture).

Это primary entry point для AI после AI-first redesign (PR_A).
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
    is_subscription_active,
    get_families_for_user, get_family_students,
    get_recent_family_chat_history, save_family_chat_message,
    clear_family_chat_history,
    save_feedback, get_message_owner,
)
from src.i18n import t, get_button_action
from src.utils import to_date_str

logger = logging.getLogger(__name__)

_AI_CHAT_STATE = 'ai_chat_mode'
# 365 дней = вся история учебного года. Claude Haiku 4.5 200K context
# спокойно ест ~3K токенов для ~500 оценок. Раньше было 30 дней — это
# было необоснованное ограничение, и welcome-текст врал юзеру.
_RECENT_DAYS = 365

def handle_ai_deeplink(message, payload: str):
    """Deep-link `/start ai_<base64_question>` из WebApp дашборда.

    Resolve семьи юзера, входит в ai_chat_mode. Если payload не пустой —
    декодирует base64 question и сразу шлёт его в AI. Pre-filled UX.

    Используется кнопками «💬 Спросить AI» в WebApp:
    - General: payload = '' → просто открыть чат
    - Drill-down «про предмет X»: payload = base64('Расскажи про X')
    """
    import base64 as _b64
    user_id = message.from_user.id if hasattr(message, 'from_user') else message.chat.id
    lang = get_user_lang(user_id)

    # Decode payload (URL-safe base64 без padding для коротких payload'ов)
    question = ""
    if payload:
        try:
            # Дополняем padding если был обрезан в URL
            padded = payload + "=" * (-len(payload) % 4)
            question = _b64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"ai_deeplink: bad payload {payload!r}: {e}")
            question = ""

    # Резолвим семью + входим в чат
    families = get_families_for_user(user_id)
    if get_parent_role(user_id) != 'admin':
        families = [f for f in families if is_subscription_active(f['id'])]
    if not families:
        _show_no_chat_dead_end(user_id, lang)
        return

    family = families[0]  # multi-family edge case: берём первую (как и start_ai_chat)
    family_id = family['id']
    set_user_state(user_id, _AI_CHAT_STATE, json.dumps({'family_id': family_id}))

    # Show welcome message
    students = get_family_students(family_id)
    if not students:
        bot.send_message(user_id, t("ai_chat_family_empty", lang))
        return
    names = ", ".join(s.get('display_name') or s['fio'] for s in students)
    bot.send_message(
        user_id,
        t("ai_chat_welcome_family", lang, names=names),
        parse_mode='HTML',
    )

    # Если был pre-filled question — сразу шлём в AI
    if question.strip():
        state = get_user_state(user_id)
        _ask_ai(user_id, question.strip(), lang, state)


def start_ai_chat(user_id: int, reply_keyboard=None):
    """Точка входа в family-scoped AI чат (NAV-001).

    Логика:
    - 0 семей с активной подпиской → dead-end сообщение с CTA-кнопкой
      «💳 Оформить подписку» (NAV-003/006).
    - 1 семья → сразу в чат, контекст ВСЕХ детей этой семьи.
    - Несколько семей (редко) → inline-выбор семьи.

    `reply_keyboard` (если задан) — постоянная reply-keyboard {Чат, Меню},
    ставится на welcome чтобы юзер сразу видел навигацию.
    """
    lang = get_user_lang(user_id)
    is_admin = get_parent_role(user_id) == 'admin'
    families = get_families_for_user(user_id)

    # Фильтруем по активной подписке (admin обходит — для тестирования parent UX).
    # См. memory project_navigation_architecture для admin bypass rationale.
    if not is_admin:
        families = [f for f in families if is_subscription_active(f['id'])]

    if not families:
        # NAV-006 dead-end: добавляем inline CTA-кнопку. Различение
        # «нет семьи вообще» vs «нет подписки» решено в _show_no_chat_dead_end.
        _show_no_chat_dead_end(user_id, lang, reply_keyboard)
        return

    if len(families) == 1:
        _enter_chat_mode(user_id, families[0], lang, reply_keyboard=reply_keyboard)
        return

    # Несколько семей — inline выбор. Edge case (родитель в 2+ семьях).
    markup = types.InlineKeyboardMarkup(row_width=1)
    for f in families:
        markup.add(types.InlineKeyboardButton(
            f['family_name'], callback_data=f"ai_pick_fam:{f['id']}"
        ))
    if reply_keyboard is not None:
        bot.send_message(user_id, t("ai_chat_pick_family", lang),
                          reply_markup=reply_keyboard)
        bot.send_message(user_id, "👇", reply_markup=markup)
    else:
        bot.send_message(user_id, t("ai_chat_pick_family", lang),
                          reply_markup=markup)


def start_ai_chat_with_keyboard(user_id: int, reply_keyboard):
    """Вариант start_ai_chat с обязательной reply-keyboard — для /start
    flow когда нужно сразу установить навигацию."""
    start_ai_chat(user_id, reply_keyboard=reply_keyboard)


def _show_no_chat_dead_end(user_id: int, lang: str, reply_keyboard=None):
    """NAV-006: dead-end когда у юзера нет семей с активной подпиской.

    Различает 2 кейса:
    - юзер вообще не привязан к семье (нет инвайта/не head) → CTA создать или
      ждать инвайта (текст в ai_chat_no_family_yet)
    - юзер в семье но подписка истекла → CTA оформить подписку с inline-кнопкой
      на /subscription.
    """
    families_all = get_families_for_user(user_id)
    if not families_all:
        # Нет семьи совсем — без inline-кнопки на /subscription (нечего платить за)
        bot.send_message(user_id, t("ai_chat_no_family_yet", lang),
                          reply_markup=reply_keyboard)
        return

    # Есть семья(и), но без активной подписки. CTA на оплату.
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        t("ai_chat_cta_subscribe", lang), callback_data="up_subscription"
    ))
    if reply_keyboard is not None:
        bot.send_message(user_id, t("ai_chat_no_subscription", lang),
                          reply_markup=reply_keyboard)
        bot.send_message(user_id, "👇", reply_markup=markup)
    else:
        bot.send_message(user_id, t("ai_chat_no_subscription", lang),
                          reply_markup=markup)


def _enter_chat_mode(user_id: int, family: dict, lang: str, reply_keyboard=None):
    """Ставит state с family_id и отправляет welcome-текст с именами всех
    детей семьи.

    PR_F: примеры вопросов вшиты в welcome-текст. Юзер либо копирует пример,
    либо пишет свой вопрос."""
    family_id = family['id']
    set_user_state(user_id, _AI_CHAT_STATE, json.dumps({
        'family_id': family_id,
    }))

    students = get_family_students(family_id)
    if not students:
        # Семья без детей — нечего обсуждать. Технически возможно сразу
        # после создания семьи до добавления ребёнка. Показываем подсказку.
        bot.send_message(user_id, t("ai_chat_family_empty", lang),
                          reply_markup=reply_keyboard)
        return

    names = ", ".join(s.get('display_name') or s['fio'] for s in students)
    bot.send_message(
        user_id,
        t("ai_chat_welcome_family", lang, names=names),
        parse_mode='HTML',
        reply_markup=reply_keyboard,
    )


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("ai_pick_fam:"))
def _on_pick_family(call):
    """NAV-001: выбор семьи (когда у юзера несколько семей)."""
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    try:
        family_id = int(call.data.split(":", 1)[1])
    except (ValueError, IndexError):
        return

    # Проверяем что семья реально доступна юзеру + подписка активна
    families = get_families_for_user(user_id)
    family = next((f for f in families if f['id'] == family_id), None)
    if not family:
        bot.send_message(user_id, t("ai_chat_no_subscription", lang))
        return
    if get_parent_role(user_id) != 'admin' and not is_subscription_active(family_id):
        # Race condition: подписка истекла между показом меню и тапом.
        # Защита от подделки family_id через DevTools.
        bot.send_message(user_id, t("ai_chat_no_subscription", lang))
        return

    _enter_chat_mode(user_id, family, lang)


@bot.message_handler(
    func=lambda m: (
        m.chat.type == 'private'
        and _is_ai_chat_state(m.from_user.id)
        and get_button_action(m.text) is None
    )
)
def _on_chat_message(message):
    """Любой текст в ai_chat_mode → вопрос для AI.

    ТОЛЬКО private-чаты. Бот добавлен в семейные группы для уведомлений; если
    родитель/админ оставался в ai_chat_mode и писал в группе, сообщение
    участника улетало в AI и бот отвечал (галлюцинировал) прямо в группе.
    Гейт chat.type == 'private' закрывает эту утечку.

    B17: `get_button_action(m.text) is None` исключает метки reply-keyboard
    главного меню (btn_grades «📈 Оценки», btn_user_menu «📱 Меню» и др.).
    `handle_menu_buttons` в main.py регистрируется ПОЗЖЕ этого хендлера, так
    что без исключения метка перехватывалась бы здесь и уходила в AI как
    вопрос. Nav-метки ({Чат, Меню, Дашборд}) не в BUTTON_ACTIONS — их ловит
    navigation.py, зарегистрированный РАНЬШЕ ai_chat. Дырки нет: любая метка
    гарантированно доходит до своего хендлера.

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


def _ask_ai(user_id: int, question: str, lang: str, state: dict,
            skip_save_user: bool = False):
    """NAV-001: family-scoped. Берёт ВСЕХ детей семьи, собирает их оценки
    с annotation student_name, спрашивает Claude (он умеет фильтровать
    по имени или сравнивать всех), сохраняет history per (telegram_id, family_id).

    NAV-007 retry: skip_save_user=True пропускает save user message
    (используется при ai_retry callback — user message уже в history)."""
    try:
        data = json.loads(state.get('data') or '{}')
        family_id = data.get('family_id')
        # Backward compat: state может содержать старый student_id (для рестартов
        # после деплоя NAV-001 — пользователи в активном чате). Резолвим family_id.
        if not family_id:
            student_id_legacy = data.get('student_id')
            if student_id_legacy:
                from src.database_manager import get_families_for_student
                fams = get_families_for_student(student_id_legacy)
                family_id = fams[0]['id'] if fams else None
    except (json.JSONDecodeError, ValueError):
        family_id = None
    if not family_id:
        bot.send_message(user_id, t("ai_chat_session_lost", lang))
        clear_user_state(user_id)
        return

    # Sanity: юзер всё ещё в этой семье?
    families = get_families_for_user(user_id)
    if not any(f['id'] == family_id for f in families):
        bot.send_message(user_id, t("ai_chat_session_lost", lang))
        clear_user_state(user_id)
        return

    students = get_family_students(family_id)
    if not students:
        bot.send_message(user_id, t("ai_chat_family_empty", lang))
        return

    # Индикатор «AI думает»
    try:
        bot.send_chat_action(user_id, 'typing')
    except Exception:
        pass

    # Собираем оценки всех детей с annotation student_name. _format_grades_context
    # читает поле student_name и префиксует [Имя] — система видит чьи оценки.
    all_grades = []
    student_names = []
    for s in students:
        name = s.get('display_name') or s['fio']
        student_names.append(name)
        s_grades = get_grade_history_for_student_all(s['id'], days=_RECENT_DAYS)
        for g in s_grades:
            # Не мутируем оригинальный dict — копия с annotation
            gg = dict(g)
            gg['student_name'] = name
            all_grades.append(gg)
    # Sort by grade_date DESC across all students (по убыванию даты — свежие первыми)
    all_grades.sort(
        key=lambda g: to_date_str(g.get('grade_date') or g.get('date_added')),
        reverse=True,
    )

    # display name для prompt'а: если 1 ребёнок — его имя, иначе перечисление
    family_label = student_names[0] if len(student_names) == 1 else ", ".join(student_names)

    # PR_H1 multi-turn история (family-scoped)
    prev_messages = get_recent_family_chat_history(user_id, family_id)

    # Сохраняем user message ДО вызова AI (orphan acceptable если AI упал).
    # NAV-007 retry: при skip_save_user не сохраняем (user msg уже в history).
    if not skip_save_user:
        save_family_chat_message(user_id, family_id, 'user', question)

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
            student_id=None,  # family-scoped — student_id не релевантен
            student_name=family_label,
            grades=all_grades,
            question=question,
            lang=lang,
            prev_messages=prev_messages,
            family_id=family_id,
            stream_callback=_on_chunk if placeholder_msg_id else None,
        )
    except Exception as e:
        logger.warning(f"ai_chat answer failed for user={user_id} family={family_id}: {e}")
        answer = None

    if not answer:
        # NAV-007: при AI fail показываем retry-кнопку. Последний user msg
        # уже в history (если не skip_save_user), retry читает его оттуда.
        retry_markup = _build_retry_markup(lang)
        if placeholder_msg_id is not None:
            try:
                bot.edit_message_text(
                    chat_id=user_id,
                    message_id=placeholder_msg_id,
                    text=t("ai_chat_error", lang),
                    reply_markup=retry_markup,
                )
                return
            except Exception:
                pass
        bot.send_message(user_id, t("ai_chat_error", lang), reply_markup=retry_markup)
        return

    # Сохраняем assistant ответ + получаем msg_id для feedback markup
    msg_id = save_family_chat_message(user_id, family_id, 'assistant', answer)

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


def _build_retry_markup(lang: str) -> types.InlineKeyboardMarkup:
    """NAV-007: inline-кнопка повторить AI запрос после fail."""
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        t("ai_chat_retry_btn", lang), callback_data="ai_retry"
    ))
    return markup


@bot.callback_query_handler(func=lambda c: c.data == "ai_retry")
def _on_retry(call):
    """NAV-007: повтор последнего вопроса при AI fail.

    Берём последний user-message из family chat history и повторяем
    запрос с skip_save_user=True (msg уже в history)."""
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    lang = get_user_lang(user_id)

    state = get_user_state(user_id)
    if not state or state.get('state') != _AI_CHAT_STATE:
        bot.send_message(user_id, t("ai_chat_session_lost", lang))
        return

    try:
        data = json.loads(state.get('data') or '{}')
        family_id = data.get('family_id')
    except (json.JSONDecodeError, ValueError):
        family_id = None
    if not family_id:
        bot.send_message(user_id, t("ai_chat_session_lost", lang))
        return

    # Находим последний user message
    history = get_recent_family_chat_history(user_id, family_id, limit=20)
    last_user_msg = next(
        (m for m in reversed(history) if m['role'] == 'user'),
        None,
    )
    if not last_user_msg:
        bot.send_message(user_id, t("ai_chat_session_lost", lang))
        return

    # Убираем retry-кнопку из предыдущего message (визуальный feedback что retry стартует)
    try:
        bot.edit_message_reply_markup(
            chat_id=user_id,
            message_id=call.message.message_id,
            reply_markup=None,
        )
    except Exception:
        pass

    _ask_ai(user_id, last_user_msg['content'], lang, state, skip_save_user=True)


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
