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
    get_family_subscription, is_head_of_any_family,
    get_plans_from_db, save_plans_to_db,
    get_promo_code, use_promo_code, cancel_subscription,
    get_family_members_telegram_ids,
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
# Telegram Stars: включается флагом без provider_token (Stars — внутренняя валюта).
STARS_ENABLED = os.environ.get("STARS_ENABLED", "").lower() in ("1", "true", "yes")

# Собираем доступные способы оплаты
PROVIDERS = {}
if CLICK_TOKEN:
    PROVIDERS['click'] = {'token': CLICK_TOKEN, 'label': '💳 Click', 'currency': 'UZS'}
if PAYME_TOKEN:
    PROVIDERS['payme'] = {'token': PAYME_TOKEN, 'label': '💳 Payme', 'currency': 'UZS'}
if not PROVIDERS and LEGACY_TOKEN:
    # Если новые токены не заданы, но есть старый — используем его
    PROVIDERS['payment'] = {'token': LEGACY_TOKEN, 'label': '💳 Оплатить', 'currency': 'UZS'}
# Stars — отдельная ветка: без provider_token, currency=XTR. Подходит для зарубежных
# пользователей (диаспора), у которых нет узбекских платёжных систем.
if STARS_ENABLED:
    PROVIDERS['stars'] = {'token': '', 'label': '⭐ Telegram Stars', 'currency': 'XTR'}
HAS_CARD_TRANSFER = bool(CARD_NUMBER)
HAS_ANY_PAYMENT = bool(PROVIDERS) or HAS_CARD_TRANSFER

# Тарифные планы по умолчанию (суммы в тийинах — 1 UZS = 100 тийин для Telegram Payments API).
# stars_amount — стоимость в Stars (целое число). Курс прикинут под 29 900 UZS ≈ $2.4 ≈ 100 ⭐
# (1 ⭐ ≈ $0.013–0.024). Админ может править через таблицу settings.
DEFAULT_PLANS = {
    'monthly': {
        'months': 1,
        'amount': 29900_00,
        'amount_display': '29 900',
        'stars_amount': 100,
    },
    'quarterly': {
        'months': 3,
        'amount': 79900_00,
        'amount_display': '79 900',
        'stars_amount': 270,
    },
    'yearly': {
        'months': 12,
        'amount': 249900_00,
        'amount_display': '249 900',
        'stars_amount': 850,
    },
}


def get_plans() -> dict:
    """Возвращает актуальные тарифы из БД или дефолтные."""
    db_plans = get_plans_from_db()
    return db_plans if db_plans else DEFAULT_PLANS


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

def _check_user_can_pay_for_family(call: types.CallbackQuery, family_id: int) -> bool:
    """Проверяет, что пользователь — член семьи или админ.
    Иначе отвечает alert и возвращает False."""
    from src.db.auth import is_member_of_family, get_parent_role
    user_id = call.from_user.id
    if get_parent_role(user_id) == 'admin' or is_member_of_family(user_id, family_id):
        return True
    lang = get_user_lang(user_id)
    bot.answer_callback_query(call.id, t("admin_no_access", lang), show_alert=True)
    logger.warning(
        f"Unauthorized payment callback: user={user_id} data={call.data} family_id={family_id}"
    )
    return False


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


# ═══════════════════════════════════════════
#  Оплата через Click / Payme (Telegram Payments)
# ═══════════════════════════════════════════

@bot.callback_query_handler(func=lambda call: call.data.startswith('sub_pay_'))
def callback_pay_via_provider(call):
    """Пользователь выбрал Click/Payme → отправляем Invoice."""
    parts = call.data.split('_')
    # sub_pay_click_3_monthly → ['sub', 'pay', 'click', '3', 'monthly']
    if len(parts) < 5:
        return
    provider_key = parts[2]
    try:
        family_id = int(parts[3])
    except ValueError:
        return
    plan_key = parts[4]
    if not _check_user_can_pay_for_family(call, family_id):
        return
    lang = get_user_lang(call.from_user.id)
    bot.answer_callback_query(call.id)

    plans = get_plans()
    if provider_key not in PROVIDERS or plan_key not in plans:
        return

    provider = PROVIDERS[provider_key]
    plan = plans[plan_key]

    title = t("sub_invoice_title", lang)

    # Telegram Stars — отдельная ветка: currency=XTR, без provider_token,
    # сумма указывается в Stars (без умножения на 100), сумма берётся из stars_amount плана.
    if provider_key == 'stars':
        stars_amount = plan.get('stars_amount')
        if not stars_amount:
            logger.error(f"Plan {plan_key} has no stars_amount configured")
            bot.send_message(call.message.chat.id, t("sub_invoice_error", lang))
            return
        description = t("sub_invoice_desc_stars", lang,
                        months=plan['months'], amount=stars_amount)
        prices = [types.LabeledPrice(
            label=t(f"sub_plan_{plan_key}_stars", lang, amount=stars_amount),
            amount=stars_amount,
        )]
        try:
            bot.send_invoice(
                chat_id=call.message.chat.id,
                title=title,
                description=description,
                invoice_payload=f"{family_id}:{plan_key}:{plan['months']}",
                provider_token="",  # Stars не использует provider_token
                currency='XTR',
                prices=prices,
                start_parameter=f"sub_{plan_key}_stars",
                is_flexible=False,
            )
        except Exception as e:
            logger.error(f"Failed to send Stars invoice: {e}")
            bot.send_message(call.message.chat.id, t("sub_invoice_error", lang))
        return

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
            currency=provider.get('currency', 'UZS'),
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

@bot.callback_query_handler(func=lambda call: call.data.startswith('sub_card_') and not call.data.startswith('sub_card_done_') and not call.data.startswith('sub_card_confirm_') and not call.data.startswith('sub_card_reject_'))
def callback_card_transfer(call):
    """Показывает реквизиты для ручного перевода."""
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

    plans = get_plans()
    if plan_key not in plans:
        return

    plan = plans[plan_key]

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
    if len(parts) < 5:
        return
    try:
        family_id = int(parts[3])
    except ValueError:
        return
    plan_key = parts[4]
    if not _check_user_can_pay_for_family(call, family_id):
        return
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    bot.answer_callback_query(call.id)

    plans = get_plans()
    if plan_key not in plans:
        return

    plan = plans[plan_key]

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
    except Exception as e:
        logger.debug(f"Could not send card rejection to user {user_id}: {e}")


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


# ═══════════════════════════════════════════
#  Промокод: ввод и применение
# ═══════════════════════════════════════════

@bot.callback_query_handler(func=lambda call: call.data == 'sub_enter_promo')
def callback_enter_promo(call):
    """Пользователь хочет ввести промокод."""
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    bot.answer_callback_query(call.id)

    msg = bot.send_message(
        call.message.chat.id,
        t("sub_promo_enter", lang),
        parse_mode='HTML'
    )
    bot.register_next_step_handler(msg, _process_promo_code)


def _process_promo_code(message):
    """Обрабатывает введённый промокод."""
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    code = message.text.strip() if message.text else ""

    if not code:
        send_content(user_id, t("sub_promo_invalid", lang))
        return

    promo = get_promo_code(code)
    if not promo:
        send_content(user_id, t("sub_promo_invalid", lang))
        return

    families = get_families_for_user(user_id)
    if not families:
        send_content(user_id, t("sub_no_family", lang))
        return

    # Если промокод даёт бесплатные месяцы
    if promo['free_months'] > 0:
        if len(families) == 1:
            _apply_promo_to_family(user_id, families[0]['id'], promo, lang)
        else:
            markup = types.InlineKeyboardMarkup()
            for fam in families:
                active = is_subscription_active(fam['id'])
                status = "✅" if active else "❌"
                markup.add(types.InlineKeyboardButton(
                    f"{status} {fam['family_name']}",
                    callback_data=f"sub_promo_apply_{fam['id']}_{promo['code']}"))
            bot.send_message(user_id, t("sub_select_family", lang),
                             reply_markup=markup, parse_mode='HTML')
    else:
        # Промокод со скидкой — показываем тарифы с учётом скидки
        plans = get_plans()
        plan_key = promo['plan']
        if plan_key != 'all' and plan_key in plans:
            plan = plans[plan_key]
            discount = promo['discount_percent']
            new_amount = int(plan['amount'] * (100 - discount) / 100)
            new_display = f"{new_amount // 100:,}".replace(',', ' ')
            bot.send_message(
                user_id,
                t("sub_promo_discount", lang,
                  code=promo['code'], discount=discount,
                  plan=t(f"sub_plan_{plan_key}", lang, amount=new_display)),
                parse_mode='HTML')
        else:
            send_content(user_id, t("sub_promo_applied_info", lang,
                                     code=promo['code'],
                                     discount=promo['discount_percent']))


@bot.callback_query_handler(func=lambda call: call.data.startswith('sub_promo_apply_'))
def callback_promo_apply(call):
    """Применение промокода к семье."""
    parts = call.data.split('_')
    family_id = int(parts[3])
    code = parts[4]
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    bot.answer_callback_query(call.id)
    _apply_promo_to_family(user_id, family_id, get_promo_code(code), lang)


def _apply_promo_to_family(user_id: int, family_id: int, promo: dict, lang: str):
    """Применяет промокод с бесплатными месяцами к семье."""
    if not promo:
        send_content(user_id, t("sub_promo_invalid", lang))
        return

    months = promo['free_months']
    extend_subscription(family_id, months)
    use_promo_code(promo['code'])

    parent_id = get_parent_id_by_telegram(user_id)
    record_payment(
        family_id=family_id,
        paid_by_parent_id=parent_id,
        amount=0,
        currency='UZS',
        plan=f'promo_{promo["code"]}',
        months=months,
    )

    _notify_family_about_subscription(family_id, months)
    send_content(user_id, t("sub_promo_success", lang, months=months, code=promo['code']))
    logger.info(f"Promo {promo['code']} applied: family={family_id}, months={months}, user={user_id}")


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
        sub_end = sub['subscription_end'][:10] if sub and sub.get('subscription_end') else "—"
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


# ═══════════════════════════════════════════
#  АДМИН: /set_prices — управление тарифами
# ═══════════════════════════════════════════

@bot.message_handler(commands=['set_prices'])
def cmd_set_prices(message):
    """Админ-команда: /set_prices — просмотр и изменение тарифов."""
    from src.database_manager import get_parent_role
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        return

    plans = get_plans()
    lines = [t("admin_prices_title", lang)]
    for key, plan in plans.items():
        lines.append(f"  <b>{key}</b>: {plan['amount_display']} сум ({plan['months']} мес.)")

    markup = types.InlineKeyboardMarkup(row_width=1)
    for key in plans:
        markup.add(types.InlineKeyboardButton(
            f"✏️ {key}", callback_data=f"setprice_{key}"))

    bot.send_message(user_id, "\n".join(lines), parse_mode='HTML', reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith('setprice_'))
def callback_set_price(call):
    """Админ выбрал тариф для редактирования."""
    from src.database_manager import get_parent_role
    user_id = call.from_user.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        bot.answer_callback_query(call.id)
        return

    plan_key = call.data.replace('setprice_', '')
    bot.answer_callback_query(call.id)

    msg = bot.send_message(
        call.message.chat.id,
        t("admin_prices_enter", lang, plan=plan_key),
        parse_mode='HTML'
    )
    bot.register_next_step_handler(msg, _process_price_input, plan_key)


def _process_price_input(message, plan_key):
    """Обрабатывает ввод новой цены."""
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    try:
        raw = message.text.strip().replace(' ', '').replace(',', '')
        amount_uzs = int(raw)
        if amount_uzs <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        send_content(user_id, t("admin_prices_invalid", lang))
        return

    plans = get_plans()
    if plan_key not in plans:
        send_content(user_id, t("admin_prices_invalid", lang))
        return

    plans[plan_key]['amount'] = amount_uzs * 100  # UZS -> tiyins
    plans[plan_key]['amount_display'] = f"{amount_uzs:,}".replace(',', ' ')
    save_plans_to_db(plans)

    send_content(user_id, t("admin_prices_updated", lang,
                              plan=plan_key,
                              amount=plans[plan_key]['amount_display']))
    logger.info(f"Admin {user_id} updated price for {plan_key} to {amount_uzs} UZS")


# ═══════════════════════════════════════════
#  АДМИН: /promo — управление промокодами
# ═══════════════════════════════════════════

@bot.message_handler(commands=['promo'])
def cmd_promo(message):
    """Админ-команда: /promo — управление промокодами."""
    from src.database_manager import get_parent_role, list_promo_codes
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        return

    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton(
        t("admin_promo_create_btn", lang), callback_data="promo_create"))
    markup.add(types.InlineKeyboardButton(
        t("admin_promo_list_btn", lang), callback_data="promo_list"))

    bot.send_message(user_id, t("admin_promo_title", lang),
                     parse_mode='HTML', reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == 'promo_list')
def callback_promo_list(call):
    """Показывает список промокодов."""
    from src.database_manager import get_parent_role, list_promo_codes
    user_id = call.from_user.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)
    codes = list_promo_codes()

    if not codes:
        bot.send_message(user_id, t("admin_promo_empty", lang))
        return

    lines = [t("admin_promo_list_title", lang)]
    markup = types.InlineKeyboardMarkup(row_width=1)
    for p in codes:
        expired = "⏰" if p.get('expires_at') else "♾"
        if p['free_months'] > 0:
            effect = f"+{p['free_months']} мес"
        else:
            effect = f"-{p['discount_percent']}%"
        lines.append(
            f"<code>{p['code']}</code> | {effect} | {p['used_count']}/{p['max_uses']} {expired}"
        )
        markup.add(types.InlineKeyboardButton(
            f"🗑 {p['code']}", callback_data=f"promo_del_{p['code']}"))

    bot.send_message(user_id, "\n".join(lines), parse_mode='HTML', reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith('promo_del_'))
def callback_promo_delete(call):
    """Удаление промокода."""
    from src.database_manager import get_parent_role, delete_promo_code
    user_id = call.from_user.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        bot.answer_callback_query(call.id)
        return

    code = call.data.replace('promo_del_', '')
    delete_promo_code(code)
    bot.answer_callback_query(call.id, t("admin_promo_deleted", lang, code=code), show_alert=True)

    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception as e:
        logger.debug(f"Could not delete promo message: {e}")


@bot.callback_query_handler(func=lambda call: call.data == 'promo_create')
def callback_promo_create(call):
    """Начинает создание промокода."""
    from src.database_manager import get_parent_role
    user_id = call.from_user.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(types.InlineKeyboardButton(
        t("admin_promo_type_free", lang), callback_data="promo_new_free"))
    markup.add(types.InlineKeyboardButton(
        t("admin_promo_type_discount", lang), callback_data="promo_new_discount"))

    bot.send_message(user_id, t("admin_promo_choose_type", lang),
                     parse_mode='HTML', reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == 'promo_new_free')
def callback_promo_new_free(call):
    """Создание промокода с бесплатными месяцами."""
    from src.database_manager import get_parent_role
    user_id = call.from_user.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        t("admin_promo_enter_free", lang),
        parse_mode='HTML'
    )
    bot.register_next_step_handler(msg, _process_promo_free)


def _process_promo_free(message):
    """Обработка: <months> <max_uses> [days_valid]"""
    from src.database_manager import create_promo_code
    import secrets
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    try:
        parts = message.text.strip().split()
        months = int(parts[0])
        max_uses = int(parts[1]) if len(parts) > 1 else 1
        expires_days = int(parts[2]) if len(parts) > 2 else None
        if months <= 0 or max_uses <= 0:
            raise ValueError
    except (ValueError, AttributeError, IndexError):
        send_content(user_id, t("admin_promo_input_error", lang))
        return

    code = secrets.token_hex(4).upper()
    ok = create_promo_code(code, plan='all', free_months=months,
                            max_uses=max_uses, expires_days=expires_days)
    if ok:
        send_content(user_id, t("admin_promo_created", lang,
                                  code=code, effect=f"+{months} мес",
                                  max_uses=max_uses))
    else:
        send_content(user_id, t("admin_promo_input_error", lang))


@bot.callback_query_handler(func=lambda call: call.data == 'promo_new_discount')
def callback_promo_new_discount(call):
    """Создание промокода со скидкой."""
    from src.database_manager import get_parent_role
    user_id = call.from_user.id
    lang = get_user_lang(user_id)

    if get_parent_role(user_id) != 'admin':
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id)
    msg = bot.send_message(
        call.message.chat.id,
        t("admin_promo_enter_discount", lang),
        parse_mode='HTML'
    )
    bot.register_next_step_handler(msg, _process_promo_discount)


def _process_promo_discount(message):
    """Обработка: <plan> <percent> <max_uses> [days_valid]"""
    from src.database_manager import create_promo_code
    import secrets
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    try:
        parts = message.text.strip().split()
        plan = parts[0]
        percent = int(parts[1])
        max_uses = int(parts[2]) if len(parts) > 2 else 1
        expires_days = int(parts[3]) if len(parts) > 3 else None
        if percent <= 0 or percent > 100 or max_uses <= 0:
            raise ValueError
    except (ValueError, AttributeError, IndexError):
        send_content(user_id, t("admin_promo_input_error", lang))
        return

    code = secrets.token_hex(4).upper()
    ok = create_promo_code(code, plan=plan, discount_percent=percent,
                            max_uses=max_uses, expires_days=expires_days)
    if ok:
        send_content(user_id, t("admin_promo_created", lang,
                                  code=code, effect=f"-{percent}%",
                                  max_uses=max_uses))
    else:
        send_content(user_id, t("admin_promo_input_error", lang))


def _notify_family_about_subscription(family_id: int, months: int):
    """Уведомляет всех членов семьи о том, что подписка активирована."""
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
