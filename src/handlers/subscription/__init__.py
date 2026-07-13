"""Обработчик подписок и платежей (пакет).

Поддержка: Click / Payme (Telegram Payments API) + Telegram Stars + ручной
перевод на карту. Разбит из god-файла на доменные модули (PR-M2):

    _common — конфиг провайдеров, авторизация платёжных callback'ов, уведомление семьи
    plans   — тарифы (DEFAULT_PLANS/get_plans) и /set_prices
    ui      — экраны статуса/выбора тарифа/способа оплаты
    payments— денежный путь: invoice / pre-checkout / successful_payment / карта
    promo   — промокоды (ввод/применение/IDOR-guard, админ /promo)
    grant   — админ-выдача: /grant_sub, /cancel_sub, подтверждение оплаты по карте

Публичный API сохранён через re-export ниже: `from src.handlers.subscription import
cmd_subscription` (и все прочие имена) работают как раньше. Импорт пакета
регистрирует все @bot-хендлеры (submodule'ы импортируются здесь)."""

# ── Регистрация хендлеров: leaf-модули (_common, plans) первыми, затем остальные.
#    Импорт submodule'а исполняет @bot.*_handler декораторы → хендлеры регистрируются.
from . import _common   # noqa: F401
from . import plans      # noqa: F401
from . import ui         # noqa: F401
from . import payments   # noqa: F401
from . import promo      # noqa: F401
from . import grant      # noqa: F401

# ── Re-export публичного API (обратная совместимость импортов) ──
from ._common import (  # noqa: F401
    PROVIDERS, HAS_CARD_TRANSFER, HAS_ANY_PAYMENT,
    CARD_NUMBER, CARD_HOLDER, STARS_ENABLED,
    CLICK_TOKEN, PAYME_TOKEN, LEGACY_TOKEN,
    _check_user_can_pay_for_family, _notify_family_about_subscription,
)
from .plans import (  # noqa: F401
    DEFAULT_PLANS, get_plans, cmd_set_prices, callback_set_price, _process_price_input,
)
from .ui import (  # noqa: F401
    cmd_subscription, callback_sub_start_buy, callback_sub_back_status,
    callback_sub_plan, callback_select_family_for_sub, _show_payment_methods,
)
from .payments import (  # noqa: F401
    _alert_admin_payment, _maybe_refund_stars, _notify_payment_success,
    callback_pay_via_provider, callback_card_transfer, callback_card_done,
    callback_contact_admin, handle_pre_checkout, handle_successful_payment,
)
from .promo import (  # noqa: F401
    callback_enter_promo, _process_promo_code, callback_promo_apply,
    _apply_promo_to_family, cmd_promo, callback_promo_list, callback_promo_delete,
    callback_promo_create, callback_promo_new_free, _process_promo_free,
    callback_promo_new_discount, _process_promo_discount,
)
from .grant import (  # noqa: F401
    callback_admin_confirm_card, callback_admin_reject_card,
    cmd_grant_subscription, _show_duration_picker, callback_admin_sub_from_menu,
    callback_grant_select_family, callback_grant_execute, _execute_grant,
    cmd_cancel_sub, callback_cancel_sub_confirm, callback_cancel_sub_execute,
    callback_cancel_sub_back, _notify_family_about_cancellation,
)
