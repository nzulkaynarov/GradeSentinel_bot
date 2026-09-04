import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from collections import defaultdict, Counter, namedtuple
from typing import List, Optional, Tuple
from telebot import types
from src.database_manager import (
    get_active_spreadsheets, add_grade, get_parents_for_student,
    update_student_display_name, queue_notification, get_user_lang,
    get_existing_grade_by_content, update_grade_by_content,
    get_active_spreadsheets_with_subscription,
    upsert_quarter_grade, get_db_connection, get_notify_mode,
    mark_grade_notified_by_content, mark_grade_notified, get_unnotified_grades,
    set_student_academic_year,
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
# (student_id, subject, grade_date) -> {'raw_text': str, 'first_seen': float}
# Ключ content-based (как и identity в БД после этапа 1C RFC) — НЕ cell_reference.
# Это устраняет race condition между monitor'ом и history_importer'ом (инцидент 2026-05-21).
_pending_grades: dict = {}
_PENDING_TTL_SECONDS = 1800  # 30 мин: очищаем зависшие pending (теоретически 2-3 цикла)


def _check_pending_confirmation(student_id: int, subject: str, grade_date: str,
                                new_raw_text: str) -> bool:
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

        key = (student_id, subject, grade_date)
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

def send_notification(telegram_ids, message, inline_markup=None, force=False,
                      ntype=None) -> bool:
    """Backward-compat обёртка над unified Sender.

    Отправляет уведомление. В тихие часы (22:00-07:00) копит в очередь.
    Пользователи в режиме 'summary_only' не получают мгновенных уведомлений
    (кроме force=True для четвертных оценок).
    message может быть dict {tg_id: msg_text} для мультиязычности или str.

    ntype: NotificationType (default GRADE_INSTANT). Для quarter передавать
    QUARTER_GRADE — это влияет на structured-логи и quiet hours policy.

    Возвращает True, если ВСЕ адресаты получили доставку (отправлено, поставлено
    в очередь тихих часов или пропущено по summary_only — всё это «доставлено, не
    повисло»). False, если хоть один адресат зафейлился или Sender не готов —
    outbox (notified_at) тогда не проставляется и sweeper дошлёт позже.
    """
    from src.notifications import get_sender, NotificationType
    try:
        sender = get_sender()
    except RuntimeError:
        logger.warning("Sender not initialized. Falling back to log placeholder.")
        for tg_id in telegram_ids:
            logger.info(f"[PLACEHOLDER -> {tg_id}]")
        return False

    nt = ntype or NotificationType.GRADE_INSTANT

    all_delivered = True
    for tg_id in telegram_ids:
        msg_text = message[tg_id] if isinstance(message, dict) else message
        kb = inline_markup[tg_id] if isinstance(inline_markup, dict) else inline_markup
        ok = sender.send(tg_id, msg_text, ntype=nt, kb=kb, force=force)
        all_delivered = all_delivered and bool(ok)
    return all_delivered


def _send_to_groups_for_student(student_id: int, message, inline_markup, parent_tg_ids):
    """Шлёт сообщение в групповые чаты, привязанные к семьям ученика.
    Язык — берём от первого родителя в `parent_tg_ids` (вся семья обычно одного языка).
    Для супергрупп с темами уважаем `message_thread_id`.

    Уважает тихие часы (22:00–07:00 Ташкент) — иначе любой баг в дедупе
    мгновенно превращается в спам в семейном чате (см. инцидент 2026-05-21:
    cell_reference cross-domain mismatch → 14 ночных уведомлений)."""
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

    from src.notifications import get_sender, NotificationType
    try:
        sender = get_sender()
    except RuntimeError:
        logger.warning(f"Sender not initialized for group send (student={student_id}).")
        return

    for grp in groups:
        chat_id = grp['chat_id']
        thread_id = grp.get('message_thread_id')
        # В тихие часы Sender сам положит в group_notification_queue
        # (по NotificationType.GRADE_GROUP — он в _DEFER_IN_QUIET).
        # Inline_markup для queue не сохраняется (callback устаревает за ночь);
        # передаём только для активного окна.
        sender.send_to_group(
            chat_id, thread_id, msg_text,
            ntype=NotificationType.GRADE_GROUP, kb=kb,
        )

def _record_student_failure(student_id: int, display_name: str):
    """Учитывает неудачную попытку чтения таблицы. После N подряд — алерт админу
    в логи + Telegram (через Sender, с retry + i18n)."""
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
            try:
                from src.notifications import get_sender, NotificationType
                from src.database_manager import get_user_lang
                import os
                admin_id = int(os.environ.get("ADMIN_ID", "0") or "0")
                lang = get_user_lang(admin_id) if admin_id else "ru"
                text = t(
                    "alert_sheet_stuck",
                    lang,
                    display_name=display_name,
                    student_id=student_id,
                    count=count,
                )
                get_sender().send_to_admin(text, ntype=NotificationType.SHEET_FAILURE)
            except Exception as e:
                logger.warning(f"Failed to send sheet-stuck alert to admin: {e}")


def _record_student_success(student_id: int):
    """Сбрасывает счётчик неудач при успешном чтении."""
    if student_id in _student_failure_counts:
        _student_failure_counts.pop(student_id, None)


# ─────────────────────────────────────────────────────────────
# Rollover учебного года (RFC 2026-09-02)
# ─────────────────────────────────────────────────────────────
# Дата последнего лога [STALE_SHEET] по ученику — чтобы не писать каждые 5 мин.
_stale_logged_on: dict = {}


def _claim_daily_recheck(student_id: int, today) -> bool:
    """True если сегодня ученика на паузе ещё не перепроверяли (и «занимает» день).

    Пауза не должна быть приговором: `academic_year` мог быть записан неверно
    (backfill по грязным данным — инцидент 2026-09-03, или инференс по листу,
    который в момент привязки был ещё пуст). Пока таблица не читается, ошибку
    нечем обнаружить, и ученик молча выпадает из мониторинга навсегда.
    Раз в сутки читаем даже приостановленный лист и сверяем год с содержимым.

    Маркер в settings (`stale_recheck:{sid}`) — переживает рестарт, поэтому
    флап сервиса не превращается в чтение на каждом цикле."""
    from src.database_manager import get_setting, set_setting

    marker_key = f"stale_recheck:{student_id}"
    stamp = today.isoformat()
    try:
        if get_setting(marker_key) == stamp:
            return False
        set_setting(marker_key, stamp)
        return True
    except Exception as e:
        # БД недоступна — не рискуем лишним чтением Sheets, оставляем паузу.
        logger.warning(f"Daily recheck marker failed for student {student_id}: {e}")
        return False


def _reconcile_academic_year(student_id: int, display_name: str, data,
                             stored_year: Optional[int], today) -> Optional[int]:
    """Сверяет записанный учебный год с содержимым уже загруженного листа.

    Записанный год — это гипотеза (backfill миграции, инференс при привязке).
    Лист — факт: оценки за январь–июнь календарного года N физически не могут
    принадлежать учебному году N/N+1. Расходятся — верим листу и чиним запись,
    иначе одна испорченная запись живёт вечно (на проде 2026-09-03 ложная оценка
    за «сегодня» дала прошлогодней таблице academic_year=2026, и рассылка
    прошлогодних оценок продолжалась).

    `stored_year is None` не трогаем: это штатное «ещё не определён», год
    выводит history_importer при импорте, а монитор страхует эхо-guard'ом.
    Возвращает год, которому стоит верить дальше."""
    from src.history_importer import sheet_grade_months, infer_sheet_academic_year

    if stored_year is None or data is None:
        return stored_year
    try:
        months = sheet_grade_months(data, context=f"student={student_id} ({display_name})")
    except Exception as e:
        logger.warning(f"Academic year reconcile failed for student {student_id}: {e}")
        return stored_year
    if not months:
        # Ни одной оценки: лист пуст, не распознан или пришёл битым. Это не
        # свидетельство ни за какой год — менять запись не на чем. Особенно важно
        # для приостановленных таблиц: иначе пустой ответ Sheets «омолодил» бы год
        # и возобновил рассылку прошлогодних оценок.
        return stored_year
    inferred = infer_sheet_academic_year(months, today=today)
    if inferred == stored_year:
        return stored_year

    logger.warning(
        f"[ACADEMIC_YEAR_CORRECTED] student_id={student_id} ({display_name}): "
        f"stored={stored_year}, sheet content says {inferred}. Fixing the record."
    )
    try:
        set_student_academic_year(student_id, inferred)
    except Exception as e:
        logger.error(f"Failed to persist corrected academic year for {student_id}: {e}")
        return stored_year
    return inferred


def _passes_stale_gate(student: dict, today) -> bool:
    """True если ученика нужно опрашивать в этом цикле.

    Не stale → да. Stale → нет, КРОМЕ одной попытки в сутки: лист читается,
    чтобы `_reconcile_academic_year` мог сверить год с содержимым и снять
    ошибочную паузу. Нэдж и алерт шлём только когда чтения не будет — иначе
    попросили бы обновить ссылку у таблицы, которая через секунду окажется
    актуальной."""
    from src.history_importer import is_sheet_stale

    if not is_sheet_stale(student.get('academic_year'), today):
        return True
    if _claim_daily_recheck(student['student_id'], today):
        return True
    _handle_stale_sheet(student, today)
    return False


def _handle_stale_sheet(student: dict, today) -> bool:
    """True если таблица ученика за прошлый учебный год → её НЕ опрашиваем.

    Побочные эффекты (best-effort, ошибки не роняют цикл): лог раз в день,
    напоминание семье обновить ссылку (раз в RELINK_NUDGE_INTERVAL_DAYS, вне
    тихих часов — чтобы кнопка «Сменить ссылку» не потерялась в очереди),
    алерт админу раз в учебный год."""
    from src.history_importer import is_sheet_stale, current_academic_year

    academic_year = student.get('academic_year')
    if not is_sheet_stale(academic_year, today):
        return False

    student_id = student['student_id']
    display_name = student.get('display_name') or student.get('fio') or str(student_id)
    current_year = current_academic_year(today)

    if _stale_logged_on.get(student_id) != today:
        _stale_logged_on[student_id] = today
        logger.warning(
            f"[STALE_SHEET] student_id={student_id} ({display_name}): sheet academic_year="
            f"{academic_year} < current {current_year}. Polling paused until relink."
        )
    try:
        _maybe_nudge_relink(student_id, display_name, current_year, today)
    except Exception as e:
        logger.warning(f"Relink nudge failed for student {student_id}: {e}")
    try:
        _maybe_alert_admin_stale(student_id, display_name, academic_year, current_year)
    except Exception as e:
        logger.warning(f"Stale-sheet admin alert failed for student {student_id}: {e}")
    return True


def _maybe_nudge_relink(student_id: int, display_name: str, current_year: int, today) -> bool:
    """Напоминание семьям ученика обновить ссылку. Маркер `relink_nudge:{sid}`
    в settings (дата последней отправки) — переживает рестарт. True если ушло."""
    from src.config import RELINK_NUDGE_INTERVAL_DAYS
    from src.database_manager import (
        get_setting, set_setting, get_families_for_student,
        get_family_members_telegram_ids, can_manage_family,
    )
    from src.notifications import get_sender, NotificationType

    if is_quiet_hours():
        return False  # кнопка не переживёт очередь тихих часов — попробуем днём
    marker_key = f"relink_nudge:{student_id}"
    last = get_setting(marker_key)
    if last:
        try:
            last_date = datetime.strptime(last, "%Y-%m-%d").date()
            if (today - last_date).days < RELINK_NUDGE_INTERVAL_DAYS:
                return False
        except ValueError:
            pass

    year_label = f"{current_year}/{str(current_year + 1)[-2:]}"
    sent_any = False
    for fam in get_families_for_student(student_id):
        f_id = fam['id']
        for tg_id in get_family_members_telegram_ids(f_id):
            lang = get_user_lang(tg_id)
            text = t("relink_nudge_new_year", lang, name=display_name, year=year_label)
            kb = None
            if can_manage_family(tg_id, f_id):
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton(
                    t("family_relink_btn", lang), callback_data=f"relink_list_{f_id}"))
            if get_sender().send(tg_id, text, ntype=NotificationType.RELINK_NUDGE,
                                 kb=kb, force=True, defer=False):
                sent_any = True
    if sent_any:
        set_setting(marker_key, today.isoformat())
        logger.info(f"[STALE_SHEET] relink nudge sent for student {student_id} ({display_name})")
    return sent_any


def _maybe_alert_admin_stale(student_id: int, display_name: str,
                             academic_year: int, current_year: int) -> None:
    """Алерт админу об устаревшей таблице — один раз на (ученик, учебный год)."""
    from src.database_manager import get_setting, set_setting
    from src.notifications import get_sender, NotificationType
    import os

    marker_key = f"stale_admin_alert:{student_id}"
    if get_setting(marker_key) == str(current_year):
        return
    admin_id = int(os.environ.get("ADMIN_ID", "0") or "0")
    lang = get_user_lang(admin_id) if admin_id else "ru"
    text = t("alert_sheet_stale", lang, display_name=display_name, student_id=student_id,
             academic_year=academic_year, current_year=current_year)
    if get_sender().send_to_admin(text, ntype=NotificationType.SHEET_FAILURE):
        set_setting(marker_key, str(current_year))


def _drop_removed_grades(student_id: int, display_name: str, grade_date: str,
                         subjects_in_sheet: set) -> int:
    """Убирает из истории сегодняшние оценки, которых больше нет в листе.

    Учитель ошибся ячейкой и стёр оценку — раньше она оставалась в БД навсегда
    и продолжала влиять на средний балл, тренды, PDF и контекст AI-чата: монитор
    умеет только добавлять и менять (аудит 2026-09-03).

    Осторожность важнее полноты, поэтому чистим ТОЛЬКО за сегодня:
      • сегодняшнюю колонку пишет исключительно монитор — importer пропускает
        всё, что `>= today`, так что чужих записей за этот день не бывает;
      • вызывается только когда в колонке что-то есть (пустой список приводит к
        `continue` выше), иначе сбой чтения листа стёр бы день целиком.
    Оценка, стёртая вместе со всей колонкой, останется — это осознанный предел.
    Уведомление не шлём: «оценку убрали» — шум, важен лишь корректный расчёт.
    """
    try:
        existing = _grades_on_date(student_id, grade_date)
    except Exception as e:
        logger.debug(f"Removed-grades check skipped for student {student_id}: {e}")
        return 0

    stale_subjects = {subject for subject, _ in existing} - subjects_in_sheet
    if not stale_subjects:
        return 0

    removed = 0
    with get_db_connection() as conn:
        cursor = conn.cursor()
        for subject in stale_subjects:
            cursor.execute(
                "DELETE FROM grade_history "
                "WHERE student_id = %s AND grade_date = %s AND subject = %s",
                (student_id, grade_date, subject),
            )
            removed += cursor.rowcount
    if removed:
        logger.info(
            f"[GRADE REMOVED] {display_name} (id={student_id}): {removed} grade(s) "
            f"gone from the sheet for {grade_date} — {', '.join(sorted(stale_subjects))}"
        )
    return removed


def _grades_on_date(student_id: int, grade_date) -> set:
    """{(subject, raw_text)} оценок ученика за дату (по grade_date)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT subject, raw_text FROM grade_history "
            "WHERE student_id = %s AND grade_date = %s",
            (student_id, grade_date),
        )
        return {(row['subject'], row['raw_text']) for row in cursor.fetchall()}


def _looks_like_last_year_echo(student_id: int, display_name: str,
                               today_pairs: list, today) -> bool:
    """Defense-in-depth для учеников с неизвестным academic_year: если ВСЕ
    «сегодняшние» оценки 1:1 совпадают с оценками ровно год назад (тот же
    предмет и значение в тот же день-месяц) — это почти наверняка прошлогодняя
    таблица, а не новые оценки. Пропускаем и логируем [STALE_ECHO]."""
    try:
        last_year = today.replace(year=today.year - 1)
    except ValueError:  # 29 февраля
        return False
    try:
        previous = _grades_on_date(student_id, last_year)
    except Exception as e:
        logger.debug(f"Echo-check query failed for student {student_id}: {e}")
        return False
    if not previous:
        return False
    today_set = {(subject, str(raw).strip()) for subject, raw in today_pairs}
    if today_set and today_set <= previous:
        logger.warning(
            f"[STALE_ECHO] student_id={student_id} ({display_name}): today's "
            f"{len(today_set)} grade(s) identical to {last_year.isoformat()} — "
            f"treating sheet as previous academic year, skipping."
        )
        return True
    return False


# Результат fetch-воркера. `persist_display_name` — флаг «display_name был
# вычислен из заголовка таблицы, его нужно записать в БД». Запись и учёт
# success/failure выполняются в ПОСЛЕДОВАТЕЛЬНОЙ фазе (см. _fetch_student_sheet).
_FetchResult = namedtuple(
    "_FetchResult", ["student", "data", "display_name", "persist_display_name"]
)


def _fetch_student_sheet(student: dict, range_name: str) -> "_FetchResult":
    """Worker: ТОЛЬКО сетевые операции (Google Sheets), БЕЗ обращений к БД.

    Крутится внутри ThreadPoolExecutor(FETCH_WORKERS=8). Раньше воркер писал в БД
    (update_student_display_name, _record_student_failure) → 8 параллельных потоков
    брали соединения из пула (DB_POOL_MAX=5) одновременно с main-хендлерами /
    scheduler'ом / heartbeat'ом → риск PoolTimeout (B12). Теперь все DB-операции
    вынесены в последовательную фазу после as_completed — воркеры только сеть.

    Возвращает _FetchResult; ошибки чтения ловятся здесь (одна сломанная таблица
    не валит цикл), но реакция на них (failure-счётчик) — уже в вызывающем коде.
    """
    student_id = student['student_id']
    fio = student['fio']
    spreadsheet_id = student['spreadsheet_id']

    display_name = student.get('display_name')
    persist_display_name = False
    if not display_name:
        try:
            sheet_title = get_spreadsheet_title(spreadsheet_id)
        except Exception as e:
            logger.error(f"Title fetch failed for student {student_id}: {e}")
            sheet_title = None
        display_name = clean_student_name(sheet_title) if sheet_title else fio
        # Запишем в БД в последовательной фазе (не из воркера).
        persist_display_name = True

    try:
        data = get_sheet_data(spreadsheet_id, range_name)
    except Exception as e:
        logger.error(f"Unexpected error fetching data for {display_name} (id={student_id}): {e}")
        return _FetchResult(student, None, display_name, persist_display_name)

    return _FetchResult(student, data, display_name, persist_display_name)


def _format_grade_message(meta: dict, grades: list, student_id: int, lang: str) -> str:
    """Форматирует уведомление об оценках ученика: детальное для одной оценки,
    батч-сообщение для нескольких. Общий код для основного пути и sweeper'а."""
    if len(grades) == 1:
        g = grades[0]
        if g['change_type'] == 'changed':
            return format_grade_change_notification(
                meta['display_name'], g['subject'], g['old_text'], g['clean_text'],
                g['grade_value'], meta['spreadsheet_id'], student_id, lang=lang
            )
        return format_grade_notification(
            meta['display_name'], g['subject'], g['clean_text'],
            g['grade_value'], meta['spreadsheet_id'], student_id, lang=lang
        )
    return format_batched_notification(
        meta['display_name'], grades, meta['spreadsheet_id'], student_id, lang=lang
    )


def _dispatch_student_notifications(student_id: int, meta: dict,
                                    parent_grades: dict) -> bool:
    """Шлёт собранные за студента оценки родителям и в семейные групповые чаты.

    Возвращает True, если ВСЕ личные уведомления доставлены (отправлены /
    поставлены в очередь тихих часов / пропущены по summary_only) — тогда caller
    проставит notified_at. Групповая отправка best-effort (личное уведомление —
    первично; группа уважает свою персистентную очередь) и на результат не влияет.

    parent_grades пуст (у ученика нет родителей) → доставлять некому, ничего не
    «повисло» → True (иначе sweeper крутил бы такую оценку вечно)."""
    if not parent_grades:
        return True

    all_delivered = True
    for tg_id, grades in parent_grades.items():
        lang = get_user_lang(tg_id)
        msg = _format_grade_message(meta, grades, student_id, lang)
        kb = _make_grade_inline_keyboard(student_id, lang)
        ok = send_notification([tg_id], {tg_id: msg}, inline_markup={tg_id: kb})
        all_delivered = all_delivered and ok

    # Групповые чаты — один проход на ученика (представительный родитель для
    # языка/клавиатуры), чтобы группа не получила N копий по числу родителей.
    rep_tg_id = next(iter(parent_grades))
    lang = get_user_lang(rep_tg_id)
    msg = _format_grade_message(meta, parent_grades[rep_tg_id], student_id, lang)
    kb = _make_grade_inline_keyboard(student_id, lang)
    _send_to_groups_for_student(student_id, msg, kb, parent_tg_ids=[rep_tg_id])

    return all_delivered


def _sweep_unnotified_grades():
    """Outbox sweeper (PR-F1): дошлёт оценки, чьи уведомления не ушли в прошлых
    циклах (крах между записью в БД и отправкой). Вызывается ПЕРВЫМ в цикле.

    Читает строки с `notified_at IS NULL`, группирует по ученику, шлёт одним
    батч-сообщением на родителя и помечает доставленные (`mark_grade_notified`).
    Не ушедшее (send failed) остаётся notified_at IS NULL → следующий цикл.

    Recovery-путь форматирует всё как 'new' (old_text недоступен после факта) —
    родитель увидит текущее значение ячейки; это приемлемо для добивки."""
    try:
        rows = get_unnotified_grades()
    except Exception as e:
        logger.error(f"Outbox sweep: failed to fetch unnotified grades: {e}")
        return
    if not rows:
        return

    logger.info(f"Outbox sweep: {len(rows)} unnotified grade(s) to resend.")

    by_student = defaultdict(list)
    for r in rows:
        by_student[r['student_id']].append(r)

    for student_id, grade_rows in by_student.items():
        first = grade_rows[0]
        meta = {
            'display_name': first['display_name'] or first['fio'],
            'spreadsheet_id': first['spreadsheet_id'],
        }
        grade_entries = [{
            'subject': r['subject'],
            'clean_text': r['raw_text'],
            'grade_value': r['grade_value'],
            'change_type': 'new',
            'old_text': None,
        } for r in grade_rows]

        try:
            parents_ids = get_parents_for_student(student_id)
        except Exception as e:
            logger.warning(f"Outbox sweep: get_parents failed (student={student_id}): {e}")
            continue

        parent_grades = {tg_id: grade_entries for tg_id in parents_ids}
        try:
            delivered = _dispatch_student_notifications(student_id, meta, parent_grades)
        except Exception as e:
            logger.warning(f"Outbox sweep: dispatch failed (student={student_id}): {e}")
            continue

        if delivered:
            for r in grade_rows:
                try:
                    mark_grade_notified(r['id'])
                except Exception as e:
                    logger.warning(
                        f"Outbox sweep: mark_grade_notified failed (id={r['id']}): {e}"
                    )


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

    # MONOSOURCE_GRADES (этап 4 RFC, 2026-05-21): monitor читает «Все оценки!»
    # как единый source of truth. До этого был «Сегодня!A1:B50», но он давал
    # ТОЛЬКО последний учебный день, и race с history_importer'ом из-за
    # разных cell_reference приводил к спаму (инцидент 2026-05-21).
    # «Все оценки!» содержит ту же информацию + всю историю; берём только
    # колонку для сегодняшней даты через _parse_master_sheet_for_date.
    from src.history_importer import (
        MASTER_SHEET_RANGE, _parse_master_sheet_for_date, current_academic_year,
        is_sheet_stale,
    )

    # Outbox sweeper (PR-F1): добить уведомления, не ушедшие в прошлых циклах
    # из-за краха между записью оценки и отправкой. Идёт ПЕРВЫМ под _polling_lock
    # — текущий цикл ещё ничего не записал, поэтому в outbox только реально
    # «повисшие» строки прошлых циклов (нет гонки с записью этого цикла).
    _sweep_unnotified_grades()

    # Метаданные для каждого студента
    student_meta = {}  # student_id -> {display_name, spreadsheet_id}

    # tashkent_today — один раз на цикл (date.isoformat() для записи в БД,
    # date object для парсера master sheet).
    tashkent_today_date = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date()
    tashkent_today = tashkent_today_date.isoformat()

    # Rollover учебного года (инцидент 2026-09-02): таблицы за ПРОШЛЫЙ учебный
    # год не опрашиваем — школа выдала новую ссылку, а в старой колонка
    # «2 сентября» (без года) совпала бы с сегодняшней датой.
    #
    # Пауза не окончательная: раз в сутки приостановленный лист всё-таки читаем
    # и сверяем год с его содержимым (_reconcile_academic_year ниже). Иначе
    # единожды неверно записанный academic_year не имеет способа исправиться —
    # ни в одну сторону: завышенный оставляет рассылку прошлогодних оценок,
    # заниженный молча выключает ученику мониторинг навсегда.
    students = [s for s in students if _passes_stale_gate(s, tashkent_today_date)]
    if not students:
        logger.info("All active students have stale (previous academic year) sheets. Nothing to poll.")
        return

    # Параллельная загрузка данных — одна сломанная таблица не блокирует остальные
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as executor:
        futures = {executor.submit(_fetch_student_sheet, s, MASTER_SHEET_RANGE): s for s in students}
        fetched = []
        for future in as_completed(futures):
            try:
                fetched.append(future.result())
            except Exception as e:
                s = futures[future]
                logger.error(f"Worker crashed for student_id={s.get('student_id')}: {e}")

    # Дальнейшая обработка — последовательная (все DB-операции). Сюда же вынесены
    # запись display_name и учёт success/failure: раньше они шли внутри
    # fetch-воркеров (8 потоков) → конкуренция за пул БД (B12). Теперь БД трогает
    # только этот единственный (monitor) поток → пул не истощается, а мутация
    # _student_failure_counts перестала быть многопоточной (бонус к thread-safety).
    for result in fetched:
        student = result.student
        data = result.data
        display_name = result.display_name
        student_id = student['student_id']
        fio = student['fio']
        spreadsheet_id = student['spreadsheet_id']

        # display_name, вычисленный воркером из заголовка таблицы, записываем тут.
        if result.persist_display_name:
            try:
                update_student_display_name(student_id, display_name)
            except Exception as e:
                logger.error(f"Failed to update display_name for {student_id}: {e}")

        student_meta[student_id] = {'display_name': display_name, 'spreadsheet_id': spreadsheet_id}

        if data is None:
            _record_student_failure(student_id, display_name)
            logger.warning(f"Data fetch returned None for {display_name}. Skipping this cycle.")
            continue

        _record_student_success(student_id)

        logger.info(f"Processing sheet for student: {display_name} (ID: {student_id})")

        # Лист в руках — сверяем записанный учебный год с его содержимым и чиним
        # запись при расхождении. Это единственный момент, когда ошибку в
        # academic_year вообще можно заметить.
        academic_year = _reconcile_academic_year(
            student_id, display_name, data, student.get('academic_year'), tashkent_today_date)
        if is_sheet_stale(academic_year, tashkent_today_date):
            # Либо суточная перепроверка подтвердила паузу, либо год только что
            # понижен по содержимому листа. Шлём нэдж/алерт и ничего не читаем.
            # Лист за сегодня уже прочитан — расходуем суточную перепроверку,
            # иначе следующий цикл прочитал бы его повторно.
            _claim_daily_recheck(student_id, tashkent_today_date)
            _handle_stale_sheet({**student, 'academic_year': academic_year,
                                 'display_name': display_name}, tashkent_today_date)
            continue

        # Извлекаем оценки за сегодняшнюю дату из «Все оценки!».
        # Пустой список — нет колонки для today (учебный год не начался / выходной).
        # academic_year таблицы → «2 сентября» прошлогодней таблицы = прошлый год,
        # с today не совпадёт (даже если stale-фильтр выше почему-то пропустил).
        today_grades_pairs = _parse_master_sheet_for_date(
            data, tashkent_today_date, context=f"student={student_id} ({display_name})",
            academic_year=(academic_year if academic_year is not None
                           else current_academic_year(tashkent_today_date)),
        )
        if today_grades_pairs and academic_year is None and _looks_like_last_year_echo(
            student_id, display_name, today_grades_pairs, tashkent_today_date
        ):
            continue
        if not today_grades_pairs:
            continue

        # Собираем уведомления ЭТОГО студента (ключ — tg_id родителя) и список
        # записанных за цикл оценок. Отправка идёт сразу после цикла по предметам
        # (не откладываем на конец обработки всех студентов) — см. dispatch ниже.
        parent_grades = defaultdict(list)
        written_keys = []  # [(subject, grade_date)] — записали, шлём уведомление

        for subject, raw_grade in today_grades_pairs:
            if not raw_grade:
                continue

            # cell_reference остался как metadata (origin tag для debug/дашборда),
            # не identity-ключ — identity это content-key (student, subject, date)
            # после этапа 1C RFC. См. инцидент 2026-05-21.
            cell_reference = f"Все оценки!{tashkent_today}:{subject}"

            # Парсим ячейку как список оценок (поддержка X/Y формата)
            new_grades = sanitize_cell(raw_grade)
            if not new_grades:
                # Мусор / неизвестный токен — пропускаем (как раньше)
                continue

            new_clean_text = _cell_raw_text(new_grades)
            new_grade_value = _cell_avg_grade(new_grades)

            existing = get_existing_grade_by_content(student_id, subject, tashkent_today)
            old_clean_text = existing['raw_text'] if existing else None
            old_grades = sanitize_cell(old_clean_text) if old_clean_text else []

            # Нет изменений по сравнению с БД — следующая ячейка.
            # Здесь же ловится случай когда history_importer успел положить
            # запись раньше — у него тот же content-key, мы её найдём.
            if old_clean_text == new_clean_text:
                continue

            # Что РЕАЛЬНО добавилось (multiset diff)
            added = _compute_added_grades(old_grades, new_grades)

            # Случай «удаления» (старое было длиннее, новых оценок нет):
            # тихо обновляем БД, без уведомления. Родителю незачем знать что
            # учитель что-то стёр.
            if not added:
                if existing:
                    update_grade_by_content(student_id, subject, tashkent_today,
                                            new_grade_value, new_clean_text)
                    logger.info(
                        f"[GRADE TRIMMED] {display_name}: {subject} "
                        f"'{old_clean_text}' -> '{new_clean_text}' (no notif)"
                    )
                continue

            # Двухфазное подтверждение: первый раз видим это изменение → ждём
            # следующего цикла. Это убирает «оценки-призраки» от опечаток учителя.
            if not _check_pending_confirmation(student_id, subject, tashkent_today, new_clean_text):
                logger.info(
                    f"[PENDING] {display_name}: {subject} '{new_clean_text}' — "
                    f"ждём подтверждения на следующем цикле"
                )
                continue

            # Подтверждено — пишем в БД.
            # grade_date = tashkent_today: дата оценки по факту.
            # notified_at сбрасывается в NULL (outbox) — проставим после доставки.
            if existing:
                update_grade_by_content(student_id, subject, tashkent_today,
                                        new_grade_value, new_clean_text,
                                        mark_unnotified=True)
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

            # Оценка записана и по ней пойдёт уведомление → регистрируем ключ для
            # outbox. notified_at проставим ТОЛЬКО после подтверждённой доставки.
            written_keys.append((subject, tashkent_today))

            grade_entry = {
                'subject': subject,
                'clean_text': new_clean_text,
                'grade_value': emo_grade_value,
                # «2» → «2/5»: показываем переход; «» → «2/5»: новая запись.
                'change_type': 'changed' if old_clean_text else 'new',
                'old_text': old_clean_text if old_clean_text else None,
            }
            for tg_id in get_parents_for_student(student_id):
                parent_grades[tg_id].append(grade_entry)

        # ── Оценки, стёртые учителем ─────────────────────────────────────
        _drop_removed_grades(
            student_id, display_name, tashkent_today,
            {subject for subject, raw in today_grades_pairs if raw},
        )

        # ── Отправка ПО ЭТОМУ студенту сразу (PR-F1 outbox) ──────────────
        # Раньше все уведомления копились в глобальный batch и слались двумя
        # проходами в конце цикла всех студентов → exception в фазе отправки
        # терял уведомления навсегда (оценки уже в БД, diff на след. цикле пуст).
        # Теперь шлём здесь и проставляем notified_at только на доставленное;
        # «повисшее» добьёт sweeper на следующем цикле.
        if written_keys:
            delivered = _dispatch_student_notifications(
                student_id, student_meta[student_id], parent_grades
            )
            if delivered:
                for subj, gdate in written_keys:
                    try:
                        mark_grade_notified_by_content(student_id, subj, gdate)
                    except Exception as e:
                        logger.warning(
                            f"mark_grade_notified failed (student={student_id}, "
                            f"subject={subj}): {e}"
                        )

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
    from src.history_importer import is_sheet_stale, current_academic_year

    for student in students:
        student_id = student['student_id']
        fio = student['fio']
        spreadsheet_id = student['spreadsheet_id']
        display_name = student.get('display_name') or fio

        if is_sheet_stale(student.get('academic_year')):
            continue  # прошлогодняя таблица — не опрашиваем (см. _handle_stale_sheet)

        # Год таблицы — часть ключа четвертных (миграция 0006). Если он ещё не
        # определён (свежая привязка), берём текущий: до первого импорта
        # четверти всё равно относятся к идущему учебному году.
        sheet_year = student.get('academic_year')
        if sheet_year is None:
            sheet_year = current_academic_year()

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

                # Получаем текущее значение ДО upsert — тем же ключом, что и
                # запись: с миграции 0006 в него входит учебный год.
                with get_db_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        SELECT raw_text FROM quarter_grades
                        WHERE student_id = %s AND academic_year = %s
                          AND subject = %s AND quarter = %s
                    ''', (student_id, sheet_year, subject, quarter))
                    existing = cursor.fetchone()

                old_text = existing['raw_text'] if existing else None

                changed = upsert_quarter_grade(student_id, subject, quarter, grade_value,
                                               clean_text, academic_year=sheet_year)

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
        from src.history_importer import import_history_for_student, is_sheet_stale
        from src.database_manager import get_active_spreadsheets

        for s in get_active_spreadsheets():
            if is_sheet_stale(s.get("academic_year")):
                continue  # прошлогодняя таблица: история уже импортирована, не тратим квоту
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
