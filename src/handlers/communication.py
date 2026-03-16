import os
import logging
import threading
from telebot import types
import time
from src.bot_instance import bot
from src.ui import send_menu_safe, send_content
from src.database_manager import (
    get_parent_role, get_user_info_by_tg_id, get_all_telegram_ids,
    set_user_state, get_user_state, clear_user_state,
    save_support_msg_map, get_support_user_id, get_user_lang
)
from src.i18n import t

from typing import Dict, Any

logger = logging.getLogger(__name__)

_broadcast_pending: Dict[int, Any] = {}

def get_admin_group_id():
    group_id = os.environ.get("ADMIN_GROUP_ID")
    if group_id:
        return int(group_id)
    return None

# ====================
# Обратная связь (Пользователь -> Админ)
# ====================
def support_started(message):
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    admin_group = get_admin_group_id()

    if not admin_group:
        send_menu_safe(user_id, t("support_unavailable", lang))
        return

    set_user_state(user_id, "awaiting_support_message")

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True,
                                        input_field_placeholder=t("support_prompt_placeholder", lang))
    markup.add(t("btn_cancel", lang))

    bot.send_message(user_id, t("support_prompt", lang), reply_markup=markup, parse_mode="HTML")

@bot.message_handler(func=lambda msg: (get_user_state(msg.chat.id) or {}).get('state') == "awaiting_support_message", content_types=['text', 'photo', 'document', 'video'])
def receive_support_message(message):
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    admin_group = get_admin_group_id()

    if message.text == t("btn_cancel", lang):
        clear_user_state(user_id)
        send_menu_safe(user_id, t("family_cancelled", lang))
        return

    clear_user_state(user_id)

    user_info = get_user_info_by_tg_id(user_id)
    familia = ", ".join(user_info['families']) if user_info and user_info.get('families') else "Неизвестно"
    fio = user_info['fio'] if user_info else message.from_user.first_name
    phone = user_info['phone'] if user_info else "Неизвестен"

    # Заголовок для админов всегда на русском (для удобства админа)
    header = t("support_admin_header", "ru", fio=fio, phone=phone, families=familia, tg_id=user_id)

    try:
        card = bot.send_message(admin_group, header, parse_mode="HTML")
        if card:
            save_support_msg_map(card.message_id, user_id)

        forwarded = bot.forward_message(admin_group, message.chat.id, message.message_id)
        if forwarded:
            save_support_msg_map(forwarded.message_id, user_id)

        send_menu_safe(user_id, t("support_sent", lang))
    except Exception as e:
        logger.error(f"Failed to forward support message to group {admin_group}: {e}")
        send_menu_safe(user_id, t("support_send_error", lang))

# ====================
# Ответ из группы (Админ -> Пользователь)
# ====================
@bot.message_handler(func=lambda msg: msg.chat.id == get_admin_group_id() and msg.reply_to_message is not None)
def reply_from_admin_group(message):
    original_msg = message.reply_to_message

    user_id = get_support_user_id(original_msg.message_id)

    if not user_id and original_msg.forward_from:
        user_id = original_msg.forward_from.id

    if not user_id:
        if original_msg.text and "TG_ID:" in original_msg.text:
            try:
                lines = original_msg.text.split('\n')
                for line in lines:
                    if "TG_ID:" in line:
                        user_id = int(line.split(':')[1].strip())
                        break
            except Exception as e:
                logger.error(f"Failed to parse user ID from support card: {e}")

    if user_id:
        send_reply_to_user(message, user_id)
    else:
        logger.warning(f"Could not find user_id for reply to message {original_msg.message_id}")

def send_reply_to_user(message, target_user_id):
    lang = get_user_lang(target_user_id)
    try:
        reply_text = t("support_admin_reply", lang, text=message.text)
        bot.send_message(target_user_id, reply_text, parse_mode="HTML")
        bot.reply_to(message, t("support_reply_ok", "ru"))
    except Exception as e:
        logger.error(f"Failed to send reply to user {target_user_id}: {e}")
        bot.reply_to(message, t("support_reply_fail", "ru"))

# ====================
# Рассылка новостей (Супер-Админ -> Пользователи)
# ====================
def broadcast_started(message):
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    if get_parent_role(user_id) != 'admin':
        return

    set_user_state(user_id, "awaiting_broadcast_message")

    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True,
                                        input_field_placeholder=t("broadcast_prompt_placeholder", lang))
    markup.add(t("btn_cancel", lang))

    bot.send_message(user_id, t("broadcast_prompt", lang), reply_markup=markup, parse_mode="HTML")

@bot.message_handler(func=lambda msg: (get_user_state(msg.chat.id) or {}).get('state') == "awaiting_broadcast_message", content_types=['text', 'photo', 'document', 'video'])
def confirm_broadcast_message(message):
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    if message.text == t("btn_cancel", lang):
        clear_user_state(user_id)
        send_menu_safe(user_id, t("broadcast_cancelled", lang))
        return

    set_user_state(user_id, "confirming_broadcast")
    _broadcast_pending[user_id] = message

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(t("broadcast_confirm_btn", lang), callback_data="broadcast_confirm"))
    markup.add(types.InlineKeyboardButton(t("btn_cancel", lang), callback_data="broadcast_cancel"))

    bot.send_message(
        user_id,
        t("broadcast_confirm", lang),
        reply_markup=markup,
        reply_to_message_id=message.message_id
    )

@bot.callback_query_handler(func=lambda call: call.data in ["broadcast_confirm", "broadcast_cancel"])
def process_broadcast_confirmation(call):
    user_id = call.message.chat.id
    lang = get_user_lang(user_id)
    state_data = get_user_state(user_id)

    if not state_data or state_data.get("state") != "confirming_broadcast":
        bot.answer_callback_query(call.id, t("broadcast_data_stale", lang))
        try:
            bot.delete_message(chat_id=user_id, message_id=call.message.message_id)
        except Exception:
            pass
        return

    if call.data == "broadcast_cancel":
        clear_user_state(user_id)
        _broadcast_pending.pop(user_id, None)
        bot.edit_message_text(t("broadcast_cancelled", lang), user_id, call.message.message_id)
        send_menu_safe(user_id, t("main_menu", lang))
        return

    bot.edit_message_text(t("broadcast_started", lang), user_id, call.message.message_id)

    original_message = _broadcast_pending.pop(user_id, None)
    clear_user_state(user_id)

    if not original_message:
        bot.send_message(user_id, t("broadcast_no_message", lang))
        send_menu_safe(user_id, t("main_menu", lang))
        return

    def _do_broadcast(target_user_id, msg_obj):
        users = get_all_telegram_ids()
        success_count = 0
        fail_count = 0

        for tg_id in users:
            if str(tg_id) == str(target_user_id):
                continue
            try:
                bot.copy_message(tg_id, from_chat_id=target_user_id, message_id=msg_obj.message_id)
                success_count += 1
                time.sleep(0.05)
            except Exception as e:
                logger.error(f"Failed to broadcast to {tg_id}: {e}")
                fail_count += 1

        bot.send_message(
            target_user_id,
            t("broadcast_done", lang, success=success_count, fail=fail_count),
            parse_mode="HTML"
        )
        send_menu_safe(target_user_id, t("main_menu", lang))

    threading.Thread(target=_do_broadcast, args=(user_id, original_message), daemon=True).start()
