"""Общие для пакета подписок: конфигурация провайдеров, авторизация платёжных
callback'ов и уведомление семьи об активации.

Leaf-модуль — не импортирует другие submodule'ы пакета (нет циклов)."""
import os
import logging
from telebot import types
from src.bot_instance import bot
from src.i18n import t
from src.utils import to_date_str
from src.database_manager import (
    get_user_lang, get_family_subscription, get_family_members_telegram_ids,
)

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


def _notify_family_about_subscription(family_id: int, months: int):
    """Уведомляет всех членов семьи о том, что подписка активирована."""
    sub = get_family_subscription(family_id)
    sub_end = to_date_str(sub['subscription_end']) if sub and sub.get('subscription_end') else "?"

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
