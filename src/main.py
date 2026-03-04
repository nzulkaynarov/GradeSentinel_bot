import os
import threading
from dotenv import load_dotenv
import logging
from telebot import types

# Load environment variables setup
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

from src.bot_instance import bot
from src.ui import send_menu_safe
from src.database_manager import init_db, get_parent_by_phone, update_parent_telegram_id, get_parent_role
from src.monitor_engine import start_polling

# Import handlers to register them
import src.handlers.admin
import src.handlers.family
import src.handlers.communication

# For direct routing in main menu
from src.handlers.admin import system_status
from src.handlers.family import cmd_list_families, cmd_add_family_start, cmd_manage_family
from src.handlers.family import get_grades_command
from src.handlers.communication import support_started, broadcast_started

# ====================
# Telegram bot setup
# ====================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.chat.id
    admin_id_env = os.environ.get("ADMIN_ID")
    
    # Автоматическая авторизация админа
    if admin_id_env and str(user_id) == str(admin_id_env):
        update_parent_telegram_id(f"admin_{user_id}", user_id) # Ensure DB linked
        send_menu_safe(user_id, "✅ Авторизация успешна! Здравствуйте, Super Admin.\n👑 Вы авторизованы как *Супер-администратор*.")
        return

    # Check if user is already saved
    from src.database_manager import get_parent_role, is_head_of_any_family, has_children_for_grades
    role = get_parent_role(user_id)
    if role:
        if role != 'admin' and not is_head_of_any_family(user_id) and not has_children_for_grades(user_id):
            send_menu_safe(user_id, "ℹ️ Ваш аккаунт зарегистрирован, но в данный момент вы не привязаны ни к одной семье.\nПожалуйста, ожидайте, пока администратор добавит вас.")
            return
            
        send_menu_safe(user_id, "✅ Вы уже авторизованы. Главное меню загружено.")
        return

    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    button = types.KeyboardButton("📱 Подтвердить номер телефона", request_contact=True)
    markup.add(button)
    
    bot.send_message(
        user_id, 
        "Привет! Я GradeSentinel. Для работы мне нужно подтвердить, что вы есть в нашей базе.\n\n"
        "Пожалуйста, нажмите кнопку ниже, чтобы поделиться контактом.",
        reply_markup=markup
    )

@bot.message_handler(content_types=['contact'])
def contact_handler(message):
    if message.contact is not None:
        phone = message.contact.phone_number
        user_id = message.chat.id
        
        parent = get_parent_by_phone(phone)
        
        if parent:
            update_parent_telegram_id(phone, user_id)
            role = parent.get('role', 'senior')
            
            from src.database_manager import is_head_of_any_family, has_children_for_grades
            
            if role != 'admin' and not is_head_of_any_family(user_id) and not has_children_for_grades(user_id):
                welcome_msg = f"ℹ️ Здравствуйте, {parent['fio']}.\nВаш номер подтвержден, но в данный момент вы не привязаны ни к одной семье. Ожидайте действий администратора."
            else:
                welcome_msg = f"✅ Авторизация успешна! Здравствуйте, {parent['fio']}.\n"
                if role == 'admin':
                    welcome_msg += "👑 Вы авторизованы как <b>Супер-администратор</b>."
                else:
                    if is_head_of_any_family(user_id):
                        welcome_msg += "🏠 Вы авторизованы как <b>Глава семьи</b>."
                    else:
                        welcome_msg += "Теперь я буду присылать вам уведомления о новых оценках."
                
            send_menu_safe(user_id, welcome_msg)
            logger.info(f"User {phone} authorized as {role}")
        else:
            bot.send_message(
                user_id, 
                "❌ Извините, ваш номер не найден в базе данных.\n"
                "Пожалуйста, свяжитесь с администратором для регистрации.",
                reply_markup=types.ReplyKeyboardRemove()
            )
            logger.warning(f"Unauthorized access attempt from phone: {phone}")
    else:
        bot.send_message(message.chat.id, "❌ Ошибка при получении контакта.")

@bot.message_handler(func=lambda m: m.text in ["📊 Статус", "🏠 Семьи", "➕ Новая семья", "🏠 Моя семья", "📈 Оценки", "💬 Поддержка", "📢 Рассылка"])
def handle_menu_buttons(message):
    """Обработчик нажатий на кнопки главного меню."""
    txt = message.text
    user_id = message.chat.id
    
    try:
        bot.delete_message(user_id, message.message_id)
    except:
        pass

    role = get_parent_role(user_id)
    logger.info(f"Button clicked: '{txt}' by user {user_id} (role: {role})")

    if txt == "📊 Статус":
        system_status(message)
    elif txt == "🏠 Семьи":
        cmd_list_families(message)
    elif txt == "➕ Новая семья":
        cmd_add_family_start(message)
    elif txt == "🏠 Моя семья":
        cmd_manage_family(message)
    elif txt == "📈 Оценки":
        get_grades_command(message)
    elif txt == "💬 Поддержка":
        support_started(message)
    elif txt == "📢 Рассылка":
        broadcast_started(message)

def start_bot():
    """Запускает Telegram бота в режиме polling."""
    logger.info("Starting Telegram Bot...")
    bot.polling(none_stop=True)

def main():
    logger.info("Initializing GradeSentinel v2.0...")
    
    # 1. Init DB
    init_db()
    
    # 2. Start monitor engine in a separate thread
    from src.monitor_engine import set_bot_instance
    set_bot_instance(bot)
    
    monitor_thread = threading.Thread(target=start_polling, args=(300,), daemon=True)
    monitor_thread.start()
    logger.info("Monitor engine thread started with bot integration.")
    
    # 3. Start telegram bot blocking main thread
    start_bot()

if __name__ == '__main__':
    main()
