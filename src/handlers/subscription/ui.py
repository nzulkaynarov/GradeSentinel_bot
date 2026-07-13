"""Экраны подписки: статус (/subscription), выбор тарифа/семьи, способы оплаты."""
import logging
from telebot import types
from src.bot_instance import bot
from src.i18n import t
from src.ui import send_content
from src.utils import to_date_str
from src.database_manager import (
    get_user_lang, get_families_for_user, get_family_subscription,
    is_subscription_active,
)
from .plans import get_plans
from ._common import (
    PROVIDERS, HAS_ANY_PAYMENT, HAS_CARD_TRANSFER, _check_user_can_pay_for_family,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  ЭКРАН 1: Статус подписки (точка входа)
# ═══════════════════════════════════════════

@bot.message_handler(commands=['subscription'])
def cmd_subscription(message):
    """Кнопка '💳 Подписка' и команда /subscription — статус + что входит + CTA.

    NAV-008: /subscription был упомянут в system prompt и /help, но раньше
    не регистрировался — теперь это полноценная команда."""
    user_id = message.chat.id if hasattr(message, 'chat') else message.from_user.id
    lang = get_user_lang(user_id)

    families = get_families_for_user(user_id)
    if not families:
        send_content(user_id, t("sub_no_family", lang))
        return

    # Статус подписки для каждой семьи
    lines = [t("sub_status_title", lang)]
    has_inactive = False
    for fam in families:
        sub = get_family_subscription(fam['id'])
        sub_end = sub['subscription_end'] if sub and sub['subscription_end'] else None
        active = is_subscription_active(fam['id'])

        if active:
            lines.append(t("sub_family_active", lang,
                           family=fam['family_name'], end=to_date_str(sub_end)))
        else:
            has_inactive = True
            lines.append(t("sub_family_inactive", lang,
                           family=fam['family_name']))

    # Что входит в подписку
    lines.append("")
    lines.append(t("sub_features", lang))

    markup = types.InlineKeyboardMarkup()

    if HAS_ANY_PAYMENT:
        if has_inactive:
            markup.add(types.InlineKeyboardButton(
                t("sub_buy_btn", lang), callback_data="sub_start_buy"))
        else:
            # Все семьи активны — предложить продлить
            markup.add(types.InlineKeyboardButton(
                t("sub_extend_btn", lang), callback_data="sub_start_buy"))
    else:
        # Нет провайдеров — кнопка "Написать админу"
        lines.append("")
        lines.append(t("sub_no_providers", lang))
        markup.add(types.InlineKeyboardButton(
            t("sub_contact_admin_btn", lang), callback_data="up_support"))

    bot.send_message(user_id, "\n".join(lines), parse_mode='HTML', reply_markup=markup)


# ═══════════════════════════════════════════
#  ЭКРАН 2: Выбор тарифа
# ═══════════════════════════════════════════

@bot.callback_query_handler(func=lambda call: call.data == 'sub_start_buy')
def callback_sub_start_buy(call):
    """Пользователь нажал 'Оформить' — выбор тарифа."""
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    bot.answer_callback_query(call.id)

    plans = get_plans()
    markup = types.InlineKeyboardMarkup()
    for plan_key, plan in plans.items():
        markup.add(types.InlineKeyboardButton(
            t(f"sub_plan_{plan_key}", lang, amount=plan['amount_display']),
            callback_data=f"sub_plan_{plan_key}"))
    markup.add(types.InlineKeyboardButton(
        "🎁 " + t("sub_promo_btn", lang), callback_data="sub_enter_promo"))
    markup.add(types.InlineKeyboardButton(
        t("family_back", lang), callback_data="sub_back_status"))

    bot.edit_message_text(
        t("sub_choose_plan", lang),
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=markup,
        parse_mode='HTML'
    )


@bot.callback_query_handler(func=lambda call: call.data == 'sub_back_status')
def callback_sub_back_status(call):
    """Кнопка 'Назад' из выбора тарифа → обратно к статусу."""
    bot.answer_callback_query(call.id)
    # Удаляем текущее сообщение и показываем статус заново
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete sub_back message: {e}")
    # Создаём фейковый message для cmd_subscription
    cmd_subscription(call.message)


# ═══════════════════════════════════════════
#  ЭКРАН 3: Выбор семьи (если несколько)
# ═══════════════════════════════════════════

@bot.callback_query_handler(func=lambda call: call.data.startswith('sub_plan_'))
def callback_sub_plan(call):
    """Пользователь выбрал тариф → семья (если >1) или сразу к оплате."""
    plan_key = call.data.replace('sub_plan_', '')
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    bot.answer_callback_query(call.id)

    plans = get_plans()
    if plan_key not in plans:
        return

    families = get_families_for_user(user_id)
    if not families:
        bot.send_message(call.message.chat.id, t("sub_no_family", lang))
        return

    if len(families) == 1:
        # Одна семья — сразу к выбору способа оплаты
        _show_payment_methods(call.message.chat.id, call.message.message_id,
                              families[0]['id'], plan_key, lang)
    else:
        # Несколько семей — выбрать
        markup = types.InlineKeyboardMarkup()
        for fam in families:
            active = is_subscription_active(fam['id'])
            status = "✅" if active else "❌"
            markup.add(types.InlineKeyboardButton(
                f"{status} {fam['family_name']}",
                callback_data=f"sub_fam_{fam['id']}_{plan_key}"))
        markup.add(types.InlineKeyboardButton(
            t("family_back", lang), callback_data="sub_start_buy"))

        bot.edit_message_text(
            t("sub_select_family", lang),
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=markup,
            parse_mode='HTML'
        )


# ═══════════════════════════════════════════
#  ЭКРАН 4: Выбор способа оплаты
# ═══════════════════════════════════════════

@bot.callback_query_handler(func=lambda call: call.data.startswith('sub_fam_'))
def callback_select_family_for_sub(call):
    """Пользователь выбрал семью → способ оплаты."""
    parts = call.data.split('_')
    if len(parts) < 4:
        return
    try:
        family_id = int(parts[2])
    except ValueError:
        return
    plan_key = parts[3]
    if not _check_user_can_pay_for_family(call, family_id):
        return
    lang = get_user_lang(call.from_user.id)
    bot.answer_callback_query(call.id)
    _show_payment_methods(call.message.chat.id, call.message.message_id,
                          family_id, plan_key, lang)


def _show_payment_methods(chat_id: int, message_id: int,
                          family_id: int, plan_key: str, lang: str):
    """Показывает экран выбора способа оплаты с итогом заказа."""
    plans = get_plans()
    plan = plans[plan_key]

    # Итог заказа
    text = t("sub_payment_summary", lang,
             months=plan['months'],
             amount=plan['amount_display'])

    markup = types.InlineKeyboardMarkup()

    # Онлайн-провайдеры
    for provider_key, provider in PROVIDERS.items():
        markup.add(types.InlineKeyboardButton(
            provider['label'],
            callback_data=f"sub_pay_{provider_key}_{family_id}_{plan_key}"))

    # Ручной перевод на карту
    if HAS_CARD_TRANSFER:
        markup.add(types.InlineKeyboardButton(
            t("sub_card_transfer_btn", lang),
            callback_data=f"sub_card_{family_id}_{plan_key}"))
    elif not PROVIDERS:
        # Нет ни провайдеров, ни карты — только через админа
        markup.add(types.InlineKeyboardButton(
            t("sub_contact_admin_btn", lang),
            callback_data=f"sub_contact_admin_{family_id}_{plan_key}"))

    markup.add(types.InlineKeyboardButton(
        t("family_back", lang), callback_data="sub_start_buy"))

    bot.edit_message_text(
        text,
        chat_id=chat_id,
        message_id=message_id,
        reply_markup=markup,
        parse_mode='HTML'
    )
