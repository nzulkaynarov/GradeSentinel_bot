"""
Обработчик подписок и платежей.
Поддержка: Click / Payme (Telegram Payments API) + ручной перевод на карту.
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

# ─── Конфигурация провайдеров из .env ───

CLICK_TOKEN = os.environ.get("CLICK_PROVIDER_TOKEN", "")
PAYME_TOKEN = os.environ.get("PAYME_PROVIDER_TOKEN", "")
# Обратная совместимость: если задан старый PAYMENT_PROVIDER_TOKEN
LEGACY_TOKEN = os.environ.get("PAYMENT_PROVIDER_TOKEN", "")
CARD_NUMBER = os.environ.get("PAYMENT_CARD_NUMBER", "")
CARD_HOLDER = os.environ.get("PAYMENT_CARD_HOLDER", "")

# Собираем доступные способы оплаты
PROVIDERS = {}
if CLICK_TOKEN:
    PROVIDERS['click'] = {'token': CLICK_TOKEN, 'label': '💳 Click'}
if PAYME_TOKEN:
    PROVIDERS['payme'] = {'token': PAYME_TOKEN, 'label': '💳 Payme'}
if not PROVIDERS and LEGACY_TOKEN:
    # Если новые токены не заданы, но есть старый — используем его
    PROVIDERS['payment'] = {'token': LEGACY_TOKEN, 'label': '💳 Оплатить'}
HAS_CARD_TRANSFER = bool(CARD_NUMBER)
HAS_ANY_PAYMENT = bool(PROVIDERS) or HAS_CARD_TRANSFER

# Тарифные планы (суммы в тийинах — 1 UZS = 100 тийин для Telegram Payments API)
PLANS = {
    'monthly': {
        'months': 1,
        'amount': 29900_00,
        'amount_display': '29 900',
    },
    'quarterly': {
        'months': 3,
        'amount': 79900_00,
        'amount_display': '79 900',
    },
    'yearly': {
        'months': 12,
        'amount': 249900_00,
        'amount_display': '249 900',
    },
}


# ═══════════════════════════════════════════
#  ЭКРАН 1: Статус подписки (точка входа)
# ═══════════════════════════════════════════

def cmd_subscription(message):
    """Кнопка '💳 Подписка' — показывает статус + что входит + CTA."""
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
                           family=fam['family_name'], end=sub_end[:10]))
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

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        t("sub_plan_monthly", lang, amount=PLANS['monthly']['amount_display']),
        callback_data="sub_plan_monthly"))
    markup.add(types.InlineKeyboardButton(
        t("sub_plan_quarterly", lang, amount=PLANS['quarterly']['amount_display']),
        callback_data="sub_plan_quarterly"))
    markup.add(types.InlineKeyboardButton(
        t("sub_plan_yearly", lang, amount=PLANS['yearly']['amount_display']),
        callback_data="sub_plan_yearly"))
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
    except Exception:
        pass
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

    if plan_key not in PLANS:
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
    family_id = int(parts[2])
    plan_key = parts[3]
    lang = get_user_lang(call.from_user.id)
    bot.answer_callback_query(call.id)
    _show_payment_methods(call.message.chat.id, call.message.message_id,
                          family_id, plan_key, lang)


def _show_payment_methods(chat_id: int, message_id: int,
                          family_id: int, plan_key: str, lang: str):
    """Показывает экран выбора способа оплаты с итогом заказа."""
    plan = PLANS[plan_key]

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


# ═══════════════════════════════════════════
#  Оплата через Click / Payme (Telegram Payments)
# ═══════════════════════════════════════════

@bot.callback_query_handler(func=lambda call: call.data.startswith('sub_pay_'))
def callback_pay_via_provider(call):
    """Пользователь выбрал Click/Payme → отправляем Invoice."""
    parts = call.data.split('_')
    # sub_pay_click_3_monthly → ['sub', 'pay', 'click', '3', 'monthly']
    provider_key = parts[2]
    family_id = int(parts[3])
    plan_key = parts[4]
    lang = get_user_lang(call.from_user.id)
    bot.answer_callback_query(call.id)

    if provider_key not in PROVIDERS or plan_key not in PLANS:
        return

    provider = PROVIDERS[provider_key]
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
            chat_id=call.message.chat.id,
            title=title,
            description=description,
            invoice_payload=f"{family_id}:{plan_key}:{plan['months']}",
            provider_token=provider['token'],
            currency='UZS',
            prices=prices,
            start_parameter=f"sub_{plan_key}",
            is_flexible=False,
        )
    except Exception as e:
        logger.error(f"Failed to send invoice ({provider_key}): {e}")
        bot.send_message(call.message.chat.id, t("sub_invoice_error", lang))


# ═══════════════════════════════════════════
#  Ручной перевод на карту
# ═══════════════════════════════════════════

@bot.callback_query_handler(func=lambda call: call.data.startswith('sub_card_'))
def callback_card_transfer(call):
    """Показывает реквизиты для ручного перевода."""
    parts = call.data.split('_')
    family_id = int(parts[2])
    plan_key = parts[3]
    lang = get_user_lang(call.from_user.id)
    bot.answer_callback_query(call.id)

    if plan_key not in PLANS:
        return

    plan = PLANS[plan_key]

    text = t("sub_card_instructions", lang,
             amount=plan['amount_display'],
             card_number=CARD_NUMBER,
             card_holder=CARD_HOLDER or t("sub_card_holder_default", lang))

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton(
        t("sub_card_done_btn", lang),
        callback_data=f"sub_card_done_{family_id}_{plan_key}"))
    markup.add(types.InlineKeyboardButton(
        t("family_back", lang), callback_data="sub_start_buy"))

    bot.edit_message_text(
        text,
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=markup,
        parse_mode='HTML'
    )


@bot.callback_query_handler(func=lambda call: call.data.startswith('sub_card_done_'))
def callback_card_done(call):
    """Пользователь нажал 'Я оплатил' — уведомляем админа."""
    parts = call.data.split('_')
    family_id = int(parts[3])
    plan_key = parts[4]
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    bot.answer_callback_query(call.id)

    if plan_key not in PLANS:
        return

    plan = PLANS[plan_key]

    # Уведомляем админа
    admin_id = os.environ.get("ADMIN_ID")
    if admin_id:
        from src.database_manager import get_parent_by_telegram
        parent = get_parent_by_telegram(user_id)
        parent_name = parent['fio'] if parent else f"TG:{user_id}"

        admin_text = t("sub_card_admin_notify", lang,
                        parent=parent_name,
                        family_id=family_id,
                        plan=f"{plan['months']} мес",
                        amount=plan['amount_display'])

        admin_markup = types.InlineKeyboardMarkup()
        admin_markup.add(types.InlineKeyboardButton(
            "✅ Подтвердить оплату",
            callback_data=f"sub_card_confirm_{family_id}_{plan_key}_{plan['months']}"))
        admin_markup.add(types.InlineKeyboardButton(
            "❌ Отклонить",
            callback_data=f"sub_card_reject_{user_id}"))

        try:
            bot.send_message(int(admin_id), admin_text,
                             parse_mode='HTML', reply_markup=admin_markup)
        except Exception as e:
            logger.error(f"Failed to notify admin about card payment: {e}")

    # Подтверждение пользователю
    bot.edit_message_text(
        t("sub_card_pending", lang),
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        parse_mode='HTML'
    )


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
    except Exception:
        pass


@bot.callback_query_handler(func=lambda call: call.data.startswith('sub_contact_admin_'))
def callback_contact_admin(call):
    """Нет провайдеров — перенаправляем в поддержку."""
    lang = get_user_lang(call.from_user.id)
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, t("sub_contact_admin_text", lang))


# ═══════════════════════════════════════════
#  Telegram Payments: Pre-checkout + Success
# ═══════════════════════════════════════════

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

    extend_subscription(family_id, months)

    send_content(user_id, t("sub_payment_success", lang, months=months))
    logger.info(f"Payment successful: family={family_id}, plan={plan_key}, "
                f"months={months}, user={user_id}")


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
        sub_end = sub['subscription_end'][:10] if sub and sub.get('subscription_end') else "—"
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


def _notify_family_about_subscription(family_id: int, months: int):
    """Уведомляет всех членов семьи о том, что подписка активирована."""
    from src.database_manager import get_family_members_telegram_ids, get_family_subscription
    sub = get_family_subscription(family_id)
    sub_end = sub['subscription_end'][:10] if sub and sub.get('subscription_end') else "?"

    tg_ids = get_family_members_telegram_ids(family_id)
    for tg_id in tg_ids:
        try:
            user_lang = get_user_lang(tg_id)
            bot.send_message(
                tg_id,
                t("sub_activated_notify", user_lang, months=months, end=sub_end),
                parse_mode='HTML'
            )
        except Exception as e:
            logger.debug(f"Could not notify {tg_id} about subscription: {e}")
