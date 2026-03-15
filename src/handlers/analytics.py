import os
import logging
import threading
import time
from datetime import datetime
from telebot import types

from src.bot_instance import bot
from src.ui import send_menu_safe, send_content
from src.database_manager import (
    get_parent_role, get_students_for_parent, get_active_spreadsheets,
    get_parents_for_student
)
from src.analytics_engine import analyze_student_grades, generate_weekly_summary

logger = logging.getLogger(__name__)

# ====================
# Команда: AI-анализ по запросу
# ====================
@bot.message_handler(commands=['ai_report'])
def ai_report_command(message):
    """Показывает AI-анализ оценок для всех детей пользователя."""
    _handle_ai_report(message.chat.id)


def cmd_ai_report(message):
    """Вызывается из кнопки главного меню."""
    _handle_ai_report(message.chat.id)


def _handle_ai_report(user_id: int):
    """Общая логика AI-отчёта."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        send_content(user_id, "🔧 <i>AI-аналитика временно недоступна (не настроен API-ключ).</i>")
        return

    students = get_students_for_parent(user_id)
    if not students:
        send_content(user_id, "ℹ️ У вас нет привязанных учеников для анализа.")
        return

    send_content(user_id, "🤖 <i>Анализирую оценки... Это может занять несколько секунд.</i>")

    for student in students:
        student_id = student['id']
        display_name = student.get('display_name') or student['fio']

        analysis = analyze_student_grades(student_id, display_name)
        if analysis:
            msg = (
                f"🤖 <b>AI-анализ: {display_name}</b>\n"
                f"📅 Период: последние 14 дней\n\n"
                f"{analysis}"
            )
            send_content(user_id, msg)
        else:
            send_content(
                user_id,
                f"📊 <b>{display_name}</b>\n"
                f"ℹ️ Недостаточно данных для анализа (нужно минимум 2 оценки за 14 дней)."
            )


# ====================
# Еженедельная рассылка AI-отчётов (воскресенье 19:00)
# ====================
_scheduler_running = False


def start_weekly_scheduler():
    """Запускает фоновый планировщик еженедельных AI-отчётов."""
    global _scheduler_running
    if _scheduler_running:
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.info("ANTHROPIC_API_KEY not set, weekly AI reports disabled.")
        return

    _scheduler_running = True
    thread = threading.Thread(target=_weekly_loop, daemon=True)
    thread.start()
    logger.info("Weekly AI report scheduler started.")


def _weekly_loop():
    """Проверяет каждый час, не пора ли отправить еженедельный отчёт."""
    while True:
        now = datetime.now()
        # Воскресенье = 6, 19:00
        if now.weekday() == 6 and now.hour == 19 and now.minute < 5:
            logger.info("Triggering weekly AI reports...")
            try:
                _send_weekly_reports()
            except Exception as e:
                logger.error(f"Error in weekly AI reports: {e}")
            # Спим час чтобы не отправить дважды
            time.sleep(3600)
        else:
            # Проверяем каждые 5 минут
            time.sleep(300)


def _send_weekly_reports():
    """Отправляет AI-сводку каждому родителю по его детям."""
    students = get_active_spreadsheets()
    # Группируем: student -> parents
    processed_pairs = set()

    for student in students:
        student_id = student['student_id']
        display_name = student.get('display_name') or student['fio']

        analysis = generate_weekly_summary(student_id, display_name)
        if not analysis:
            continue

        parent_ids = get_parents_for_student(student_id)
        for tg_id in parent_ids:
            pair_key = (tg_id, student_id)
            if pair_key in processed_pairs:
                continue
            processed_pairs.add(pair_key)

            msg = (
                f"📊 <b>Еженедельный AI-отчёт</b>\n"
                f"👨‍🎓 {display_name}\n\n"
                f"{analysis}"
            )
            try:
                bot.send_message(tg_id, msg, parse_mode='HTML')
                time.sleep(0.1)  # Rate limit
            except Exception as e:
                logger.error(f"Failed to send weekly report to {tg_id}: {e}")
