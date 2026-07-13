"""Промокоды: ввод и применение (сторона пользователя) + админ-управление
(/promo, создание/список/удаление).

IDOR-guard (PR-C/S1): callback_promo_apply зовёт _check_user_can_pay_for_family;
_apply_promo_to_family дублирует проверку членства (defense-in-depth) — денежный путь."""
import logging
from telebot import types
from src.bot_instance import bot
from src.i18n import t
from src.ui import send_content
from src.db.connection import get_db_connection
from src.rate_limiter import is_rate_limited
from src.database_manager import (
    get_user_lang, get_families_for_user, is_subscription_active,
    get_promo_code, use_promo_code, get_parent_id_by_telegram,
    extend_subscription, record_payment,
)
from .plans import get_plans
from ._common import _notify_family_about_subscription, _check_user_can_pay_for_family

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════
#  Промокод: ввод и применение
# ═══════════════════════════════════════════

@bot.callback_query_handler(func=lambda call: call.data == 'sub_enter_promo')
def callback_enter_promo(call):
    """Пользователь хочет ввести промокод."""
    user_id = call.from_user.id
    lang = get_user_lang(user_id)
    bot.answer_callback_query(call.id)

    from src.database_manager import set_user_state
    set_user_state(user_id, "awaiting_promo_code", "")
    bot.send_message(
        call.message.chat.id,
        t("sub_promo_enter", lang),
        parse_mode='HTML'
    )


def _process_promo_code(message):
    """Обрабатывает введённый промокод. State awaiting_promo_code → clear."""
    from src.database_manager import clear_user_state
    user_id = message.chat.id
    lang = get_user_lang(user_id)

    # S3: троттлинг ввода промокода — защита от перебора кодов.
    # Состояние НЕ чистим, чтобы юзер мог повторить после паузы.
    if is_rate_limited(user_id):
        send_content(user_id, t("rate_limited", lang))
        return

    code = message.text.strip() if message.text else ""
    clear_user_state(user_id)

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
    # callback_data: sub_promo_apply_{family_id}_{code}. Код может содержать
    # '_' → берём family_id из parts[3], а код — остаток после 4-го '_'.
    parts = call.data.split('_')
    if len(parts) < 5:
        bot.answer_callback_query(call.id)
        return
    try:
        family_id = int(parts[3])
    except ValueError:
        bot.answer_callback_query(call.id)
        return
    code = '_'.join(parts[4:])
    user_id = call.from_user.id
    lang = get_user_lang(user_id)

    # S1 (IDOR): тот же гейт, что у платёжных путей sub_pay_/sub_fam_.
    # Без него crafted callback продлевал чужую подписку и жёг max_uses промо.
    # _check_user_can_pay_for_family сам отвечает alert'ом при отказе.
    if not _check_user_can_pay_for_family(call, family_id):
        return

    bot.answer_callback_query(call.id)
    _apply_promo_to_family(user_id, family_id, get_promo_code(code), lang)


def _apply_promo_to_family(user_id: int, family_id: int, promo: dict, lang: str):
    """Применяет промокод с бесплатными месяцами к семье.

    Порядок критичен: СНАЧАЛА занимаем слот промокода (атомарный guard
    `use_promo_code` под `WHERE used_count < max_uses`), и ТОЛЬКО при успехе —
    продлеваем подписку + пишем платёж. Иначе гонка при max_uses=1 давала
    двойное начисление. Всё в ОДНОЙ транзакции — при сбое откатывается и слот."""
    if not promo:
        send_content(user_id, t("sub_promo_invalid", lang))
        return

    # S1 (IDOR) defense-in-depth: даже если сюда попали в обход callback-гейта,
    # применить промо можно только к своей семье (или админом). Оба вызова
    # выше уже проверены, но эта функция трогает деньги — гейт обязателен.
    from src.db.auth import is_member_of_family, get_parent_role
    if get_parent_role(user_id) != 'admin' and not is_member_of_family(user_id, family_id):
        logger.warning(
            f"Blocked promo IDOR: user={user_id} not member of family={family_id}"
        )
        send_content(user_id, t("admin_no_access", lang))
        return

    months = promo['free_months']
    code = promo['code']
    parent_id = get_parent_id_by_telegram(user_id)

    try:
        with get_db_connection() as conn:
            # Занимаем слот ДО начисления. Если исчерпан (гонка/повтор) — стоп.
            if not use_promo_code(code, conn=conn):
                send_content(user_id, t("sub_promo_invalid", lang))
                return
            extend_subscription(family_id, months, conn=conn)
            record_payment(
                family_id=family_id,
                paid_by_parent_id=parent_id,
                amount=0,
                currency='UZS',
                plan=f'promo_{code}',
                months=months,
                conn=conn,
            )
    except Exception as e:
        logger.exception(f"Promo apply failed code={code} family={family_id} user={user_id}: {e}")
        send_content(user_id, t("sub_promo_invalid", lang))
        return

    _notify_family_about_subscription(family_id, months)
    send_content(user_id, t("sub_promo_success", lang, months=months, code=code))
    logger.info(f"Promo {code} applied: family={family_id}, months={months}, user={user_id}")


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
    from src.database_manager import set_user_state
    set_user_state(user_id, "awaiting_promo_free", "")
    bot.send_message(
        call.message.chat.id,
        t("admin_promo_enter_free", lang),
        parse_mode='HTML'
    )


def _process_promo_free(message):
    """Обработка: <months> <max_uses> [days_valid]. State awaiting_promo_free → clear."""
    from src.database_manager import create_promo_code, clear_user_state
    import secrets
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    clear_user_state(user_id)

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
    from src.database_manager import set_user_state
    set_user_state(user_id, "awaiting_promo_discount", "")
    bot.send_message(
        call.message.chat.id,
        t("admin_promo_enter_discount", lang),
        parse_mode='HTML'
    )


def _process_promo_discount(message):
    """Обработка: <plan> <percent> <max_uses> [days_valid]. State awaiting_promo_discount → clear."""
    from src.database_manager import create_promo_code, clear_user_state
    import secrets
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    clear_user_state(user_id)

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
