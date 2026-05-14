"""State-machine handlers для multi-step flows на user_states.

Заменяет хрупкий `bot.register_next_step_handler` (in-memory — теряется при
рестарте бота → пользователь застревает посредине flow) на persistent state
в таблице user_states.

Текущее покрытие — family creation flow:
- `awaiting_family_name`: ждём название новой семьи
  - data='selfserve': self-serve flow (юзер автоматически становится главой)
  - data='': admin-initiated (после ввода имени → выбор главы)
- `awaiting_head_choice`: ждём выбор «сам глава» / «назначить другого»
  - data=family_name
- `awaiting_head_fio`: ждём ФИО внешнего главы
  - data=family_name
- `awaiting_head_phone`: ждём телефон главы
  - data=json {family_name, head_fio}

ВАЖНО про порядок регистрации: эти handler'ы импортируются из main.py
ПЕРВЫМИ среди handler'ов, чтобы pyTelegramBotAPI обходил их раньше
generic-обработчиков (text='*' etc). См. main.py:_import_handlers().

NOT покрыто (пока): subscription.py promo/price flows — 4 callsite'а
register_next_step_handler. Оставлены до отдельной сессии (платёжный код,
повышенная осторожность).
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


@bot.message_handler(func=lambda m: _state_is(m.from_user.id, 'awaiting_family_name'))
def _on_family_name(message):
    """User вводит название новой семьи. process_family_name сам разрулит
    self-serve vs admin flow через get_user_state.data."""
    from src.handlers.admin import process_family_name
    process_family_name(message)


@bot.message_handler(func=lambda m: _state_is(m.from_user.id, 'awaiting_head_choice'))
def _on_head_choice(message):
    """User выбирает «сам главой» / «назначить другого»."""
    from src.handlers.admin import process_head_choice
    state = get_user_state(message.from_user.id)
    family_name = state.get('data') or '' if state else ''
    process_head_choice(message, family_name)


@bot.message_handler(func=lambda m: _state_is(m.from_user.id, 'awaiting_head_fio'))
def _on_head_fio(message):
    """User вводит ФИО внешнего главы."""
    from src.handlers.admin import process_head_fio
    state = get_user_state(message.from_user.id)
    family_name = state.get('data') or '' if state else ''
    process_head_fio(message, family_name)


@bot.message_handler(func=lambda m: _state_is(m.from_user.id, 'awaiting_head_phone'))
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
