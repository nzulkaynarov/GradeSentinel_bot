import sqlite3
import re
import logging
from telebot import types
from src.bot_instance import bot
from src.ui import send_menu_safe, send_content
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
    send_content(message.chat.id, help_text)

@bot.message_handler(commands=['status'])
def system_status(message):
    user_id = message.chat.id
    
    if is_user_admin(user_id):
        from src.database_manager import get_global_stats
        stats = get_global_stats()
        status_text = (
            "🛰 <b>Глобальный статус системы</b>\n\n"
            f"👥 Всего семей: <b>{stats.get('families', 0)}</b>\n"
            f"👨‍👩‍👧‍👦 Зарегистрировано родителей: <b>{stats.get('parents', 0)}</b>\n"
            f"🎓 Активных учеников: <b>{stats.get('students', 0)}</b>\n"
            f"🔄 Обработано записей (история): <b>{stats.get('history_records', 0)}</b>\n\n"
            "⚙️ Мониторинг: <b>Работает</b>"
        )
    else:
        from src.database_manager import get_user_stats, is_head_of_any_family, has_children_for_grades
        if not is_head_of_any_family(user_id) and not has_children_for_grades(user_id):
            return
            
        stats = get_user_stats(user_id)
        status_text = (
            "📊 <b>Ваша статистика</b>\n\n"
            f"🏠 Состоите в семьях: <b>{stats.get('families', 0)}</b>\n"
            f"🎓 Привязано детей: <b>{stats.get('students', 0)}</b>\n"
            f"🔄 Уведомлений в истории: <b>{stats.get('history_records', 0)}</b>\n\n"
            "💡 <i>Бот активно проверяет дневники ваших детей каждые 5 минут.</i>"
        )
        
    send_content(user_id, status_text)

@bot.message_handler(commands=['add_family'])
def cmd_add_family_start(message):
    """Супер-админ: Начало создания семьи."""
    user_id = message.from_user.id
    if get_parent_role(user_id) != 'admin':
        bot.send_message(message.chat.id, "⛔ У вас нет прав доступа к этой команде.")
        return
        
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton("❌ Отмена"))
    
    msg = bot.send_message(
        message.chat.id, 
        "🏗 *Создание новой семьи*\n\nВведите название семьи (фамилию):", 
        parse_mode='Markdown',
        reply_markup=markup
    )
    bot.register_next_step_handler(msg, process_family_name)

@bot.message_handler(commands=['list_families'])
def cmd_list_families(message, user_id=None):
    """Супер-админ: Показать список всех семей с кнопками управления."""
    target_user_id = user_id if user_id else message.from_user.id
    if get_parent_role(target_user_id) != 'admin':
        bot.send_message(message.chat.id, "⛔ У вас нет прав доступа к этой команде.")
        return
        
    families = get_all_families()
    if not families:
        send_menu_safe(message.chat.id, "📭 В базе пока нет ни одной семьи.")
        return
        
    markup = types.InlineKeyboardMarkup()
    for f in families:
        head = f['head_fio'] if f['head_fio'] else "Не назначен"
        btn_text = f"🏠 {f['family_name']} ({head} - {f['child_count']}/5)"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"admin_manage_{f['id']}"))
        
    send_menu_safe(message.chat.id, "📋 <b>Список всех семей:</b>\nВыберите семью для управления:", inline_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_manage_'))
def callback_admin_manage(call):
    """Меню управления конкретной семьей для админа."""
    from src.handlers.family import _send_family_manage_menu
    f_id = int(call.data.split('_')[2])
    _send_family_manage_menu(call.message.chat.id, f_id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data == 'back_to_families')
def callback_back_to_families(call):
    cmd_list_families(call.message, user_id=call.from_user.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_family_'))
def callback_delete_family(call):
    f_id = int(call.data.split('_')[2])
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("⚠️ ДА, УДАЛИТЬ СЕМЬЮ", callback_data=f"confirm_delete_family_{f_id}"))
    markup.add(types.InlineKeyboardButton("Отмена", callback_data=f"admin_manage_{f_id}"))
    bot.edit_message_text("Вы уверены? Это удалит всех детей и родственников из этой семьи.", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_delete_family_'))
def callback_confirm_delete_family(call):
    f_id = int(call.data.split('_')[3])
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM family_links WHERE family_id = ?", (f_id,))
        cursor.execute("DELETE FROM families WHERE id = ?", (f_id,))
        conn.commit()
    
    bot.answer_callback_query(call.id, "✅ Семья полностью удалена")
    cmd_list_families(call.message, user_id=call.from_user.id)

def process_family_name(message):
    family_name = message.text.strip()
    if family_name == "❌ Отмена":
        send_menu_safe(message.chat.id, "Действие отменено.")
        return
        
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
            
            from src.database_manager import set_family_head
            set_family_head(f_id, parent_id)
                
            send_content(
                message.chat.id, 
                f"✅ <b>Семья '{family_name}' создана!</b>\n\nВы назначены главой. Теперь вы можете использовать 🏠 Моя семья для управления."
            )
        except Exception as e:
            send_content(message.chat.id, f"❌ Ошибка: {e}")
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
        p_id = add_parent(head_fio, head_phone, role='senior')
        link_parent_to_family(f_id, p_id)
        
        from src.database_manager import set_family_head
        set_family_head(f_id, p_id)
        
        send_content(
            message.chat.id, 
            f"✅ <b>Семья успешно создана!</b>\n\n"
            f"🏘 Семья: {family_name}\n"
            f"👑 Глава: {head_fio}\n"
            f"📞 Телефон: {head_phone}\n\n"
            "Статус: Активен"
        )
    except Exception as e:
        send_content(message.chat.id, f"❌ Ошибка в базе данных: {e}")
