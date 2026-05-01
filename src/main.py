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
from src.database_manager import init_db, get_parent_by_phone, update_parent_telegram_id, get_parent_role, get_user_lang
from src.i18n import load_translations, t, BUTTON_ACTIONS
from src.monitor_engine import start_polling

# Import handlers to register them
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

    # Автоматическая авторизация админа
    if admin_id_env and str(user_id) == str(admin_id_env):
        update_parent_telegram_id(f"admin_{user_id}", user_id)
        send_menu_safe(user_id, t("auth_admin_welcome", lang))
        return

    # Check if user is already saved
    from src.database_manager import is_head_of_any_family, has_children_for_grades
    role = get_parent_role(user_id)
    if role:
        if role != 'admin' and not is_head_of_any_family(user_id) and not has_children_for_grades(user_id):
            send_menu_safe(user_id, t("auth_not_linked", lang, btn_support=t("btn_support", lang)))
            return

        send_menu_safe(user_id, t("auth_already", lang))
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

            # Сохраняем выбранный язык (если выбран при /start)
            if chosen_lang:
                set_user_lang(user_id, chosen_lang)
            lang = chosen_lang or get_user_lang(user_id)

            role = parent.get('role', 'senior')
            from src.database_manager import is_head_of_any_family, has_children_for_grades

            if role != 'admin' and not is_head_of_any_family(user_id) and not has_children_for_grades(user_id):
                welcome_msg = t("auth_not_linked_contact", lang, name=parent['fio'])
            else:
                welcome_msg = t("auth_success", lang, name=parent['fio'])
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
                _start_onboarding(user_id, parent['fio'].split()[0] if parent['fio'] else 'друг')
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
            # Перед kbd убираем reply-клаву (request_contact уже не нужна)
            bot.send_message(user_id, t("auth_phone_not_found", lang),
                              reply_markup=types.ReplyKeyboardRemove())
            bot.send_message(user_id, " ", reply_markup=inline_markup)

            # Сохраняем телефон чтобы при выборе "Создать семью" сразу зарегать
            from src.database_manager import set_user_state
            set_user_state(user_id, "pending_selfserve_phone", phone)

            from src.utils import mask_phone
            logger.warning(f"Unauthorized access attempt from phone: {mask_phone(phone)}")
    else:
        lang = get_user_lang(message.chat.id)
        bot.send_message(message.chat.id, t("auth_contact_error", lang))


# ═══════════════════════════════════════════
#  Пользовательская панель (единая точка входа)
# ═══════════════════════════════════════════

def cmd_user_menu(message):
    """Главная пользовательская панель с inline-кнопками."""
    user_id = message.chat.id if hasattr(message, 'chat') else message.from_user.id
    _show_user_panel(user_id)


def _show_user_panel(chat_id: int, message_id: int = None):
    """Показывает пользовательскую панель."""
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

    # Пустое состояние — пользователь без семьи и без детей.
    # Раньше показывали "Свяжитесь с админом" — тупик. Теперь даём
    # actionable выбор: создать свою семью или подождать инвайт-ссылку.
    if not families and not has_kids and not is_head:
        text = t("user_panel_empty", lang)
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            t("btn_create_my_family", lang), callback_data="up_create_family"
        ))
        markup.add(types.InlineKeyboardButton(
            t("btn_have_invite", lang), callback_data="up_have_invite"
        ))
        markup.row(
            types.InlineKeyboardButton(t("user_panel_support", lang), callback_data="up_support"),
            types.InlineKeyboardButton(t("user_panel_lang", lang), callback_data="up_lang"),
        )
    else:
        if fam_lines:
            fam_text = "\n".join(fam_lines)
        else:
            fam_text = t("sub_no_family", lang)

        text = t("user_panel_title", lang, families_info=fam_text)

        markup = types.InlineKeyboardMarkup(row_width=2)

        if has_kids:
            markup.row(
                types.InlineKeyboardButton(t("user_panel_grades", lang), callback_data="up_grades"),
                types.InlineKeyboardButton(t("user_panel_ai", lang), callback_data="up_ai"),
            )

        if is_head:
            markup.row(
                types.InlineKeyboardButton(t("user_panel_family", lang), callback_data="up_family"),
                types.InlineKeyboardButton(t("user_panel_subscription", lang), callback_data="up_subscription"),
            )
        elif has_kids:
            markup.add(types.InlineKeyboardButton(
                t("user_panel_subscription", lang), callback_data="up_subscription"))

        markup.row(
            types.InlineKeyboardButton(t("user_panel_support", lang), callback_data="up_support"),
            types.InlineKeyboardButton(t("btn_notifications", lang), callback_data="up_notifications"),
        )
        markup.add(types.InlineKeyboardButton(t("user_panel_lang", lang), callback_data="up_lang"))

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
    clear_user_state(user_id)

    # Дальше тот же flow что у уже зарегистрированного юзера:
    set_user_state(user_id, "selfserve_family_name", "1")
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton(t("btn_cancel", lang)))
    msg = bot.send_message(
        call.message.chat.id, t("family_create_title", lang),
        parse_mode='Markdown', reply_markup=markup,
    )
    from src.handlers.admin import process_family_name
    bot.register_next_step_handler(msg, process_family_name)


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
    msg = bot.send_message(
        call.message.chat.id,
        t("family_create_title", lang),
        parse_mode='Markdown',
        reply_markup=markup
    )
    # Сохраняем намерение в state, чтобы process_family_name знал что
    # юзер автоматически становится главой (без второго экрана выбора).
    from src.database_manager import set_user_state
    set_user_state(user_id, "selfserve_family_name", "1")
    from src.handlers.admin import process_family_name
    bot.register_next_step_handler(msg, process_family_name)


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
    """Запускает 3-экранный визард приветствия. Можно skip на любом шаге."""
    lang = get_user_lang(chat_id)
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton(t("btn_next", lang), callback_data="onb_2"),
        types.InlineKeyboardButton(t("btn_skip", lang), callback_data="onb_skip"),
    )
    bot.send_message(
        chat_id, t("onboard_step1", lang, name=user_name),
        reply_markup=markup, parse_mode='HTML'
    )


@bot.callback_query_handler(func=lambda call: call.data in ("onb_2", "onb_3", "onb_done", "onb_skip"))
def callback_onboarding(call):
    bot.answer_callback_query(call.id)
    lang = get_user_lang(call.from_user.id)
    chat_id = call.message.chat.id
    msg_id = call.message.message_id

    if call.data == "onb_skip":
        try:
            bot.delete_message(chat_id, msg_id)
        except Exception:
            pass
        _show_user_panel(chat_id)
        return

    if call.data == "onb_2":
        markup = types.InlineKeyboardMarkup()
        markup.row(
            types.InlineKeyboardButton(t("btn_next", lang), callback_data="onb_3"),
            types.InlineKeyboardButton(t("btn_skip", lang), callback_data="onb_skip"),
        )
        try:
            bot.edit_message_text(t("onboard_step2", lang), chat_id, msg_id,
                                   reply_markup=markup, parse_mode='HTML')
        except Exception as e:
            logger.debug(f"onb_2 edit failed: {e}")
        return

    if call.data == "onb_3":
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(t("btn_done", lang), callback_data="onb_done"))
        try:
            bot.edit_message_text(t("onboard_step3", lang), chat_id, msg_id,
                                   reply_markup=markup, parse_mode='HTML')
        except Exception as e:
            logger.debug(f"onb_3 edit failed: {e}")
        return

    if call.data == "onb_done":
        try:
            bot.edit_message_text(t("onboard_done", lang), chat_id, msg_id, parse_mode='HTML')
        except Exception:
            pass
        _show_user_panel(chat_id)
        return


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


@bot.callback_query_handler(func=lambda call: call.data == 'up_family')
def callback_up_family(call):
    """Управление семьёй из пользовательской панели."""
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete panel message for family: {e}")
    cmd_manage_family(call.message)


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

_HEARTBEAT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", ".heartbeat"
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
        # Default scope — на случай если новый scope-API не поддержан
        bot.set_my_commands([
            types.BotCommand("start", "Начать / авторизоваться"),
            types.BotCommand("help", "Справка по боту"),
            types.BotCommand("grades", "Оценки за сегодня"),
            types.BotCommand("status", "Статус и статистика"),
        ])

        # Private chats — пользовательские команды
        bot.set_my_commands(
            [
                types.BotCommand("start", "Начать / авторизоваться"),
                types.BotCommand("help", "Справка по боту"),
                types.BotCommand("grades", "Оценки за сегодня"),
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
