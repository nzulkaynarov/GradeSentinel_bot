"""Обработчик настроек: выбор языка."""
import logging
from telebot import types
from src.bot_instance import bot
from src.ui import send_menu_safe
from src.database_manager import get_user_lang, set_user_lang
from src.i18n import t, SUPPORTED_LANGS, load_translations

logger = logging.getLogger(__name__)


def cmd_settings(message):
    """Вызывается из кнопки главного меню — показывает выбор языка."""
    _show_lang_menu(message.chat.id)


def _show_lang_menu(chat_id: int):
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("🇷🇺 Русский", callback_data="set_lang_ru"),
        types.InlineKeyboardButton("🇺🇿 O'zbek", callback_data="set_lang_uz"),
        types.InlineKeyboardButton("🇬🇧 English", callback_data="set_lang_en"),
    )
    send_menu_safe(chat_id, t("lang_select"), inline_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith('set_lang_'))
def callback_set_lang(call):
    """Пользователь выбрал язык в настройках."""
    lang = call.data.replace('set_lang_', '')
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    if lang not in SUPPORTED_LANGS:
        bot.answer_callback_query(call.id, "❌")
        return

    set_user_lang(user_id, lang)

    # Перестраиваем маппинг кнопок (новые тексты на новом языке)
    from src.i18n import _build_button_actions
    _build_button_actions()

    bot.answer_callback_query(call.id, t("lang_changed", lang))

    # Сбрасываем кэш панели (язык изменился)
    from src.main import _invalidate_panel_cache
    _invalidate_panel_cache(chat_id)

    # Возвращаемся в пользовательскую панель на новом языке
    from src.database_manager import get_parent_role
    role = get_parent_role(user_id)
    if role == 'admin':
        try:
            bot.delete_message(chat_id, call.message.message_id)
        except Exception as e:
            logger.debug(f"Could not delete lang selection message: {e}")
        send_menu_safe(chat_id, t("lang_changed", lang))
    else:
        # Показываем панель через edit (без мигания)
        from src.main import _show_user_panel
        _show_user_panel(chat_id, call.message.message_id)
