import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from collections import defaultdict, Counter
from typing import List, Optional, Tuple
from telebot import types
from src.database_manager import (
    get_active_spreadsheets, add_grade, get_parents_for_student,
    update_student_display_name, queue_notification, get_user_lang,
    get_existing_grade, update_grade, get_active_spreadsheets_with_subscription,
    upsert_quarter_grade, get_db_connection, get_notify_mode
)
from src.google_sheets import get_sheet_data, get_spreadsheet_title
from src.data_cleaner import sanitize_grade, sanitize_cell
from src.utils import clean_student_name
from src.notification_helpers import (
    format_grade_notification, format_grade_change_notification, is_quiet_hours,
    format_quarter_new_notification, format_quarter_change_notification,
    format_batched_notification
)
from src.i18n import t

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_bot = None

from src.config import (
    FETCH_WORKERS as _FETCH_WORKERS,
    SHEET_FAILURE_THRESHOLD as _FAILURE_THRESHOLD,
    SHEET_FAILURE_ALERT_COOLDOWN_HOURS as _FAILURE_ALERT_COOLDOWN_HOURS,
)

# Защита от перекрытия циклов polling
_polling_lock = threading.Lock()
# Учёт consecutive failures по ученикам — для алерта при «зависшей» таблице
_student_failure_counts: dict = defaultdict(int)
# Предотвращаем повторные алерты по одному и тому же ученику чаще раза в день
_last_failure_alert: dict = {}

# ─────────────────────────────────────────────────────────────
# Двухфазное подтверждение оценок («fixtures»)
# ─────────────────────────────────────────────────────────────
# Защита от ложных уведомлений из-за опечаток учителя:
# учитель ввёл «5», бот моментально шлёт уведомление, через минуту
# учитель стирает / меняет — родитель получил «оценку-призрак».
#
# Решение: НЕ слать сразу. Первый раз увидели изменение → положили в
# pending. На следующем polling-цикле (через ~5 мин) если значение всё
# ещё то же → подтверждено, пишем в БД + уведомление. Если изменилось /
# пропало → молча отбрасываем (типо).
#
# Хранение in-memory: переживает один-два цикла, при рестарте
# пересоздаётся (грейды попадут в pending заново на след. цикле → 5-10
# мин задержки после рестарта). TTL чистит stale записи.
_pending_lock = threading.Lock()
# (student_id, cell_reference) -> {'raw_text': str, 'first_seen': float}
_pending_grades: dict = {}
_PENDING_TTL_SECONDS = 1800  # 30 мин: очищаем зависшие pending (теоретически 2-3 цикла)


def _check_pending_confirmation(student_id: int, cell_ref: str, new_raw_text: str) -> bool:
    """True если значение совпало с прошлым циклом (подтверждено).
    False если первый раз видим или значение изменилось — пометили pending,
    ждём следующий цикл."""
    now = time.time()
    with _pending_lock:
        # GC старых записей
        stale = [k for k, v in _pending_grades.items()
                 if now - v['first_seen'] > _PENDING_TTL_SECONDS]
        for k in stale:
            _pending_grades.pop(k, None)

        key = (student_id, cell_ref)
        existing = _pending_grades.get(key)
        if existing and existing['raw_text'] == new_raw_text:
            _pending_grades.pop(key, None)
            return True
        # Новое или изменённое pending — запоминаем, не уведомляем сейчас
        _pending_grades[key] = {'raw_text': new_raw_text, 'first_seen': now}
        return False


def _compute_added_grades(
    old: List[Tuple[Optional[float], str]],
    new: List[Tuple[Optional[float], str]],
) -> List[Tuple[Optional[float], str]]:
    """Multiset diff: какие оценки появились в new которых не было в old.
    «2» → «2/5» вернёт [(5.0, '5')]. «» → «2/5» вернёт обе. «2/5» → «2» вернёт []."""
    old_counter = Counter(t for _, t in old)
    added: List[Tuple[Optional[float], str]] = []
    for g, t in new:
        if old_counter[t] > 0:
            old_counter[t] -= 1
        else:
            added.append((g, t))
    return added


def _cell_avg_grade(grades: List[Tuple[Optional[float], str]]) -> Optional[float]:
    """Среднее численных оценок в ячейке для grade_history.grade_value.
    Для «2/5» вернёт 3.5. Спец-токены («н») игнорируются."""
    nums = [g for g, _ in grades if g is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def _cell_raw_text(grades: List[Tuple[Optional[float], str]]) -> str:
    """«Канонический» raw_text ячейки: соединение через «/».
    Для [(2,'2'),(5,'5')] → '2/5'. Для [(None,'н')] → 'н'."""
    return "/".join(t for _, t in grades)

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

def send_notification(telegram_ids, message, inline_markup=None, force=False):
    """
    Отправляет уведомление. В тихие часы (22:00-07:00) копит в очередь.
    Пользователи в режиме 'summary_only' не получают мгновенных уведомлений
    (кроме force=True для четвертных оценок).
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

        # Проверяем режим уведомлений
        if not force:
            notify_mode = get_notify_mode(tg_id)
            if notify_mode == 'summary_only':
                logger.info(f"Skipped notification for TG:{tg_id} (summary_only mode)")
                continue

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


def _send_to_groups_for_student(student_id: int, message, inline_markup, parent_tg_ids):
    """Шлёт сообщение в групповые чаты, привязанные к семьям ученика.
    Язык — берём от первого родителя в `parent_tg_ids` (вся семья обычно одного языка).
    Для супергрупп с темами уважаем `message_thread_id`."""
    from src.db.groups import get_groups_for_student
    try:
        groups = get_groups_for_student(student_id)
    except Exception as e:
        logger.error(f"Failed to fetch groups for student {student_id}: {e}")
        return
    if not groups:
        return

    # Выбираем версию сообщения. Если message — dict, берём по первому родителю.
    # Если все варианты совпадают — пофиг чьим языком пользоваться.
    if isinstance(message, dict):
        first_tg = next(iter(parent_tg_ids), None) if parent_tg_ids else None
        msg_text = message.get(first_tg) if first_tg in message else next(iter(message.values()), "")
    else:
        msg_text = message

    if isinstance(inline_markup, dict):
        first_tg = next(iter(parent_tg_ids), None) if parent_tg_ids else None
        kb = inline_markup.get(first_tg) if first_tg in inline_markup else None
    else:
        kb = inline_markup

    for grp in groups:
        chat_id = grp['chat_id']
        thread_id = grp.get('message_thread_id')
        try:
            kwargs = {
                'parse_mode': 'HTML',
                'disable_web_page_preview': True,
                'reply_markup': kb,
            }
            if thread_id is not None:
                kwargs['message_thread_id'] = thread_id
            _bot.send_message(chat_id, msg_text, **kwargs)
            logger.info(
                f"Group notification sent to chat={chat_id} thread={thread_id} (student={student_id})"
            )
        except Exception as e:
            # Бот мог быть кикнут, потерять права, или тема удалена.
            # Не валим уведомления родителям из-за этого.
            logger.warning(f"Failed to send group notification to {chat_id} (thread={thread_id}): {e}")

def _record_student_failure(student_id: int, display_name: str):
    """Учитывает неудачную попытку чтения таблицы. После N подряд — алерт админу."""
    _student_failure_counts[student_id] += 1
    count = _student_failure_counts[student_id]
    if count >= _FAILURE_THRESHOLD:
        last_alert = _last_failure_alert.get(student_id)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if last_alert is None or (now - last_alert).total_seconds() > _FAILURE_ALERT_COOLDOWN_HOURS * 3600:
            _last_failure_alert[student_id] = now
            logger.error(
                f"[SHEET STUCK] student_id={student_id} ({display_name}): "
                f"{count} consecutive failures fetching data"
            )


def _record_student_success(student_id: int):
    """Сбрасывает счётчик неудач при успешном чтении."""
    if student_id in _student_failure_counts:
        _student_failure_counts.pop(student_id, None)


def _fetch_student_sheet(student: dict, range_name: str):
    """Worker: загружает данные таблицы одного ученика. Возвращает (student, data, display_name).
    Гарантирует что одна сломанная таблица не валит весь цикл — все исключения ловятся."""
    student_id = student['student_id']
    fio = student['fio']
    spreadsheet_id = student['spreadsheet_id']

    display_name = student.get('display_name')
    if not display_name:
        try:
            sheet_title = get_spreadsheet_title(spreadsheet_id)
        except Exception as e:
            logger.error(f"Title fetch failed for student {student_id}: {e}")
            sheet_title = None
        display_name = clean_student_name(sheet_title) if sheet_title else fio
        try:
            update_student_display_name(student_id, display_name)
        except Exception as e:
            logger.error(f"Failed to update display_name for {student_id}: {e}")

    try:
        data = get_sheet_data(spreadsheet_id, range_name)
    except Exception as e:
        logger.error(f"Unexpected error fetching data for {display_name} (id={student_id}): {e}")
        _record_student_failure(student_id, display_name)
        return student, None, display_name

    if data is None:
        _record_student_failure(student_id, display_name)
    else:
        _record_student_success(student_id)

    return student, data, display_name


def check_for_new_grades():
    if not _polling_lock.acquire(blocking=False):
        logger.warning("Previous polling cycle still running, skipping this iteration")
        return
    try:
        _check_for_new_grades_impl()
    finally:
        _polling_lock.release()


def _check_for_new_grades_impl():
    students = get_active_spreadsheets_with_subscription()
    if not students:
        logger.info("No active students with spreadsheets found.")
        return

    logger.info(f"Starting check for {len(students)} students (parallel, workers={_FETCH_WORKERS}).")

    RANGE_NAME = "Сегодня!A1:B50"

    # Smart Batching: собираем все оценки за цикл, отправляем сгруппированно
    batch = defaultdict(list)
    # Метаданные для каждого студента
    student_meta = {}  # student_id -> {display_name, spreadsheet_id}

    # Параллельная загрузка данных — одна сломанная таблица не блокирует остальные
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as executor:
        futures = {executor.submit(_fetch_student_sheet, s, RANGE_NAME): s for s in students}
        fetched = []
        for future in as_completed(futures):
            try:
                fetched.append(future.result())
            except Exception as e:
                s = futures[future]
                logger.error(f"Worker crashed for student_id={s.get('student_id')}: {e}")

    # Дальнейшая обработка — последовательная (все DB-операции)
    for student, data, display_name in fetched:
        student_id = student['student_id']
        fio = student['fio']
        spreadsheet_id = student['spreadsheet_id']

        student_meta[student_id] = {'display_name': display_name, 'spreadsheet_id': spreadsheet_id}

        if data is None:
            logger.warning(f"Data fetch returned None for {display_name}. Skipping this cycle.")
            continue

        logger.info(f"Processing sheet for student: {display_name} (ID: {student_id})")

        for row_idx, row in enumerate(data[1:], start=2):
            if not isinstance(row, list) or len(row) < 2:
                continue

            subject = str(row[0]).strip()
            raw_grade = str(row[1]).strip()

            if not raw_grade:
                continue

            # Используем дату по Ташкенту (UTC+5) для корректной привязки к учебному дню
            # Ключ: предмет + дата (не row_idx, т.к. строки могут сдвигаться при вставке/удалении)
            tashkent_today = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date().isoformat()
            cell_reference = f"Сегодня!{subject}:{tashkent_today}"

            # Парсим ячейку как список оценок (поддержка X/Y формата)
            new_grades = sanitize_cell(raw_grade)
            if not new_grades:
                # Мусор / неизвестный токен — пропускаем (как раньше)
                continue

            new_clean_text = _cell_raw_text(new_grades)
            new_grade_value = _cell_avg_grade(new_grades)

            existing = get_existing_grade(student_id, cell_reference)
            old_clean_text = existing['raw_text'] if existing else None
            old_grades = sanitize_cell(old_clean_text) if old_clean_text else []

            # Нет изменений по сравнению с БД — следующая ячейка
            if old_clean_text == new_clean_text:
                continue

            # Что РЕАЛЬНО добавилось (multiset diff)
            added = _compute_added_grades(old_grades, new_grades)

            # Случай «удаления» (старое было длиннее, новых оценок нет):
            # тихо обновляем БД, без уведомления. Родителю незачем знать что
            # учитель что-то стёр.
            if not added:
                if existing:
                    update_grade(student_id, cell_reference, new_grade_value, new_clean_text)
                    logger.info(
                        f"[GRADE TRIMMED] {display_name}: {subject} "
                        f"'{old_clean_text}' -> '{new_clean_text}' (no notif)"
                    )
                continue

            # Двухфазное подтверждение: первый раз видим это изменение → ждём
            # следующего цикла. Это убирает «оценки-призраки» от опечаток учителя.
            if not _check_pending_confirmation(student_id, cell_reference, new_clean_text):
                logger.info(
                    f"[PENDING] {display_name}: {subject} '{new_clean_text}' — "
                    f"ждём подтверждения на следующем цикле"
                )
                continue

            # Подтверждено — пишем в БД.
            # grade_date = tashkent_today: дата оценки по факту (это «сегодня»
            # с точки зрения учебного дня, и она же зашита в cell_reference).
            if existing:
                update_grade(student_id, cell_reference, new_grade_value, new_clean_text)
                logger.info(
                    f"[GRADE CHANGED] {display_name}: {subject} "
                    f"'{old_clean_text}' -> '{new_clean_text}' (added: {[t for _, t in added]})"
                )
            else:
                add_grade(student_id, subject, new_grade_value, new_clean_text,
                          cell_reference, grade_date=tashkent_today)
                logger.info(f"[NEW GRADE] {display_name} got '{new_clean_text}' in {subject}")

            # Грейд для эмоционального заголовка — среднее ДОБАВЛЕННЫХ
            # (чтобы эмоция отражала ЧТО НОВОЕ пришло, а не что было до).
            added_nums = [g for g, _ in added if g is not None]
            emo_grade_value = (sum(added_nums) / len(added_nums)) if added_nums else None

            parents_ids = get_parents_for_student(student_id)
            for tg_id in parents_ids:
                if old_clean_text:
                    # «2» → «2/5»: показываем переход полной ячейки
                    batch[(tg_id, student_id)].append({
                        'subject': subject,
                        'clean_text': new_clean_text,
                        'grade_value': emo_grade_value,
                        'change_type': 'changed',
                        'old_text': old_clean_text,
                    })
                else:
                    # «» → «2/5»: новая запись, отображаем полную ячейку
                    batch[(tg_id, student_id)].append({
                        'subject': subject,
                        'clean_text': new_clean_text,
                        'grade_value': emo_grade_value,
                        'change_type': 'new',
                        'old_text': None,
                    })

    # Отправляем собранные уведомления
    for (tg_id, student_id), grades in batch.items():
        meta = student_meta[student_id]
        lang = get_user_lang(tg_id)

        if len(grades) == 1:
            # Одна оценка — детальное уведомление (как раньше)
            g = grades[0]
            if g['change_type'] == 'changed':
                msg = format_grade_change_notification(
                    meta['display_name'], g['subject'], g['old_text'], g['clean_text'],
                    g['grade_value'], meta['spreadsheet_id'], student_id, lang=lang
                )
            else:
                msg = format_grade_notification(
                    meta['display_name'], g['subject'], g['clean_text'],
                    g['grade_value'], meta['spreadsheet_id'], student_id, lang=lang
                )
        else:
            # 2+ оценок — батч-сообщение
            msg = format_batched_notification(
                meta['display_name'], grades,
                meta['spreadsheet_id'], student_id, lang=lang
            )

        kb = _make_grade_inline_keyboard(student_id, lang)
        send_notification([tg_id], {tg_id: msg}, inline_markup={tg_id: kb})

    # Рассылка в семейные групповые чаты — отдельным проходом по уникальным
    # студентам, чтобы группа не получила N копий одного уведомления (по числу
    # родителей). Берём представительного родителя для языка/клавиатуры.
    seen_students = {}
    for (tg_id, sid), grades in batch.items():
        if sid not in seen_students:
            seen_students[sid] = (tg_id, grades)
    for sid, (rep_tg_id, grades) in seen_students.items():
        meta = student_meta[sid]
        lang = get_user_lang(rep_tg_id)
        if len(grades) == 1:
            g = grades[0]
            if g['change_type'] == 'changed':
                msg = format_grade_change_notification(
                    meta['display_name'], g['subject'], g['old_text'], g['clean_text'],
                    g['grade_value'], meta['spreadsheet_id'], sid, lang=lang
                )
            else:
                msg = format_grade_notification(
                    meta['display_name'], g['subject'], g['clean_text'],
                    g['grade_value'], meta['spreadsheet_id'], sid, lang=lang
                )
        else:
            msg = format_batched_notification(
                meta['display_name'], grades,
                meta['spreadsheet_id'], sid, lang=lang
            )
        kb = _make_grade_inline_keyboard(sid, lang)
        _send_to_groups_for_student(sid, msg, kb, parent_tg_ids=[rep_tg_id])

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
                    send_notification(parents_ids, messages, inline_markup=keyboards, force=True)
                    # Дублируем в групповые чаты семьи (язык берём от первого родителя)
                    rep_tg = parents_ids[0]
                    _send_to_groups_for_student(
                        student_id, messages[rep_tg], keyboards[rep_tg], parent_tg_ids=[rep_tg]
                    )
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
                    send_notification(parents_ids, messages, inline_markup=keyboards, force=True)
                    rep_tg = parents_ids[0]
                    _send_to_groups_for_student(
                        student_id, messages[rep_tg], keyboards[rep_tg], parent_tg_ids=[rep_tg]
                    )

    logger.info("Quarter grades check completed.")


_last_all_grades_sync_ts = 0.0
_ALL_GRADES_SYNC_INTERVAL_SECONDS = 3600.0  # раз в час


def _maybe_sync_all_grades():
    """Раз в час перечитывает лист «Все оценки» для всех студентов.

    «Все оценки» — единый source of truth со 2 сентября (начало учебного года).
    Лист «Сегодня» (читаемый каждые 5 мин) — только для real-time уведомлений
    о текущем дне. «Неделя» — view для родителей в Sheets, бот его не читает.

    Если бот лежал несколько дней (миграция, downtime), пропущенные оценки
    подтянутся при ближайшем sync. UNIQUE на cell_reference защищает от
    дубликатов при повторных проходах.

    Cost: ~24 read/day per student × Sheets quota 300/min/user = огромный запас.
    """
    global _last_all_grades_sync_ts
    now = time.time()
    if now - _last_all_grades_sync_ts < _ALL_GRADES_SYNC_INTERVAL_SECONDS:
        return

    try:
        from src.history_importer import import_history_for_student
        from src.database_manager import get_active_spreadsheets

        for s in get_active_spreadsheets():
            try:
                result = import_history_for_student(s["student_id"], s["spreadsheet_id"])
                if result["imported"] > 0:
                    logger.info(
                        f"All-grades sync for student {s['student_id']}: "
                        f"+{result['imported']} new grades"
                    )
            except Exception as e:
                logger.warning(
                    f"All-grades sync failed for student {s['student_id']}: {e}"
                )

        _last_all_grades_sync_ts = now
    except Exception as e:
        logger.error(f"All-grades sync top-level error: {e}")


def start_polling(interval_seconds: Optional[int] = None):
    from src.config import POLLING_INTERVAL
    from src.error_reporter import report
    if interval_seconds is None:
        interval_seconds = POLLING_INTERVAL
    logger.info(f"Starting GradeSentinel monitor engine (interval: {interval_seconds}s)")
    while True:
        try:
            check_for_new_grades()
            _maybe_sync_all_grades()
        except Exception as e:
            report("monitor.cycle", e)

        logger.info(f"Sleeping for {interval_seconds} seconds...")
        time.sleep(interval_seconds)

if __name__ == "__main__":
    start_polling(10)
