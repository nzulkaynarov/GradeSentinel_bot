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
    user_id = message.chat.id
    admin_id_env = os.environ.get("ADMIN_ID")
    
    # Автоматическая авторизация админа
    if admin_id_env and str(user_id) == str(admin_id_env):
        bot.send_message(
            user_id,
            "✅ Авторизация успешна! Здравствуйте, Super Admin.\n👑 Вы авторизованы как *Супер-администратор* по телеграм ID.",
            reply_markup=types.ReplyKeyboardRemove(),
            parse_mode='Markdown'
        )
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
                welcome_msg += "👑 Вы авторизованы как *Супер-администратор*."
            elif role == 'head':
                welcome_msg += "🏠 Вы авторизованы как *Глава семьи*. Используйте /manage_family для управления."
            else:
                welcome_msg += "Теперь я буду присылать вам уведомления о новых оценках."
                
            bot.send_message(user_id, welcome_msg, reply_markup=types.ReplyKeyboardRemove(), parse_mode='Markdown')
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
    from src.database_manager import get_parent_role, get_all_families
    
    user_id = message.from_user.id
    if get_parent_role(user_id) != 'admin':
        bot.reply_to(message, "⛔ У вас нет прав доступа к этой команде.")
        return
        
    families = get_all_families()
    if not families:
        bot.reply_to(message, "📭 В базе пока нет ни одной семьи.")
        return
        
    report = "📋 *Список всех семей:*\n\n"
    for f in families:
        head = f['head_fio'] if f['head_fio'] else "Не назначен"
        report += f"🏠 *{f['family_name']}* (ID: {f['id']})\n"
        report += f"👤 Глава: {head}\n"
        report += f"🧒 Детей: {f['child_count']}/5\n"
        report += "──────────────────\n"
        
    bot.send_message(message.chat.id, report, parse_mode='Markdown')

def process_family_name(message):
    family_name = message.text.strip()
    if not family_name:
        bot.reply_to(message, "❌ Название не может быть пустым. Попробуйте снова /add_family")
        return
        
    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add(types.KeyboardButton("👑 Сделать меня главой"))
    markup.add(types.KeyboardButton("👤 Назначить другого"))
    
    msg = bot.send_message(
        message.chat.id, 
        f"📝 Семья: *{family_name}*\n\nВы хотите стать главой этой семьи или назначить другого человека?", 
        reply_markup=markup, 
        parse_mode='Markdown'
    )
    bot.register_next_step_handler(msg, process_head_choice, family_name)

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
                
            f_id = add_family(family_name)
            link_parent_to_family(f_id, row['id'])
            
            bot.send_message(
                message.chat.id, 
                f"✅ *Семья '{family_name}' создана!*\n\nВы назначены главой. Теперь вы можете использовать /manage_family для управления.",
                reply_markup=types.ReplyKeyboardRemove(),
                parse_mode='Markdown'
            )
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}", reply_markup=types.ReplyKeyboardRemove())
    else:
        msg = bot.send_message(message.chat.id, "Введите *ФИО Главы семьи*:", reply_markup=types.ReplyKeyboardRemove(), parse_mode='Markdown')
        bot.register_next_step_handler(msg, process_head_fio, family_name)

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
        
        bot.send_message(
            message.chat.id, 
            f"✅ *Семья успешно создана!*\n\n"
            f"🏘 Семья: {family_name}\n"
            f"👑 Глава: {head_fio}\n"
            f"📞 Телефон: {head_phone}\n\n"
            "Статус: Активен",
            parse_mode='Markdown'
        )
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка в базе данных: {e}")

@bot.message_handler(commands=['manage_family'])
def cmd_manage_family(message):
    """Глава семьи: Меню управления членами и детьми."""
    from src.database_manager import get_parent_role, get_family_by_head, get_child_count
    
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
    bot.send_message(message.chat.id, f"🏠 *Управление семьей*\nДетей в базе: {child_count}/5", reply_markup=markup, parse_mode='Markdown')

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
    msg = bot.send_message(call.message.chat.id, "Отправьте ссылку на Google Таблицу ребенка:")
    bot.register_next_step_handler(msg, process_add_child_step, f_id)

def process_add_child_step(message, f_id):
    from src.database_manager import add_student, link_student_to_family, get_child_count
    from src.google_sheets import get_spreadsheet_title
    
    url = message.text.strip()
    if "docs.google.com/spreadsheets/d/" not in url:
        bot.reply_to(message, "❌ Некорректная ссылка на Google Таблицу.")
        return
        
    try:
        # Извлекаем ID из ссылки
        ss_id = url.split("/d/")[1].split("/")[0]
        
        if get_child_count(int(f_id)) >= 5:
            bot.reply_to(message, "⚠️ Достигнут лимит: максимум 5 детей на семью.")
            return
            
        # Пытаемся получить имя из таблицы
        title = get_spreadsheet_title(ss_id)
        if not title: title = "Новый ученик"
        
        s_id = add_student(title, ss_id)
        link_student_to_family(int(f_id), s_id)
        
        bot.send_message(
            message.chat.id, 
            f"✅ *Ребенок успешно добавлен!*\n\n"
            f"👤 Имя: {title}\n"
            f"📊 Статус: Мониторинг активирован\n\n"
            f"ℹ️ *Инструкция для получения уведомлений:*\n"
            f"1. Отправьте остальным членам семьи ссылку на этого бота.\n"
            f"2. Им нужно нажать /start и подтвердить свой номер телефона.\n"
            f"3. Как только в таблице появится новая оценка, все привязанные члены семьи получат мгновенное уведомление.\n\n"
            f"💡 Вы также можете в любой момент проверить текущие оценки командой /grades",
            parse_mode='Markdown'
        )
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка при добавлении: {e}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('add_member_'))
def callback_add_member(call):
    f_id = call.data.split('_')[2]
    msg = bot.send_message(call.message.chat.id, "Введите ФИО и номер телефона родственника (через пробел):\nПример: `Иванов Иван 998901234567`", parse_mode='Markdown')
    bot.register_next_step_handler(msg, process_add_member_step, f_id)

def process_add_member_step(message, f_id):
    from src.database_manager import add_parent, link_parent_to_family
    
    try:
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
        
        bot.reply_to(message, f"✅ Родственник {fio} добавлен в семью.")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {e}")
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
        
        # Получаем реальное имя из заголовка таблицы
        sheet_title = get_spreadsheet_title(spreadsheet_id)
        from src.utils import clean_student_name
        display_name = clean_student_name(sheet_title) if sheet_title else fio
        
        bot.send_message(user_id, f"🔄 Запрашиваю данные для: {display_name}...")
        
        # Range is hardcoded for now as in monitor_engine
        data = get_sheet_data(spreadsheet_id, "Сегодня!A1:B50")
        if not data:
            bot.send_message(user_id, f"⚠️ Не удалось получить данные для {display_name}. Проверьте доступ бота к таблице.")
            continue
            
        report = f"📊 *Оценки {display_name} за сегодня:*\n\n"
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
            
        report += f"\n\n[🔗 Открыть таблицу](https://docs.google.com/spreadsheets/d/{spreadsheet_id})"
        bot.send_message(user_id, report, parse_mode='Markdown', disable_web_page_preview=True)

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
