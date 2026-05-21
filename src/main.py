import os
import time
import threading
from collections import defaultdict
from dotenv import load_dotenv
import logging
from telebot import types

# Load environment variables setup
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Конфиг и rate limiter вынесены в отдельные модули
from src.config import (
    RATE_LIMIT_MAX, RATE_LIMIT_WINDOW,
    PANEL_CACHE_TTL, POLLING_INTERVAL, HEARTBEAT_INTERVAL,
)
from src.rate_limiter import is_rate_limited

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

from src.bot_instance import bot
from src.ui import send_menu_safe
from src.database_manager import init_db, get_parent_by_phone, update_parent_telegram_id, update_parent_first_name, get_greeting_name, get_parent_role, get_user_lang
from src.i18n import load_translations, t, BUTTON_ACTIONS
from src.monitor_engine import start_polling

# Import handlers to register them
# state_flows ПЕРВЫЙ — он регистрирует state-machine message_handler'ы которые
# должны обходиться pyTelegramBotAPI'ом раньше generic-обработчиков из других
# модулей. Регистрация в порядке import.
import src.handlers.state_flows  # noqa: F401
# navigation handlers для постоянной reply-keyboard {Чат, Дашборд, Меню}
# регистрируются ПЕРЕД ai_chat — точное совпадение с label-кнопкой
# должно перехватываться здесь, а не уходить как вопрос в AI (PR_F).
import src.handlers.navigation  # noqa: F401
# ai_chat — message_handler по state ai_chat_mode, ловит вопросы родителя.
import src.handlers.ai_chat  # noqa: F401
import src.handlers.admin
import src.handlers.family
import src.handlers.communication
import src.handlers.analytics
import src.handlers.settings
import src.handlers.subscription
import src.handlers.invite
import src.handlers.group

# For direct routing in main menu
from src.handlers.admin import system_status, cmd_list_families, cmd_add_family_start, cmd_admin_panel
from src.handlers.family import cmd_manage_family, get_grades_command
from src.handlers.communication import support_started, broadcast_started
from src.handlers.analytics import cmd_ai_report
from src.handlers.settings import cmd_settings
from src.handlers.subscription import cmd_subscription
from src.database_manager import is_head_of_any_family, has_children_for_grades, get_families_for_user, is_subscription_active, get_family_subscription

# ====================
# Telegram bot setup
# ====================
@bot.message_handler(commands=['help'])
def send_help(message):
    """Справка по командам бота — адаптируется под роль пользователя."""
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    from src.database_manager import is_head_of_any_family

    # Базовая справка для всех
    text = t("help_parent", lang)

    # Дополнение для глав семей
    if is_head_of_any_family(user_id):
        text += t("help_head", lang)

    # Дополнение для админа
    if get_parent_role(user_id) == 'admin':
        text += t("help_admin", lang)

    send_menu_safe(user_id, text)


@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    admin_id_env = os.environ.get("ADMIN_ID")

    # Проверяем deep link (инвайт)
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith('inv_'):
        invite_code = args[1][4:]
        from src.handlers.invite import handle_invite_deeplink
        handle_invite_deeplink(message, invite_code)
        return

    # Автоматическая авторизация админа.
    # PR_F-hotfix: admin НЕ попадает в AI-чат как default — он работает с
    # admin panel. Чистим ai_chat_mode state если случайно туда зашёл,
    # шлём приветствие + кнопку «🛠 Управление».
    if admin_id_env and str(user_id) == str(admin_id_env):
        update_parent_telegram_id(f"admin_{user_id}", user_id)
        update_parent_first_name(user_id, message.from_user.first_name)
        _show_admin_welcome(user_id, lang)
        return

    # Check if user is already saved
    from src.database_manager import is_head_of_any_family, has_children_for_grades, clear_user_state, get_user_state
    role = get_parent_role(user_id)
    if role:
        update_parent_first_name(user_id, message.from_user.first_name)

        # PR_F-hotfix: admin в БД (не env) тоже идёт в admin panel, не в AI-чат.
        if role == 'admin':
            _show_admin_welcome(user_id, lang)
            return

        # /start всегда чистит ai_chat_mode для не-админов — escape hatch
        # если юзер застрял в чате.
        st = get_user_state(user_id)
        if st and st.get('state') == 'ai_chat_mode':
            clear_user_state(user_id)

        if not is_head_of_any_family(user_id) and not has_children_for_grades(user_id):
            send_menu_safe(user_id, t("auth_not_linked", lang, btn_support=t("btn_support", lang)))
            return

        # PR_F: авторизованный юзер с детьми → СРАЗУ в AI-чат, не в меню.
        if has_children_for_grades(user_id):
            _enter_default_chat(user_id)
        else:
            _show_user_panel(user_id)
        return

    # Для неавторизованных — показываем выбор языка, затем авторизацию
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("🇷🇺 Русский", callback_data="start_lang_ru"),
        types.InlineKeyboardButton("🇺🇿 O'zbek", callback_data="start_lang_uz"),
        types.InlineKeyboardButton("🇬🇧 English", callback_data="start_lang_en"),
    )
    bot.send_message(user_id, t("lang_select"), reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith('start_lang_'))
def callback_start_lang(call):
    """Выбор языка при первом /start — сохраняем и показываем авторизацию."""
    from src.database_manager import set_user_lang
    lang = call.data.replace('start_lang_', '')
    user_id = call.message.chat.id

    # Для неавторизованных — пока не можем сохранить в БД (нет записи parents),
    # запоминаем в user_states как временное
    from src.database_manager import set_user_state
    set_user_state(user_id, "pending_lang", lang)

    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(user_id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete start_lang message: {e}")

    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True,
                                        input_field_placeholder=t("auth_placeholder", lang))
    button = types.KeyboardButton(t("btn_share_contact", lang), request_contact=True)
    markup.add(button)

    bot.send_message(user_id, t("auth_welcome", lang), reply_markup=markup)


@bot.message_handler(content_types=['contact'])
def contact_handler(message):
    if message.contact is not None:
        phone = message.contact.phone_number
        user_id = message.chat.id

        # Проверяем состояние пользователя (язык или инвайт)
        from src.database_manager import get_user_state, clear_user_state, set_user_lang
        state = get_user_state(user_id)
        chosen_lang = state.get('data') if state and state.get('state') == 'pending_lang' else None
        pending_invite = state.get('data') if state and state.get('state') == 'pending_invite' else None

        if chosen_lang:
            clear_user_state(user_id)

        # Если это инвайт — обрабатываем через invite handler
        if pending_invite:
            clear_user_state(user_id)
            from src.handlers.invite import process_invite_after_contact
            if process_invite_after_contact(user_id, phone, pending_invite):
                return

        parent = get_parent_by_phone(phone)

        if parent:
            update_parent_telegram_id(phone, user_id)
            update_parent_first_name(user_id, message.from_user.first_name)
            # Перечитываем после update — приветствие использует свежее имя
            parent = get_parent_by_phone(phone) or parent

            # Сохраняем выбранный язык (если выбран при /start)
            if chosen_lang:
                set_user_lang(user_id, chosen_lang)
            lang = chosen_lang or get_user_lang(user_id)

            role = parent.get('role', 'senior')
            greeting_name = get_greeting_name(parent)
            from src.database_manager import is_head_of_any_family, has_children_for_grades

            if role != 'admin' and not is_head_of_any_family(user_id) and not has_children_for_grades(user_id):
                welcome_msg = t("auth_not_linked_contact", lang, name=greeting_name)
            else:
                welcome_msg = t("auth_success", lang, name=greeting_name)
                if role == 'admin':
                    welcome_msg += t("auth_role_admin", lang)
                elif is_head_of_any_family(user_id):
                    welcome_msg += t("auth_role_head", lang)
                else:
                    welcome_msg += t("auth_role_parent", lang, btn_grades=t("btn_grades", lang))

            send_menu_safe(user_id, welcome_msg)
            from src.utils import mask_phone
            logger.info(f"User {mask_phone(phone)} authorized as {role}")

            # Запускаем onboarding wizard если юзер новый (никогда не получал
            # /start раньше — определяем по отсутствию записи в user_states
            # с ключом onboarding_done)
            from src.database_manager import get_user_state, set_user_state
            onb_state = get_user_state(user_id)
            if not (onb_state and onb_state.get('state') == 'onboarding_done'):
                set_user_state(user_id, 'onboarding_done', '1')
                _start_onboarding(user_id, greeting_name)
        else:
            lang = chosen_lang or 'ru'
            # Self-serve путь вместо тупика: юзер не в БД → две кнопки
            # «Создать свою семью» и «У меня инвайт-ссылка». Админу
            # больше не нужно регистрировать каждого вручную.
            inline_markup = types.InlineKeyboardMarkup()
            inline_markup.add(types.InlineKeyboardButton(
                t("btn_create_my_family", lang), callback_data="up_create_family_new"
            ))
            inline_markup.add(types.InlineKeyboardButton(
                t("btn_have_invite", lang), callback_data="up_have_invite"
            ))
            admin_id_env = os.environ.get("ADMIN_ID")
            if admin_id_env:
                inline_markup.add(types.InlineKeyboardButton(
                    t("btn_contact_admin", lang),
                    url=f"tg://user?id={admin_id_env}"
                ))
            # Сначала убираем reply-клавиатуру (request_contact не нужна).
            # Используем служебное «...» которое сразу удаляем, чтобы не оставлять
            # пустое сообщение в чате. Затем основное сообщение с inline-кнопками.
            # ВАЖНО: раньше посылали " " (пробел) — Telegram теперь возвращает 400
            # "text must be non-empty", из-за чего кнопки не появлялись и юзер
            # видел тупик с одним только вопросом «Что хотите сделать?».
            try:
                kbd_remover = bot.send_message(
                    user_id, "…", reply_markup=types.ReplyKeyboardRemove()
                )
                bot.delete_message(user_id, kbd_remover.message_id)
            except Exception as e:
                logger.debug(f"Could not remove reply keyboard: {e}")
            bot.send_message(user_id, t("auth_phone_not_found", lang),
                              reply_markup=inline_markup)

            # Сохраняем телефон чтобы при выборе "Создать семью" сразу зарегать
            from src.database_manager import set_user_state
            set_user_state(user_id, "pending_selfserve_phone", phone)

            from src.utils import mask_phone
            logger.warning(f"Unauthorized access attempt from phone: {mask_phone(phone)}")
    else:
        lang = get_user_lang(message.chat.id)
        bot.send_message(message.chat.id, t("auth_contact_error", lang))


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
        markup.row(
            types.KeyboardButton(t("nav_chat", lang)),
            types.KeyboardButton(t("nav_menu", lang)),
        )
        if is_admin:
            # Admin в parent-mode видит toggle обратно в admin
            markup.add(types.KeyboardButton(t("nav_admin_panel", lang)))
    return markup


def _show_admin_welcome(chat_id: int, lang: str):
    """Приветствие админа: текст + reply-keyboard admin-режима
    {🛠 Управление, 👨 Я родитель}. Toggle между режимами — через эту
    постоянную reply-keyboard (PR role-toggle 21.05).

    Admin тапает «🛠 Управление» → admin panel; «👨 Я родитель» → parent
    режим (только если есть дети). Inline-кнопки из welcome убраны
    (дублировали label'ы)."""
    from src.database_manager import clear_user_state, get_user_state
    st = get_user_state(chat_id)
    if st and st.get('state') == 'ai_chat_mode':
        clear_user_state(chat_id)

    bot.send_message(chat_id, t("auth_admin_welcome", lang),
                      reply_markup=_build_reply_keyboard(lang, mode='admin'),
                      parse_mode='HTML')


def _enter_default_chat(chat_id: int):
    """Стартует parent-режим (AI-чат + reply-keyboard) для любого юзера.

    PR role-toggle: если юзер — admin (через «👨 Я родитель» из admin
    режима), reply-keyboard содержит дополнительный toggle «🛠 Управление»
    для возврата обратно. Не-admin видит только {💬 Чат, ⚙️ Меню}."""
    from src.handlers.ai_chat import start_ai_chat_with_keyboard
    lang = get_user_lang(chat_id)
    is_admin = get_parent_role(chat_id) == 'admin'
    keyboard = _build_reply_keyboard(lang, mode='parent', is_admin=is_admin)
    start_ai_chat_with_keyboard(chat_id, keyboard)


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
            status = f"✅ до {fs['sub']['subscription_end'][:10]}"
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


@bot.callback_query_handler(func=lambda call: call.data == 'open_admin_panel')
def callback_open_admin_panel(call):
    """Inline-кнопка из admin welcome → открывает admin panel."""
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete admin welcome: {e}")
    cmd_admin_panel(call.message)


@bot.callback_query_handler(func=lambda call: call.data == 'open_admin_as_parent')
def callback_open_admin_as_parent(call):
    """«👨 Я родитель» — admin переключается в parent-режим про своих детей.

    Tier 2 admin-audit: owner проекта = admin с собственными детьми, и ему
    нужно тестировать parent UX (AI-чат, дашборд) без второго аккаунта.
    Возврат в admin panel через /start (он снова покажет admin welcome)."""
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete admin welcome: {e}")
    _enter_default_chat(call.from_user.id)


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


@bot.callback_query_handler(func=lambda call: call.data == 'up_grades')
def callback_up_grades(call):
    """Оценки из пользовательской панели."""
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete panel message for grades: {e}")
    get_grades_command(call.message)
    _show_user_panel(call.message.chat.id)


@bot.callback_query_handler(func=lambda call: call.data == 'up_ai')
def callback_up_ai(call):
    """AI-анализ из пользовательской панели."""
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete panel message for AI: {e}")
    cmd_ai_report(call.message)
    _show_user_panel(call.message.chat.id)


@bot.callback_query_handler(func=lambda call: call.data == 'up_ai_chat')
def callback_up_ai_chat(call):
    """Запуск AI-чата прямо в Telegram. Открывает state ai_chat_mode,
    далее ai_chat.py handler ловит все text-сообщения от юзера."""
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete panel message for AI chat: {e}")
    from src.handlers.ai_chat import start_ai_chat
    start_ai_chat(call.from_user.id)


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

@bot.message_handler(func=lambda m: m.text in BUTTON_ACTIONS)
def handle_menu_buttons(message):
    """Обработчик нажатий на кнопки главного меню (мультиязычный)."""
    action = BUTTON_ACTIONS[message.text]
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

_HEARTBEAT_PATH = os.environ.get(
    "HEARTBEAT_PATH",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", ".heartbeat"
    ),
)


def _heartbeat_loop():
    """Раз в N секунд touch'ит файл data/.heartbeat. Docker healthcheck смотрит mtime —
    если файл «протух» (>3 минут без обновлений), значит main thread/polling завис.
    Интервал берётся из config.HEARTBEAT_INTERVAL."""
    while True:
        try:
            os.makedirs(os.path.dirname(_HEARTBEAT_PATH), exist_ok=True)
            with open(_HEARTBEAT_PATH, "w") as f:
                f.write(str(int(time.time())))
        except Exception as e:
            logger.warning(f"Heartbeat write failed: {e}")
        time.sleep(HEARTBEAT_INTERVAL)


def start_bot():
    """Запускает Telegram бота в режиме polling."""
    logger.info("Starting Telegram Bot...")
    threading.Thread(target=_heartbeat_loop, daemon=True).start()
    bot.polling(none_stop=True)

def main():
    logger.info("Initializing GradeSentinel v2.0...")

    # 0. Error reporter (Sentry hook, no-op без SENTRY_DSN)
    from src.error_reporter import _try_init_sentry
    _try_init_sentry()

    # 1. Init DB
    init_db()

    # 2. Load translations
    load_translations()

    # 3. Start monitor engine in a separate thread
    from src.monitor_engine import set_bot_instance
    set_bot_instance(bot)

    monitor_thread = threading.Thread(target=start_polling, args=(POLLING_INTERVAL,), daemon=True)
    monitor_thread.start()
    logger.info("Monitor engine thread started with bot integration.")

    # 4. Start weekly AI report scheduler (if API key available)
    from src.handlers.analytics import start_weekly_scheduler
    start_weekly_scheduler()

    # 5. Start daily schedulers (evening summary, quiet hours flush, bot alive)
    from src.schedulers import start_daily_schedulers, set_bot_instance as set_scheduler_bot
    set_scheduler_bot(bot)
    start_daily_schedulers()

    # 6. Register bot commands in Telegram menu
    _register_bot_commands()

    # 7. Start telegram bot blocking main thread
    start_bot()


def _register_bot_commands():
    """Регистрирует команды бота в меню Telegram (кнопка / в чате).

    Разные scope для приватных и групповых чатов:
    - в личке: /start /help /grades /status;
    - в группе: /set_thread /unlink_group (управление привязкой к семье).
    Без scope-разделения в группе подсказывались бы /grades и /status, которые там не работают."""
    try:
        # NAV-008: добавлены /ai_report и /subscription — раньше они были
        # доступны как handler'ы, но не отображались в BotFather menu (кнопка «/»).
        # Default scope — на случай если новый scope-API не поддержан
        bot.set_my_commands([
            types.BotCommand("start", "Начать / авторизоваться"),
            types.BotCommand("help", "Справка по боту"),
            types.BotCommand("grades", "Оценки за сегодня"),
            types.BotCommand("ai_report", "AI-анализ за 2 недели"),
            types.BotCommand("subscription", "Подписка"),
            types.BotCommand("status", "Статус и статистика"),
        ])

        # Private chats — пользовательские команды
        bot.set_my_commands(
            [
                types.BotCommand("start", "Начать / авторизоваться"),
                types.BotCommand("help", "Справка по боту"),
                types.BotCommand("grades", "Оценки за сегодня"),
                types.BotCommand("ai_report", "AI-анализ за 2 недели"),
                types.BotCommand("subscription", "Подписка"),
                types.BotCommand("status", "Статус и статистика"),
            ],
            scope=types.BotCommandScopeAllPrivateChats(),
        )

        # Group chats — управление привязкой к семье
        bot.set_my_commands(
            [
                types.BotCommand("set_thread", "Указать тему по ссылке"),
                types.BotCommand("unlink_group", "Отвязать чат от семьи"),
            ],
            scope=types.BotCommandScopeAllGroupChats(),
        )

        logger.info("Bot commands registered (private + group scopes).")
    except Exception as e:
        logger.warning(f"Could not set bot commands: {e}")

if __name__ == '__main__':
    main()
