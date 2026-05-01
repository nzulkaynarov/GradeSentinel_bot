"""Семейные групповые чаты — бот пересылает уведомления об оценках в групповой чат семьи.

Сценарий:
1. Глава семьи добавляет бота в групповой чат (с детьми, бабушками, etc.)
2. Бот ловит событие `new_chat_members` (себя в списке) → шлёт сообщение
   с inline-кнопками выбора семьи (только из тех, где user — head или admin).
3. После клика на кнопку — chat_id привязан к family_id в БД.
4. ОПЦИОНАЛЬНО: для супергрупп с темами Telegram не даёт добавить бота
   прямо в тему (он становится участником всей группы), и событие
   `new_chat_members` приходит без message_thread_id → бот по умолчанию
   пишет в General. Чтобы направить уведомления в нужную тему, юзер
   присылает боту ссылку на любое сообщение в теме (`https://t.me/c/.../<topic_id>/<msg_id>`),
   из которой парсится thread_id.

Безопасность:
- Привязать группу может только глава семьи или admin (не любой участник).
- Один chat_id = одна привязка (UNIQUE).
- Сменить тему / отвязать — `/set_thread` / `/unlink_group` в чате
  (тот же admin/head check).
"""
import logging
from typing import Optional
from telebot import types

from src.bot_instance import bot
from src.database_manager import (
    get_user_lang, get_parent_role, get_families_for_head,
    get_parent_id_by_telegram, get_family_for_group,
    set_user_state, get_user_state, clear_user_state,
)
from src.db.groups import link_group_to_family, unlink_group, update_group_thread
from src.group_utils import parse_topic_link as _parse_topic_link
from src.i18n import t

logger = logging.getLogger(__name__)

# State для multi-step flow (юзер прислал ссылку на тему)
STATE_AWAITING_TOPIC_LINK = "awaiting_topic_link"


def _is_group_chat(chat) -> bool:
    return chat.type in ('group', 'supergroup')


def _is_supergroup_with_topics(chat) -> bool:
    """True для supergroup с включёнными темами (forum)."""
    return chat.type == 'supergroup' and getattr(chat, 'is_forum', False)


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
    # message_thread_id события new_chat_members — обычно НЕТ для тематических групп
    # (Telegram не даёт добавлять в конкретную тему). Сохраняем на случай если
    # вдруг есть — но в большинстве сценариев будет None.
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
        return

    families_to_offer = head_families
    if is_admin and not families_to_offer:
        from src.database_manager import get_all_families
        families_to_offer = [{'id': f['id'], 'family_name': f['family_name']}
                             for f in get_all_families()]

    markup = types.InlineKeyboardMarkup()
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
    initial_thread_id = thread_marker if thread_marker >= 0 else None

    user_id = call.from_user.id
    lang = get_user_lang(user_id) or 'ru'

    if user_id != original_inviter_id:
        bot.answer_callback_query(call.id, t("group_not_inviter", lang), show_alert=True)
        return

    is_admin = get_parent_role(user_id) == 'admin'
    user_families = {f['id'] for f in get_families_for_head(user_id)}
    if not is_admin and family_id not in user_families:
        bot.answer_callback_query(call.id, t("admin_no_access", lang), show_alert=True)
        return

    chat_id = call.message.chat.id
    chat_title = call.message.chat.title or ""
    parent_id = get_parent_id_by_telegram(user_id)
    if parent_id is None:
        bot.answer_callback_query(call.id, t("family_account_not_found", lang), show_alert=True)
        return

    success = link_group_to_family(family_id, chat_id, chat_title, parent_id,
                                    message_thread_id=initial_thread_id)
    if not success:
        existing = get_family_for_group(chat_id)
        fam_name = existing['family_name'] if existing else "?"
        bot.answer_callback_query(call.id)
        bot.edit_message_text(
            t("group_already_linked", lang, family=fam_name),
            chat_id=chat_id, message_id=call.message.message_id, parse_mode='HTML'
        )
        return

    bot.answer_callback_query(call.id, t("group_linked_alert", lang))
    logger.info(f"Group {chat_id} linked to family {family_id} by user {user_id}")

    # Для супергрупп с темами предлагаем указать тему ссылкой.
    # Для обычных групп — просто подтверждаем привязку.
    if _is_supergroup_with_topics(call.message.chat):
        # Просим прислать ссылку на сообщение в нужной теме.
        # State в БД переживает рестарт.
        set_user_state(user_id, STATE_AWAITING_TOPIC_LINK, str(chat_id))
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton(
            t("group_skip_thread_btn", lang),
            callback_data=f"gskip_{user_id}_{chat_id}"
        ))
        bot.edit_message_text(
            t("group_linked_ask_thread", lang),
            chat_id=chat_id, message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode='HTML'
        )
    else:
        bot.edit_message_text(
            t("group_linked", lang),
            chat_id=chat_id, message_id=call.message.message_id, parse_mode='HTML'
        )


@bot.callback_query_handler(func=lambda call: call.data.startswith('gskip_'))
def callback_group_skip_thread(call: types.CallbackQuery):
    """Юзер пропустил шаг указания темы — оставляем как есть (General или
    то что было на момент добавления)."""
    parts = call.data.split('_')
    if len(parts) != 3:
        return
    try:
        original_user_id = int(parts[1])
    except ValueError:
        return
    if call.from_user.id != original_user_id:
        bot.answer_callback_query(call.id)
        return

    clear_user_state(call.from_user.id)
    lang = get_user_lang(call.from_user.id) or 'ru'
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        t("group_linked", lang),
        chat_id=call.message.chat.id, message_id=call.message.message_id,
        parse_mode='HTML'
    )


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


@bot.message_handler(commands=['unlink_group'])
def cmd_unlink_group(message: types.Message):
    """Команда в групповом чате — отвязывает группу от семьи."""
    if not _is_group_chat(message.chat):
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    lang = get_user_lang(user_id) or 'ru'

    existing = get_family_for_group(chat_id)
    if not existing:
        bot.reply_to(message, t("group_not_linked", lang))
        return

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


@bot.message_handler(commands=['set_thread'])
def cmd_set_thread(message: types.Message):
    """Команда в группе: /set_thread <link> — указать тему через ссылку.
    /set_thread без аргументов — сбросить (писать в General)."""
    if not _is_group_chat(message.chat):
        return

    chat_id = message.chat.id
    user_id = message.from_user.id
    lang = get_user_lang(user_id) or 'ru'

    existing = get_family_for_group(chat_id)
    if not existing:
        bot.reply_to(message, t("group_not_linked", lang))
        return

    is_admin = get_parent_role(user_id) == 'admin'
    user_head_families = {f['id'] for f in get_families_for_head(user_id)}
    if not is_admin and existing['family_id'] not in user_head_families:
        bot.reply_to(message, t("admin_no_access", lang))
        return

    parts = (message.text or '').split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        # Сброс — пишем в General
        update_group_thread(chat_id, None)
        bot.reply_to(message, t("group_thread_cleared", lang))
        return

    link = parts[1].strip()
    thread_id = _parse_topic_link(link)
    if thread_id is None:
        bot.reply_to(message, t("group_thread_link_invalid", lang))
        return

    update_group_thread(chat_id, thread_id)
    bot.reply_to(message, t("group_thread_set", lang, thread_id=thread_id))
    logger.info(f"Group {chat_id} thread set to {thread_id} by user {user_id}")


@bot.message_handler(
    func=lambda msg: msg.chat.type == 'private'
                    and (get_user_state(msg.chat.id) or {}).get('state') == STATE_AWAITING_TOPIC_LINK,
    content_types=['text']
)
def receive_topic_link_private(message: types.Message):
    """Юзер прислал в личку ссылку на тему после привязки группы.
    Иногда юзер пишет в группу — для этого случая отдельный обработчик ниже."""
    _process_topic_link(message, message.chat.id)


@bot.message_handler(
    func=lambda msg: msg.chat.type in ('group', 'supergroup')
                    and (get_user_state(msg.from_user.id) or {}).get('state') == STATE_AWAITING_TOPIC_LINK
                    and msg.text and 't.me/' in msg.text,
    content_types=['text']
)
def receive_topic_link_in_group(message: types.Message):
    """Юзер прислал ссылку прямо в группу."""
    _process_topic_link(message, message.from_user.id)


def _process_topic_link(message: types.Message, user_id: int):
    state = get_user_state(user_id) or {}
    if state.get('state') != STATE_AWAITING_TOPIC_LINK:
        return
    try:
        chat_id = int(state.get('data') or 0)
    except (TypeError, ValueError):
        clear_user_state(user_id)
        return

    lang = get_user_lang(user_id) or 'ru'
    text = (message.text or '').strip()

    # Перепроверка прав (state мог пережить смену роли)
    existing = get_family_for_group(chat_id)
    if not existing:
        clear_user_state(user_id)
        return
    is_admin = get_parent_role(user_id) == 'admin'
    user_head_families = {f['id'] for f in get_families_for_head(user_id)}
    if not is_admin and existing['family_id'] not in user_head_families:
        clear_user_state(user_id)
        return

    thread_id = _parse_topic_link(text)
    if thread_id is None:
        # Не валидная ссылка — попросим прислать ещё раз. Не очищаем state.
        try:
            bot.reply_to(message, t("group_thread_link_invalid_retry", lang))
        except Exception:
            pass
        return

    update_group_thread(chat_id, thread_id)
    clear_user_state(user_id)
    try:
        bot.reply_to(message, t("group_thread_set", lang, thread_id=thread_id))
    except Exception as e:
        logger.debug(f"Failed to reply to topic link confirmation: {e}")
    logger.info(f"Group {chat_id} thread set to {thread_id} by user {user_id} (via state flow)")
