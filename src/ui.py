from telebot import types
from src.bot_instance import bot
from src.database_manager import get_last_menu_id, update_last_menu_id, get_parent_role

def get_main_menu(chat_id: int) -> types.ReplyKeyboardMarkup:
    """Возвращает клавиатуру главного меню в зависимости от сводных ролей пользователя."""
    from src.database_manager import get_parent_role, is_head_of_any_family, has_children_for_grades
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    
    role = get_parent_role(chat_id)
    is_head = is_head_of_any_family(chat_id)
    has_children = has_children_for_grades(chat_id)
    
    if role == 'admin':
        markup.row("📊 Статус", "🏠 Семьи")
        markup.row("➕ Новая семья", "📢 Рассылка")
    elif is_head or has_children:
        markup.row("📊 Статус")
        
    if is_head:
        markup.row("🏠 Моя семья")
        
    if has_children:
        markup.row("📈 Оценки")
        
    if role or is_head or has_children:
        markup.row("💬 Поддержка")
            
    # Если вообще нет кнопок (пустой пользователь), дадим хотя бы заглушку
    if len(markup.keyboard) == 0:
        markup.row("⏳ Ожидание привязки")
        
    return markup

def send_menu_safe(chat_id: int, text: str, reply_markup=None, inline_markup=None):
    """
    Отправляет техническое меню навигации, удаляя предыдущее сообщение 
    навигационного меню для поддержания чистоты чата. КОНТЕНТ (оценки) сюда нельзя отправлять.
    """
    last_id = get_last_menu_id(chat_id)
    if last_id:
        try:
            bot.delete_message(chat_id, last_id)
        except Exception:
            pass

    if not reply_markup:
        reply_markup = get_main_menu(chat_id)

    final_markup = inline_markup if inline_markup else reply_markup
    
    msg = bot.send_message(chat_id, text, reply_markup=final_markup, parse_mode='HTML')
    update_last_menu_id(chat_id, msg.message_id)

def send_content(chat_id: int, text: str, reply_markup=None):
    """
    Отправляет контент, который ДОЛЖЕН остаться в истории чата (например, оценки).
    Всегда прикрепляет клавиатуру главного меню, чтобы не было "тупиков".
    """
    if not reply_markup:
        reply_markup = get_main_menu(chat_id)
        
    bot.send_message(chat_id, text, reply_markup=reply_markup, parse_mode='HTML', disable_web_page_preview=True)
