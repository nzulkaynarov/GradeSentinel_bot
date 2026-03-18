import time
import logging
from datetime import date
from telebot import types
from src.database_manager import (
    get_active_spreadsheets, add_grade, get_parents_for_student,
    update_student_display_name, queue_notification, get_user_lang,
    get_existing_grade, update_grade, get_active_spreadsheets_with_subscription,
    upsert_quarter_grade, get_db_connection
)
from src.google_sheets import get_sheet_data, get_spreadsheet_title
from src.data_cleaner import sanitize_grade
from src.utils import clean_student_name
from src.notification_helpers import (
    format_grade_notification, format_grade_change_notification, is_quiet_hours,
    format_quarter_new_notification, format_quarter_change_notification
)
from src.i18n import t

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_bot = None

def set_bot_instance(bot):
    global _bot
    _bot = bot

def _make_grade_inline_keyboard(student_id: int, lang: str = 'ru') -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton(t("btn_seen", lang), callback_data=f"grade_seen_{student_id}"),
        types.InlineKeyboardButton(t("btn_today_all", lang), callback_data=f"grade_today_{student_id}")
    )
    return markup

def send_notification(telegram_ids, message, inline_markup=None):
    """
    Отправляет уведомление. В тихие часы (22:00-07:00) копит в очередь.
    message может быть dict {tg_id: msg_text} для мультиязычности или str.
    """
    if not _bot:
        logger.warning("Bot instance not set. Using logger placeholder.")
        for tg_id in telegram_ids:
            logger.info(f"[PLACEHOLDER -> {tg_id}]")
        return

    quiet = is_quiet_hours()

    for tg_id in telegram_ids:
        msg_text = message[tg_id] if isinstance(message, dict) else message
        try:
            if quiet:
                queue_notification(tg_id, msg_text)
                logger.info(f"Notification queued (quiet hours) for TG:{tg_id}")
            else:
                lang = get_user_lang(tg_id)
                kb = inline_markup[tg_id] if isinstance(inline_markup, dict) else inline_markup
                _bot.send_message(
                    tg_id, msg_text, parse_mode='HTML',
                    disable_web_page_preview=True,
                    reply_markup=kb
                )
                logger.info(f"Notification sent to TG:{tg_id}")
        except Exception as e:
            logger.error(f"Failed to send notification to {tg_id}: {e}")

def check_for_new_grades():
    students = get_active_spreadsheets_with_subscription()
    if not students:
        logger.info("No active students with spreadsheets found.")
        return

    logger.info(f"Starting check for {len(students)} students.")

    RANGE_NAME = "Сегодня!A1:B50"

    for student in students:
        student_id = student['student_id']
        fio = student['fio']
        spreadsheet_id = student['spreadsheet_id']

        display_name = student.get('display_name')
        if not display_name:
            sheet_title = get_spreadsheet_title(spreadsheet_id)
            display_name = clean_student_name(sheet_title) if sheet_title else fio
            update_student_display_name(student_id, display_name)

        logger.info(f"Checking sheet for student: {display_name} (ID: {student_id})")

        try:
            data = get_sheet_data(spreadsheet_id, RANGE_NAME)
        except Exception as e:
            logger.error(f"Unexpected error fetching data for {display_name}: {e}")
            continue

        if data is None:
            logger.warning(f"Data fetch returned None for {display_name}. Skipping this cycle.")
            continue

        for row_idx, row in enumerate(data[1:], start=2):
            if not isinstance(row, list) or len(row) < 2:
                continue

            subject = str(row[0]).strip()
            raw_grade = str(row[1]).strip()

            if not raw_grade:
                continue

            # Дата в cell_reference — чтобы каждый день создавались новые записи.
            # Без даты "Сегодня!B2" от вчера блокирует INSERT сегодняшней оценки.
            today = date.today().isoformat()
            cell_reference = f"Сегодня!B{row_idx}:{today}"

            grade_value, clean_text = sanitize_grade(raw_grade)

            is_new = add_grade(student_id, subject, grade_value, clean_text, cell_reference)

            if is_new and clean_text:
                logger.info(f"[NEW GRADE] {display_name} got '{clean_text}' in {subject}")
                parents_ids = get_parents_for_student(student_id)
                if parents_ids:
                    messages = {}
                    keyboards = {}
                    for tg_id in parents_ids:
                        lang = get_user_lang(tg_id)
                        messages[tg_id] = format_grade_notification(
                            display_name, subject, clean_text,
                            grade_value, spreadsheet_id, student_id, lang=lang
                        )
                        keyboards[tg_id] = _make_grade_inline_keyboard(student_id, lang)
                    send_notification(parents_ids, messages, inline_markup=keyboards)
            elif not is_new and clean_text:
                # Проверяем, изменилась ли оценка
                existing = get_existing_grade(student_id, cell_reference)
                if existing and existing['raw_text'] != clean_text:
                    old_text = existing['raw_text']
                    update_grade(student_id, cell_reference, grade_value, clean_text)
                    logger.info(f"[GRADE CHANGED] {display_name}: {subject} '{old_text}' -> '{clean_text}'")
                    parents_ids = get_parents_for_student(student_id)
                    if parents_ids:
                        messages = {}
                        keyboards = {}
                        for tg_id in parents_ids:
                            lang = get_user_lang(tg_id)
                            messages[tg_id] = format_grade_change_notification(
                                display_name, subject, old_text, clean_text,
                                grade_value, spreadsheet_id, student_id, lang=lang
                            )
                            keyboards[tg_id] = _make_grade_inline_keyboard(student_id, lang)
                        send_notification(parents_ids, messages, inline_markup=keyboards)

SKIP_SUBJECTS = {'посещаемость', '0', ''}


def _make_quarter_inline_keyboard(student_id: int, lang: str = 'ru') -> types.InlineKeyboardMarkup:
    markup = types.InlineKeyboardMarkup(row_width=1)
    markup.add(
        types.InlineKeyboardButton(t("btn_today_all", lang), callback_data=f"grade_today_{student_id}")
    )
    return markup


def check_for_quarter_changes():
    """Проверяет изменения четвертных оценок для всех активных студентов."""
    students = get_active_spreadsheets_with_subscription()
    if not students:
        return

    logger.info(f"Checking quarter grades for {len(students)} students.")

    RANGE_NAME = "Четверти!A1:G50"

    for student in students:
        student_id = student['student_id']
        fio = student['fio']
        spreadsheet_id = student['spreadsheet_id']
        display_name = student.get('display_name') or fio

        try:
            data = get_sheet_data(spreadsheet_id, RANGE_NAME)
        except Exception as e:
            logger.error(f"Error fetching quarters for {display_name}: {e}")
            continue

        if not data or len(data) < 2:
            continue

        for row in data[1:]:
            if not row or len(row) < 2:
                continue

            subject = str(row[0]).strip()
            if not subject or subject.lower() in SKIP_SUBJECTS:
                continue
            try:
                int(subject)
                continue
            except ValueError:
                pass

            for col_idx in range(1, min(len(row), 7)):
                cell_value = str(row[col_idx]).strip()
                if not cell_value:
                    continue

                quarter = col_idx  # 1=1ч, 2=2ч, 3=3ч, 4=4ч, 5=год

                from src.data_cleaner import sanitize_grade
                grade_value, clean_text = sanitize_grade(cell_value)
                if clean_text is None:
                    continue

                # Получаем текущее значение ДО upsert
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        SELECT raw_text FROM quarter_grades
                        WHERE student_id = ? AND subject = ? AND quarter = ?
                    ''', (student_id, subject, quarter))
                    existing = cursor.fetchone()

                old_text = existing['raw_text'] if existing else None

                changed = upsert_quarter_grade(student_id, subject, quarter, grade_value, clean_text)

                if not changed:
                    continue

                parents_ids = get_parents_for_student(student_id)
                if not parents_ids:
                    continue

                if old_text is None:
                    # Новая четвертная оценка
                    logger.info(f"[NEW QUARTER] {display_name}: {subject} Q{quarter} = {clean_text}")
                    messages = {}
                    keyboards = {}
                    for tg_id in parents_ids:
                        lang = get_user_lang(tg_id)
                        messages[tg_id] = format_quarter_new_notification(
                            display_name, subject, quarter, clean_text,
                            grade_value, spreadsheet_id, student_id, lang=lang
                        )
                        keyboards[tg_id] = _make_quarter_inline_keyboard(student_id, lang)
                    send_notification(parents_ids, messages, inline_markup=keyboards)
                else:
                    # Изменение четвертной оценки
                    logger.info(f"[QUARTER CHANGED] {display_name}: {subject} Q{quarter} '{old_text}' -> '{clean_text}'")
                    messages = {}
                    keyboards = {}
                    for tg_id in parents_ids:
                        lang = get_user_lang(tg_id)
                        messages[tg_id] = format_quarter_change_notification(
                            display_name, subject, quarter, old_text, clean_text,
                            grade_value, spreadsheet_id, student_id, lang=lang
                        )
                        keyboards[tg_id] = _make_quarter_inline_keyboard(student_id, lang)
                    send_notification(parents_ids, messages, inline_markup=keyboards)

    logger.info("Quarter grades check completed.")


def start_polling(interval_seconds=300):
    logger.info(f"Starting GradeSentinel monitor engine (interval: {interval_seconds}s)")
    while True:
        try:
            check_for_new_grades()
        except Exception as e:
            logger.error(f"Error during polling cycle: {e}")

        logger.info(f"Sleeping for {interval_seconds} seconds...")
        time.sleep(interval_seconds)

if __name__ == "__main__":
    start_polling(10)
