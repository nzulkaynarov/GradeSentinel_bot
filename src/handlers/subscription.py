"""
Обработчик подписок и платежей через Telegram Payments API.
Поддержка: Click / Payme через провайдер-токен.
"""
import os
import logging
from telebot import types
from src.bot_instance import bot
from src.ui import send_menu_safe, send_content
from src.database_manager import (
    get_user_lang, get_families_for_user, get_parent_id_by_telegram,
    is_subscription_active, extend_subscription, record_payment,
    get_family_subscription, is_head_of_any_family
)
from src.i18n import t

logger = logging.getLogger(__name__)

# Провайдер-токен из .env (Click/Payme)
PROVIDER_TOKEN = os.environ.get("PAYMENT_PROVIDER_TOKEN", "")

# Тарифные планы (суммы в тийинах — 1 UZS = 100 тийин для Telegram)
# Telegram Payments API ожидает amount в минимальных единицах валюты
PLANS = {
    'monthly': {
        'months': 1,
        'amount': 29900_00,  # 29,900 UZS в тийинах
        'amount_display': '29 900',
    },
    'quarterly': {
        'months': 3,
        'amount': 79900_00,  # 79,900 UZS (скидка ~11%)
        'amount_display': '79 900',
    },
    'yearly': {
        'months': 12,
        'amount': 249900_00,  # 249,900 UZS (скидка ~30%)
        'amount_display': '249 900',
    },
}


def cmd_subscription(message):
    """Показывает статус подписки и тарифы."""
    user_id = message.chat.id if hasattr(message, 'chat') else message.from_user.id
    lang = get_user_lang(user_id)

    if not PROVIDER_TOKEN:
        send_content(user_id, t("sub_unavailable", lang))
        return

    families = get_families_for_user(user_id)
    if not families:
        send_content(user_id, t("sub_no_family", lang))
        return

    # Показываем статус подписки для каждой семьи
    lines = [t("sub_status_title", lang)]
    for fam in families:
        sub = get_family_subscription(fam['id'])
        sub_end = sub['subscription_end'] if sub and sub['subscription_end'] else None
        active = is_subscription_active(fam['id'])

        if active:
            lines.append(t("sub_family_active", lang,
                           family=fam['family_name'], end=sub_end[:10]))
        else:
            lines.append(t("sub_family_inactive", lang,
                           family=fam['family_name']))

    # Кнопки тарифов
    markup = types.InlineKeyboardMarkup()
    for plan_key, plan in PLANS.items():
        label = t(f"sub_plan_{plan_key}", lang, amount=plan['amount_display'])
        markup.add(types.InlineKeyboardButton(
            label, callback_data=f"sub_buy_{plan_key}"))

    send_content(user_id, "\n".join(lines))
    bot.send_message(user_id, t("sub_choose_plan", lang), reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith('sub_buy_'))
def callback_buy_plan(call):
    """Пользователь выбрал тариф — выбираем семью если несколько."""
    plan_key = call.data.replace('sub_buy_', '')
    user_id = call.from_user.id
    lang = get_user_lang(user_id)

    bot.answer_callback_query(call.id)

    if plan_key not in PLANS:
        return

    families = get_families_for_user(user_id)
    if not families:
        bot.send_message(call.message.chat.id, t("sub_no_family", lang))
        return

    if len(families) == 1:
        _send_invoice(call.message.chat.id, families[0]['id'], plan_key, lang)
    else:
        markup = types.InlineKeyboardMarkup()
        for fam in families:
            markup.add(types.InlineKeyboardButton(
                fam['family_name'],
                callback_data=f"sub_fam_{fam['id']}_{plan_key}"))
        bot.send_message(call.message.chat.id,
                         t("sub_select_family", lang), reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith('sub_fam_'))
def callback_select_family_for_sub(call):
    """Пользователь выбрал семью для оплаты."""
    parts = call.data.split('_')
    family_id = int(parts[2])
    plan_key = parts[3]
    lang = get_user_lang(call.from_user.id)
    bot.answer_callback_query(call.id)
    _send_invoice(call.message.chat.id, family_id, plan_key, lang)


def _send_invoice(chat_id: int, family_id: int, plan_key: str, lang: str):
    """Отправляет инвойс (счёт) через Telegram Payments API."""
    plan = PLANS[plan_key]

    title = t("sub_invoice_title", lang)
    description = t("sub_invoice_desc", lang,
                     months=plan['months'], amount=plan['amount_display'])

    prices = [types.LabeledPrice(
        label=t(f"sub_plan_{plan_key}", lang, amount=plan['amount_display']),
        amount=plan['amount']
    )]

    try:
        bot.send_invoice(
            chat_id=chat_id,
            title=title,
            description=description,
            invoice_payload=f"{family_id}:{plan_key}:{plan['months']}",
            provider_token=PROVIDER_TOKEN,
            currency='UZS',
            prices=prices,
            start_parameter=f"sub_{plan_key}",
            is_flexible=False,
        )
    except Exception as e:
        logger.error(f"Failed to send invoice: {e}")
        bot.send_message(chat_id, t("sub_invoice_error", lang))


@bot.pre_checkout_query_handler(func=lambda query: True)
def handle_pre_checkout(pre_checkout_query):
    """Подтверждение перед оплатой — валидируем payload."""
    try:
        payload = pre_checkout_query.invoice_payload
        parts = payload.split(':')
        if len(parts) != 3:
            bot.answer_pre_checkout_query(pre_checkout_query.id, ok=False,
                                          error_message="Invalid payment data")
            return

        family_id = int(parts[0])
        # Проверяем что семья существует
        from src.database_manager import get_family_subscription
        sub = get_family_subscription(family_id)
        if sub is None:
            bot.answer_pre_checkout_query(pre_checkout_query.id, ok=False,
                                          error_message="Family not found")
            return

        bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
    except Exception as e:
        logger.error(f"Pre-checkout error: {e}")
        bot.answer_pre_checkout_query(pre_checkout_query.id, ok=False,
                                      error_message="Internal error")


@bot.message_handler(content_types=['successful_payment'])
def handle_successful_payment(message):
    """Обработка успешного платежа — активация подписки."""
    payment = message.successful_payment
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    payload = payment.invoice_payload
    parts = payload.split(':')
    family_id = int(parts[0])
    plan_key = parts[1]
    months = int(parts[2])

    parent_id = get_parent_id_by_telegram(user_id)

    # Записываем платёж
    record_payment(
        family_id=family_id,
        paid_by_parent_id=parent_id,
        amount=payment.total_amount,
        currency=payment.currency,
        plan=plan_key,
        months=months,
        telegram_charge_id=payment.telegram_payment_charge_id,
        provider_charge_id=payment.provider_payment_charge_id,
    )

    # Активируем подписку
    extend_subscription(family_id, months)

    send_content(
        user_id,
        t("sub_payment_success", lang, months=months)
    )
    logger.info(f"Payment successful: family={family_id}, plan={plan_key}, "
                f"months={months}, user={user_id}")


# Команда для админа — выдать подписку вручную
@bot.message_handler(commands=['grant_sub'])
def cmd_grant_subscription(message):
    """Админ-команда: /grant_sub <family_id> <months>"""
    from src.database_manager import get_parent_role
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        return

    args = message.text.split()
    if len(args) < 3:
        bot.send_message(user_id, t("sub_grant_usage", lang))
        return

    try:
        family_id = int(args[1])
        months = int(args[2])
    except ValueError:
        bot.send_message(user_id, t("sub_grant_usage", lang))
        return

    extend_subscription(family_id, months)

    parent_id = get_parent_id_by_telegram(user_id)
    record_payment(
        family_id=family_id,
        paid_by_parent_id=parent_id,
        amount=0,
        currency='UZS',
        plan='admin_grant',
        months=months,
    )

    send_content(user_id, t("sub_granted", lang, family_id=family_id, months=months))
    logger.info(f"Admin {user_id} granted {months} months to family {family_id}")
