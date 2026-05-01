import logging
from typing import Optional, Tuple
from telebot import types
from src.bot_instance import bot
from src.ui import send_menu_safe, send_content
from src.database_manager import (
    get_user_lang, set_user_state, get_user_state, clear_user_state,
)
from src.db.auth import can_manage_family
from src.i18n import t

logger = logging.getLogger(__name__)

# Имена state'ов для multi-step flow (в БД user_states.state)
STATE_AWAITING_CHILD_URL = "awaiting_child_url"
STATE_AWAITING_MEMBER_INFO = "awaiting_member_info"
STATE_AWAITING_MEMBER_FIO = "awaiting_member_fio"
STATE_AWAITING_MEMBER_PHONE = "awaiting_member_phone"


def _parse_int_args(call_data: str, prefix: str, count: int) -> Optional[Tuple[int, ...]]:
    """Безопасный парсинг callback_data вида 'prefix_arg1_arg2_...'.
    Возвращает кортеж int-ов или None если формат неверный."""
    if not call_data.startswith(prefix):
        return None
    rest = call_data[len(prefix):]
    parts = rest.split('_')
    if len(parts) != count:
        return None
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def _check_family_access(call: types.CallbackQuery, family_id: int) -> bool:
    """Проверяет, что пользователь имеет право управлять данной семьёй.
    При отсутствии прав отвечает alert и возвращает False."""
    if can_manage_family(call.from_user.id, family_id):
        return True
    lang = get_user_lang(call.from_user.id)
    bot.answer_callback_query(call.id, t("admin_no_access", lang), show_alert=True)
    logger.warning(
        f"Unauthorized callback access: user={call.from_user.id} "
        f"data={call.data} family_id={family_id}"
    )
    return False

@bot.message_handler(commands=['manage_family'])
def cmd_manage_family(message):
    from src.database_manager import is_head_of_any_family, get_families_for_head

    user_id = message.chat.id
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
    from src.database_manager import get_child_count, get_parent_role, is_subscription_active, get_family_subscription
    lang = get_user_lang(chat_id)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(t("family_invite_btn", lang), callback_data=f"gen_invite_{f_id}"))
    markup.add(types.InlineKeyboardButton(t("family_add_child_btn", lang), callback_data=f"add_child_{f_id}"))
    markup.add(types.InlineKeyboardButton(t("family_list_btn", lang), callback_data=f"list_edit_{f_id}"))

    role = get_parent_role(chat_id)
    if role == 'admin':
        # Кнопка подписки со статусом
        active = is_subscription_active(f_id)
        sub = get_family_subscription(f_id)
        if active and sub and sub.get('subscription_end'):
            sub_label = t("family_sub_btn_active", lang, end=sub['subscription_end'][:10])
        else:
            sub_label = t("family_sub_btn_inactive", lang)
        markup.add(types.InlineKeyboardButton(sub_label, callback_data=f"admin_sub_{f_id}"))

        markup.add(types.InlineKeyboardButton(t("family_delete_btn", lang), callback_data=f"delete_family_{f_id}"))
        markup.add(types.InlineKeyboardButton(t("family_back_btn", lang), callback_data="back_to_families"))
    else:
        # Для глав семей (не админов) — кнопка назад в меню
        markup.add(types.InlineKeyboardButton(t("user_panel_back", lang), callback_data="up_back"))

    child_count = get_child_count(f_id)
    text = t("family_manage_title", lang, count=child_count)
    if message_id_to_edit:
        bot.edit_message_text(text, chat_id, message_id_to_edit, reply_markup=markup, parse_mode='HTML')
    else:
        send_menu_safe(chat_id, text, inline_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('open_manage_'))
def callback_open_manage(call):
    args = _parse_int_args(call.data, 'open_manage_', 1)
    if not args:
        return
    f_id = args[0]
    if not _check_family_access(call, f_id):
        return
    _send_family_manage_menu(call.message.chat.id, f_id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('list_edit_'))
def callback_list_edit(call):
    from src.database_manager import get_family_members, get_family_students
    args = _parse_int_args(call.data, 'list_edit_', 1)
    if not args:
        return
    f_id = args[0]
    if not _check_family_access(call, f_id):
        return
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
    args = _parse_int_args(call.data, 'del_par_', 2)
    if not args:
        return
    f_id, p_id = args
    if not _check_family_access(call, f_id):
        return
    lang = get_user_lang(call.from_user.id)

    if delete_parent_from_family(f_id, p_id):
        bot.answer_callback_query(call.id, t("family_member_deleted", lang))
        callback_list_edit(call)
    else:
        bot.answer_callback_query(call.id, t("family_head_no_delete", lang), show_alert=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith('del_stud_'))
def callback_del_student(call):
    from src.database_manager import delete_student_from_family
    args = _parse_int_args(call.data, 'del_stud_', 2)
    if not args:
        return
    f_id, s_id = args
    if not _check_family_access(call, f_id):
        return
    lang = get_user_lang(call.from_user.id)

    delete_student_from_family(f_id, s_id)
    bot.answer_callback_query(call.id, t("family_child_deleted", lang))
    callback_list_edit(call)

@bot.callback_query_handler(func=lambda call: call.data.startswith('back_manage_'))
def callback_back_manage(call):
    args = _parse_int_args(call.data, 'back_manage_', 1)
    if not args:
        return
    f_id = args[0]
    if not _check_family_access(call, f_id):
        return
    _send_family_manage_menu(call.message.chat.id, f_id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('gen_invite_'))
def callback_gen_invite(call):
    args = _parse_int_args(call.data, 'gen_invite_', 1)
    if not args:
        return
    f_id = args[0]
    if not _check_family_access(call, f_id):
        return
    bot.answer_callback_query(call.id)
    from src.handlers.invite import generate_invite_link
    generate_invite_link(call.message.chat.id, f_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('add_child_'))
def callback_add_child(call):
    args = _parse_int_args(call.data, 'add_child_', 1)
    if not args:
        return
    f_id = args[0]
    if not _check_family_access(call, f_id):
        return
    lang = get_user_lang(call.from_user.id)

    # Сохраняем state в БД — переживёт рестарт бота посередине flow
    set_user_state(call.from_user.id, STATE_AWAITING_CHILD_URL, str(f_id))

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True,
                                        input_field_placeholder=t("child_url_placeholder", lang))
    markup.add(types.KeyboardButton(t("btn_cancel", lang)))

    bot.send_message(call.message.chat.id, t("child_enter_url", lang), reply_markup=markup)


@bot.message_handler(
    func=lambda msg: (get_user_state(msg.chat.id) or {}).get('state') == STATE_AWAITING_CHILD_URL,
    content_types=['text']
)
def receive_child_url(message):
    """Обрабатывает текст после нажатия 'Добавить ребёнка'. Persistent через user_states."""
    state = get_user_state(message.chat.id)
    try:
        f_id = int(state.get('data', '0'))
    except (TypeError, ValueError):
        clear_user_state(message.chat.id)
        return
    clear_user_state(message.chat.id)
    process_add_child_step(message, f_id)


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
    except Exception as e:
        logger.debug(f"Could not delete child URL message: {e}")

    if "docs.google.com/spreadsheets/d/" not in url:
        send_menu_safe(message.chat.id, t("child_url_invalid", lang))
        return

    try:
        if "/d/" in url:
            ss_id = url.split("/d/")[1].split("/")[0]
        else:
            send_menu_safe(message.chat.id, t("child_url_no_id", lang))
            return

        from src.config import MAX_CHILDREN_PER_FAMILY
        current_count = get_child_count(f_id)
        if current_count >= MAX_CHILDREN_PER_FAMILY:
            send_menu_safe(message.chat.id, t("child_limit_reached", lang, count=current_count))
            return

        # Чёткое сообщение если бот не имеет доступа к таблице (вместо
        # абстрактного "произошла ошибка"). Различаем PermissionError по
        # статусу 403/404 от googleapiclient.
        title = None
        try:
            title = get_spreadsheet_title(ss_id)
        except Exception as se:
            err_str = str(se)
            logger.error(f"Error calling Sheets API: {se}")
            if '403' in err_str or '404' in err_str or 'permission' in err_str.lower():
                send_menu_safe(message.chat.id, t("child_no_access_error", lang))
                return
            # Иначе — прочая ошибка, но таблица возможно есть. Пробуем дальше с заглушкой.

        if not title:
            title = t("default_student_name", lang)

        from src.utils import clean_student_name
        display_name = clean_student_name(title)

        s_id = add_student(title, ss_id, display_name=display_name)
        link_student_to_family(f_id, s_id)

        send_content(
            message.chat.id,
            t("child_added", lang, name=title, btn_grades=t("btn_grades", lang))
        )

        # Фоновый импорт исторических и четвертных оценок + бесплатный AI-анализ
        import threading
        def _bg_import():
            try:
                from src.history_importer import import_history_for_student, import_quarters_for_student
                result = import_history_for_student(s_id, ss_id)
                q_result = import_quarters_for_student(s_id, ss_id)
                total = result['imported'] + q_result['imported']
                if total > 0:
                    send_content(
                        message.chat.id,
                        t("history_imported", lang,
                          count=total, name=display_name)
                    )

                    # Бесплатный первый AI-анализ
                    import os
                    if os.environ.get("ANTHROPIC_API_KEY") and total >= 2:
                        try:
                            from src.analytics_engine import analyze_student_grades
                            analysis = analyze_student_grades(s_id, display_name, lang=lang)
                            if analysis:
                                msg = t("ai_first_free", lang) + "\n\n"
                                msg += t("ai_report_title", lang, name=display_name, analysis=analysis)
                                send_content(message.chat.id, msg)
                        except Exception as ai_err:
                            logger.error(f"Free AI analysis failed for {s_id}: {ai_err}")
            except Exception as e:
                logger.error(f"Background history import failed for student {s_id}: {e}")
        threading.Thread(target=_bg_import, daemon=True).start()
    except Exception as e:
        logger.exception("Unexpected error in process_add_child_step")
        send_content(message.chat.id, t("child_add_error", lang))

@bot.callback_query_handler(func=lambda call: call.data.startswith('add_member_'))
def callback_add_member(call):
    args = _parse_int_args(call.data, 'add_member_', 1)
    if not args:
        return
    f_id = args[0]
    if not _check_family_access(call, f_id):
        return
    lang = get_user_lang(call.from_user.id)

    # Двухшаговый flow: сначала ФИО, потом телефон. Хранилище — user_states (persistent).
    set_user_state(call.from_user.id, STATE_AWAITING_MEMBER_FIO, str(f_id))

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton(t("btn_cancel", lang)))

    bot.send_message(
        call.message.chat.id,
        t("member_step1_fio", lang),
        parse_mode='HTML',
        reply_markup=markup
    )


@bot.message_handler(
    func=lambda msg: (get_user_state(msg.chat.id) or {}).get('state') == STATE_AWAITING_MEMBER_FIO,
    content_types=['text']
)
def receive_member_fio(message):
    """Шаг 1: получили ФИО. Сохраняем и просим телефон."""
    import json
    lang = get_user_lang(message.chat.id)
    if not message.text or message.text == t("btn_cancel", lang):
        clear_user_state(message.chat.id)
        send_menu_safe(message.chat.id, t("family_cancelled", lang))
        return

    state = get_user_state(message.chat.id)
    try:
        f_id = int(state.get('data', '0'))
    except (TypeError, ValueError):
        clear_user_state(message.chat.id)
        return

    fio = message.text.strip()
    if len(fio) < 3:
        bot.send_message(message.chat.id, t("family_fio_too_short", lang))
        return

    # Сохраняем {family_id, fio} JSON в data
    payload = json.dumps({'f_id': f_id, 'fio': fio})
    set_user_state(message.chat.id, STATE_AWAITING_MEMBER_PHONE, payload)

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True,
                                        input_field_placeholder=t("member_phone_placeholder", lang))
    markup.add(types.KeyboardButton(t("btn_cancel", lang)))
    bot.send_message(
        message.chat.id,
        t("member_step2_phone", lang, fio=fio),
        parse_mode='HTML',
        reply_markup=markup
    )


@bot.message_handler(
    func=lambda msg: (get_user_state(msg.chat.id) or {}).get('state') == STATE_AWAITING_MEMBER_PHONE,
    content_types=['text']
)
def receive_member_phone(message):
    """Шаг 2: получили телефон. Валидируем формат, создаём связку."""
    import json
    import re
    from src.database_manager import (
        add_parent, link_parent_to_family, get_parent_by_phone,
    )
    lang = get_user_lang(message.chat.id)

    if not message.text or message.text == t("btn_cancel", lang):
        clear_user_state(message.chat.id)
        send_menu_safe(message.chat.id, t("family_cancelled", lang))
        return

    state = get_user_state(message.chat.id)
    try:
        data = json.loads(state.get('data') or '{}')
        f_id = int(data['f_id'])
        fio = data['fio']
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        clear_user_state(message.chat.id)
        return

    # Нормализация и валидация телефона. Узбекские номера 998XXXXXXXXX.
    phone_raw = message.text.strip()
    phone = re.sub(r'[^\d]', '', phone_raw)
    if not re.match(r'^998\d{9}$', phone):
        # Не очищаем state — даём шанс ввести ещё раз
        bot.send_message(message.chat.id, t("member_phone_invalid", lang), parse_mode='HTML')
        return

    clear_user_state(message.chat.id)

    try:
        existing = get_parent_by_phone(phone)
        if existing:
            # Не плодим дубликаты — линкуем существующий parent к семье
            link_parent_to_family(f_id, existing['id'])
            send_content(message.chat.id, t("member_already_exists", lang))
        else:
            p_id = add_parent(fio, phone, role='senior')
            link_parent_to_family(f_id, p_id)
            send_content(message.chat.id, t("member_added", lang, name=fio))
    except Exception as e:
        logger.error(f"Error adding family member: {e}", exc_info=True)
        send_content(message.chat.id, t("member_add_error", lang))

@bot.message_handler(commands=['grades'])
def get_grades_command(message):
    # ВАЖНО: используем from_user.id, а не chat.id, иначе в группе command
    # выполняется от имени chat_id группы (отрицательный id), и students не находятся.
    user_id = message.from_user.id if getattr(message, 'from_user', None) else message.chat.id
    chat_id = message.chat.id
    lang = get_user_lang(user_id)
    from src.database_manager import get_students_for_parent

    students = get_students_for_parent(user_id)
    if not students:
        # Отвечаем туда, где была команда (в группе — в группу, в личке — в личку),
        # но искали по from_user.id (правильный источник истины).
        bot.send_message(chat_id, t("grades_no_students", lang))
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
        send_menu_safe(chat_id, t("grades_select_child", lang), inline_markup=markup)
    else:
        _show_student_grades(chat_id, students[0])
        _show_webapp_button(chat_id)


def _show_student_grades(chat_id: int, student: dict):
    from src.database_manager import get_today_grades_for_student
    from src.utils import clean_student_name
    lang = get_user_lang(chat_id)

    fio = student['fio']
    spreadsheet_id = student['spreadsheet_id']
    display_name = student.get('display_name') or clean_student_name(fio)

    grades = get_today_grades_for_student(student['id'])

    report_lines = [t("grades_title", lang, name=display_name)]
    grades_found = False

    for g in grades:
        if g['raw_text']:
            report_lines.append(f"🔹 {g['subject']}: <b>{g['raw_text']}</b>")
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
    except Exception as e:
        logger.debug(f"Could not remove grade_seen reply markup: {e}")


@bot.callback_query_handler(func=lambda call: call.data.startswith('grade_today_'))
def callback_grade_today(call):
    from src.database_manager import get_students_for_parent, get_today_grades_for_student
    lang = get_user_lang(call.from_user.id)
    chat_id = call.message.chat.id

    student_id = int(call.data.replace('grade_today_', ''))
    bot.answer_callback_query(call.id)

    students = get_students_for_parent(call.from_user.id)
    student = next((s for s in students if s['id'] == student_id), None)

    if not student:
        bot.send_message(chat_id, t("grades_student_not_found", lang))
        return

    grades = get_today_grades_for_student(student_id)
    if not grades:
        send_content(chat_id, t("grades_today_none", lang))
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

    # Показываем оценки с кнопкой открытия таблицы
    spreadsheet_id = student.get('spreadsheet_id', '')
    if spreadsheet_id:
        lines.append(f"\n<a href='https://docs.google.com/spreadsheets/d/{spreadsheet_id}'>{t('grades_open_sheet', lang)}</a>")

    send_content(chat_id, "\n".join(lines))
