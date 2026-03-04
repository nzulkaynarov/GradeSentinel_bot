from telebot import types
from src.bot_instance import bot
from src.database_manager import get_last_menu_id, update_last_menu_id, get_parent_role

def get_main_menu(role: str) -> types.ReplyKeyboardMarkup:
    """Возвращает клавиатуру главного меню в зависимости от роли."""
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    if role == 'admin':
        markup.row("📊 Статус", "🏠 Семьи")
        markup.row("➕ Новая семья")
    elif role == 'head':
        markup.row("🏠 Моя семья", "📈 Оценки")
    else:
        markup.row("📈 Оценки")
    return markup

def send_menu_safe(chat_id: int, text: str, reply_markup=None, inline_markup=None):
    """
    Отправляет меню, удаляя предыдущее сообщение меню для поддержания чистоты чата.
    """
    last_id = get_last_menu_id(chat_id)
    if last_id:
        try:
            bot.delete_message(chat_id, last_id)
        except Exception:
            pass

    role = get_parent_role(chat_id)
    if not reply_markup:
        reply_markup = get_main_menu(role)

    final_markup = inline_markup if inline_markup else reply_markup
    
    msg = bot.send_message(chat_id, text, reply_markup=final_markup, parse_mode='HTML')
    update_last_menu_id(chat_id, msg.message_id)
