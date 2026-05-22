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
from datetime import datetime, timedelta, timezone

from src.notification_helpers import TIMEZONE_OFFSET_HOURS
from src.i18n import t

logger = logging.getLogger(__name__)

_bot = None
_scheduler_started = False


def set_bot_instance(bot):
    global _bot
    _bot = bot


def _get_local_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=TIMEZONE_OFFSET_HOURS)


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
    'weekly_text_digest': threading.Lock(),
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


def _track_ai_outcome(job_name: str, success: bool):
    """NAV-010: трекать AI fail/success по scheduler job'у.

    После _AI_FAIL_THRESHOLD подряд-fails уведомляем admin'а через Telegram
    (с cooldown 24h — не спамим). При success резетим счётчик.

    Используется в jobs которые делают Anthropic API вызовы:
    - _check_proactive_alerts (proactive)
    - _send_weekly_reports в analytics.py (weekly)
    Если ANTHROPIC_API_KEY невалидный или Anthropic down — admin узнает быстро."""
    import os as _os
    from src.database_manager import get_setting, set_setting

    key = f"ai_consec_fails_{job_name}"
    last_notify_key = f"ai_fail_last_notify_{job_name}"

    if success:
        try:
            if int(get_setting(key) or "0") > 0:
                logger.info(f"AI job '{job_name}' recovered.")
        except (ValueError, TypeError):
            pass
        set_setting(key, "0")
        return

    # Fail — incrementим счётчик
    try:
        current = int(get_setting(key) or "0")
    except (ValueError, TypeError):
        current = 0
    current += 1
    set_setting(key, str(current))

    if current < _AI_FAIL_THRESHOLD:
        return

    # Проверяем cooldown notification'а — не больше раза в _AI_FAIL_NOTIFY_COOLDOWN_HOURS
    last_notify = get_setting(last_notify_key)
    now_iso = _get_local_now().isoformat()
    if last_notify:
        try:
            last_dt = datetime.fromisoformat(last_notify)
            if (_get_local_now() - last_dt).total_seconds() < _AI_FAIL_NOTIFY_COOLDOWN_HOURS * 3600:
                return
        except (ValueError, TypeError):
            pass

    admin_id_raw = _os.environ.get("ADMIN_ID")
    if not admin_id_raw or not _bot:
        return
    try:
        admin_id = int(admin_id_raw)
    except (ValueError, TypeError):
        return
    try:
        _bot.send_message(
            admin_id,
            f"⚠️ <b>AI service issue</b>\n\n"
            f"Job <code>{job_name}</code> failed {current} times in a row.\n"
            f"Check <code>ANTHROPIC_API_KEY</code> и Anthropic status.\n\n"
            f"Следующий alert не раньше чем через {_AI_FAIL_NOTIFY_COOLDOWN_HOURS}ч.",
            parse_mode='HTML',
        )
        set_setting(last_notify_key, now_iso)
        logger.warning(
            f"NAV-010: admin notified about {job_name} failing {current} times."
        )
    except Exception as e:
        logger.warning(f"Failed to notify admin about AI failures ({job_name}): {e}")


_AI_FAIL_THRESHOLD = 3
_AI_FAIL_NOTIFY_COOLDOWN_HOURS = 24


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
            from src.error_reporter import report
            report(f"scheduler.{job}", e, marker=marker)
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

            # PR_H5: proactive AI alerts (17:00 — после окончания школьного дня,
            # до evening summary в 19:00). Раз в день, dedup на 48ч в БД.
            if now.hour == 17 and now.minute < 6:
                _run_job_safe('proactive_alerts', today_str, _check_proactive_alerts)

            # Проверка четвертных оценок 2 раза в день: 12:00 и 18:00
            if now.hour in (12, 18) and now.minute < 6:
                marker = f"{today_str}_{now.hour}"
                _run_job_safe('quarter', marker, _check_quarter_grades)

            # Еженедельная чистка БД (воскресенье, 03:00 по Ташкенту)
            if now.weekday() == 6 and now.hour == 3 and now.minute < 6:
                _run_job_safe('cleanup', today_str, _run_weekly_cleanup)

            # Бесплатный текстовый weekly digest для всех (воскресенье, 18:00).
            # AI-версия (премиум) идёт отдельным scheduler'ом в 19:00 в analytics.py.
            if now.weekday() == 6 and now.hour == 18 and now.minute < 6:
                _run_job_safe('weekly_text_digest', today_str, _send_weekly_text_digest)

        except Exception as e:
            from src.error_reporter import report
            report("scheduler.loop", e)

        time.sleep(180)


def _dedup_preserve_order(messages):
    """Удаляет точные дубли сообщений с сохранением порядка первого появления.

    Defense in depth для morning flush — если когда-нибудь баг создаст
    дубли в очереди (как инцидент 2026-05-21 с cell_reference race),
    родитель получит каждое сообщение один раз, не 14 копий.
    """
    seen = set()
    result = []
    for m in messages:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result


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

        # Defense in depth: дедуплицируем сообщения с сохранением порядка.
        # Если когда-нибудь monitor / другой writer положит одну оценку
        # несколько раз (как в инциденте 2026-05-21), родитель не получит
        # 14 копий — только одну. Этот fallback path рендерится без
        # агрегации по предметам, так что dedup здесь — последняя защита.
        queued_messages = _dedup_preserve_order(queued_messages)

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

    # Параллельный flush для групповых чатов. Inline-markup не сохраняли —
    # шлём plain HTML, и без агрегации по предметам (текст уже отформатирован
    # monitor'ом при queue). Это «дамп всё что накопилось» — допустимый UX
    # для семейного чата (там и так шумно), цель — не потерять сообщения.
    from src.database_manager import (
        get_all_queued_group_targets, get_and_clear_queued_group_notifications,
    )
    group_targets = get_all_queued_group_targets()
    if group_targets:
        logger.info(f"Flushing group notification queue for {len(group_targets)} targets.")
    for tgt in group_targets:
        chat_id = tgt['chat_id']
        thread_id = tgt['message_thread_id']
        messages = get_and_clear_queued_group_notifications(chat_id, thread_id)
        # Defense in depth: дедуп для групп — см. комментарий выше.
        messages = _dedup_preserve_order(messages)
        if not messages:
            continue
        for m in messages:
            try:
                kwargs = {'parse_mode': 'HTML', 'disable_web_page_preview': True}
                if thread_id is not None:
                    kwargs['message_thread_id'] = thread_id
                _bot.send_message(chat_id, m, **kwargs)
                time.sleep(0.05)
            except Exception as e:
                # Бот мог быть кикнут / тема удалена — не блокируем остальные.
                logger.warning(
                    f"Failed to flush group notification to chat={chat_id} thread={thread_id}: {e}"
                )


def _send_daily_evening_summary():
    from src.database_manager import (
        get_all_parents_with_children, get_today_grades_for_student,
        get_yesterday_grades_for_student, get_user_lang
    )
    from src.notifications import get_sender, NotificationType

    if not _bot:
        return

    try:
        sender = get_sender()
    except RuntimeError:
        logger.warning("Sender not initialized for evening summary.")
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
        sender.send(tg_id, msg, ntype=NotificationType.EVENING_SUMMARY)
        time.sleep(0.1)

    logger.info("Daily evening summaries sent.")


def _send_bot_alive_status():
    from src.database_manager import get_all_parents_with_children, has_recent_grades_for_parent, get_user_lang
    from src.notifications import get_sender, NotificationType

    if not _bot:
        return
    try:
        sender = get_sender()
    except RuntimeError:
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
        if sender.send(tg_id, t("bot_alive", lang), ntype=NotificationType.BOT_ALIVE):
            sent_count += 1
        time.sleep(0.05)

    logger.info(f"Bot alive status: sent to {sent_count} parents (with 48h+ silence).")


def _check_subscription_expiry():
    """Проверяет истечение подписок и предупреждает пользователей.

    Per-family-per-window idempotency через scheduler_last_sub_expiry_{fid}_{window}.
    Раньше (без маркера) запуск scheduler'а дважды за день = двойное сообщение,
    при рестарте бота 10:00+10:05 → юзеры получали уведомление 2 раза.
    """
    from src.database_manager import (
        get_families_expiring_in_days, get_families_expired_today,
        get_family_members_telegram_ids, get_user_lang,
        get_setting, set_setting,
    )
    from src.notifications import get_sender, NotificationType

    if not _bot:
        return
    try:
        sender = get_sender()
    except RuntimeError:
        return

    today_str = _get_local_now().strftime("%Y-%m-%d")

    def _already_sent(family_id: int, window: str) -> bool:
        marker_key = f"sub_expiry_sent_{family_id}_{window}"
        return get_setting(marker_key) == today_str

    def _mark_sent(family_id: int, window: str):
        marker_key = f"sub_expiry_sent_{family_id}_{window}"
        set_setting(marker_key, today_str)

    windows = [
        (7, "sub_expiry_7d", "7d"),
        (1, "sub_expiry_1d", "1d"),
    ]

    for days, lang_key, window in windows:
        families = get_families_expiring_in_days(days)
        for family in families:
            fid = family['family_id']
            if _already_sent(fid, window):
                continue
            tg_ids = get_family_members_telegram_ids(fid)
            for tg_id in tg_ids:
                lang = get_user_lang(tg_id)
                sender.send(
                    tg_id, t(lang_key, lang),
                    ntype=NotificationType.SUBSCRIPTION_EXPIRY,
                )
                time.sleep(0.05)
            _mark_sent(fid, window)

    # Истёкшие сегодня
    expired = get_families_expired_today()
    for family in expired:
        fid = family['family_id']
        if _already_sent(fid, "0d"):
            continue
        tg_ids = get_family_members_telegram_ids(fid)
        for tg_id in tg_ids:
            lang = get_user_lang(tg_id)
            sender.send(
                tg_id, t("sub_expiry_0d", lang),
                ntype=NotificationType.SUBSCRIPTION_EXPIRY,
            )
            time.sleep(0.05)
        _mark_sent(fid, "0d")

    logger.info("Subscription expiry check completed.")


def _check_quarter_grades():
    """Запускает проверку четвертных оценок через monitor_engine."""
    try:
        from src.monitor_engine import check_for_quarter_changes
        logger.info("Running scheduled quarter grades check...")
        check_for_quarter_changes()
    except Exception as e:
        logger.error(f"Quarter grades check failed: {e}")


def _send_weekly_text_digest():
    """Бесплатный воскресный текстовый дайджест для всех родителей.
    AI-версия премиум — это отдельный scheduler в analytics.py.
    Здесь — простой пересчёт: лучший/худший предмет, среднее, всего оценок."""
    from collections import defaultdict
    from src.database_manager import (
        get_all_parents_with_children, get_grade_history_for_student,
        get_user_lang,
    )

    if not _bot:
        return

    logger.info("Sending free weekly text digests...")
    parent_data = get_all_parents_with_children()
    by_parent = defaultdict(list)
    for row in parent_data:
        by_parent[row['telegram_id']].append(row)

    today = _get_local_now()
    week_start = (today - timedelta(days=7)).strftime('%d.%m')
    week_end = today.strftime('%d.%m')
    week_range = f"{week_start}–{week_end}"

    sent = 0
    for tg_id, children in by_parent.items():
        lang = get_user_lang(tg_id)
        sections = []
        for child in children:
            grades = get_grade_history_for_student(child['student_id'], days=7)
            display_name = child.get('display_name') or child['fio']
            if not grades:
                continue
            numeric = [g['grade_value'] for g in grades if g['grade_value'] is not None]
            if not numeric:
                continue
            avg = sum(numeric) / len(numeric)
            # Лучший/худший предмет — берём по среднему
            by_subj = defaultdict(list)
            for g in grades:
                if g['grade_value'] is not None:
                    by_subj[g['subject']].append(g['grade_value'])
            subj_avg = {s: sum(v)/len(v) for s, v in by_subj.items()}

            section = [t("weekly_digest_title", lang, name=display_name, week_range=week_range)]
            section.append(t("weekly_digest_total", lang, count=len(grades), avg=f"{avg:.1f}"))
            if len(subj_avg) >= 2:
                best_s = max(subj_avg, key=subj_avg.get)
                worst_s = min(subj_avg, key=subj_avg.get)
                section.append(t("weekly_digest_best", lang, subject=best_s, grade=f"{subj_avg[best_s]:.1f}"))
                if subj_avg[worst_s] <= 3.5:
                    section.append(t("weekly_digest_worst", lang, subject=worst_s, grade=f"{subj_avg[worst_s]:.1f}"))
            sections.append("\n".join(section))

        if not sections:
            continue

        msg = "\n\n".join(sections) + t("weekly_digest_premium_hint", lang)
        from src.notifications import get_sender, NotificationType
        if get_sender().send(tg_id, msg, ntype=NotificationType.WEEKLY_DIGEST):
            sent += 1
        time.sleep(0.05)
    logger.info(f"Weekly text digest sent to {sent} parents.")


def _check_proactive_alerts():
    """PR_H5: ежедневно (17:00) сканирует активных учеников на аномалии
    в оценках и шлёт AI-сгенерированный alert родителям семьи.

    MVP — один тип аномалии: серия из 3+ оценок ≤3 за последние 7 дней.
    Dedup'нуто через proactive_alerts (cooldown 48ч на тип×ребёнка).
    В тихие часы alert кладётся в notification_queue → flush утром.

    Безопасно деградирует: один сломанный student не валит цикл."""
    from src.db.families import get_active_spreadsheets_with_subscription, get_families_for_student
    from src.database_manager import (
        was_alerted_recently, save_alert, get_user_lang,
        get_family_members_telegram_ids,
    )
    from src.analytics_engine import detect_anomalies, generate_proactive_alert

    students = get_active_spreadsheets_with_subscription()
    if not students:
        return

    total_sent = 0
    total_skipped_dedup = 0
    total_anomalies = 0
    ai_calls = 0       # NAV-010: счётчик вызовов AI в этом цикле
    ai_successes = 0   # NAV-010: счётчик успешных AI ответов

    for s in students:
        student_id = s['student_id']
        student_name = s.get('display_name') or s.get('fio') or 'ученик'
        try:
            anomalies = detect_anomalies(student_id)
        except Exception as e:
            logger.warning(f"detect_anomalies failed for student {student_id}: {e}")
            continue

        for anomaly in anomalies:
            total_anomalies += 1
            atype = anomaly['type']

            if was_alerted_recently(student_id, atype):
                total_skipped_dedup += 1
                continue

            # Резолвим семьи для отправки — берём ВСЕХ telegram_id членов
            families = get_families_for_student(student_id)
            if not families:
                continue
            recipients = []
            for fam in families:
                recipients.extend(get_family_members_telegram_ids(fam['id']))
            recipients = list(dict.fromkeys(recipients))  # dedup
            if not recipients:
                continue

            # Lang берём первого получателя — у разных родителей в семье
            # обычно один язык. Если разные — first wins, остальные получат
            # на том же языке. Acceptable для MVP.
            lang = get_user_lang(recipients[0]) or 'ru'

            ai_calls += 1
            text = generate_proactive_alert(student_name, anomaly, lang=lang)
            if not text:
                # NAV-010: счётчик AI fail; цикл всё равно continue.
                # Если ВСЕ вызовы в этом цикле fail'нут → trigger admin alert
                # (см. _track_ai_outcome вызов после loop'а).
                continue
            ai_successes += 1

            # Префиксуем заголовком чтобы alert выделялся среди обычных
            # notification'ов об оценках. Заголовок через locale (раньше
            # был hardcoded dict heading_by_lang).
            full_text = f"{t('alert_grades_heading', lang)}\n\n{text}"

            from src.notifications import get_sender, NotificationType
            sender = get_sender()
            for tg_id in recipients:
                if sender.send(tg_id, full_text, ntype=NotificationType.PROACTIVE_ALERT):
                    total_sent += 1
                time.sleep(0.05)  # gentle pacing

            try:
                save_alert(student_id, atype)
            except Exception as e:
                logger.warning(f"save_alert failed for student {student_id}: {e}")

    logger.info(
        f"Proactive alerts cycle: anomalies={total_anomalies}, "
        f"sent={total_sent}, deduped={total_skipped_dedup}, "
        f"ai_calls={ai_calls}, ai_successes={ai_successes}"
    )
    # NAV-010: если все AI вызовы fail'нули, считаем cycle проваленным.
    # 0 calls (нет аномалий) = не fail (нет данных для оценки).
    if ai_calls > 0:
        _track_ai_outcome('proactive_alerts', success=(ai_successes > 0))


def _run_weekly_cleanup():
    """Архивирование старых оценок и чистка истёкших инвайтов/очередей.
    Безопасно вызывать в любое время. Параметры по умолчанию из src/config.py."""
    from src.db.maintenance import (
        archive_old_grades, cleanup_old_notification_queue, cleanup_expired_invites
    )
    try:
        archive_old_grades()
        cleanup_old_notification_queue()
        cleanup_expired_invites()
    except Exception as e:
        from src.error_reporter import report
        report("scheduler.weekly_cleanup", e)


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
