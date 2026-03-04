import os
import threading
import sqlite3
from dotenv import load_dotenv
import logging
import telebot
from telebot import types
from src.database_manager import (
    init_db, get_parent_by_phone, update_parent_telegram_id, 
    get_parent_role, get_last_menu_id, update_last_menu_id,
    DB_PATH
)
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
    user_id = message.chat.id
    admin_id_env = os.environ.get("ADMIN_ID")
    
    # Автоматическая авторизация админа
    if admin_id_env and str(user_id) == str(admin_id_env):
        update_parent_telegram_id(f"admin_{user_id}", user_id) # Ensure DB linked
        send_menu_safe(user_id, "✅ Авторизация успешна! Здравствуйте, Super Admin.\n👑 Вы авторизованы как *Супер-администратор*.")
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
            
            welcome_msg = f"✅ Авторизация успешна! Здравствуйте, {parent['fio']}.\n"
            if role == 'admin':
                welcome_msg += "👑 Вы авторизованы как <b>Супер-администратор</b>."
            elif role == 'head':
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

# ====================
# UI/UX Navigation Helpers
# ====================

def get_main_menu(role):
    """Возвращает клавиатуру главного меню в зависимости от роли."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if role == 'admin':
        markup.row("📊 Статус", "🏠 Семьи")
        markup.row("➕ Новая семья")
    elif role == 'head':
        markup.row("🏠 Моя семья", "📈 Оценки")
    else:
        markup.row("📈 Оценки")
    return markup

def send_menu_safe(chat_id, text, reply_markup=None, inline_markup=None):
    """
    Отправляет меню, удаляя предыдущее сообщение меню для поддержания чистоты чата.
    """
    last_id = get_last_menu_id(chat_id)
    if last_id:
        try:
            bot.delete_message(chat_id, last_id)
        except Exception:
            pass # Сообщение могло быть уже удалено или устарело (>48ч)

    role = get_parent_role(chat_id)
    if not reply_markup:
        reply_markup = get_main_menu(role)

    # Используем inline_markup если он передан, иначе основной
    final_markup = inline_markup if inline_markup else reply_markup
    
    msg = bot.send_message(chat_id, text, reply_markup=final_markup, parse_mode='HTML')
    update_last_menu_id(chat_id, msg.message_id)

@bot.message_handler(func=lambda m: m.text in ["📊 Статус", "🏠 Семьи", "➕ Новая семья", "🏠 Моя семья", "📈 Оценки"])
def main_menu_buttons_handler(message):
    """Обработчик нажатий на кнопки главного меню."""
    txt = message.text
    user_id = message.chat.id
    
    # Удаляем само сообщение с текстом кнопки, чтобы не мусорить
    try:
        bot.delete_message(user_id, message.message_id)
    except:
        pass

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

# ====================
# Admin commands
# ====================
def is_user_admin(user_id):
    """Проверяет, является ли пользователь администратором."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute('SELECT role FROM parents WHERE telegram_id = ?', (user_id,))
        row = cursor.fetchone()
        return row and row['role'] == 'admin'

@bot.message_handler(commands=['admin_help'])
def admin_help(message):
    if not is_user_admin(message.chat.id):
        return
        
    help_text = (
        "🛠 <b>Панель администратора GradeSentinel</b>\n\n"
        "Кнопки внизу экрана помогут вам перемещаться по разделам.\n"
        "Бот автоматически удаляет старые меню, чтобы чат оставался чистым."
    )
    send_menu_safe(message.chat.id, help_text)

@bot.message_handler(commands=['status'])
def system_status(message):
    if not is_user_admin(message.chat.id):
        return
    
    from src.database_manager import get_active_spreadsheets
    students = get_active_spreadsheets()
    
    status_text = (
        "🛰 <b>Статус системы</b>\n\n"
        f"📊 Активных студентов: {len(students)}\n"
        "⚙️ Мониторинг: Работает"
    )
    send_menu_safe(message.chat.id, status_text)

# ====================
# Admin & Family Management
# ====================

def validate_phone(phone: str) -> bool:
    """Проверяет маску телефона (Узбекистан 998XXXXXXXXX)."""
    import re
    clean_phone = phone.replace("+", "").replace(" ", "").replace("-", "")
    return bool(re.match(r"^998\d{9}$", clean_phone))

@bot.message_handler(commands=['add_family'])
def cmd_add_family_start(message):
    """Супер-админ: Начало создания семьи."""
    from src.database_manager import get_parent_role
    
    user_id = message.from_user.id
    if get_parent_role(user_id) != 'admin':
        bot.reply_to(message, "⛔ У вас нет прав доступа к этой команде.")
        return
        
    msg = bot.send_message(message.chat.id, "🏗 *Создание новой семьи*\n\nВведите название семьи (фамилию):", parse_mode='Markdown')
    bot.register_next_step_handler(msg, process_family_name)

@bot.message_handler(commands=['list_families'])
def cmd_list_families(message):
    """Супер-админ: Показать список всех семей."""
    from src.database_manager import get_all_families
    
    user_id = message.from_user.id
    if get_parent_role(user_id) != 'admin':
        bot.reply_to(message, "⛔ У вас нет прав доступа к этой команде.")
        return
        
    families = get_all_families()
    if not families:
        send_menu_safe(message.chat.id, "📭 В базе пока нет ни одной семьи.")
        return
        
    report = "📋 <b>Список всех семей:</b>\n\n"
    for f in families:
        head = f['head_fio'] if f['head_fio'] else "Не назначен"
        report += f"🏠 <b>{f['family_name']}</b> (ID: {f['id']})\n"
        report += f"👤 Глава: {head}\n"
        report += f"🧒 Детей: {f['child_count']}/5\n"
        report += "──────────────────\n"
        
    send_menu_safe(message.chat.id, report)

def process_family_name(message):
    family_name = message.text.strip()
    if not family_name:
        bot.reply_to(message, "❌ Название не может быть пустым. Попробуйте снова /add_family")
        return
        
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add(types.KeyboardButton("👑 Сделать меня главой"))
    markup.add(types.KeyboardButton("👤 Назначить другого"))
    
    send_menu_safe(
        message.chat.id, 
        f"📝 Семья: <b>{family_name}</b>\n\nВы хотите стать главой этой семьи или назначить другого человека?", 
        reply_markup=markup
    )
    bot.register_next_step_handler_by_chat_id(message.chat.id, process_head_choice, family_name)

def process_head_choice(message, family_name):
    if message.text == "👑 Сделать меня главой":
        from src.database_manager import add_family, link_parent_to_family, get_db_connection
        user_id = message.from_user.id
        
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT id FROM parents WHERE telegram_id = ?', (user_id,))
                row = cursor.fetchone()
                
            if not row:
                bot.reply_to(message, "❌ Ваша учетная запись не найдена в базе. Сначала пройдите авторизацию.", reply_markup=types.ReplyKeyboardRemove())
                return
                
            send_menu_safe(
                message.chat.id, 
                f"✅ <b>Семья '{family_name}' создана!</b>\n\nВы назначены главой. Теперь вы можете использовать 🏠 Моя семья для управления."
            )
        except Exception as e:
            send_menu_safe(message.chat.id, f"❌ Ошибка: {e}")
    else:
        # Для назначения другого ФИО и телефон запрашиваются обычными сообщениями (не меню)
        # Но последнее меню мы удалим
        send_menu_safe(message.chat.id, "Введите <b>ФИО Главы семьи</b>:")
        bot.register_next_step_handler_by_chat_id(message.chat.id, process_head_fio, family_name)

def process_head_fio(message, family_name):
    head_fio = message.text.strip()
    if len(head_fio) < 3:
        bot.reply_to(message, "❌ Слишком короткое ФИО. Попробуйте снова /add_family")
        return
        
    msg = bot.send_message(message.chat.id, f"👤 Глава: {head_fio}\n\nВведите *номер телефона* (998XXXXXXXXX):", parse_mode='Markdown')
    bot.register_next_step_handler(msg, process_head_phone, family_name, head_fio)

def process_head_phone(message, family_name, head_fio):
    head_phone = message.text.strip()
    
    if not validate_phone(head_phone):
        bot.reply_to(message, "❌ Неверный формат телефона. Должно быть 12 цифр (начиная с 998). Попробуйте снова /add_family")
        return
        
    try:
        from src.database_manager import add_family, add_parent, link_parent_to_family
        
        f_id = add_family(family_name)
        p_id = add_parent(head_fio, head_phone, role='head')
        link_parent_to_family(f_id, p_id)
        
        send_menu_safe(
            message.chat.id, 
            f"✅ <b>Семья успешно создана!</b>\n\n"
            f"🏘 Семья: {family_name}\n"
            f"👑 Глава: {head_fio}\n"
            f"📞 Телефон: {head_phone}\n\n"
            "Статус: Активен"
        )
    except Exception as e:
        send_menu_safe(message.chat.id, f"❌ Ошибка в базе данных: {e}")

@bot.message_handler(commands=['manage_family'])
def cmd_manage_family(message):
    """Глава семьи: Меню управления членами и детьми."""
    from src.database_manager import get_family_by_head, get_child_count
    
    user_id = message.from_user.id
    role = get_parent_role(user_id)
    if role != 'head' and role != 'admin':
        bot.reply_to(message, "⛔ Эта команда доступна только главам семей.")
        return
        
    f_id = get_family_by_head(user_id)
    if not f_id and role != 'admin':
        bot.reply_to(message, "❌ Семья не найдена.")
        return
        
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("➕ Добавить родственника", callback_data=f"add_member_{f_id}"))
    markup.add(types.InlineKeyboardButton("🧒 Добавить ребенка", callback_data=f"add_child_{f_id}"))
    markup.add(types.InlineKeyboardButton("📋 Список и Удаление", callback_data=f"list_edit_{f_id}"))
    
    child_count = get_child_count(f_id)
    send_menu_safe(message.chat.id, f"🏠 <b>Управление семьей</b>\nДетей в базе: {child_count}/5", inline_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('list_edit_'))
def callback_list_edit(call):
    from src.database_manager import get_family_members, get_family_students
    f_id = int(call.data.split('_')[2])
    
    members = get_family_members(f_id)
    students = get_family_students(f_id)
    
    markup = types.InlineKeyboardMarkup()
    
    # Сначала дети
    if students:
        markup.add(types.InlineKeyboardButton("─── ДЕТИ ───", callback_data="none"))
        for s in students:
            markup.add(types.InlineKeyboardButton(f"❌ {s['fio']}", callback_data=f"del_stud_{f_id}_{s['id']}"))
            
    # Потом родственники
    if members:
        markup.add(types.InlineKeyboardButton("─── РОДСТВЕННИКИ ───", callback_data="none"))
        for m in members:
            label = f"{m['fio']} ({m['role']})"
            if m['role'] != 'head':
                markup.add(types.InlineKeyboardButton(f"❌ {label}", callback_data=f"del_par_{f_id}_{m['id']}"))
            else:
                markup.add(types.InlineKeyboardButton(f"👑 {label}", callback_data="none"))
                
    markup.add(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"back_manage_{f_id}"))
    bot.edit_message_text("Выберите кого нужно *удалить* из семьи:", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('del_par_'))
def callback_del_parent(call):
    from src.database_manager import delete_parent_from_family
    parts = call.data.split('_')
    f_id, p_id = int(parts[2]), int(parts[3])
    
    if delete_parent_from_family(f_id, p_id):
        bot.answer_callback_query(call.id, "✅ Родственник удален")
        callback_list_edit(call)
    else:
        bot.answer_callback_query(call.id, "⚠️ Нельзя удалить главу семьи", show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('del_stud_'))
def callback_del_student(call):
    from src.database_manager import delete_student_from_family
    parts = call.data.split('_')
    f_id, s_id = int(parts[2]), int(parts[3])
    
    delete_student_from_family(f_id, s_id)
    bot.answer_callback_query(call.id, "✅ Ребенок удален")
    callback_list_edit(call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('back_manage_'))
def callback_back_manage(call):
    # Просто вызываем заново меню управления, но через edit
    from src.database_manager import get_child_count
    f_id = int(call.data.split('_')[2])
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("➕ Добавить родственника", callback_data=f"add_member_{f_id}"))
    markup.add(types.InlineKeyboardButton("🧒 Добавить ребенка", callback_data=f"add_child_{f_id}"))
    markup.add(types.InlineKeyboardButton("📋 Список и Удаление", callback_data=f"list_edit_{f_id}"))
    
    child_count = get_child_count(f_id)
    bot.edit_message_text(f"🏠 *Управление семьей*\nДетей в базе: {child_count}/5", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('add_child_'))
def callback_add_child(call):
    f_id = call.data.split('_')[2]
    send_menu_safe(call.message.chat.id, "Отправьте ссылку на Google Таблицу ребенка:")
    bot.register_next_step_handler_by_chat_id(call.message.chat.id, process_add_child_step, f_id)

def process_add_child_step(message, f_id):
    from src.database_manager import add_student, link_student_to_family, get_child_count
    from src.google_sheets import get_spreadsheet_title
    
    url = message.text.strip()
    # Удаляем сообщение пользователя с ссылкой
    try: bot.delete_message(message.chat.id, message.message_id)
    except: pass

    if "docs.google.com/spreadsheets/d/" not in url:
        send_menu_safe(message.chat.id, "❌ Некорректная ссылка на Google Таблицу.")
        return
        
    try:
        # Извлекаем ID из ссылки
        ss_id = url.split("/d/")[1].split("/")[0]
        
        if get_child_count(int(f_id)) >= 5:
            send_menu_safe(message.chat.id, "⚠️ Достигнут лимит: максимум 5 детей на семью.")
            return
            
        # Пытаемся получить имя из таблицы
        title = get_spreadsheet_title(ss_id)
        if not title: title = "Новый ученик"
        
        s_id = add_student(title, ss_id)
        link_student_to_family(int(f_id), s_id)
        
        send_menu_safe(
            message.chat.id, 
            f"✅ <b>Ребенок успешно добавлен!</b>\n\n"
            f"👤 Имя: {title}\n"
            f"📊 Статус: Мониторинг активирован\n\n"
            f"💡 Вы также можете в любой момент проверить текущие оценки кнопкой '📈 Оценки'"
        )
    except Exception as e:
        send_menu_safe(message.chat.id, f"❌ Ошибка при добавлении: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('add_member_'))
def callback_add_member(call):
    f_id = call.data.split('_')[2]
    send_menu_safe(call.message.chat.id, "Введите ФИО и номер телефона родственника (через пробел):\nПример: <code>Иванов Иван 998901234567</code>")
    bot.register_next_step_handler_by_chat_id(call.message.chat.id, process_add_member_step, f_id)

def process_add_member_step(message, f_id):
    from src.database_manager import add_parent, link_parent_to_family
    
    try:
        # Удаляем сообщение пользователя с данными
        try: bot.delete_message(message.chat.id, message.message_id)
        except: pass

        parts = message.text.split(maxsplit=2)
        if len(parts) < 3:
            # Если только имя и номер
            fio = parts[0]
            phone = parts[1]
        else:
            fio = parts[0] + " " + parts[1]
            phone = parts[2]
            
        p_id = add_parent(fio, phone, role='senior')
        link_parent_to_family(int(f_id), p_id)
        
        send_menu_safe(message.chat.id, f"✅ Родственник <b>{fio}</b> добавлен в семью.")
    except Exception as e:
        send_menu_safe(message.chat.id, f"❌ Ошибка: {e}")
@bot.message_handler(commands=['grades'])
def get_grades_command(message):
    """По запросу выводит текущие оценки всех детей родителя."""
    user_id = message.chat.id
    from src.database_manager import get_students_for_parent
    from src.google_sheets import get_sheet_data, get_spreadsheet_title
    from src.data_cleaner import sanitize_grade
    
    students = get_students_for_parent(user_id)
    if not students:
        bot.send_message(user_id, "ℹ️ У вас нет привязанных учеников. Обратитесь к администратору.")
        return
        
    for student in students:
        fio = student['fio']
        spreadsheet_id = student['spreadsheet_id']
        
        sheet_title = get_spreadsheet_title(spreadsheet_id)
        from src.utils import clean_student_name
        display_name = clean_student_name(sheet_title) if sheet_title else fio
        
        data = get_sheet_data(spreadsheet_id, "Сегодня!A1:B50")
        if not data:
            send_menu_safe(user_id, f"⚠️ Не удалось получить данные для {display_name}.")
            continue
            
        report: str = f"📊 <b>Оценки {display_name} за сегодня:</b>\n\n"
        grades_found = False
        
        for row in data[1:]:
            if len(row) < 2: continue
            subject = row[0].strip()
            raw_grade = row[1].strip()
            if not raw_grade or not subject: continue
            
            _, clean_text = sanitize_grade(raw_grade)
            if clean_text:
                report += f"🔹 {subject}: <b>{clean_text}</b>\n"
                grades_found = True
        
        if not grades_found:
            report += "За сегодня записей/оценок пока нет."
            
            report += f"\n\n<a href='https://docs.google.com/spreadsheets/d/{spreadsheet_id}'>🔗 Открыть таблицу</a>"
            send_menu_safe(user_id, report)

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
