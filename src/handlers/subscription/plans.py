"""Тарифные планы и админ-команда /set_prices."""
import copy
import logging
from telebot import types
from src.bot_instance import bot
from src.i18n import t
from src.ui import send_content
from src.database_manager import get_plans_from_db, save_plans_to_db, get_user_lang

logger = logging.getLogger(__name__)


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
    """Возвращает актуальные тарифы из БД или дефолтные.

    Возвращает deepcopy DEFAULT_PLANS, а не сам объект: `_process_price_input`
    делает `plans[k]['amount'] = ...` in-place перед save_plans_to_db — без
    копии это мутировало бы глобальный дефолт (и следующий вызов без БД-записи
    отдал бы испорченные цены)."""
    db_plans = get_plans_from_db()
    return db_plans if db_plans else copy.deepcopy(DEFAULT_PLANS)


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

    from src.database_manager import set_user_state
    set_user_state(user_id, "awaiting_admin_price", plan_key)
    bot.send_message(
        call.message.chat.id,
        t("admin_prices_enter", lang, plan=plan_key),
        parse_mode='HTML'
    )


def _process_price_input(message, plan_key):
    """Обрабатывает ввод новой цены. State awaiting_admin_price → clear."""
    from src.database_manager import clear_user_state
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    clear_user_state(user_id)

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
