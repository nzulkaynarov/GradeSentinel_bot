import time
import logging
import json
from src.database_manager import get_active_spreadsheets, add_grade, get_parents_for_student
from src.google_sheets import get_sheet_data
from src.data_cleaner import sanitize_grade

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global bot instance for notifications
_bot = None

def set_bot_instance(bot):
    """Устанавливает глобальный экземпляр бота для отправки уведомлений."""
    global _bot
    _bot = bot

def send_notification(telegram_ids, message):
    """Отправляет уведомление в Telegram через реальный API."""
    if not _bot:
        logger.warning("Bot instance not set. Using logger placeholder.")
        for tg_id in telegram_ids:
            logger.info(f"[PLACEHOLDER -> {tg_id}] {message}")
        return

    for tg_id in telegram_ids:
        try:
            _bot.send_message(tg_id, message, parse_mode='Markdown')
            logger.info(f"Notification sent to TG:{tg_id}")
        except Exception as e:
            logger.error(f"Failed to send notification to {tg_id}: {e}")

def check_for_new_grades():
    """Единичный пробег по всем активным таблицам студентов."""
    students = get_active_spreadsheets()
    if not students:
        logger.info("No active students with spreadsheets found.")
        return

    logger.info(f"Starting check for {len(students)} students.")
    
    # Диапазон, где могут быть оценки на "Сегодня"
    # Для примера предположим, что предметы идут в столбце A (с A2), а оценки в столбце B.
    # В реальном проекте маппинг будет зависеть от структуры вашего дневника.
    RANGE_NAME = "Сегодня!A1:B50"
    
    for student in students:
        student_id = student['student_id']
        fio = student['fio']
        spreadsheet_id = student['spreadsheet_id']
        
        logger.info(f"Checking sheet for student: {fio} (ID: {student_id})")
        data = get_sheet_data(spreadsheet_id, RANGE_NAME)
        
        if not data:
            logger.warning(f"Could not fetch data for {fio}. Skipping.")
            continue
            
        # Упрощенная логика парсинга для примера (Предмет в col 0, Оценка в col 1)
        # Начинаем с индекса 1, пропуская заголовки
        for row_idx, row in enumerate(data[1:], start=2):
            if len(row) < 2:
                continue # Пустая строка или нет оценки
                
            subject = row[0].strip()
            raw_grade = row[1].strip()
            
            if not raw_grade:
                continue
                
            cell_reference = f"Сегодня!B{row_idx}"
            
            # Чистим данные
            grade_value, clean_text = sanitize_grade(raw_grade)
            
            # Пытаемся сохранить в БД (если True, значит новая оценка)
            is_new = add_grade(student_id, subject, grade_value, clean_text, cell_reference)
            
            if is_new:
                logger.info(f"[NEW GRADE] {fio} got '{clean_text}' in {subject}")
                # Уведомляем родителей
                parents_ids = get_parents_for_student(student_id)
                if parents_ids:
                    msg = (
                        f"🔔 *Новая запись в дневнике!*\n"
                        f"👨‍🎓 Ученик: {fio}\n"
                        f"📚 Предмет: {subject}\n"
                        f"📝 Значение: {clean_text}"
                    )
                    if grade_value is not None:
                        msg += f"\n⭐ Оценка для статистики: {grade_value}"
                        
                    send_notification(parents_ids, msg)

def start_polling(interval_seconds=300):
    """Запускает бесконечный цикл мониторинга."""
    logger.info(f"Starting GradeSentinel monitor engine (interval: {interval_seconds}s)")
    while True:
        try:
            check_for_new_grades()
        except Exception as e:
            logger.error(f"Error during polling cycle: {e}")
        
        logger.info(f"Sleeping for {interval_seconds} seconds...")
        time.sleep(interval_seconds)

if __name__ == "__main__":
    start_polling(10) # 10 секунд для тестов
