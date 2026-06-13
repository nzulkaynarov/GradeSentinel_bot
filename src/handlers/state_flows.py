"""State-machine handlers для multi-step flows на user_states.

Заменяет хрупкий `bot.register_next_step_handler` (in-memory — теряется при
рестарте бота → пользователь застревает посредине flow) на persistent state
в таблице user_states.

Покрытие — family creation + subscription/promo/price flows:

**Family creation:**
- `awaiting_family_name` (data='selfserve'|''): ждём название новой семьи
- `awaiting_head_choice` (data=family_name): «сам глава» / «назначить другого»
- `awaiting_head_fio` (data=family_name): ФИО внешнего главы
- `awaiting_head_phone` (data=json {family_name, head_fio}): телефон главы

**Subscription / promo / price (admin + user):**
- `awaiting_promo_code`: юзер вводит промокод (для применения к семье)
- `awaiting_admin_price` (data=plan_key): админ вводит новую цену тарифа
- `awaiting_promo_free`: админ вводит «<months> <max_uses> [days_valid]»
  для создания промокода с бесплатными месяцами
- `awaiting_promo_discount`: админ вводит «<plan> <percent> <max_uses> [days]»

ВАЖНО про порядок регистрации: этот модуль импортируется из main.py
ПЕРВЫМ среди handler'ов, чтобы pyTelegramBotAPI обходил эти handler'ы
раньше generic-обработчиков.
"""
import json
import logging

from src.bot_instance import bot
from src.db.state import get_user_state

logger = logging.getLogger(__name__)


def _state_is(user_id: int, expected: str) -> bool:
    """True если у пользователя сейчас именно этот state."""
    s = get_user_state(user_id)
    return bool(s and s.get('state') == expected)


@bot.message_handler(func=lambda m: m.chat.type == 'private' and _state_is(m.from_user.id, 'awaiting_family_name'))
def _on_family_name(message):
    """User вводит название новой семьи. process_family_name сам разрулит
    self-serve vs admin flow через get_user_state.data."""
    from src.handlers.admin import process_family_name
    process_family_name(message)


@bot.message_handler(func=lambda m: m.chat.type == 'private' and _state_is(m.from_user.id, 'awaiting_head_choice'))
def _on_head_choice(message):
    """User выбирает «сам главой» / «назначить другого»."""
    from src.handlers.admin import process_head_choice
    state = get_user_state(message.from_user.id)
    family_name = state.get('data') or '' if state else ''
    process_head_choice(message, family_name)


@bot.message_handler(func=lambda m: m.chat.type == 'private' and _state_is(m.from_user.id, 'awaiting_head_fio'))
def _on_head_fio(message):
    """User вводит ФИО внешнего главы."""
    from src.handlers.admin import process_head_fio
    state = get_user_state(message.from_user.id)
    family_name = state.get('data') or '' if state else ''
    process_head_fio(message, family_name)


@bot.message_handler(func=lambda m: m.chat.type == 'private' and _state_is(m.from_user.id, 'awaiting_head_phone'))
def _on_head_phone(message):
    """User вводит телефон главы. data — json {family_name, head_fio}."""
    from src.handlers.admin import process_head_phone
    state = get_user_state(message.from_user.id)
    try:
        ctx = json.loads(state.get('data') or '{}') if state else {}
    except (ValueError, TypeError):
        ctx = {}
    family_name = ctx.get('family_name', '')
    head_fio = ctx.get('head_fio', '')
    process_head_phone(message, family_name, head_fio)


# ─── Subscription / promo / price flows ─────────────────────────────
@bot.message_handler(func=lambda m: m.chat.type == 'private' and _state_is(m.from_user.id, 'awaiting_promo_code'))
def _on_promo_code(message):
    """Юзер вводит промокод для применения к своей семье."""
    from src.handlers.subscription import _process_promo_code
    _process_promo_code(message)


@bot.message_handler(func=lambda m: m.chat.type == 'private' and _state_is(m.from_user.id, 'awaiting_admin_price'))
def _on_admin_price(message):
    """Админ вводит новую цену для тарифа. data — plan_key."""
    from src.handlers.subscription import _process_price_input
    state = get_user_state(message.from_user.id)
    plan_key = state.get('data') or '' if state else ''
    _process_price_input(message, plan_key)


@bot.message_handler(func=lambda m: m.chat.type == 'private' and _state_is(m.from_user.id, 'awaiting_promo_free'))
def _on_promo_free(message):
    """Админ создаёт промокод с бесплатными месяцами."""
    from src.handlers.subscription import _process_promo_free
    _process_promo_free(message)


@bot.message_handler(func=lambda m: m.chat.type == 'private' and _state_is(m.from_user.id, 'awaiting_promo_discount'))
def _on_promo_discount(message):
    """Админ создаёт промокод со скидкой."""
    from src.handlers.subscription import _process_promo_discount
    _process_promo_discount(message)
