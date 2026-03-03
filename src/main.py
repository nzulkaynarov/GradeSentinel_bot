import os
import threading
import logging
import telebot
from src.database_manager import init_db
from src.monitor_engine import start_polling

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Replace with python-dotenv in production
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")
bot = telebot.TeleBot(BOT_TOKEN)

# ====================
# Telegram bot setup
# ====================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "Добро пожаловать в GradeSentinel! Отправьте свой номер телефона для верификации (заглушка).")

def start_bot():
    """Запускает Telegram бота в режиме polling."""
    logger.info("Starting Telegram Bot...")
    bot.polling(none_stop=True)

def main():
    logger.info("Initializing GradeSentinel v2.0...")
    
    # 1. Init DB
    init_db()
    
    # 2. Start monitor engine in a separate thread
    monitor_thread = threading.Thread(target=start_polling, args=(300,), daemon=True)
    monitor_thread.start()
    logger.info("Monitor engine thread started.")
    
    # 3. Start telegram bot blocking main thread
    start_bot()

if __name__ == '__main__':
    main()
