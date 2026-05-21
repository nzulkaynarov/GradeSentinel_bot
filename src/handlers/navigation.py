"""Handlers для постоянной reply-keyboard {💬 Чат, 📊 Дашборд, ⚙️ Меню} (PR_F).

Регистрируется в main.py СРАЗУ ПОСЛЕ state_flows и ДО ai_chat, чтобы
точное совпадение текста с label кнопки перехватывалось здесь, а не
улетало в AI как вопрос.

«📊 Дашборд» — это `KeyboardButton(web_app=...)`: Telegram сам открывает
Mini App при тапе, message с текстом «📊 Дашборд» не приходит. Поэтому
тут только Чат и Меню.
"""
import logging

from src.bot_instance import bot
from src.database_manager import get_user_lang
from src.i18n import t

logger = logging.getLogger(__name__)


def _matches_label(message, key: str) -> bool:
    """True если text сообщения точно равен локализованному label кнопки."""
    if not message.text:
        return False
    lang = get_user_lang(message.chat.id)
    return message.text.strip() == t(key, lang).strip()


@bot.message_handler(func=lambda m: _matches_label(m, "nav_chat"))
def _on_nav_chat(message):
    """Reply-keyboard «💬 Чат» — переключение в AI-чат.

    Если юзер уже в ai_chat_mode — заново отправляем welcome. Если нет
    (например, вышел в меню) — re-enter."""
    from src.handlers.ai_chat import start_ai_chat
    start_ai_chat(message.chat.id)


@bot.message_handler(func=lambda m: _matches_label(m, "nav_menu"))
def _on_nav_menu(message):
    """Reply-keyboard «⚙️ Меню» — открывает inline-панель с
    family / subscription / settings / support."""
    from src.main import _show_user_panel
    _show_user_panel(message.chat.id)
