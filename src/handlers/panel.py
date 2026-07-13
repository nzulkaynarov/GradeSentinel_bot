"""Пользовательская inline-панель + reply-keyboard навигация + меню-роутинг.

Вынесено из ``src/main.py`` (PR-M3, чистый рефакторинг — поведение не меняется).

Содержит:
- панель-кэш (`_panel_cache` + `_get_panel_data` + `_invalidate_panel_cache`),
  thread-safe под локом;
- `_show_user_panel` — главное inline-меню (family / subscription / settings /
  support / dashboard) + empty-state для новых юзеров;
- все `up_*` callback-хендлеры + `callback_set_notify`;
- `_start_onboarding` — короткое welcome для нового юзера;
- `_build_reply_keyboard` — постоянная reply-keyboard {Чат / Дашборд / Меню};
- `_set_dashboard_menu_button` — chat menu button на WebApp-дашборд;
- `handle_menu_buttons` — диспетчер нажатий reply-кнопок главного меню.

Регистрация хендлеров: модуль импортируется из ``src/main.py`` ПОСЛЕ
state_flows / navigation / ai_chat и остальных handler-модулей, поэтому
`handle_menu_buttons` (generic func-handler) регистрируется последним — метки,
не пойманные навигацией/ai_chat, проваливаются сюда.
"""
import os
import time
import threading
import logging

from telebot import types

from src.bot_instance import bot
from src.config import PANEL_CACHE_TTL
from src.rate_limiter import is_rate_limited
from src.utils import to_date_str
from src.i18n import t, get_button_action
from src.database_manager import (
    get_user_lang, get_parent_role,
    is_head_of_any_family, has_children_for_grades, get_families_for_user,
    is_subscription_active, get_family_subscription,
)

# Точки входа для меню-роутинга. panel импортируется main'ом ПОСЛЕ этих
# handler-модулей, поэтому module-level импорт безопасен (нет цикла).
from src.handlers.admin import (
    system_status, cmd_list_families, cmd_add_family_start, cmd_admin_panel,
)
from src.handlers.family import cmd_manage_family, get_grades_command
from src.handlers.communication import support_started, broadcast_started
from src.handlers.analytics import cmd_ai_report
from src.handlers.settings import cmd_settings
from src.handlers.subscription import cmd_subscription

logger = logging.getLogger(__name__)

_panel_cache: dict = {}  # {chat_id: (timestamp, data_dict)}
_panel_cache_lock = threading.Lock()


def _get_panel_data(chat_id: int) -> dict:
    """Returns cached panel data or fetches fresh from DB. Thread-safe."""
    now = time.time()
    with _panel_cache_lock:
        cached = _panel_cache.get(chat_id)
        if cached and (now - cached[0]) < PANEL_CACHE_TTL:
            return cached[1]

    data = {
        'is_head': is_head_of_any_family(chat_id),
        'has_kids': has_children_for_grades(chat_id),
        'families': get_families_for_user(chat_id),
    }
    # Fetch subscription status per family
    fam_subs = []
    for fam in data['families']:
        active = is_subscription_active(fam['id'])
        sub = get_family_subscription(fam['id'])
        fam_subs.append({
            'id': fam['id'],
            'family_name': fam['family_name'],
            'active': active,
            'sub': sub,
        })
    data['fam_subs'] = fam_subs
    with _panel_cache_lock:
        _panel_cache[chat_id] = (now, data)
    return data


def _invalidate_panel_cache(chat_id: int):
    """Invalidates panel cache for a user (call after data changes). Thread-safe."""
    with _panel_cache_lock:
        _panel_cache.pop(chat_id, None)


# ═══════════════════════════════════════════
#  Пользовательская панель + AI-first navigation (PR_F)
# ═══════════════════════════════════════════

def _build_reply_keyboard(lang: str, mode: str = 'parent', is_admin: bool = False) -> types.ReplyKeyboardMarkup:
    """Постоянная reply-keyboard внизу — варианты:

    mode='parent' (default, для всех родителей):
      ┌──────────┬──────────┐
      │ 💬 Чат   │ ⚙️ Меню  │
      └──────────┴──────────┘
      Если is_admin=True (admin сейчас в parent-mode) — добавляется
      строка с «🛠 Управление» для toggle обратно в admin.

    mode='admin' (только для admin):
      ┌──────────────┬──────────────┐
      │ 🛠 Управление│ 👨 Я родитель│
      └──────────────┴──────────────┘
      Toggle в parent — через «Я родитель».

    Для не-admin'ов is_admin игнорируется. Они никогда не видят admin-кнопки.
    """
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True)
    if mode == 'admin':
        markup.row(
            types.KeyboardButton(t("nav_admin_panel", lang)),
            types.KeyboardButton(t("nav_as_parent", lang)),
        )
    else:  # parent mode
        # 3 кнопки: Чат / Дашборд / Меню. «📊 Дашборд» добавлен по user
        # feedback — нужен постоянный 1-tap (2 на самом деле — reply-button
        # → inline message → WebApp т.к. reply-keyboard.WebApp не передаёт
        # initData по Telegram API constraint).
        markup.row(
            types.KeyboardButton(t("nav_chat", lang)),
            types.KeyboardButton(t("nav_dashboard", lang)),
            types.KeyboardButton(t("nav_menu", lang)),
        )
        if is_admin:
            # Admin в parent-mode видит toggle обратно в admin
            markup.add(types.KeyboardButton(t("nav_admin_panel", lang)))
    return markup


def _set_dashboard_menu_button(user_id: int, lang: str):
    """Ставит chat menu button (слева от input) как WebApp button.
    Telegram show'ит её всегда — родитель имеет постоянный 1-tap доступ к
    дашборду. KeyboardButton.web_app не передаёт initData, поэтому используем
    chat menu (передаёт)."""
    webapp_url = os.environ.get("WEBAPP_URL")
    if not webapp_url:
        return
    try:
        bot.set_chat_menu_button(
            chat_id=user_id,
            menu_button=types.MenuButtonWebApp(
                text=t("menu_btn_dashboard", lang),
                web_app=types.WebAppInfo(url=f"{webapp_url}/webapp"),
            ),
        )
    except Exception as e:
        logger.debug(f"set_chat_menu_button failed for {user_id}: {e}")

    # Для неавторизованных — показываем выбор языка, затем авторизацию
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("🇷🇺 Русский", callback_data="start_lang_ru"),
        types.InlineKeyboardButton("🇺🇿 O'zbek", callback_data="start_lang_uz"),
        types.InlineKeyboardButton("🇬🇧 English", callback_data="start_lang_en"),
    )
    bot.send_message(user_id, t("lang_select"), reply_markup=markup)


def cmd_user_menu(message):
    """Главная пользовательская панель с inline-кнопками."""
    user_id = message.chat.id if hasattr(message, 'chat') else message.from_user.id
    _show_user_panel(user_id)


def _show_user_panel(chat_id: int, message_id: int = None):
    """Показывает inline-меню (Family / Subscription / Settings / Support).

    PR_F: меню больше НЕ содержит «Чат» и «Дашборд» — они в постоянной
    reply-keyboard внизу. Это убирает дублирование. Меню — только для
    редких actions (family management, subscription, settings, support).
    Empty state — отдельная ветка с CTA «создать семью» / «инвайт»."""
    lang = get_user_lang(chat_id)
    panel = _get_panel_data(chat_id)
    is_head = panel['is_head']
    has_kids = panel['has_kids']
    families = panel['families']

    # Собираем информацию о семьях из кэша
    fam_lines = []
    for fs in panel['fam_subs']:
        if fs['active'] and fs['sub'] and fs['sub'].get('subscription_end'):
            status = f"✅ до {to_date_str(fs['sub']['subscription_end'])}"
        else:
            status = "❌"
        fam_lines.append(f"🏠 <b>{fs['family_name']}</b> — {status}")

    if not families and not has_kids and not is_head:
        # Empty state: новый юзер без семьи и без детей.
        # CTA — создать семью ИЛИ инвайт (на твоё усмотрение PR_F: оставил
        # обе — это разные intent'ы, не сливаются).
        text = t("user_panel_empty", lang)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            t("btn_create_my_family", lang), callback_data="up_create_family"
        ))
        markup.add(types.InlineKeyboardButton(
            t("btn_have_invite", lang), callback_data="up_have_invite"
        ))
        markup.add(types.InlineKeyboardButton(
            t("user_panel_support", lang), callback_data="up_support"
        ))
    else:
        if fam_lines:
            fam_text = "\n".join(fam_lines)
        else:
            fam_text = t("sub_no_family", lang)

        # NAV-002: head без детей видит «Меню» без явного гайда что делать.
        # Используем enhanced text c CTA-описанием — кнопка «Добавить ребёнка»
        # ниже + текст объясняет почему её надо нажать первой.
        if is_head and not has_kids:
            text = t("user_panel_title_head_no_kids", lang, families_info=fam_text)
        else:
            text = t("user_panel_title", lang, families_info=fam_text)
        markup = types.InlineKeyboardMarkup(row_width=2)

        # 📊 Дашборд — inline WebApp button (передаёт signed initData).
        # PR_F-hotfix #59: reply-keyboard WebApp button НЕ передаёт initData,
        # поэтому Дашборд переехал сюда. Daily access — 1 клик через Меню.
        if has_kids:
            webapp_url = os.environ.get("WEBAPP_URL")
            if webapp_url:
                markup.add(types.InlineKeyboardButton(
                    t("btn_webapp", lang),
                    web_app=types.WebAppInfo(url=f"{webapp_url}/webapp"),
                ))

        # CTA «Добавить ребёнка» — primary action для head без детей.
        if is_head and not has_kids:
            markup.add(types.InlineKeyboardButton(
                t("user_panel_add_child", lang), callback_data="up_add_child"
            ))

        # Family + Subscription
        if is_head:
            markup.row(
                types.InlineKeyboardButton(t("user_panel_family", lang), callback_data="up_family"),
                types.InlineKeyboardButton(t("user_panel_subscription", lang), callback_data="up_subscription"),
            )
        elif has_kids:
            markup.add(types.InlineKeyboardButton(
                t("user_panel_subscription", lang), callback_data="up_subscription"))

        # Settings (язык + уведомления в одном экране) + Support
        markup.row(
            types.InlineKeyboardButton(t("user_panel_settings", lang), callback_data="up_settings"),
            types.InlineKeyboardButton(t("user_panel_support", lang), callback_data="up_support"),
        )

    if message_id:
        try:
            bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                                  reply_markup=markup, parse_mode='HTML')
            return
        except Exception as e:
            logger.debug(f"Could not edit user panel message: {e}")

    bot.send_message(chat_id, text, reply_markup=markup, parse_mode='HTML')


@bot.callback_query_handler(func=lambda call: call.data == 'up_back')
def callback_up_back(call):
    """Назад в пользовательскую панель."""
    bot.answer_callback_query(call.id)
    _show_user_panel(call.message.chat.id, call.message.message_id)


@bot.callback_query_handler(func=lambda call: call.data == 'up_create_family_new')
def callback_up_create_family_new(call):
    """Self-serve регистрация: юзера нет в БД, у него уже сохранён phone в pending_selfserve_phone.
    Создаём parent + предлагаем ввести имя семьи."""
    from src.database_manager import (
        get_user_state, clear_user_state, add_parent, update_parent_telegram_id,
        set_user_state,
    )
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    state = get_user_state(user_id)
    phone = state.get('data') if state and state.get('state') == 'pending_selfserve_phone' else None
    if not phone:
        # Сессия потерялась — попросим начать с /start
        bot.send_message(call.message.chat.id, t("auth_contact_error", lang))
        return
    # Создаём parent record
    fio_default = call.from_user.first_name or "User"
    if call.from_user.last_name:
        fio_default = f"{fio_default} {call.from_user.last_name}"
    add_parent(fio_default, phone, role='senior')
    update_parent_telegram_id(phone, user_id)
    update_parent_first_name(user_id, call.from_user.first_name)
    clear_user_state(user_id)

    # Дальше тот же flow что у уже зарегистрированного юзера. Состояние
    # хранится в user_states (persistent — переживёт рестарт бота), маршрут
    # на следующий шаг — через src/handlers/state_flows.py.
    # data='selfserve' — маркер для process_family_name чтобы сразу сделать
    # юзера главой без второго экрана выбора.
    set_user_state(user_id, "awaiting_family_name", "selfserve")
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton(t("btn_cancel", lang)))
    bot.send_message(
        call.message.chat.id, t("family_create_title", lang),
        parse_mode='Markdown', reply_markup=markup,
    )


@bot.callback_query_handler(func=lambda call: call.data == 'up_create_family')
def callback_up_create_family(call):
    """Self-serve: юзер без семьи решил создать свою. Запускаем тот же flow что у админа."""
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete panel before create_family: {e}")
    # Используем существующий flow создания семьи
    from src.handlers.admin import callback_ap_new_family
    # Подделываем call как будто пришло из админ-панели — но для не-админов
    # cmd_add_family_start защищён ролью. Поэтому делаем обход: создаём
    # упрощённый flow «сделать меня главой» сразу.
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton(t("btn_cancel", lang)))
    # Состояние в user_states (persistent), маршрут — state_flows._on_family_name.
    # data='selfserve' → process_family_name автоматически делает юзера главой.
    from src.database_manager import set_user_state
    set_user_state(user_id, "awaiting_family_name", "selfserve")
    bot.send_message(
        call.message.chat.id,
        t("family_create_title", lang),
        parse_mode='Markdown',
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data == 'up_have_invite')
def callback_up_have_invite(call):
    """Юзер сообщает что у него есть инвайт-ссылка. Объясняем как ею воспользоваться."""
    bot.answer_callback_query(call.id)
    lang = get_user_lang(call.from_user.id)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        t("user_panel_back", lang), callback_data="up_back"
    ))
    bot.edit_message_text(
        t("no_invite_help", lang),
        chat_id=call.message.chat.id, message_id=call.message.message_id,
        reply_markup=markup, parse_mode='HTML'
    )


# ═══════════════════════════════════════════
#  Onboarding wizard — 3 экрана для нового юзера
# ═══════════════════════════════════════════

def _start_onboarding(chat_id: int, user_name: str):
    """Короткое welcome (1 экран вместо 3): что бот делает + 1 CTA.
    Если у юзера уже есть дети — переходим в чат. Иначе — кнопка «Добавить ребёнка»."""
    lang = get_user_lang(chat_id)
    has_kids = has_children_for_grades(chat_id)
    is_head = is_head_of_any_family(chat_id)

    if has_kids:
        # Уже есть ребёнок → сразу в чат с AI
        bot.send_message(chat_id, t("onboard_done", lang, name=user_name), parse_mode='HTML')
        # _enter_default_chat живёт в src/main.py (auth/welcome flow); отложенный
        # импорт разрывает цикл main ↔ panel.
        from src.main import _enter_default_chat
        _enter_default_chat(chat_id)
        return

    # Head без детей или новый юзер: показываем CTA «Добавить ребёнка»
    markup = types.InlineKeyboardMarkup()
    if is_head:
        markup.add(types.InlineKeyboardButton(
            t("user_panel_add_child", lang), callback_data="up_add_child"
        ))
    bot.send_message(chat_id, t("onboard_short", lang, name=user_name),
                      reply_markup=markup, parse_mode='HTML')


@bot.callback_query_handler(func=lambda call: call.data == 'up_family')
def callback_up_family(call):
    """Управление семьёй из пользовательской панели."""
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete panel message for family: {e}")
    cmd_manage_family(call.message)


@bot.callback_query_handler(func=lambda call: call.data == 'up_add_child')
def callback_up_add_child(call):
    """Self-serve добавление ученика через Sheets URL прямо из user_panel.

    PR_C: shortcut к существующему process_add_child_step flow без перехода
    через «Моя семья → Добавить ребёнка». Для head ровно одной семьи —
    сразу в state STATE_AWAITING_CHILD_URL. Для head нескольких — спрашиваем."""
    bot.answer_callback_query(call.id)
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    from src.database_manager import get_families_for_head, set_user_state
    families = get_families_for_head(user_id)
    if not families:
        bot.send_message(user_id, t("add_child_no_family", lang))
        return

    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete panel for add_child: {e}")

    if len(families) == 1:
        f_id = families[0]['id']
        set_user_state(user_id, "awaiting_child_url", str(f_id))
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True,
                                            input_field_placeholder=t("child_url_placeholder", lang))
        markup.add(types.KeyboardButton(t("btn_cancel", lang)))
        bot.send_message(user_id, t("child_enter_url", lang), reply_markup=markup)
        return

    # Несколько семей — спросить какую
    markup = types.InlineKeyboardMarkup(row_width=1)
    for fam in families:
        markup.add(types.InlineKeyboardButton(
            f"🏠 {fam['family_name']}", callback_data=f"add_child_{fam['id']}"
        ))
    markup.add(types.InlineKeyboardButton(t("user_panel_back", lang), callback_data="up_back"))
    bot.send_message(user_id, t("add_child_pick_family", lang), reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == 'up_subscription')
def callback_up_subscription(call):
    """Подписка из пользовательской панели."""
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete panel message for subscription: {e}")
    cmd_subscription(call.message)
    _show_user_panel(call.message.chat.id)


@bot.callback_query_handler(func=lambda call: call.data == 'up_support')
def callback_up_support(call):
    """Поддержка из пользовательской панели."""
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete panel message for support: {e}")
    support_started(call.message)


@bot.callback_query_handler(func=lambda call: call.data == 'up_settings')
def callback_up_settings(call):
    """Объединённый экран настроек (PR_F): язык + уведомления вместо двух
    отдельных кнопок в user_panel."""
    bot.answer_callback_query(call.id)
    lang = get_user_lang(call.from_user.id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton(
        t("settings_lang", lang), callback_data="up_lang"
    ))
    markup.add(types.InlineKeyboardButton(
        t("settings_notifications", lang), callback_data="up_notifications"
    ))
    markup.add(types.InlineKeyboardButton(
        t("user_panel_back", lang), callback_data="up_back"
    ))
    try:
        bot.edit_message_text(
            t("settings_title", lang),
            chat_id=call.message.chat.id, message_id=call.message.message_id,
            reply_markup=markup, parse_mode='HTML'
        )
    except Exception as e:
        logger.debug(f"Could not edit settings panel: {e}")


@bot.callback_query_handler(func=lambda call: call.data == 'up_notifications')
def callback_up_notifications(call):
    """Настройки уведомлений."""
    bot.answer_callback_query(call.id)
    from src.database_manager import get_notify_mode
    chat_id = call.message.chat.id
    lang = get_user_lang(chat_id)
    current_mode = get_notify_mode(call.from_user.id)
    mode_label = t("notify_mode_instant", lang) if current_mode == 'instant' else t("notify_mode_summary", lang)

    markup = types.InlineKeyboardMarkup(row_width=1)
    if current_mode == 'instant':
        markup.add(types.InlineKeyboardButton(t("notify_btn_summary", lang), callback_data="set_notify_summary_only"))
    else:
        markup.add(types.InlineKeyboardButton(t("notify_btn_instant", lang), callback_data="set_notify_instant"))
    markup.add(types.InlineKeyboardButton(t("user_panel_back", lang), callback_data="up_back"))

    try:
        bot.edit_message_text(
            t("notify_settings_title", lang, mode=mode_label),
            chat_id=chat_id, message_id=call.message.message_id,
            reply_markup=markup, parse_mode='HTML'
        )
    except Exception as e:
        logger.debug(f"Could not edit notify settings: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith('set_notify_'))
def callback_set_notify(call):
    """Переключает режим уведомлений."""
    from src.database_manager import set_notify_mode
    mode = 'instant' if call.data == 'set_notify_instant' else 'summary_only'
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    set_notify_mode(user_id, mode)
    lang = get_user_lang(user_id)

    key = "notify_changed_instant" if mode == 'instant' else "notify_changed_summary"
    bot.answer_callback_query(call.id, t(key, lang)[:200])

    _invalidate_panel_cache(chat_id)
    _show_user_panel(chat_id, call.message.message_id)


@bot.callback_query_handler(func=lambda call: call.data == 'up_lang')
def callback_up_lang(call):
    """Смена языка из пользовательской панели — показываем выбор прямо в панели."""
    bot.answer_callback_query(call.id)
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("🇷🇺 Русский", callback_data="set_lang_ru"),
        types.InlineKeyboardButton("🇺🇿 O'zbek", callback_data="set_lang_uz"),
        types.InlineKeyboardButton("🇬🇧 English", callback_data="set_lang_en"),
    )
    markup.add(types.InlineKeyboardButton(
        t("user_panel_back", get_user_lang(call.message.chat.id)),
        callback_data="up_back"))
    try:
        bot.edit_message_text(
            t("lang_select"), chat_id=call.message.chat.id,
            message_id=call.message.message_id, reply_markup=markup)
    except Exception as e:
        logger.debug(f"Could not edit panel for lang selection: {e}")
        cmd_settings(call.message)


# ═══════════════════════════════════════════
#  Обработка Reply-кнопок главного меню
# ═══════════════════════════════════════════

@bot.message_handler(
    func=lambda m: m.chat.type == 'private' and get_button_action(m.text) is not None
)
def handle_menu_buttons(message):
    """Обработчик нажатий на кнопки главного меню (мультиязычный).

    Только private: reply-keyboard кнопки живут лишь в личке. В группе
    совпадение текста не должно перехватываться (бот там — для уведомлений)."""
    action = get_button_action(message.text)
    if action is None:
        # Гонка: язык сменился между func-проверкой и телом → метка уже
        # не в актуальном маппинге. Молча выходим (юзер повторит тап).
        return
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    if is_rate_limited(user_id):
        bot.send_message(user_id, t("rate_limited", lang))
        return

    try:
        bot.delete_message(user_id, message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete menu button message: {e}")

    role = get_parent_role(user_id)
    logger.info(f"Button action: '{action}' by user {user_id} (role: {role})")

    if action == "admin_panel":
        cmd_admin_panel(message)
    elif action == "user_menu":
        cmd_user_menu(message)
    elif action == "status":
        system_status(message)
    elif action == "families":
        cmd_list_families(message)
    elif action == "new_family":
        cmd_add_family_start(message)
    elif action == "my_family":
        cmd_manage_family(message)
    elif action == "grades":
        get_grades_command(message)
    elif action == "ai_analysis":
        cmd_ai_report(message)
    elif action == "support":
        support_started(message)
    elif action == "broadcast":
        broadcast_started(message)
    elif action == "subscription":
        cmd_subscription(message)
    elif action == "settings":
        cmd_settings(message)
