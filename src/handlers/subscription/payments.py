"""Денежный путь: Telegram Payments (Click/Payme/Stars), invoice, pre-checkout,
successful_payment, ручной перевод на карту (сторона пользователя).

Путь атомарен и идемпотентен (PR-B): record_payment + extend_subscription в одной
транзакции, UNIQUE charge_id ловит дубли, admin-алерт + рефанд Stars при сбое."""
import os
import logging
from telebot import types
from src.bot_instance import bot
from src.i18n import t
from src.ui import send_content
from src.db.connection import UniqueViolation, get_db_connection
from src.database_manager import (
    get_user_lang, get_parent_id_by_telegram, get_family_subscription,
    record_payment, extend_subscription,
)
from .plans import get_plans
from ._common import (
    PROVIDERS, HAS_CARD_TRANSFER, CARD_NUMBER, CARD_HOLDER,
    _check_user_can_pay_for_family,
)

logger = logging.getLogger(__name__)


def _alert_admin_payment(text: str):
    """Best-effort алерт админу по денежному пути (деньги уже у Telegram)."""
    try:
        from src.notifications import NotificationType, get_sender
        get_sender().send_to_admin(text, ntype=NotificationType.PAYMENT_SUCCESS)
    except Exception as e:
        logger.error(f"Admin payment alert failed: {e}")


def _maybe_refund_stars(payment, user_id: int):
    """Возврат Telegram Stars при неудачной обработке (только currency=XTR).

    UZS-платежи (Click/Payme) через provider — рефанд руками провайдера, не API.
    """
    if getattr(payment, 'currency', None) != 'XTR':
        return
    charge_id = getattr(payment, 'telegram_payment_charge_id', None)
    if not charge_id:
        return
    try:
        bot.refund_star_payment(user_id, charge_id)
        logger.info(f"Refunded star payment charge={charge_id} user={user_id}")
    except Exception as e:
        logger.error(f"Star refund failed charge={charge_id} user={user_id}: {e}")


def _notify_payment_success(user_id: int, lang: str, months: int, family_id: int):
    """Уведомление юзеру об активной подписке — best-effort (деньги уже списаны,
    подписка уже активна). Сбой здесь НЕ откатывает транзакцию."""
    try:
        send_content(user_id, t("sub_payment_success", lang, months=months))
    except Exception as e:
        logger.error(
            f"Payment notify failed for user={user_id} family={family_id}: {e}. "
            f"Subscription is active despite notification failure."
        )
        _alert_admin_payment(
            f"⚠️ <b>Payment notify failed</b>\n\n"
            f"family={family_id} user={user_id} months={months}\n"
            f"Subscription активирована, но юзер не получил подтверждение.\n"
            f"Err: <code>{e}</code>"
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
    """Обработка успешного платежа — активация подписки.

    Деньги УЖЕ у Telegram → любой сбой в денежном пути обязан оставить след
    (admin alert) и по возможности рефанд (Stars). Ключевые гарантии:
      • payload парсится защищённо (3 части, int в try) — иначе алерт + рефанд;
      • record_payment + extend_subscription — ОДНА транзакция (атомарность);
      • дубль charge_id (повторная доставка) ловится UniqueViolation → no-op,
        подписка не двоится;
      • paid_by=NULL допустим (payer без строки parents) — аудит не теряется.
    """
    payment = message.successful_payment
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    # ── 1. Защищённый парсинг server-controlled payload ──
    payload = payment.invoice_payload or ""
    parts = payload.split(':')
    try:
        if len(parts) != 3:
            raise ValueError(f"expected 3 parts, got {len(parts)}")
        family_id = int(parts[0])
        plan_key = parts[1]
        months = int(parts[2])
    except (ValueError, TypeError) as e:
        logger.error(
            f"Malformed invoice_payload {payload!r} from user={user_id}: {e}. "
            f"Money taken by Telegram — alerting admin + refund (if Stars)."
        )
        _alert_admin_payment(
            f"🔴 <b>Malformed payment payload</b>\n\n"
            f"user={user_id} payload=<code>{payload}</code>\n"
            f"Деньги списаны Telegram, подписка НЕ активирована.\n"
            f"Err: <code>{e}</code>"
        )
        _maybe_refund_stars(payment, user_id)
        return

    # ── 2. Резолвим плательщика (может отсутствовать в parents) ──
    parent_id = get_parent_id_by_telegram(user_id)
    if parent_id is None:
        logger.warning(
            f"Payer user={user_id} has no parents row — recording payment with "
            f"paid_by=NULL (family={family_id})."
        )
        _alert_admin_payment(
            f"⚠️ <b>Payment from unregistered payer</b>\n\n"
            f"user={user_id} family={family_id} months={months}\n"
            f"Записываем платёж с paid_by=NULL, подписка активируется."
        )

    # ── 3. Денежный путь: record_payment + extend_subscription в ОДНОЙ транзакции ──
    try:
        with get_db_connection() as conn:
            record_payment(
                family_id=family_id,
                paid_by_parent_id=parent_id,
                amount=payment.total_amount,
                currency=payment.currency,
                plan=plan_key,
                months=months,
                telegram_charge_id=payment.telegram_payment_charge_id,
                provider_charge_id=payment.provider_payment_charge_id,
                conn=conn,
            )
            extend_subscription(family_id, months, conn=conn)
    except UniqueViolation:
        # Дубль telegram_payment_charge_id → повторная доставка того же
        # successful_payment. Подписка уже активирована первой доставкой —
        # НЕ продлеваем повторно. Юзеру шлём подтверждение идемпотентно.
        logger.info(
            f"Duplicate successful_payment ignored: "
            f"charge={payment.telegram_payment_charge_id} family={family_id} user={user_id}"
        )
        _notify_payment_success(user_id, lang, months, family_id)
        return
    except Exception as e:
        logger.exception(
            f"Payment DB path failed: family={family_id} user={user_id} "
            f"charge={payment.telegram_payment_charge_id}: {e}. "
            f"Money taken by Telegram — alerting admin + refund (if Stars)."
        )
        _alert_admin_payment(
            f"🔴 <b>Payment DB failure — money at risk</b>\n\n"
            f"family={family_id} user={user_id} months={months}\n"
            f"charge=<code>{payment.telegram_payment_charge_id}</code>\n"
            f"Деньги списаны, подписка НЕ активирована. Требуется ручная проверка.\n"
            f"Err: <code>{e}</code>"
        )
        _maybe_refund_stars(payment, user_id)
        return

    logger.info(f"Payment successful: family={family_id}, plan={plan_key}, "
                f"months={months}, user={user_id}")

    _notify_payment_success(user_id, lang, months, family_id)
