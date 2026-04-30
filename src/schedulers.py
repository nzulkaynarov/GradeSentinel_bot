"""
Фоновые планировщики:
1. Ежедневная вечерняя сводка с трендами (19:00)
2. Утренняя агрегация отложенных уведомлений (07:00)
3. Статус «бот работает» (15:00, только если 48ч+ тишины)
4. Проверка четвертных оценок (12:00, 18:00)
5. Предупреждение об истечении подписки (10:00)
"""
import time
import logging
import threading
from datetime import datetime, timedelta

from src.notification_helpers import TIMEZONE_OFFSET_HOURS
from src.i18n import t

logger = logging.getLogger(__name__)

_bot = None
_scheduler_started = False


def set_bot_instance(bot):
    global _bot
    _bot = bot


def _get_local_now() -> datetime:
    return datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET_HOURS)


def start_daily_schedulers():
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True

    thread = threading.Thread(target=_scheduler_loop, daemon=True)
    thread.start()
    logger.info("Daily schedulers started (evening summary, quiet hours flush, bot alive).")

    # Одноразовый импорт истории для существующих студентов (в фоне)
    import_thread = threading.Thread(target=_startup_history_import, daemon=True)
    import_thread.start()


_job_locks = {
    'evening': threading.Lock(),
    'morning': threading.Lock(),
    'alive': threading.Lock(),
    'quarter': threading.Lock(),
    'subscription': threading.Lock(),
    'cleanup': threading.Lock(),
}

# In-memory кэш маркеров: {job: marker}. Источник правды — БД (settings),
# но проверяем сначала память, чтобы не делать read на каждом tick'е (180с).
# При первом запуске после рестарта лениво подгружаем из БД (см. _check_marker).
_marker_cache: dict = {}
_marker_cache_lock = threading.Lock()


def _last_run_key(job: str) -> str:
    return f"scheduler_last_{job}"


def _check_marker(job: str, marker: str) -> bool:
    """True, если задача с таким маркером УЖЕ выполнялась.

    Сначала смотрим в память; если пусто — лениво читаем из БД (один раз
    после рестарта). DB-write делаем только в _set_marker (после успеха).
    """
    with _marker_cache_lock:
        cached = _marker_cache.get(job)
        if cached is not None:
            return cached == marker

    # Холодный кэш — читаем БД один раз
    from src.database_manager import get_setting
    db_marker = get_setting(_last_run_key(job), "") or ""
    with _marker_cache_lock:
        # На случай гонки — не перезаписываем уже выставленный другим потоком
        if job not in _marker_cache:
            _marker_cache[job] = db_marker
    return db_marker == marker


def _set_marker(job: str, marker: str):
    """Записывает маркер в БД и в кэш. Вызывается после успешного выполнения job'а."""
    from src.database_manager import set_setting
    set_setting(_last_run_key(job), marker)
    with _marker_cache_lock:
        _marker_cache[job] = marker


def _run_job_safe(job: str, marker: str, func):
    """Запускает job под локом с проверкой, что задача ещё не выполнена сегодня.
    Маркер хранится в settings (переживает рестарт) + кэшируется в памяти
    (избегаем read на каждом tick'е)."""
    lock = _job_locks[job]
    if not lock.acquire(blocking=False):
        logger.warning(f"Scheduler job '{job}' already running, skipping overlap")
        return
    try:
        if _check_marker(job, marker):
            return  # уже выполнялось
        logger.info(f"Scheduler running job '{job}' (marker={marker})")
        try:
            func()
            _set_marker(job, marker)
        except Exception as e:
            logger.error(f"Scheduler job '{job}' failed: {e}", exc_info=True)
    finally:
        lock.release()


def _scheduler_loop():
    while True:
        try:
            now = _get_local_now()
            today_str = now.date().isoformat()

            # Проверка подписок раз в день в 10:00
            if now.hour == 10 and now.minute < 6:
                _run_job_safe('subscription', today_str, _check_subscription_expiry)

            if now.hour == 7 and now.minute < 6:
                _run_job_safe('morning', today_str, _flush_quiet_hours_queue)

            if now.hour == 15 and now.minute < 6:
                _run_job_safe('alive', today_str, _send_bot_alive_status)

            if now.hour == 19 and now.minute < 6:
                _run_job_safe('evening', today_str, _send_daily_evening_summary)

            # Проверка четвертных оценок 2 раза в день: 12:00 и 18:00
            if now.hour in (12, 18) and now.minute < 6:
                marker = f"{today_str}_{now.hour}"
                _run_job_safe('quarter', marker, _check_quarter_grades)

            # Еженедельная чистка БД (воскресенье, 03:00 по Ташкенту)
            if now.weekday() == 6 and now.hour == 3 and now.minute < 6:
                _run_job_safe('cleanup', today_str, _run_weekly_cleanup)

        except Exception as e:
            logger.error(f"Error in daily scheduler loop: {e}", exc_info=True)

        time.sleep(180)


def _flush_quiet_hours_queue():
    """Утренняя сводка: агрегирует ночные оценки по ученикам вместо свалки сырых сообщений."""
    from src.database_manager import (
        get_all_queued_telegram_ids, get_and_clear_queued_notifications,
        get_user_lang, get_students_for_parent, get_overnight_grades_for_student,
    )
    from src.notification_helpers import get_emotional_header

    if not _bot:
        return

    tg_ids = get_all_queued_telegram_ids()
    if not tg_ids:
        logger.info("No queued notifications to flush.")
        return

    logger.info(f"Flushing quiet hours queue for {len(tg_ids)} users.")

    for tg_id in tg_ids:
        # Очищаем очередь (обязательно, даже если сводка пустая)
        queued_messages = get_and_clear_queued_notifications(tg_id)
        if not queued_messages:
            continue

        lang = get_user_lang(tg_id)

        # Собираем реальную сводку из БД (дедуплицировано по предмету)
        students = get_students_for_parent(tg_id)
        student_blocks = []
        total_grades = 0

        for student in students:
            grades = get_overnight_grades_for_student(student['id'])
            if not grades:
                continue

            total_grades += len(grades)
            display_name = student.get('display_name') or student['fio']
            spreadsheet_id = student.get('spreadsheet_id', '')

            lines = [f"👨‍🎓 <b>{display_name}</b>\n"]
            numeric_grades = []

            for g in grades:
                _, emoji = get_emotional_header(g['grade_value'], g['raw_text'], lang)
                lines.append(f"  {g['subject']}: <b>{g['raw_text']}</b>  {emoji}")
                if g['grade_value'] is not None:
                    numeric_grades.append(g['grade_value'])

            if numeric_grades:
                avg = sum(numeric_grades) / len(numeric_grades)
                lines.append(f"\n  {t('daily_avg', lang, avg=f'{avg:.1f}')}")

            if spreadsheet_id:
                lines.append(
                    f"\n  <a href='https://docs.google.com/spreadsheets/d/{spreadsheet_id}'>"
                    f"{t('grades_open_sheet', lang)}</a>"
                )

            student_blocks.append("\n".join(lines))

        if student_blocks:
            header = t("quiet_morning_header", lang, count=total_grades)
            msg = header + "\n\n" + "\n\n".join(student_blocks)

            # Telegram limit: 4096 chars
            if len(msg) > 4000:
                # Шлём по одному ученику
                try:
                    _bot.send_message(tg_id, header, parse_mode='HTML')
                    time.sleep(0.05)
                    for block in student_blocks:
                        _bot.send_message(tg_id, block, parse_mode='HTML',
                                          disable_web_page_preview=True)
                        time.sleep(0.05)
                except Exception as e:
                    logger.error(f"Failed to send morning summary to {tg_id}: {e}")
            else:
                try:
                    _bot.send_message(tg_id, msg, parse_mode='HTML',
                                      disable_web_page_preview=True)
                except Exception as e:
                    logger.error(f"Failed to send morning summary to {tg_id}: {e}")
        else:
            # Нет оценок в БД (возможно, четвертные или другие уведомления) —
            # отправляем оригинальные сообщения из очереди как fallback
            header = t("quiet_morning_header", lang, count=len(queued_messages))
            combined = header + "\n\n" + "\n\n➖➖➖➖➖➖\n\n".join(queued_messages)
            try:
                if len(combined) > 4000:
                    _bot.send_message(tg_id, header, parse_mode='HTML')
                    time.sleep(0.05)
                    for qm in queued_messages:
                        _bot.send_message(tg_id, qm, parse_mode='HTML',
                                          disable_web_page_preview=True)
                        time.sleep(0.05)
                else:
                    _bot.send_message(tg_id, combined, parse_mode='HTML',
                                      disable_web_page_preview=True)
            except Exception as e:
                logger.error(f"Failed to send fallback morning messages to {tg_id}: {e}")


def _send_daily_evening_summary():
    from src.database_manager import (
        get_all_parents_with_children, get_today_grades_for_student,
        get_yesterday_grades_for_student, get_user_lang
    )

    if not _bot:
        return

    logger.info("Sending daily evening summaries...")

    parent_data = get_all_parents_with_children()

    from collections import defaultdict
    parents_map = defaultdict(list)
    for row in parent_data:
        parents_map[row['telegram_id']].append(row)

    for tg_id, children in parents_map.items():
        lang = get_user_lang(tg_id)
        summaries = []
        for child in children:
            grades = get_today_grades_for_student(child['student_id'])
            if not grades:
                continue

            lines = [f"👨‍🎓 <b>{child['display_name']}</b>\n"]
            numeric_grades = []
            subject_grades = {}

            for g in grades:
                lines.append(f"  {g['subject']}: <b>{g['raw_text']}</b>")
                if g['grade_value'] is not None:
                    numeric_grades.append(g['grade_value'])
                    subject_grades[g['subject']] = g['grade_value']

            if numeric_grades:
                avg = sum(numeric_grades) / len(numeric_grades)
                avg_line = t('daily_avg', lang, avg=f'{avg:.1f}')

                # Сравнение со вчера
                yesterday = get_yesterday_grades_for_student(child['student_id'])
                yesterday_numeric = [g['grade_value'] for g in yesterday if g['grade_value'] is not None]
                if yesterday_numeric:
                    y_avg = sum(yesterday_numeric) / len(yesterday_numeric)
                    if avg > y_avg + 0.05:
                        avg_line += f" {t('daily_trend_up', lang, yesterday=f'{y_avg:.1f}')}"
                    elif avg < y_avg - 0.05:
                        avg_line += f" {t('daily_trend_down', lang, yesterday=f'{y_avg:.1f}')}"

                lines.append(f"\n  {avg_line}")

                # Лучший и худший предмет
                if len(subject_grades) >= 2:
                    best_subj = max(subject_grades, key=subject_grades.get)
                    worst_subj = min(subject_grades, key=subject_grades.get)
                    if subject_grades[best_subj] > subject_grades[worst_subj]:
                        lines.append(f"  {t('daily_best', lang, subject=best_subj, grade=int(subject_grades[best_subj]))}")
                        if subject_grades[worst_subj] <= 3:
                            lines.append(f"  {t('daily_worst', lang, subject=worst_subj, grade=int(subject_grades[worst_subj]))}")

                lines.append(f"  {t('daily_total', lang, count=len(grades))}")

            summaries.append("\n".join(lines))

        if not summaries:
            continue

        msg = t("daily_summary_title", lang) + "\n\n" + "\n\n".join(summaries)

        try:
            _bot.send_message(tg_id, msg, parse_mode='HTML')
            time.sleep(0.1)
        except Exception as e:
            logger.error(f"Failed to send evening summary to {tg_id}: {e}")

    logger.info("Daily evening summaries sent.")


def _send_bot_alive_status():
    from src.database_manager import get_all_parents_with_children, has_recent_grades_for_parent, get_user_lang

    if not _bot:
        return

    logger.info("Checking bot alive status (only for parents with 48h+ silence)...")

    parent_data = get_all_parents_with_children()
    notified = set()
    sent_count = 0

    for row in parent_data:
        tg_id = row['telegram_id']
        if tg_id in notified:
            continue
        notified.add(tg_id)

        # Отправляем только если за последние 48 часов не было ни одной оценки
        if has_recent_grades_for_parent(tg_id, hours=48):
            continue

        lang = get_user_lang(tg_id)
        try:
            _bot.send_message(tg_id, t("bot_alive", lang), parse_mode='HTML')
            time.sleep(0.05)
            sent_count += 1
        except Exception as e:
            logger.error(f"Failed to send alive status to {tg_id}: {e}")

    logger.info(f"Bot alive status: sent to {sent_count} parents (with 48h+ silence).")


def _check_subscription_expiry():
    """Проверяет истечение подписок и предупреждает пользователей."""
    from src.database_manager import (
        get_families_expiring_in_days, get_families_expired_today,
        get_family_members_telegram_ids, get_user_lang
    )

    if not _bot:
        return

    warnings = [
        (7, "sub_expiry_7d"),
        (1, "sub_expiry_1d"),
    ]

    for days, key in warnings:
        families = get_families_expiring_in_days(days)
        for family in families:
            tg_ids = get_family_members_telegram_ids(family['family_id'])
            for tg_id in tg_ids:
                lang = get_user_lang(tg_id)
                try:
                    _bot.send_message(tg_id, t(key, lang), parse_mode='HTML')
                    time.sleep(0.05)
                except Exception as e:
                    logger.error(f"Failed to send sub warning to {tg_id}: {e}")

    # Истёкшие сегодня
    expired = get_families_expired_today()
    for family in expired:
        tg_ids = get_family_members_telegram_ids(family['family_id'])
        for tg_id in tg_ids:
            lang = get_user_lang(tg_id)
            try:
                _bot.send_message(tg_id, t("sub_expiry_0d", lang), parse_mode='HTML')
                time.sleep(0.05)
            except Exception as e:
                logger.error(f"Failed to send sub expired to {tg_id}: {e}")

    logger.info("Subscription expiry check completed.")


def _check_quarter_grades():
    """Запускает проверку четвертных оценок через monitor_engine."""
    try:
        from src.monitor_engine import check_for_quarter_changes
        logger.info("Running scheduled quarter grades check...")
        check_for_quarter_changes()
    except Exception as e:
        logger.error(f"Quarter grades check failed: {e}")


def _run_weekly_cleanup():
    """Архивирование старых оценок и чистка истёкших инвайтов/очередей.
    Безопасно вызывать в любое время."""
    from src.database_manager import (
        archive_old_grades, cleanup_old_notification_queue, cleanup_expired_invites
    )
    try:
        archive_old_grades(days=180)
        cleanup_old_notification_queue(hours=48)
        cleanup_expired_invites(days=30)
    except Exception as e:
        logger.error(f"Weekly cleanup failed: {e}", exc_info=True)


def _startup_history_import():
    """Одноразовый импорт истории при запуске бота."""
    try:
        time.sleep(10)  # Даём боту прогреться
        from src.history_importer import import_history_for_all_students
        logger.info("Starting one-time history import for existing students...")
        import_history_for_all_students()
        logger.info("One-time history import completed.")
    except Exception as e:
        logger.error(f"Startup history import failed: {e}")
