import sqlite3
import re
import logging
from telebot import types
from src.bot_instance import bot
from src.ui import send_menu_safe
from src.database_manager import DB_PATH, get_active_spreadsheets, get_parent_role, get_all_families, add_family, add_parent, link_parent_to_family, get_db_connection

logger = logging.getLogger(__name__)

def validate_phone(phone: str) -> bool:
    """Проверяет маску телефона (Узбекистан 998XXXXXXXXX)."""
    clean_phone = phone.replace("+", "").replace(" ", "").replace("-", "")
    return bool(re.match(r"^998\d{9}$", clean_phone))

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

@bot.message_handler(commands=['add_family'])
def cmd_add_family_start(message):
    """Супер-админ: Начало создания семьи."""
    user_id = message.from_user.id
    if get_parent_role(user_id) != 'admin':
        bot.send_message(message.chat.id, "⛔ У вас нет прав доступа к этой команде.")
        return
        
    msg = bot.send_message(message.chat.id, "🏗 *Создание новой семьи*\n\nВведите название семьи (фамилию):", parse_mode='Markdown')
    bot.register_next_step_handler(msg, process_family_name)

@bot.message_handler(commands=['list_families'])
def cmd_list_families(message):
    """Супер-админ: Показать список всех семей."""
    user_id = message.from_user.id
    if get_parent_role(user_id) != 'admin':
        bot.send_message(message.chat.id, "⛔ У вас нет прав доступа к этой команде.")
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
        bot.send_message(message.chat.id, "❌ Название не может быть пустым. Попробуйте снова /add_family")
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
        user_id = message.from_user.id
        
        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT id FROM parents WHERE telegram_id = ?', (user_id,))
                row = cursor.fetchone()
                
            if not row:
                bot.send_message(message.chat.id, "❌ Ваша учетная запись не найдена в базе. Сначала пройдите авторизацию.", reply_markup=types.ReplyKeyboardRemove())
                return
            
            parent_id = row['id']
            f_id = add_family(family_name)
            link_parent_to_family(f_id, parent_id)
            
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE parents SET role = 'head' WHERE id = ? AND role != 'admin'", (parent_id,))
                
            send_menu_safe(
                message.chat.id, 
                f"✅ <b>Семья '{family_name}' создана!</b>\n\nВы назначены главой. Теперь вы можете использовать 🏠 Моя семья для управления."
            )
        except Exception as e:
            send_menu_safe(message.chat.id, f"❌ Ошибка: {e}")
    else:
        send_menu_safe(message.chat.id, "Введите <b>ФИО Главы семьи</b>:")
        bot.register_next_step_handler_by_chat_id(message.chat.id, process_head_fio, family_name)

def process_head_fio(message, family_name):
    head_fio = message.text.strip()
    if len(head_fio) < 3:
        bot.send_message(message.chat.id, "❌ Слишком короткое ФИО. Попробуйте снова /add_family")
        return
        
    msg = bot.send_message(message.chat.id, f"👤 Глава: {head_fio}\n\nВведите *номер телефона* (998XXXXXXXXX):", parse_mode='Markdown')
    bot.register_next_step_handler(msg, process_head_phone, family_name, head_fio)

def process_head_phone(message, family_name, head_fio):
    head_phone = message.text.strip()
    
    if not validate_phone(head_phone):
        bot.send_message(message.chat.id, "❌ Неверный формат телефона. Должно быть 12 цифр (начиная с 998). Попробуйте снова /add_family")
        return
        
    try:
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
