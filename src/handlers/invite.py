"""
Обработчик инвайт-ссылок для семей.
Глава семьи генерирует ссылку → отправляет родственнику →
родственник переходит по ссылке → автоматически привязывается к семье.
"""
import logging
from telebot import types
from src.bot_instance import bot
from src.ui import send_menu_safe, send_content
from src.database_manager import (
    get_user_lang, get_parent_id_by_telegram, create_invite,
    get_invite, use_invite, link_parent_to_family, add_parent,
    get_parent_by_phone, update_parent_telegram_id, get_parent_role
)
from src.i18n import t

logger = logging.getLogger(__name__)

BOT_USERNAME = None


def _get_bot_username():
    """Кэшируем username бота."""
    global BOT_USERNAME
    if not BOT_USERNAME:
        try:
            BOT_USERNAME = bot.get_me().username
        except Exception:
            BOT_USERNAME = "GradeSentinelBot"
    return BOT_USERNAME


def generate_invite_link(chat_id: int, family_id: int):
    """Генерирует инвайт-ссылку для семьи и отправляет главе."""
    lang = get_user_lang(chat_id)
    parent_id = get_parent_id_by_telegram(chat_id)

    if not parent_id:
        bot.send_message(chat_id, t("family_account_not_found", lang))
        return

    code = create_invite(family_id, parent_id, expires_hours=48)
    username = _get_bot_username()
    link = f"https://t.me/{username}?start=inv_{code}"

    send_content(
        chat_id,
        t("invite_generated", lang, link=link)
    )
    logger.info(f"Invite generated for family {family_id} by parent {parent_id}")


def handle_invite_deeplink(message, invite_code: str):
    """Обрабатывает переход по инвайт-ссылке."""
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    invite = get_invite(invite_code)
    if not invite:
        bot.send_message(user_id, t("invite_invalid", lang))
        return

    family_name = invite['family_name']
    family_id = invite['family_id']

    # Проверяем, авторизован ли пользователь
    parent_id = get_parent_id_by_telegram(user_id)

    if parent_id:
        # Уже в системе — атомарно используем инвайт (защита от гонки),
        # затем привязываем к семье
        if not use_invite(invite_code, parent_id):
            bot.send_message(user_id, t("invite_expired", lang))
            return
        link_parent_to_family(family_id, parent_id)
        send_menu_safe(user_id, t("invite_accepted", lang, family=family_name))
        logger.info(f"Existing user {user_id} joined family {family_id} via invite")
    else:
        # Новый пользователь — нужна авторизация через контакт
        from src.database_manager import set_user_state
        set_user_state(user_id, "pending_invite", invite_code)

        markup = types.ReplyKeyboardMarkup(one_time_keyboard=True, resize_keyboard=True,
                                            input_field_placeholder=t("auth_placeholder", lang))
        button = types.KeyboardButton(t("btn_share_contact", lang), request_contact=True)
        markup.add(button)
        bot.send_message(
            user_id,
            t("invite_auth_required", lang, family=family_name),
            reply_markup=markup
        )


def process_invite_after_contact(user_id: int, phone: str, invite_code: str):
    """Завершает процесс инвайта после авторизации по контакту."""
    lang = get_user_lang(user_id) or 'ru'

    invite = get_invite(invite_code)
    if not invite:
        bot.send_message(user_id, t("invite_expired", lang))
        return False

    family_id = invite['family_id']
    family_name = invite['family_name']

    # Проверяем, есть ли пользователь в БД по телефону
    parent = get_parent_by_phone(phone)
    if parent:
        parent_id = parent['id']
        update_parent_telegram_id(phone, user_id)
    else:
        # Создаём нового родителя
        parent_id = add_parent(f"User_{user_id}", phone, role='senior')
        update_parent_telegram_id(phone, user_id)

    # Атомарно используем инвайт (защита от гонки), затем привязываем к семье
    if not use_invite(invite_code, parent_id):
        bot.send_message(user_id, t("invite_expired", lang))
        return False
    link_parent_to_family(family_id, parent_id)

    send_menu_safe(user_id, t("invite_accepted", lang, family=family_name))
    logger.info(f"New user {user_id} joined family {family_id} via invite after auth")
    return True
