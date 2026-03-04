import os
import logging
from telebot import types
import time
from src.bot_instance import bot
from src.ui import send_menu_safe, send_content
from src.database_manager import get_parent_role, get_user_info_by_tg_id, get_all_telegram_ids

from typing import Dict, Any

logger = logging.getLogger(__name__)

# Состояния для общения
user_states: Dict[int, Any] = {}

def get_admin_group_id():
    """Возвращает ID админ-группы из .env"""
    group_id = os.environ.get("ADMIN_GROUP_ID")
    if group_id:
        return int(group_id)
    return None

# ====================
# Обратная связь (Пользователь -> Админ)
# ====================
def support_started(message):
    user_id = message.chat.id
    admin_group = get_admin_group_id()
    
    if not admin_group:
        send_menu_safe(user_id, "🔧 <i>Функция поддержки временно недоступна (не настроена группа).</i>")
        return
        
    user_states[user_id] = "awaiting_support_message"
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add("❌ Отмена")
    
    bot.send_message(
        user_id, 
        "📝 <b>Техническая поддержка</b>\n\nНапишите ваш вопрос, пожелание или опишите проблему. Вы можете отправить текст или фото с описанием.\n\nАдминистратор ответит вам при первой возможности.", 
        reply_markup=markup,
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda msg: user_states.get(msg.chat.id) == "awaiting_support_message", content_types=['text', 'photo', 'document', 'video'])
def receive_support_message(message):
    user_id = message.chat.id
    admin_group = get_admin_group_id()
    
    if message.text == "❌ Отмена":
        user_states.pop(user_id, None)
        send_menu_safe(user_id, "Действие отменено.")
        return
        
    user_states.pop(user_id, None)
    
    # Получаем информацию о пользователе для подписи
    user_info = get_user_info_by_tg_id(user_id)
    familia = ", ".join(user_info['families']) if user_info and user_info.get('families') else "Неизвестно"
    fio = user_info['fio'] if user_info else message.from_user.first_name
    phone = user_info['phone'] if user_info else "Неизвестен"
    
    # Формируем заголовок для админов
    header = f"📩 <b>Новое обращение в поддержку!</b>\n"
    header += f"👤 От: {fio}\n"
    header += f"📞 Телефон: <code>+{phone}</code>\n"
    header += f"🏠 Семьи: {familia}\n"
    header += f"🆔 TG_ID: <code>{user_id}</code>\n"
    header += f"➖➖➖➖➖➖➖➖➖➖\n"
    
    try:
        # Сначала отправляем заголовок-карточку
        bot.send_message(admin_group, header, parse_mode="HTML")
        # Затем пересылаем само сообщение (чтобы сохранить фото/документы)
        bot.forward_message(admin_group, message.chat.id, message.message_id)
        
        send_menu_safe(user_id, "✅ <b>Сообщение отправлено!</b>\nСпасибо за обратную связь. Администратор уже получил ваше сообщение и ответит вам здесь же.")
    except Exception as e:
        logger.error(f"Failed to forward support message to group {admin_group}: {e}")
        send_menu_safe(user_id, "❌ Произошла ошибка при отправке сообщения. Пожалуйста, попробуйте позже.")

# ====================
# Ответ из группы (Админ -> Пользователь)
# ====================
@bot.message_handler(func=lambda msg: msg.chat.id == get_admin_group_id() and msg.reply_to_message is not None)
def reply_from_admin_group(message):
    """
    Перехватывает ответы внутри админской группы на пересланные сообщения пользователей.
    И отправляет текст ответа оригинальному пользователю.
    """
    # Проверяем, что исходное сообщение было переслано от пользователя
    original_msg = message.reply_to_message
    if not original_msg.forward_from:
        # Если пересылка скрыта настройками приватности, мы можем попытаться найти ID в заголовке
        # Но для простоты сейчас обрабатываем только открытые пересылки
        # P.S: В предыдущей функции мы пересылали с forward_message. 
        # Если юзер скрыл профиль, forward_from будет None.
        
        # Попробуем извлечь ID из текста, если кто-то ответил на карточку
        if original_msg.text and "TG_ID:" in original_msg.text:
            try:
                # Извлекаем ID из формата 🆔 TG_ID: 123456
                lines = original_msg.text.split('\n')
                for line in lines:
                    if "TG_ID:" in line:
                        user_id = int(line.split(':')[1].strip())
                        send_reply_to_user(message, user_id)
                        return
            except Exception as e:
                logger.error(f"Failed to parse user ID from support card: {e}")
        return
        
    user_id = original_msg.forward_from.id
    send_reply_to_user(message, user_id)

def send_reply_to_user(message, target_user_id):
    """Вспомогательная функция для отправки ответа пользователю"""
    try:
        reply_text = f"👨‍💻 <b>Ответ от администратора:</b>\n\n{message.text}"
        bot.send_message(target_user_id, reply_text, parse_mode="HTML")
        
        # Подтверждаем админу в группе
        bot.reply_to(message, "✅ Ответ успешно доставлен пользователю!")
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка отправки: пользователь заблокировал бота или удален. ({str(e)})")

# ====================
# Рассылка новостей (Супер-Админ -> Пользователи)
# ====================
def broadcast_started(message):
    user_id = message.chat.id
    if get_parent_role(user_id) != 'admin':
        return
        
    user_states[user_id] = "awaiting_broadcast_message"
    
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add("❌ Отмена")
    
    bot.send_message(
        user_id, 
        "📢 <b>Режим рассылки новостей</b>\n\nОтправьте сообщение (текст, фото или видео), которое нужно разослать <b>всем</b> зарегистрированным пользователям бота.\n\nПеред отправкой у вас будет возможность подтвердить действие.", 
        reply_markup=markup,
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda msg: user_states.get(msg.chat.id) == "awaiting_broadcast_message", content_types=['text', 'photo', 'document', 'video'])
def confirm_broadcast_message(message):
    user_id = message.chat.id
    
    if message.text == "❌ Отмена":
        user_states.pop(user_id, None)
        send_menu_safe(user_id, "Рассылка отменена.")
        return
        
    user_states[user_id] = {"state": "confirming_broadcast", "message_obj": message}
    
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("✅ Да, начать рассылку", callback_data="broadcast_confirm"))
    markup.add(types.InlineKeyboardButton("❌ Отмена", callback_data="broadcast_cancel"))
    
    bot.send_message(
        user_id, 
        "Вы готовы разослать это сообщение всем пользователям базы?", 
        reply_markup=markup,
        reply_to_message_id=message.message_id
    )

@bot.callback_query_handler(func=lambda call: call.data in ["broadcast_confirm", "broadcast_cancel"])
def process_broadcast_confirmation(call):
    user_id = call.message.chat.id
    state_data = user_states.get(user_id)
    
    if not isinstance(state_data, dict) or state_data.get("state") != "confirming_broadcast":
        bot.answer_callback_query(call.id, "Данные устарели.")
        bot.delete_message(chat_id=user_id, message_id=call.message.message_id)
        return
        
    if call.data == "broadcast_cancel":
        user_states.pop(user_id, None)
        bot.edit_message_text("Рассылка отменена.", user_id, call.message.message_id)
        send_menu_safe(user_id, "Главное меню")
        return
        
    # Начинаем рассылку
    bot.edit_message_text("🚀 Рассылка началась. Пожалуйста, подождите...", user_id, call.message.message_id)
    
    original_message: types.Message = state_data["message_obj"]
    user_states.pop(user_id, None)
    
    users = get_all_telegram_ids()
    success_count: int = 0
    fail_count: int = 0
    
    for tg_id in users:
        # Не отправляем самому себе
        if str(tg_id) == str(user_id):
            continue
            
        try:
            bot.copy_message(tg_id, from_chat_id=user_id, message_id=original_message.message_id)
            success_count += 1
            time.sleep(0.05) # Небольшая пауза во избежание флуда
        except Exception as e:
            logger.error(f"Failed to broadcast to {tg_id}: {e}")
            fail_count += 1
            
    bot.send_message(
        user_id, 
        f"✅ <b>Рассылка завершена!</b>\n\nУспешно доставлено: {success_count}\nОшибок/Блокировок: {fail_count}",
        parse_mode="HTML"
    )
    send_menu_safe(user_id, "Главное меню")
