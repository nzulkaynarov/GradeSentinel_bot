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
    get_parents_for_student, get_user_lang, get_grade_history_for_student_all
)
from src.analytics_engine import analyze_student_grades, generate_weekly_summary, AIAnalyticsError
from src.i18n import t

logger = logging.getLogger(__name__)

# ====================
# Команда: AI-анализ по запросу
# ====================
@bot.message_handler(commands=['ai_report'])
def ai_report_command(message):
    _handle_ai_report(message.chat.id)


def cmd_ai_report(message):
    _handle_ai_report(message.chat.id)


def _handle_ai_report(user_id: int):
    lang = get_user_lang(user_id)
    if not os.environ.get("ANTHROPIC_API_KEY"):
        send_content(user_id, t("ai_unavailable", lang))
        return

    # AI-анализ — премиум функция, проверяем подписку
    from src.database_manager import has_any_active_subscription
    if not has_any_active_subscription(user_id):
        role = get_parent_role(user_id)
        if role != 'admin':
            send_content(user_id, t("sub_required_ai", lang))
            return

    students = get_students_for_parent(user_id)
    if not students:
        send_content(user_id, t("ai_no_students", lang))
        return

    send_content(user_id, t("ai_loading", lang))

    for student in students:
        student_id = student['id']
        display_name = student.get('display_name') or student['fio']

        try:
            analysis = analyze_student_grades(student_id, display_name, lang=lang)
        except AIAnalyticsError:
            send_content(user_id, t("ai_error", lang, name=display_name))
            continue

        if analysis:
            msg = t("ai_report_title", lang, name=display_name, analysis=analysis)
            send_content(user_id, msg)
        else:
            send_content(user_id, t("ai_not_enough_data", lang, name=display_name))


# ====================
# Еженедельная рассылка AI-отчётов (воскресенье 19:00)
# ====================
_scheduler_running = False


def start_weekly_scheduler():
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
    while True:
        now = datetime.now()
        if now.weekday() == 6 and now.hour == 19 and now.minute < 5:
            logger.info("Triggering weekly AI reports...")
            try:
                _send_weekly_reports()
            except Exception as e:
                logger.error(f"Error in weekly AI reports: {e}")
            time.sleep(3600)
        else:
            time.sleep(300)


def _send_weekly_reports():
    students = get_active_spreadsheets()
    processed_pairs = set()

    # NAV-010: считаем AI fail/success по cycle. Если все Anthropic вызовы
    # fail'нут — _track_ai_outcome предупредит admin'а через Telegram
    # после _AI_FAIL_THRESHOLD подряд-проваленных cycles.
    ai_calls = 0
    ai_successes = 0

    for student in students:
        student_id = student['student_id']
        display_name = student.get('display_name') or student['fio']

        # Лето/каникулы: нет свежих оценок за неделю → отчёт пустой
        # (analyze_student_grades вернёт None ДО вызова Anthropic). Пропускаем,
        # НЕ считая за AI-fail — иначе пустота шлёт ложный admin-алерт «Check
        # ANTHROPIC_API_KEY» (так было 3 воскресенья подряд: 31.05/07.06/14.06).
        # Порог зеркалит guard анализатора (7 дней, ≥2 числовых оценки).
        recent = get_grade_history_for_student_all(student_id, days=7)
        if len([g for g in recent if g.get('grade_value') is not None]) < 2:
            continue

        parent_ids = get_parents_for_student(student_id)
        for tg_id in parent_ids:
            pair_key = (tg_id, student_id)
            if pair_key in processed_pairs:
                continue
            processed_pairs.add(pair_key)

            lang = get_user_lang(tg_id)
            ai_calls += 1
            analysis = generate_weekly_summary(student_id, display_name, lang=lang)
            if not analysis:
                continue
            ai_successes += 1

            msg = t("ai_weekly_title", lang, name=display_name, analysis=analysis)
            try:
                bot.send_message(tg_id, msg, parse_mode='HTML')
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"Failed to send weekly report to {tg_id}: {e}")

    # NAV-010 tracking — если все вызовы fail'нули, предупредить admin.
    if ai_calls > 0:
        from src.schedulers import _track_ai_outcome
        _track_ai_outcome('weekly_reports', success=(ai_successes > 0))
