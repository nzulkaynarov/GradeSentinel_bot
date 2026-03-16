import re
import logging
from telebot import types
from src.bot_instance import bot
from src.ui import send_menu_safe, send_content
from src.database_manager import get_parent_role, get_all_families, add_family, add_parent, link_parent_to_family, get_db_connection, get_user_lang
from src.i18n import t

logger = logging.getLogger(__name__)

def validate_phone(phone: str) -> bool:
    """Проверяет маску телефона (Узбекистан 998XXXXXXXXX)."""
    clean_phone = phone.replace("+", "").replace(" ", "").replace("-", "")
    return bool(re.match(r"^998\d{9}$", clean_phone))

def is_user_admin(user_id):
    return get_parent_role(user_id) == 'admin'

@bot.message_handler(commands=['admin_help'])
def admin_help(message):
    if not is_user_admin(message.chat.id):
        return
    lang = get_user_lang(message.chat.id)
    send_content(message.chat.id, t("admin_help", lang))

@bot.message_handler(commands=['status'])
def system_status(message):
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    if is_user_admin(user_id):
        from src.database_manager import get_global_stats
        stats = get_global_stats()
        send_content(user_id, t("status_global", lang, **stats))
    else:
        from src.database_manager import get_user_stats, is_head_of_any_family, has_children_for_grades
        if not is_head_of_any_family(user_id) and not has_children_for_grades(user_id):
            return
        stats = get_user_stats(user_id)
        send_content(user_id, t("status_user", lang, **stats))

@bot.message_handler(commands=['add_family'])
def cmd_add_family_start(message):
    user_id = message.from_user.id
    lang = get_user_lang(user_id)
    if get_parent_role(user_id) != 'admin':
        bot.send_message(message.chat.id, t("admin_no_access", lang))
        return

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(types.KeyboardButton(t("btn_cancel", lang)))

    msg = bot.send_message(
        message.chat.id,
        t("family_create_title", lang),
        parse_mode='Markdown',
        reply_markup=markup
    )
    bot.register_next_step_handler(msg, process_family_name)

@bot.message_handler(commands=['list_families'])
def cmd_list_families(message, user_id=None):
    target_user_id = user_id if user_id else message.from_user.id
    lang = get_user_lang(target_user_id)
    if get_parent_role(target_user_id) != 'admin':
        bot.send_message(message.chat.id, t("admin_no_access", lang))
        return

    families = get_all_families()
    if not families:
        send_menu_safe(message.chat.id, t("admin_no_families", lang))
        return

    markup = types.InlineKeyboardMarkup()
    for f in families:
        head = f['head_fio'] if f['head_fio'] else t("admin_head_not_set", lang)
        btn_text = f"🏠 {f['family_name']} ({head} - {f['child_count']}/5)"
        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"admin_manage_{f['id']}"))

    send_menu_safe(message.chat.id, t("admin_families_list", lang), inline_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_manage_'))
def callback_admin_manage(call):
    from src.handlers.family import _send_family_manage_menu
    f_id = int(call.data.split('_')[2])
    _send_family_manage_menu(call.message.chat.id, f_id, call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data == 'back_to_families')
def callback_back_to_families(call):
    cmd_list_families(call.message, user_id=call.from_user.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('delete_family_'))
def callback_delete_family(call):
    f_id = int(call.data.split('_')[2])
    lang = get_user_lang(call.from_user.id)
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(t("admin_delete_confirm_btn", lang), callback_data=f"confirm_delete_family_{f_id}"))
    markup.add(types.InlineKeyboardButton(t("btn_cancel", lang), callback_data=f"admin_manage_{f_id}"))
    bot.edit_message_text(t("admin_confirm_delete", lang), call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('confirm_delete_family_'))
def callback_confirm_delete_family(call):
    f_id = int(call.data.split('_')[3])
    lang = get_user_lang(call.from_user.id)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM family_links WHERE family_id = ?", (f_id,))
        cursor.execute("DELETE FROM families WHERE id = ?", (f_id,))
        conn.commit()

    bot.answer_callback_query(call.id, t("admin_deleted", lang))
    cmd_list_families(call.message, user_id=call.from_user.id)

def process_family_name(message):
    family_name = message.text.strip()
    lang = get_user_lang(message.chat.id)
    if family_name == t("btn_cancel", lang):
        send_menu_safe(message.chat.id, t("family_cancelled", lang))
        return

    if not family_name:
        bot.send_message(message.chat.id, t("family_name_empty", lang))
        return

    markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True)
    markup.add(types.KeyboardButton(t("btn_make_me_head", lang)))
    markup.add(types.KeyboardButton(t("btn_assign_other", lang)))

    send_menu_safe(
        message.chat.id,
        t("family_choose_head", lang, name=family_name),
        reply_markup=markup
    )
    bot.register_next_step_handler_by_chat_id(message.chat.id, process_head_choice, family_name)

def process_head_choice(message, family_name):
    lang = get_user_lang(message.chat.id)
    if message.text == t("btn_make_me_head", lang):
        user_id = message.from_user.id

        try:
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT id FROM parents WHERE telegram_id = ?', (user_id,))
                row = cursor.fetchone()

            if not row:
                bot.send_message(message.chat.id, t("family_account_not_found", lang), reply_markup=types.ReplyKeyboardRemove())
                return

            parent_id = row['id']
            f_id = add_family(family_name)
            link_parent_to_family(f_id, parent_id)

            from src.database_manager import set_family_head
            set_family_head(f_id, parent_id)

            send_content(message.chat.id, t("family_created_self", lang, name=family_name))
        except Exception as e:
            logger.error(f"Error creating family with self as head: {e}")
            send_content(message.chat.id, t("family_error", lang))
    else:
        send_menu_safe(message.chat.id, t("family_enter_head_fio", lang))
        bot.register_next_step_handler_by_chat_id(message.chat.id, process_head_fio, family_name)

def process_head_fio(message, family_name):
    head_fio = message.text.strip()
    lang = get_user_lang(message.chat.id)
    if len(head_fio) < 3:
        bot.send_message(message.chat.id, t("family_fio_too_short", lang))
        return

    msg = bot.send_message(message.chat.id, t("family_enter_phone", lang, fio=head_fio), parse_mode='Markdown')
    bot.register_next_step_handler(msg, process_head_phone, family_name, head_fio)

def process_head_phone(message, family_name, head_fio):
    head_phone = message.text.strip()
    lang = get_user_lang(message.chat.id)

    if not validate_phone(head_phone):
        bot.send_message(message.chat.id, t("family_phone_invalid", lang))
        return

    try:
        f_id = add_family(family_name)
        p_id = add_parent(head_fio, head_phone, role='senior')
        link_parent_to_family(f_id, p_id)

        from src.database_manager import set_family_head
        set_family_head(f_id, p_id)

        send_content(
            message.chat.id,
            t("family_created_other", lang, family=family_name, head=head_fio, phone=head_phone)
        )
    except Exception as e:
        logger.error(f"Error creating family with external head: {e}")
        send_content(message.chat.id, t("family_error", lang))
