import logging
from typing import List
from telebot import types
from src.bot_instance import bot
from src.ui import send_menu_safe, send_content

logger = logging.getLogger(__name__)

@bot.message_handler(commands=['manage_family'])
def cmd_manage_family(message):
    """Глава семьи: Меню управления членами и детьми."""
    from src.database_manager import get_family_by_head, get_child_count, get_parent_role
    
    user_id = message.from_user.id
    role = get_parent_role(user_id)
    if role != 'head' and role != 'admin':
        bot.send_message(message.chat.id, "⛔ Эта команда доступна только главам семей.")
        return
        
    f_id = get_family_by_head(user_id)
    if not f_id and role != 'admin':
        bot.send_message(message.chat.id, "❌ Семья не найдена.")
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
    
    if students:
        markup.add(types.InlineKeyboardButton("─── ДЕТИ ───", callback_data="none"))
        for s in students:
            markup.add(types.InlineKeyboardButton(f"❌ {s['fio']}", callback_data=f"del_stud_{f_id}_{s['id']}"))
            
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
    logger.info(f"Callback add_child triggered for family_id: {f_id}")
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton("❌ Отмена"))
    
    msg = bot.send_message(
        call.message.chat.id, 
        "Отправьте ссылку на Google Таблицу ребенка.\nИли нажмите 'Отмена':",
        reply_markup=markup
    )
    bot.register_next_step_handler(msg, process_add_child_step, f_id)

def process_add_child_step(message, f_id):
    from src.database_manager import add_student, link_student_to_family, get_child_count
    from src.google_sheets import get_spreadsheet_title
    
    if not message.text or message.text == "❌ Отмена":
        send_menu_safe(message.chat.id, "Действие отменено.")
        return

    url = message.text.strip()
    logger.info(f"Processing add_child for family {f_id}, url: {url}")
    
    try: bot.delete_message(message.chat.id, message.message_id)
    except Exception as de: logger.warning(f"Failed to delete user message: {de}")

    if "docs.google.com/spreadsheets/d/" not in url:
        send_menu_safe(message.chat.id, "❌ Некорректная ссылка на Google Таблицу.\nУбедитесь, что она содержит <code>/spreadsheets/d/</code>")
        return
        
    try:
        if "/d/" in url:
            ss_id = url.split("/d/")[1].split("/")[0]
        else:
            send_menu_safe(message.chat.id, "❌ Не удалось извлечь ID таблицы из ссылки.")
            return
            
        logger.info(f"Extracted spreadsheet_id: {ss_id}")
        
        current_count = get_child_count(int(f_id))
        if current_count >= 5:
            send_menu_safe(message.chat.id, f"⚠️ Достигнут лимит: в семье уже {current_count} детей (максимум 5).")
            return
            
        try:
            title = get_spreadsheet_title(ss_id)
            logger.info(f"Sheets API title: {title}")
        except Exception as se:
            logger.error(f"Error calling Sheets API: {se}")
            title = None

        if not title: 
            title = "Новый ученик"
        
        s_id = add_student(title, ss_id)
        logger.info(f"Student added to DB with id {s_id}")
        
        link_student_to_family(int(f_id), s_id)
        logger.info(f"Student {s_id} linked to family {f_id}")
        
        send_content(
            message.chat.id, 
            f"✅ <b>Ребенок успешно добавлен!</b>\n\n"
            f"👤 Имя: {title}\n"
            f"📊 Статус: Мониторинг активирован\n\n"
            f"💡 Вы также можете в любой момент проверить текущие оценки кнопкой '📈 Оценки'"
        )
    except Exception as e:
        logger.exception("Unexpected error in process_add_child_step")
        send_content(message.chat.id, f"❌ Произошла ошибка: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data.startswith('add_member_'))
def callback_add_member(call):
    f_id = call.data.split('_')[2]
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton("❌ Отмена"))
    
    msg = bot.send_message(
        call.message.chat.id, 
        "Введите ФИО и номер телефона родственника (через пробел):\n"
        "Пример: <code>Иванов Иван 998901234567</code>\n\n"
        "Или нажмите 'Отмена'.",
        parse_mode='HTML',
        reply_markup=markup
    )
    bot.register_next_step_handler(msg, process_add_member_step, f_id)

def process_add_member_step(message, f_id):
    from src.database_manager import add_parent, link_parent_to_family
    
    if not message.text or message.text == "❌ Отмена":
        send_menu_safe(message.chat.id, "Действие отменено.")
        return
    
    try:
        try: bot.delete_message(message.chat.id, message.message_id)
        except: pass

        parts = message.text.split(maxsplit=2)
        if len(parts) < 3:
            fio = parts[0]
            phone = parts[1]
        else:
            fio = parts[0] + " " + parts[1]
            phone = parts[2]
            
        p_id = add_parent(fio, phone, role='senior')
        link_parent_to_family(int(f_id), p_id)
        
        send_content(message.chat.id, f"✅ Родственник <b>{fio}</b> добавлен в семью.")
    except Exception as e:
        send_content(message.chat.id, f"❌ Ошибка: {e}")

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
            
        report_lines = [f"📊 <b>Оценки {display_name} за сегодня:</b>\n"]
        grades_found = False
        
        for row in data[1:]:
            if not isinstance(row, list) or len(row) < 2: continue
            subject = str(row[0]).strip()
            raw_grade = str(row[1]).strip()
            if not raw_grade or not subject: continue
            
            _, clean_text = sanitize_grade(raw_grade)
            if clean_text:
                report_lines.append(f"🔹 {subject}: <b>{clean_text}</b>")
                grades_found = True
        
        if not grades_found:
            report_lines.append("За сегодня записей/оценок пока нет.")
            
        report_lines.append(f"\n<a href='https://docs.google.com/spreadsheets/d/{spreadsheet_id}'>🔗 Открыть таблицу</a>")
        send_content(user_id, "\n".join(report_lines))
