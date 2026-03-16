import logging
from telebot import types
from src.bot_instance import bot
from src.ui import send_menu_safe, send_content
from src.database_manager import get_user_lang
from src.i18n import t

logger = logging.getLogger(__name__)

@bot.message_handler(commands=['manage_family'])
def cmd_manage_family(message):
    from src.database_manager import is_head_of_any_family, get_families_for_head

    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    if not is_head_of_any_family(user_id):
        bot.send_message(message.chat.id, t("family_head_only", lang))
        return

    families = get_families_for_head(user_id)
    if not families:
        bot.send_message(message.chat.id, t("family_not_found", lang))
        return

    if len(families) > 1:
        markup = types.InlineKeyboardMarkup()
        for f in families:
            markup.add(types.InlineKeyboardButton(f["family_name"], callback_data=f"open_manage_{f['id']}"))
        send_menu_safe(message.chat.id, t("family_select", lang), inline_markup=markup)
        return

    _send_family_manage_menu(message.chat.id, families[0]['id'])

def _send_family_manage_menu(chat_id, f_id, message_id_to_edit=None):
    from src.database_manager import get_child_count, get_parent_role
    lang = get_user_lang(chat_id)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(t("family_add_member_btn", lang), callback_data=f"add_member_{f_id}"))
    markup.add(types.InlineKeyboardButton(t("family_add_child_btn", lang), callback_data=f"add_child_{f_id}"))
    markup.add(types.InlineKeyboardButton(t("family_list_btn", lang), callback_data=f"list_edit_{f_id}"))

    role = get_parent_role(chat_id)
    if role == 'admin':
        markup.add(types.InlineKeyboardButton(t("family_delete_btn", lang), callback_data=f"delete_family_{f_id}"))
        markup.add(types.InlineKeyboardButton(t("family_back_btn", lang), callback_data="back_to_families"))

    child_count = get_child_count(f_id)
    text = t("family_manage_title", lang, count=child_count)
    if message_id_to_edit:
        bot.edit_message_text(text, chat_id, message_id_to_edit, reply_markup=markup, parse_mode='HTML')
    else:
        send_menu_safe(chat_id, text, inline_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('open_manage_'))
def callback_open_manage(call):
    f_id = int(call.data.split('_')[2])
    _send_family_manage_menu(call.message.chat.id, f_id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('list_edit_'))
def callback_list_edit(call):
    from src.database_manager import get_family_members, get_family_students
    f_id = int(call.data.split('_')[2])
    lang = get_user_lang(call.from_user.id)

    members = get_family_members(f_id)
    students = get_family_students(f_id)

    markup = types.InlineKeyboardMarkup()

    if students:
        markup.add(types.InlineKeyboardButton(t("family_children_header", lang), callback_data="none"))
        for s in students:
            markup.add(types.InlineKeyboardButton(f"❌ {s['fio']}", callback_data=f"del_stud_{f_id}_{s['id']}"))

    if members:
        markup.add(types.InlineKeyboardButton(t("family_members_header", lang), callback_data="none"))
        for m in members:
            role_label = t("family_role_head", lang) if m.get('is_head') else t("family_role_member", lang)
            label = f"{m['fio']} ({role_label})"
            if not m.get('is_head'):
                markup.add(types.InlineKeyboardButton(f"❌ {label}", callback_data=f"del_par_{f_id}_{m['id']}"))
            else:
                markup.add(types.InlineKeyboardButton(f"👑 {label}", callback_data="none"))

    markup.add(types.InlineKeyboardButton(t("family_back", lang), callback_data=f"back_manage_{f_id}"))
    bot.edit_message_text(t("family_delete_prompt", lang), call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode='Markdown')

@bot.callback_query_handler(func=lambda call: call.data.startswith('del_par_'))
def callback_del_parent(call):
    from src.database_manager import delete_parent_from_family
    lang = get_user_lang(call.from_user.id)
    parts = call.data.split('_')
    f_id, p_id = int(parts[2]), int(parts[3])

    if delete_parent_from_family(f_id, p_id):
        bot.answer_callback_query(call.id, t("family_member_deleted", lang))
        callback_list_edit(call)
    else:
        bot.answer_callback_query(call.id, t("family_head_no_delete", lang), show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('del_stud_'))
def callback_del_student(call):
    from src.database_manager import delete_student_from_family
    lang = get_user_lang(call.from_user.id)
    parts = call.data.split('_')
    f_id, s_id = int(parts[2]), int(parts[3])

    delete_student_from_family(f_id, s_id)
    bot.answer_callback_query(call.id, t("family_child_deleted", lang))
    callback_list_edit(call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('back_manage_'))
def callback_back_manage(call):
    f_id = int(call.data.split('_')[2])
    _send_family_manage_menu(call.message.chat.id, f_id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('add_child_'))
def callback_add_child(call):
    f_id = call.data.split('_')[2]
    lang = get_user_lang(call.from_user.id)

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True,
                                        input_field_placeholder=t("child_url_placeholder", lang))
    markup.add(types.KeyboardButton(t("btn_cancel", lang)))

    msg = bot.send_message(call.message.chat.id, t("child_enter_url", lang), reply_markup=markup)
    bot.register_next_step_handler(msg, process_add_child_step, f_id)

def process_add_child_step(message, f_id):
    from src.database_manager import add_student, link_student_to_family, get_child_count
    from src.google_sheets import get_spreadsheet_title
    lang = get_user_lang(message.chat.id)

    if not message.text or message.text == t("btn_cancel", lang):
        send_menu_safe(message.chat.id, t("family_cancelled", lang))
        return

    url = message.text.strip()

    try:
        bot.delete_message(message.chat.id, message.message_id)
    except Exception:
        pass

    if "docs.google.com/spreadsheets/d/" not in url:
        send_menu_safe(message.chat.id, t("child_url_invalid", lang))
        return

    try:
        if "/d/" in url:
            ss_id = url.split("/d/")[1].split("/")[0]
        else:
            send_menu_safe(message.chat.id, t("child_url_no_id", lang))
            return

        current_count = get_child_count(int(f_id))
        if current_count >= 5:
            send_menu_safe(message.chat.id, t("child_limit_reached", lang, count=current_count))
            return

        try:
            title = get_spreadsheet_title(ss_id)
        except Exception as se:
            logger.error(f"Error calling Sheets API: {se}")
            title = None

        if not title:
            title = "Новый ученик"

        from src.utils import clean_student_name
        display_name = clean_student_name(title)

        s_id = add_student(title, ss_id, display_name=display_name)
        link_student_to_family(int(f_id), s_id)

        send_content(
            message.chat.id,
            t("child_added", lang, name=title, btn_grades=t("btn_grades", lang))
        )
    except Exception as e:
        logger.exception("Unexpected error in process_add_child_step")
        send_content(message.chat.id, t("child_add_error", lang))

@bot.callback_query_handler(func=lambda call: call.data.startswith('add_member_'))
def callback_add_member(call):
    f_id = call.data.split('_')[2]
    lang = get_user_lang(call.from_user.id)

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True,
                                        input_field_placeholder=t("member_enter_placeholder", lang))
    markup.add(types.KeyboardButton(t("btn_cancel", lang)))

    msg = bot.send_message(
        call.message.chat.id,
        t("member_enter", lang),
        parse_mode='HTML',
        reply_markup=markup
    )
    bot.register_next_step_handler(msg, process_add_member_step, f_id)

def process_add_member_step(message, f_id):
    from src.database_manager import add_parent, link_parent_to_family
    lang = get_user_lang(message.chat.id)

    if not message.text or message.text == t("btn_cancel", lang):
        send_menu_safe(message.chat.id, t("family_cancelled", lang))
        return

    try:
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass

        parts = message.text.split(maxsplit=2)
        if len(parts) < 3:
            fio = parts[0]
            phone = parts[1]
        else:
            fio = parts[0] + " " + parts[1]
            phone = parts[2]

        p_id = add_parent(fio, phone, role='senior')
        link_parent_to_family(int(f_id), p_id)

        send_content(message.chat.id, t("member_added", lang, name=fio))
    except Exception as e:
        logger.error(f"Error adding family member: {e}")
        send_content(message.chat.id, t("member_add_error", lang))

@bot.message_handler(commands=['grades'])
def get_grades_command(message):
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    from src.database_manager import get_students_for_parent

    students = get_students_for_parent(user_id)
    if not students:
        bot.send_message(user_id, t("grades_no_students", lang))
        return

    if len(students) > 1:
        markup = types.InlineKeyboardMarkup()
        for s in students:
            from src.utils import clean_student_name
            display = s.get('display_name') or clean_student_name(s['fio'])
            markup.add(types.InlineKeyboardButton(
                f"👤 {display}", callback_data=f"show_grades_{s['id']}"
            ))
        markup.add(types.InlineKeyboardButton(
            t("btn_all_children", lang), callback_data="show_grades_all"
        ))
        send_menu_safe(user_id, t("grades_select_child", lang), inline_markup=markup)
    else:
        _show_student_grades(user_id, students[0])
        _show_webapp_button(user_id)


def _show_student_grades(chat_id: int, student: dict):
    from src.google_sheets import get_sheet_data, get_spreadsheet_title
    from src.data_cleaner import sanitize_grade
    from src.utils import clean_student_name
    lang = get_user_lang(chat_id)

    fio = student['fio']
    spreadsheet_id = student['spreadsheet_id']

    sheet_title = get_spreadsheet_title(spreadsheet_id)
    display_name = clean_student_name(sheet_title) if sheet_title else fio

    data = get_sheet_data(spreadsheet_id, "Сегодня!A1:B50")
    if not data:
        send_content(chat_id, t("grades_fetch_error", lang, name=display_name))
        return

    report_lines = [t("grades_title", lang, name=display_name)]
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
        report_lines.append(t("grades_no_today", lang))

    report_lines.append(f"\n<a href='https://docs.google.com/spreadsheets/d/{spreadsheet_id}'>{t('grades_open_sheet', lang)}</a>")
    send_content(chat_id, "\n".join(report_lines))


def _show_webapp_button(chat_id: int):
    from src.ui import get_webapp_button
    lang = get_user_lang(chat_id)
    webapp_markup = get_webapp_button(lang)
    if webapp_markup:
        bot.send_message(chat_id, t("grades_webapp_hint", lang), reply_markup=webapp_markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith('show_grades_'))
def callback_show_grades(call):
    from src.database_manager import get_students_for_parent
    lang = get_user_lang(call.from_user.id)
    chat_id = call.message.chat.id
    data_part = call.data.replace('show_grades_', '')

    bot.answer_callback_query(call.id)

    if data_part == 'all':
        students = get_students_for_parent(call.from_user.id)
        for s in students:
            _show_student_grades(chat_id, s)
    else:
        student_id = int(data_part)
        students = get_students_for_parent(call.from_user.id)
        student = next((s for s in students if s['id'] == student_id), None)
        if student:
            _show_student_grades(chat_id, student)
        else:
            bot.send_message(chat_id, t("grades_student_not_found", lang))

    _show_webapp_button(chat_id)


@bot.callback_query_handler(func=lambda call: call.data.startswith('grade_seen_'))
def callback_grade_seen(call):
    lang = get_user_lang(call.from_user.id)
    bot.answer_callback_query(call.id, t("btn_seen", lang) + "!")
    try:
        bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data.startswith('grade_today_'))
def callback_grade_today(call):
    from src.database_manager import get_students_for_parent, get_today_grades_for_student
    lang = get_user_lang(call.from_user.id)

    student_id = int(call.data.replace('grade_today_', ''))
    bot.answer_callback_query(call.id)

    students = get_students_for_parent(call.from_user.id)
    student = next((s for s in students if s['id'] == student_id), None)

    if not student:
        bot.send_message(call.message.chat.id, t("grades_student_not_found", lang))
        return

    grades = get_today_grades_for_student(student_id)
    if not grades:
        send_content(call.message.chat.id, t("grades_today_none", lang))
        return

    from src.utils import clean_student_name
    display_name = student.get('display_name') or clean_student_name(student['fio'])
    lines = [t("grades_today_title", lang, name=display_name)]
    numeric = []

    for g in grades:
        lines.append(f"🔹 {g['subject']}: <b>{g['raw_text']}</b>")
        if g['grade_value'] is not None:
            numeric.append(g['grade_value'])

    if numeric:
        avg = sum(numeric) / len(numeric)
        lines.append(f"\n{t('grades_avg', lang, avg=f'{avg:.1f}', count=len(numeric))}")

    send_content(call.message.chat.id, "\n".join(lines))
