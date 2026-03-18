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

# Rate limiting: max 5 requests per 10 seconds per user
_rate_limit_store: dict = defaultdict(list)
RATE_LIMIT_MAX = 5
RATE_LIMIT_WINDOW = 10  # seconds

def is_rate_limited(user_id: int) -> bool:
    """Проверяет, превышен ли лимит запросов для пользователя."""
    now = time.time()
    timestamps = _rate_limit_store[user_id]
    _rate_limit_store[user_id] = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW]
    if len(_rate_limit_store[user_id]) >= RATE_LIMIT_MAX:
        return True
    _rate_limit_store[user_id].append(now)
    return False

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

# For direct routing in main menu
from src.handlers.admin import system_status, cmd_list_families, cmd_add_family_start
from src.handlers.family import cmd_manage_family, get_grades_command
from src.handlers.communication import support_started, broadcast_started
from src.handlers.analytics import cmd_ai_report
from src.handlers.settings import cmd_settings
from src.handlers.subscription import cmd_subscription

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
    except Exception:
        pass

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
            logger.info(f"User {phone} authorized as {role}")
        else:
            lang = chosen_lang or 'ru'
            admin_id_env = os.environ.get("ADMIN_ID")
            not_found_text = t("auth_phone_not_found", lang)
            if admin_id_env:
                inline_markup = types.InlineKeyboardMarkup()
                inline_markup.add(types.InlineKeyboardButton(
                    t("btn_contact_admin", lang),
                    url=f"tg://user?id={admin_id_env}"
                ))
                bot.send_message(user_id, not_found_text, reply_markup=inline_markup)
            else:
                bot.send_message(user_id, not_found_text, reply_markup=types.ReplyKeyboardRemove())
            logger.warning(f"Unauthorized access attempt from phone: {phone}")
    else:
        lang = get_user_lang(message.chat.id)
        bot.send_message(message.chat.id, t("auth_contact_error", lang))


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

    if action == "status":
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

def start_bot():
    """Запускает Telegram бота в режиме polling."""
    logger.info("Starting Telegram Bot...")
    bot.polling(none_stop=True)

def main():
    logger.info("Initializing GradeSentinel v2.0...")

    # 1. Init DB
    init_db()

    # 2. Load translations
    load_translations()

    # 3. Start monitor engine in a separate thread
    from src.monitor_engine import set_bot_instance
    set_bot_instance(bot)

    monitor_thread = threading.Thread(target=start_polling, args=(300,), daemon=True)
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
    """Регистрирует команды бота в меню Telegram (кнопка / в чате)."""
    try:
        bot.set_my_commands([
            types.BotCommand("start", "Начать / авторизоваться"),
            types.BotCommand("help", "Справка по боту"),
            types.BotCommand("grades", "Оценки за сегодня"),
            types.BotCommand("status", "Статус и статистика"),
        ])
        logger.info("Bot commands registered in Telegram menu.")
    except Exception as e:
        logger.warning(f"Could not set bot commands: {e}")

if __name__ == '__main__':
    main()
