"""
Фоновые планировщики:
1. Ежедневная вечерняя сводка (19:00 по местному)
2. Утренняя рассылка отложенных уведомлений (07:00 по местному)
3. Статус «бот работает» (15:00 по местному, если нет оценок за день)
"""
import time
import logging
import threading
from datetime import datetime, timedelta

from src.notification_helpers import TIMEZONE_OFFSET_HOURS

logger = logging.getLogger(__name__)

_bot = None
_scheduler_started = False


def set_bot_instance(bot):
    global _bot
    _bot = bot


def _get_local_now() -> datetime:
    """Текущее время по местному часовому поясу (UTC+5)."""
    return datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET_HOURS)


def start_daily_schedulers():
    """Запускает все ежедневные фоновые задачи."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    thread = threading.Thread(target=_scheduler_loop, daemon=True)
    thread.start()
    logger.info("Daily schedulers started (evening summary, quiet hours flush, bot alive).")


def _scheduler_loop():
    """Единый цикл проверки всех ежедневных задач каждые 3 минуты."""
    # Трекеры, чтобы не дублировать отправку в одном дне
    last_evening_date = None
    last_morning_date = None
    last_alive_date = None

    while True:
        try:
            now = _get_local_now()
            today = now.date()

            # 07:00-07:05 — рассылка отложенных уведомлений (тихие часы)
            if now.hour == 7 and now.minute < 6 and last_morning_date != today:
                last_morning_date = today
                _flush_quiet_hours_queue()

            # 15:00-15:05 — статус «бот работает» если нет оценок
            if now.hour == 15 and now.minute < 6 and last_alive_date != today:
                last_alive_date = today
                _send_bot_alive_status()

            # 19:00-19:05 — ежедневная вечерняя сводка
            if now.hour == 19 and now.minute < 6 and last_evening_date != today:
                last_evening_date = today
                _send_daily_evening_summary()

        except Exception as e:
            logger.error(f"Error in daily scheduler loop: {e}")

        time.sleep(180)  # Каждые 3 минуты


def _flush_quiet_hours_queue():
    """Утренняя рассылка: отправляет все уведомления, накопленные за ночь."""
    from src.database_manager import get_all_queued_telegram_ids, get_and_clear_queued_notifications

    if not _bot:
        return

    tg_ids = get_all_queued_telegram_ids()
    if not tg_ids:
        logger.info("No queued notifications to flush.")
        return

    logger.info(f"Flushing quiet hours queue for {len(tg_ids)} users.")

    for tg_id in tg_ids:
        messages = get_and_clear_queued_notifications(tg_id)
        if not messages:
            continue

        header = f"🌅 <b>Доброе утро! За ночь поступило {len(messages)} уведомлений:</b>\n"
        try:
            _bot.send_message(tg_id, header, parse_mode='HTML')
            time.sleep(0.05)
        except Exception as e:
            logger.error(f"Failed to send morning header to {tg_id}: {e}")
            continue

        for msg in messages:
            try:
                _bot.send_message(tg_id, msg, parse_mode='HTML', disable_web_page_preview=True)
                time.sleep(0.05)
            except Exception as e:
                logger.error(f"Failed to send queued msg to {tg_id}: {e}")


def _send_daily_evening_summary():
    """Ежедневная вечерняя сводка оценок за день (19:00)."""
    from src.database_manager import get_all_parents_with_children, get_today_grades_for_student

    if not _bot:
        return

    logger.info("Sending daily evening summaries...")

    parent_data = get_all_parents_with_children()

    # Группируем: tg_id -> [{student_id, display_name}]
    from collections import defaultdict
    parents_map = defaultdict(list)
    for row in parent_data:
        parents_map[row['telegram_id']].append(row)

    for tg_id, children in parents_map.items():
        # Собираем сводку по всем детям
        summaries = []
        for child in children:
            grades = get_today_grades_for_student(child['student_id'])
            if not grades:
                continue

            lines = [f"👨‍🎓 <b>{child['display_name']}</b>\n"]
            numeric_grades = []

            for g in grades:
                lines.append(f"  {g['subject']}: <b>{g['raw_text']}</b>")
                if g['grade_value'] is not None:
                    numeric_grades.append(g['grade_value'])

            if numeric_grades:
                avg = sum(numeric_grades) / len(numeric_grades)
                lines.append(f"\n  Средний балл: <b>{avg:.1f}</b>")
                lines.append(f"  Всего записей: {len(grades)}")

            summaries.append("\n".join(lines))

        if not summaries:
            continue

        msg = "📋 <b>Итоги дня</b>\n\n" + "\n\n".join(summaries)

        try:
            _bot.send_message(tg_id, msg, parse_mode='HTML')
            time.sleep(0.1)
        except Exception as e:
            logger.error(f"Failed to send evening summary to {tg_id}: {e}")

    logger.info("Daily evening summaries sent.")


def _send_bot_alive_status():
    """Отправляет сообщение «бот работает» родителям, у которых за день нет оценок."""
    from src.database_manager import get_all_parents_with_children, has_today_grades_for_parent

    if not _bot:
        return

    logger.info("Sending bot alive status to parents with no grades today...")

    parent_data = get_all_parents_with_children()
    notified = set()

    for row in parent_data:
        tg_id = row['telegram_id']
        if tg_id in notified:
            continue
        notified.add(tg_id)

        if has_today_grades_for_parent(tg_id):
            continue  # У этого родителя уже были оценки — не нужно

        try:
            _bot.send_message(
                tg_id,
                "✅ <b>Бот активен</b>\n\n"
                "Сегодня новых оценок пока не поступало. "
                "Я слежу за дневником и сообщу сразу, как появится новая запись.",
                parse_mode='HTML'
            )
            time.sleep(0.05)
        except Exception as e:
            logger.error(f"Failed to send alive status to {tg_id}: {e}")

    logger.info("Bot alive status sent.")
