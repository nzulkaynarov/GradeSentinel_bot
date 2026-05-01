"""Семейные групповые чаты — бот пересылает уведомления об оценках в групповой чат семьи.

Сценарий:
1. Глава семьи добавляет бота в групповой чат (с детьми, бабушками, etc.)
2. Бот ловит событие `new_chat_members` (себя в списке) → шлёт сообщение
   с inline-кнопками выбора семьи (только из тех, где user — head или admin).
3. После клика на кнопку — chat_id привязан к family_id в БД.
4. monitor_engine.send_notification дублирует уведомления об оценках
   во все привязанные группы (с дедупликацией по студенту).

Безопасность:
- Привязать группу может только глава семьи или admin (не любой её участник).
- Один chat_id может быть привязан к одной семье (UNIQUE).
- Чтобы отвязать — `/unlink_group` в самой группе (тот же admin/head check).
"""
import logging
from typing import Optional
from telebot import types

from src.bot_instance import bot
from src.database_manager import (
    get_user_lang, get_parent_role, get_families_for_head,
    get_parent_id_by_telegram, get_family_for_group,
)
from src.db.groups import link_group_to_family, unlink_group
from src.i18n import t

logger = logging.getLogger(__name__)


def _is_group_chat(chat) -> bool:
    return chat.type in ('group', 'supergroup')


@bot.message_handler(content_types=['new_chat_members'])
def on_bot_added_to_group(message: types.Message):
    """Срабатывает когда любого нового участника добавили в групповой чат.
    Реагируем только если в списке — мы сами."""
    if not _is_group_chat(message.chat):
        return

    me_id = bot.get_me().id
    bot_added = any(member.id == me_id for member in (message.new_chat_members or []))
    if not bot_added:
        return

    chat_id = message.chat.id
    chat_title = message.chat.title or ""
    # Для супергрупп с темами захватываем тред, в который пришло событие.
    # У обычных групп этого поля нет → None (= writes в General).
    thread_id = getattr(message, 'message_thread_id', None)
    inviter = message.from_user
    inviter_id = inviter.id
    lang = get_user_lang(inviter_id) or 'ru'

    # Уже привязан к какой-то семье?
    existing = get_family_for_group(chat_id)
    if existing:
        bot.send_message(
            chat_id,
            t("group_already_linked", lang, family=existing['family_name']),
            parse_mode='HTML',
            message_thread_id=thread_id,
        )
        return

    # Авторизация: пригласивший должен быть admin или главой хотя бы одной семьи
    is_admin = get_parent_role(inviter_id) == 'admin'
    head_families = get_families_for_head(inviter_id)
    if not is_admin and not head_families:
        bot.send_message(
            chat_id,
            t("group_inviter_not_authorized", lang),
            parse_mode='HTML',
            message_thread_id=thread_id,
        )
        # Не уходим из группы автоматически — пусть юзер сам решит. Просто ничего не делаем.
        return

    # Если у юзера ровно одна семья (или admin с одной собственной) — спрашиваем подтверждение,
    # но всё равно через кнопку (чтобы не было автопривязки случайной).
    families_to_offer = head_families
    if is_admin and not families_to_offer:
        # Admin без своих семей — даём список всех (редкий кейс, обычно admin ещё и head)
        from src.database_manager import get_all_families
        families_to_offer = [{'id': f['id'], 'family_name': f['family_name']} for f in get_all_families()]

    markup = types.InlineKeyboardMarkup()
    # Кодируем thread_id в callback_data (-1 = None, чтобы не парсить пустую строку)
    thread_marker = thread_id if thread_id is not None else -1
    for fam in families_to_offer:
        markup.add(types.InlineKeyboardButton(
            f"🏠 {fam['family_name']}",
            callback_data=f"glink_{fam['id']}_{inviter_id}_{thread_marker}"
        ))
    markup.add(types.InlineKeyboardButton(
        t("btn_cancel", lang), callback_data=f"gcancel_{inviter_id}"
    ))

    bot.send_message(
        chat_id,
        t("group_choose_family", lang, name=inviter.first_name or "user"),
        reply_markup=markup,
        parse_mode='HTML',
        message_thread_id=thread_id,
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('glink_'))
def callback_group_link(call: types.CallbackQuery):
    """Юзер кликнул на кнопку выбора семьи в групповом чате."""
    parts = call.data.split('_')
    # glink_<family_id>_<inviter_id>_<thread_id_or_-1>
    if len(parts) != 4:
        return
    try:
        family_id = int(parts[1])
        original_inviter_id = int(parts[2])
        thread_marker = int(parts[3])
    except ValueError:
        return
    thread_id = thread_marker if thread_marker >= 0 else None

    user_id = call.from_user.id
    lang = get_user_lang(user_id) or 'ru'

    # Только тот, кто пригласил бота, может выбрать семью.
    # Иначе любой участник группы мог бы перебить выбор.
    if user_id != original_inviter_id:
        bot.answer_callback_query(
            call.id, t("group_not_inviter", lang), show_alert=True
        )
        return

    # Перепроверка прав (на случай если за время с момента приглашения юзер потерял head)
    is_admin = get_parent_role(user_id) == 'admin'
    user_families = {f['id'] for f in get_families_for_head(user_id)}
    if not is_admin and family_id not in user_families:
        bot.answer_callback_query(
            call.id, t("admin_no_access", lang), show_alert=True
        )
        return

    chat_id = call.message.chat.id
    chat_title = call.message.chat.title or ""
    parent_id = get_parent_id_by_telegram(user_id)
    if parent_id is None:
        bot.answer_callback_query(call.id, t("family_account_not_found", lang), show_alert=True)
        return

    success = link_group_to_family(family_id, chat_id, chat_title, parent_id,
                                    message_thread_id=thread_id)
    if not success:
        # Кто-то параллельно привязал тот же chat_id (или гонка)
        existing = get_family_for_group(chat_id)
        fam_name = existing['family_name'] if existing else "?"
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            t("group_already_linked", lang, family=fam_name),
            chat_id=chat_id, message_id=call.message.message_id, parse_mode='HTML'
        )
        return

    bot.answer_callback_query(call.id, t("group_linked_alert", lang))
    bot.edit_message_text(
        t("group_linked", lang),
        chat_id=chat_id, message_id=call.message.message_id, parse_mode='HTML'
    )
    logger.info(
        f"Group {chat_id} (thread={thread_id}) linked to family {family_id} by user {user_id}"
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('gcancel_'))
def callback_group_cancel(call: types.CallbackQuery):
    parts = call.data.split('_')
    try:
        original_inviter_id = int(parts[1])
    except (IndexError, ValueError):
        return
    if call.from_user.id != original_inviter_id:
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete group cancel msg: {e}")


@bot.message_handler(commands=['unlink_group'])
def cmd_unlink_group(message: types.Message):
    """Команда в групповом чате — отвязывает группу от семьи.
    Доступно admin и главе семьи, к которой группа привязана."""
    if not _is_group_chat(message.chat):
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    lang = get_user_lang(user_id) or 'ru'

    existing = get_family_for_group(chat_id)
    if not existing:
        bot.reply_to(message, t("group_not_linked", lang))
        return

    # Авторизация: admin OR head привязанной семьи
    is_admin = get_parent_role(user_id) == 'admin'
    user_head_families = {f['id'] for f in get_families_for_head(user_id)}
    if not is_admin and existing['family_id'] not in user_head_families:
        bot.reply_to(message, t("admin_no_access", lang))
        return

    if unlink_group(chat_id):
        bot.reply_to(message, t("group_unlinked", lang, family=existing['family_name']))
        logger.info(f"Group {chat_id} unlinked by user {user_id}")
    else:
        bot.reply_to(message, t("group_not_linked", lang))


@bot.message_handler(content_types=['left_chat_member'])
def on_bot_removed_from_group(message: types.Message):
    """Если бота кикнули из группы — автоматически отвязываем."""
    if not _is_group_chat(message.chat):
        return
    left = message.left_chat_member
    if not left:
        return
    try:
        me_id = bot.get_me().id
    except Exception:
        return
    if left.id != me_id:
        return

    chat_id = message.chat.id
    if unlink_group(chat_id):
        logger.info(f"Bot removed from group {chat_id} — auto-unlinked")
