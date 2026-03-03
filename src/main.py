import os
import threading
import sqlite3
from dotenv import load_dotenv
import logging
import telebot
from telebot import types
from src.database_manager import init_db, get_parent_by_phone, update_parent_telegram_id, DB_PATH
from src.monitor_engine import start_polling

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN or ":" not in BOT_TOKEN:
    logger.error("BOT_TOKEN is missing or invalid in environment!")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)

# ====================
# Telegram bot setup
# ====================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    button = types.KeyboardButton("📱 Подтвердить номер телефона", request_contact=True)
    markup.add(button)
    
    bot.send_message(
        message.chat.id, 
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
            is_admin = parent.get('is_admin', 0)
            
            welcome_msg = f"✅ Авторизация успешна! Здравствуйте, {parent['fio']}.\n"
            if is_admin:
                welcome_msg += "👑 Вы авторизованы как администратор. Вам доступны команды управления."
            else:
                welcome_msg += "Теперь я буду присылать вам уведомления о новых оценках."
                
            bot.send_message(user_id, welcome_msg, reply_markup=types.ReplyKeyboardRemove())
            logger.info(f"User {phone} authorized as {'admin' if is_admin else 'parent'}")
        else:
            bot.send_message(
                user_id, 
                "❌ Извините, ваш номер не найден в базе данных.\n"
                "Пожалуйста, свяжитесь с администратором для регистрации.",
                reply_markup=types.ReplyKeyboardRemove()
            )
            logger.warning(f"Unauthorized access attempt from phone: {phone}")

# ====================
# Admin commands
# ====================
def is_user_admin(user_id):
    """Проверяет, является ли пользователь администратором."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT is_admin FROM parents WHERE telegram_id = ?', (user_id,))
        row = cursor.fetchone()
        return row and row['is_admin'] == 1

@bot.message_handler(commands=['admin_help'])
def admin_help(message):
    if not is_user_admin(message.chat.id):
        return
        
    help_text = (
        "🛠 *Панель администратора GradeSentinel*\n\n"
        "/add_parent fio phone [admin 0/1] — Добавить родителя\n"
        "/add_student fio spreadsheet_id — Добавить ученика\n"
        "/add_family name — Создать семью\n"
        "/status — Состояние системы"
    )
    bot.send_message(message.chat.id, help_text, parse_mode='Markdown')

@bot.message_handler(commands=['status'])
def system_status(message):
    if not is_user_admin(message.chat.id):
        return
    
    from src.database_manager import get_active_spreadsheets
    students = get_active_spreadsheets()
    
    status_text = (
        "🛰 *Статус системы*\n"
        f"📊 Активных студентов: {len(students)}\n"
        "⚙️ Мониторинг: Работает"
    )
    bot.send_message(message.chat.id, status_text, parse_mode='Markdown')

@bot.message_handler(commands=['grades'])
def get_grades_command(message):
    """По запросу выводит текущие оценки всех детей родителя."""
    user_id = message.chat.id
    from src.database_manager import get_students_for_parent
    from src.google_sheets import get_sheet_data
    from src.data_cleaner import sanitize_grade
    
    students = get_students_for_parent(user_id)
    if not students:
        bot.send_message(user_id, "ℹ️ У вас нет привязанных учеников. Обратитесь к администратору.")
        return
        
    for student in students:
        fio = student['fio']
        spreadsheet_id = student['spreadsheet_id']
        bot.send_message(user_id, f"🔄 Запрашиваю данные для: {fio}...")
        
        # Range is hardcoded for now as in monitor_engine
        data = get_sheet_data(spreadsheet_id, "Сегодня!A1:B50")
        if not data:
            bot.send_message(user_id, f"⚠️ Не удалось получить данные для {fio}. Проверьте доступ бота к таблице.")
            continue
            
        report = f"📊 *Оценки {fio} за сегодня:*\n\n"
        grades_found = False
        
        # Пропускаем заголовки (data[0])
        for row in data[1:]:
            if len(row) < 2: continue
            subject = row[0].strip()
            raw_grade = row[1].strip()
            if not raw_grade or not subject: continue
            
            _, clean_text = sanitize_grade(raw_grade)
            if clean_text:
                report += f"🔹 {subject}: *{clean_text}*\n"
                grades_found = True
        
        if not grades_found:
            report += "За сегодня записей/оценок пока нет."
            
        bot.send_message(user_id, report, parse_mode='Markdown')

def start_bot():
    """Запускает Telegram бота в режиме polling."""
    logger.info("Starting Telegram Bot...")
    bot.polling(none_stop=True)

def main():
    # Load environment variables from .env
    load_dotenv()
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
