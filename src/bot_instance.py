import os
import telebot
from dotenv import load_dotenv
import logging

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN or ":" not in BOT_TOKEN:
    logging.error("BOT_TOKEN is missing or invalid in environment!")
    exit(1)

bot = telebot.TeleBot(BOT_TOKEN)
