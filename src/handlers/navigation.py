"""Handlers для постоянной reply-keyboard навигации (PR role-toggle).

Reply-keyboard варианты:
  Parent-режим: {💬 Чат, ⚙️ Меню}, плюс для admin'а доп строка {🛠 Управление}
  Admin-режим:  {🛠 Управление, 👨 Я родитель}

Регистрируется в main.py СРАЗУ ПОСЛЕ state_flows и ДО ai_chat, чтобы
точное совпадение текста с label-кнопки перехватывалось здесь, а не
улетало в AI как вопрос родителя.
"""
import logging

from src.bot_instance import bot
from src.database_manager import get_user_lang, get_parent_role, has_children_for_grades
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
    """«💬 Чат» — переключение в AI-чат. Если уже в чате — re-enter welcome."""
    from src.handlers.ai_chat import start_ai_chat
    start_ai_chat(message.chat.id)


@bot.message_handler(func=lambda m: _matches_label(m, "nav_dashboard"))
def _on_nav_dashboard(message):
    """«📊 Дашборд» — открыть Mini App. Из-за Telegram API quirk
    reply-keyboard WebApp button НЕ передаёт initData → дашборд получает
    401. Workaround: тап reply-кнопки → шлём inline message с
    InlineKeyboardButton.web_app (передаёт initData) → юзер тапает её →
    открывается Mini App с auth.

    2 тапа вместо 1, но reply-кнопка всегда видна (parent-mode {Чат /
    Дашборд / Меню}) — лучше чем «inline в меню» (3+ тапа всегда)."""
    import os
    user_id = message.chat.id
    lang = get_user_lang(user_id)
    webapp_url = os.environ.get("WEBAPP_URL")
    if not webapp_url:
        bot.send_message(user_id, t("dashboard_unavailable", lang))
        return
    from telebot import types as _types
    markup = _types.InlineKeyboardMarkup()
    markup.add(_types.InlineKeyboardButton(
        t("dashboard_open_btn", lang),
        web_app=_types.WebAppInfo(url=f"{webapp_url}/webapp"),
    ))
    bot.send_message(user_id, t("dashboard_intro", lang), reply_markup=markup)


@bot.message_handler(func=lambda m: _matches_label(m, "nav_menu"))
def _on_nav_menu(message):
    """«⚙️ Меню» — открывает inline-панель: family / subscription / settings /
    support + дашборд (для родителей с детьми)."""
    from src.main import _show_user_panel
    _show_user_panel(message.chat.id)


@bot.message_handler(func=lambda m: _matches_label(m, "nav_admin_panel"))
def _on_nav_admin_panel(message):
    """«🛠 Управление» — открывает admin panel. ТОЛЬКО для admin'а.

    Если admin был в parent-режиме (state ai_chat_mode) — нужно переключить
    reply-keyboard обратно в admin-mode. Reply-keyboard ставится на любое
    сообщение от бота; cmd_admin_panel шлёт panel — но не ставит reply.
    Шлём отдельное короткое сообщение для смены keyboard, потом panel.

    Защита: если не-admin случайно тапнул label (теоретически невозможно —
    у него нет этой кнопки в keyboard), просто игнорируем."""
    user_id = message.chat.id
    if get_parent_role(user_id) != 'admin':
        return

    from src.main import _build_reply_keyboard
    from src.handlers.admin import cmd_admin_panel
    from src.database_manager import clear_user_state, get_user_state

    # Если был в parent-режиме — чистим ai_chat_mode state
    st = get_user_state(user_id)
    if st and st.get('state') == 'ai_chat_mode':
        clear_user_state(user_id)

    # Меняем reply-keyboard на admin-mode минимальным сообщением,
    # затем admin panel inline.
    lang = get_user_lang(user_id)
    bot.send_message(user_id, "🛠",
                      reply_markup=_build_reply_keyboard(lang, mode='admin'))
    cmd_admin_panel(message)


@bot.message_handler(func=lambda m: _matches_label(m, "nav_as_parent"))
def _on_nav_as_parent(message):
    """«👨 Я родитель» — admin переключается в parent-режим.

    Только для admin'а с детьми. Reply-keyboard становится parent-mode
    {💬 Чат, ⚙️ Меню, 🛠 Управление}, открывается AI-чат."""
    user_id = message.chat.id
    if get_parent_role(user_id) != 'admin':
        return
    if not has_children_for_grades(user_id):
        # admin без детей — нечего показывать в parent-режиме
        bot.send_message(user_id, t("ai_chat_no_students", get_user_lang(user_id)))
        return
    from src.main import _enter_default_chat
    _enter_default_chat(user_id)
