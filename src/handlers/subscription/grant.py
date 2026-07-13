"""Админ-выдача подписок: /grant_sub, /cancel_sub, подтверждение оплаты по карте
и inline-выдача из меню семьи."""
import os
import logging
from telebot import types
from src.bot_instance import bot
from src.i18n import t
from src.ui import send_content
from src.utils import to_date_str
from src.database_manager import (
    get_user_lang, get_parent_id_by_telegram, get_family_subscription,
    is_subscription_active, extend_subscription, record_payment,
    cancel_subscription, get_family_members_telegram_ids,
)
from ._common import _notify_family_about_subscription

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Ручной перевод: админ подтверждает / отклоняет
# ═══════════════════════════════════════════

@bot.callback_query_handler(func=lambda call: call.data.startswith('sub_card_confirm_'))
def callback_admin_confirm_card(call):
    """Админ подтвердил оплату по карте → активируем подписку."""
    from src.database_manager import get_parent_role
    if get_parent_role(call.from_user.id) != 'admin':
        bot.answer_callback_query(call.id)
        return

    parts = call.data.split('_')
    # sub_card_confirm_3_monthly_3
    family_id = int(parts[3])
    plan_key = parts[4]
    months = int(parts[5])
    lang = get_user_lang(call.from_user.id)
    bot.answer_callback_query(call.id)

    extend_subscription(family_id, months)

    parent_id = get_parent_id_by_telegram(call.from_user.id)
    record_payment(
        family_id=family_id,
        paid_by_parent_id=parent_id,
        amount=0,
        currency='UZS',
        plan=f'card_{plan_key}',
        months=months,
    )

    # Уведомляем всех членов семьи
    _notify_family_about_subscription(family_id, months)

    bot.edit_message_text(
        t("sub_card_confirmed_admin", lang, family_id=family_id, months=months),
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode='HTML'
    )
    logger.info(f"Admin confirmed card payment: family={family_id}, months={months}")


@bot.callback_query_handler(func=lambda call: call.data.startswith('sub_card_reject_'))
def callback_admin_reject_card(call):
    """Админ отклонил оплату."""
    from src.database_manager import get_parent_role
    if get_parent_role(call.from_user.id) != 'admin':
        bot.answer_callback_query(call.id)
        return

    user_id = int(call.data.replace('sub_card_reject_', ''))
    lang = get_user_lang(call.from_user.id)
    bot.answer_callback_query(call.id)

    bot.edit_message_text(
        "❌ Оплата отклонена.",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id
    )

    # Уведомляем пользователя
    user_lang = get_user_lang(user_id)
    try:
        bot.send_message(user_id, t("sub_card_rejected_user", user_lang))
    except Exception as e:
        logger.debug(f"Could not send card rejection to user {user_id}: {e}")


# ═══════════════════════════════════════════
#  АДМИН: /grant_sub + кнопка в меню семьи
# ═══════════════════════════════════════════

@bot.message_handler(commands=['grant_sub'])
def cmd_grant_subscription(message):
    """Админ-команда: /grant_sub — интерактивный выбор семьи и срока."""
    from src.database_manager import get_parent_role, get_all_families
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        return

    # Быстрый режим: /grant_sub <family_id> <months>
    args = message.text.split()
    if len(args) >= 3:
        try:
            family_id = int(args[1])
            months = int(args[2])
            _execute_grant(user_id, family_id, months, lang)
            send_content(user_id, t("sub_granted", lang, family_id=family_id, months=months))
            return
        except ValueError:
            pass

    # Интерактивный режим
    families = get_all_families()
    if not families:
        bot.send_message(user_id, t("sub_no_families_exist", lang))
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for fam in families:
        active = is_subscription_active(fam['id'])
        sub = get_family_subscription(fam['id'])
        sub_end = to_date_str(sub['subscription_end']) if sub and sub.get('subscription_end') else "—"
        status = "✅" if active else "❌"
        label = f"{status} #{fam['id']} {fam['family_name']} ({fam.get('head_fio', '?')}) → {sub_end}"
        markup.add(types.InlineKeyboardButton(
            label, callback_data=f"gsub_fam_{fam['id']}"))

    bot.send_message(user_id, t("sub_grant_select_family", lang), reply_markup=markup)


def _show_duration_picker(chat_id: int, message_id: int, family_id: int, lang: str):
    """Показывает inline-кнопки выбора срока подписки (для админа)."""
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.add(
        types.InlineKeyboardButton("1 мес", callback_data=f"gsub_do_{family_id}_1"),
        types.InlineKeyboardButton("3 мес", callback_data=f"gsub_do_{family_id}_3"),
        types.InlineKeyboardButton("6 мес", callback_data=f"gsub_do_{family_id}_6"),
    )
    markup.add(
        types.InlineKeyboardButton("12 мес", callback_data=f"gsub_do_{family_id}_12"),
        types.InlineKeyboardButton("∞ Навсегда", callback_data=f"gsub_do_{family_id}_999"),
    )
    markup.add(
        types.InlineKeyboardButton(t("family_back", lang), callback_data=f"admin_manage_{family_id}"),
    )

    bot.edit_message_text(
        t("sub_grant_select_months", lang, family_id=family_id),
        chat_id=chat_id,
        message_id=message_id,
        reply_markup=markup
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('admin_sub_'))
def callback_admin_sub_from_menu(call):
    """Админ нажал 'Подписка' в меню управления семьёй."""
    from src.database_manager import get_parent_role
    user_id = call.from_user.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        bot.answer_callback_query(call.id)
        return

    family_id = int(call.data.replace('admin_sub_', ''))
    bot.answer_callback_query(call.id)
    _show_duration_picker(call.message.chat.id, call.message.message_id, family_id, lang)


@bot.callback_query_handler(func=lambda call: call.data.startswith('gsub_fam_'))
def callback_grant_select_family(call):
    """Админ выбрал семью из /grant_sub — теперь выбор срока."""
    from src.database_manager import get_parent_role
    user_id = call.from_user.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        bot.answer_callback_query(call.id)
        return

    family_id = int(call.data.replace('gsub_fam_', ''))
    bot.answer_callback_query(call.id)
    _show_duration_picker(call.message.chat.id, call.message.message_id, family_id, lang)


@bot.callback_query_handler(func=lambda call: call.data.startswith('gsub_do_'))
def callback_grant_execute(call):
    """Админ выбрал срок — выдаём подписку."""
    from src.database_manager import get_parent_role
    user_id = call.from_user.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        bot.answer_callback_query(call.id)
        return

    parts = call.data.split('_')
    family_id = int(parts[2])
    months = int(parts[3])

    _execute_grant(user_id, family_id, months, lang)

    bot.answer_callback_query(call.id,
        t("sub_granted", lang, family_id=family_id, months=months),
        show_alert=True)

    # Возвращаемся в меню управления семьёй с обновлённым статусом
    from src.handlers.family import _send_family_manage_menu
    _send_family_manage_menu(call.message.chat.id, family_id, call.message.message_id)


def _execute_grant(admin_id: int, family_id: int, months: int, lang: str):
    """Выполняет выдачу подписки, записывает в историю, уведомляет членов семьи."""
    extend_subscription(family_id, months)

    parent_id = get_parent_id_by_telegram(admin_id)
    record_payment(
        family_id=family_id,
        paid_by_parent_id=parent_id,
        amount=0,
        currency='UZS',
        plan='admin_grant',
        months=months,
    )

    # Уведомляем всех членов семьи об активации подписки
    _notify_family_about_subscription(family_id, months)

    logger.info(f"Admin {admin_id} granted {months} months to family {family_id}")


# ═══════════════════════════════════════════
#  АДМИН: /cancel_sub — отмена подписки
# ═══════════════════════════════════════════

@bot.message_handler(commands=['cancel_sub'])
def cmd_cancel_sub(message):
    """Админ-команда: /cancel_sub — отменить подписку у семьи."""
    from src.database_manager import get_parent_role, get_all_families
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        return

    # Быстрый режим: /cancel_sub <family_id>
    args = message.text.split()
    if len(args) >= 2:
        try:
            family_id = int(args[1])
            cancel_subscription(family_id)
            _notify_family_about_cancellation(family_id)
            send_content(user_id, t("sub_cancelled_admin", lang, family_id=family_id))
            return
        except ValueError:
            pass

    # Интерактивный режим — показать только семьи с активной подпиской
    families = get_all_families()
    active_fams = [f for f in families if is_subscription_active(f['id'])]
    if not active_fams:
        bot.send_message(user_id, t("sub_cancel_no_active", lang))
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    for fam in active_fams:
        sub = get_family_subscription(fam['id'])
        sub_end = to_date_str(sub['subscription_end']) if sub and sub.get('subscription_end') else "—"
        label = f"❌ #{fam['id']} {fam['family_name']} (до {sub_end})"
        markup.add(types.InlineKeyboardButton(
            label, callback_data=f"csub_confirm_{fam['id']}"))

    bot.send_message(user_id, t("sub_cancel_select", lang), reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith('csub_confirm_'))
def callback_cancel_sub_confirm(call):
    """Подтверждение отмены подписки."""
    from src.database_manager import get_parent_role
    user_id = call.from_user.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        bot.answer_callback_query(call.id)
        return

    family_id = int(call.data.replace('csub_confirm_', ''))
    bot.answer_callback_query(call.id)

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        t("sub_cancel_yes", lang),
        callback_data=f"csub_do_{family_id}"))
    markup.add(types.InlineKeyboardButton(
        t("family_back", lang),
        callback_data="csub_back"))

    bot.edit_message_text(
        t("sub_cancel_confirm_prompt", lang, family_id=family_id),
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=markup,
        parse_mode='HTML'
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('csub_do_'))
def callback_cancel_sub_execute(call):
    """Выполнение отмены подписки."""
    from src.database_manager import get_parent_role
    user_id = call.from_user.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        bot.answer_callback_query(call.id)
        return

    family_id = int(call.data.replace('csub_do_', ''))
    cancel_subscription(family_id)
    _notify_family_about_cancellation(family_id)

    bot.answer_callback_query(call.id,
        t("sub_cancelled_admin", lang, family_id=family_id), show_alert=True)
    bot.edit_message_text(
        t("sub_cancelled_admin", lang, family_id=family_id),
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode='HTML'
    )
    logger.info(f"Admin {user_id} cancelled subscription for family {family_id}")


@bot.callback_query_handler(func=lambda call: call.data == 'csub_back')
def callback_cancel_sub_back(call):
    """Назад из отмены подписки."""
    bot.answer_callback_query(call.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete cancel_sub_back message: {e}")


def _notify_family_about_cancellation(family_id: int):
    """Уведомляет членов семьи об отмене подписки."""
    tg_ids = get_family_members_telegram_ids(family_id)
    for tg_id in tg_ids:
        try:
            user_lang = get_user_lang(tg_id)
            bot.send_message(tg_id, t("sub_cancelled_notify", user_lang),
                             parse_mode='HTML')
        except Exception as e:
            logger.debug(f"Could not notify {tg_id} about cancellation: {e}")
