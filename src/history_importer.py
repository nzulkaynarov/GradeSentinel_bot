"""
Импорт исторических оценок из листа "Все оценки".

Структура листа:
  - Строка 1: заголовок (пропускаем)
  - Строка 2: "Оценки" | дата1 | дата2 | ... (заголовки столбцов = даты)
  - Строки 3-17+: предмет | оценка | оценка | ... (строки = предметы)
  - Строка 18: "Посещаемость" (пропускаем)
  - Строки 19+: служебные (0, 0, 0 — пропускаем)

Формат дат в заголовках: "2 сентября", "14 март Сб", "1 октября" и т.д.
"""

import re
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple

from src.google_sheets import get_sheet_data
from src.data_cleaner import sanitize_grade, sanitize_cell
from src.database_manager import get_db_connection

logger = logging.getLogger(__name__)


def _tashkent_now() -> datetime:
    """Текущее «сейчас» по Ташкенту (UTC+5, без DST), наивный datetime.

    Единый способ проекта: naive-UTC + 5ч (тот же, что в monitor_engine,
    analytics_engine, database_manager). Вечером у сервера в UTC (~19:00-23:59 UTC)
    по Ташкенту уже «завтра», поэтому год/сегодня НЕЛЬЗЯ брать из `datetime.now()`
    (локальное/UTC время сервера): на границе учебного года (31 авг/1 сен,
    31 дек/1 янв) это отнесло бы дату к соседнему учебному году."""
    return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)


def _tashkent_today_date():
    """Сегодняшняя дата по Ташкенту (UTC+5). Зона ответственности monitor'а —
    history-sync на эту дату НЕ пишет, чтобы не конфликтовать с двухфазным
    подтверждением (race из инцидента 13.05.2026)."""
    return _tashkent_now().date()


# ─── Учебный год ─────────────────────────────────────────────────────
# Учебный год идентифицируется годом его НАЧАЛА: 2025 = 2025/26.
# Даты в шапке листов без года («2 сентября»), поэтому год берётся из
# `students.academic_year` (RFC 2026-09-02), а не от текущей даты. Иначе в
# сентябре прошлогодняя таблица даёт колонку «2 сентября» = сегодня → монитор
# рассылает прошлогодние оценки как новые (инцидент 2026-09-02).

ACADEMIC_YEAR_START_MONTH = 9
# Месяцы «весеннего» полугодия, в которых бывают оценки: январь–май (в июне
# уже каникулы). Колонка с оценкой в этих месяцах = лист охватывает весну.
_SPRING_MONTHS = frozenset(range(1, 7))
# С августа новая ссылка на таблицу — уже про предстоящий учебный год.
_LINK_NEXT_YEAR_FROM_MONTH = 8


def current_academic_year(today=None) -> int:
    """Учебный год (год начала), в который попадает дата `today` (по Ташкенту).
    Сентябрь–декабрь → year, январь–август → year-1."""
    if today is None:
        today = _tashkent_today_date()
    return today.year if today.month >= ACADEMIC_YEAR_START_MONTH else today.year - 1


def year_for_month(month: int, academic_year: int) -> int:
    """Календарный год для месяца `month` внутри учебного года `academic_year`."""
    return academic_year if month >= ACADEMIC_YEAR_START_MONTH else academic_year + 1


def infer_sheet_academic_year(grade_months, today=None) -> int:
    """Выводит учебный год таблицы по месяцам колонок, в которых ЕСТЬ оценки.

    Шапка нового листа может быть заполнена датами на весь год вперёд, поэтому
    смотрим только на колонки с оценками:
      • есть оценки в январе–июне → лист охватывает весну → это учебный год,
        закончившийся (или идущий) весной текущего календарного года: year-1;
      • иначе (только осенние оценки или пусто) → с августа считаем таблицу
        предстоящим учебным годом (year), до августа — текущим (year-1).
    """
    if today is None:
        today = _tashkent_today_date()
    months = set(grade_months)
    if months & _SPRING_MONTHS:
        return today.year - 1
    if today.month >= _LINK_NEXT_YEAR_FROM_MONTH:
        return today.year
    return today.year - 1


def is_sheet_stale(academic_year: Optional[int], today=None) -> bool:
    """True если таблица относится к ПРОШЛОМУ учебному году (её больше не надо
    опрашивать — школа выдала новую ссылку). NULL = год ещё не определён →
    не считаем устаревшей."""
    if academic_year is None:
        return False
    return academic_year < current_academic_year(today)


# Маппинг русских названий месяцев (полные формы + распространённые сокращения).
# ВАЖНО: префиксы должны быть УНИКАЛЬНЫМИ — иначе 'март'.startswith('м') матчит
# короткое 'м' и парсит «мая» как март (реальный баг найден в листе «Неделя»
# где даты в формате «3 мая вс»).
MONTH_MAP = {
    # Длинные формы (родительный падеж + именительный)
    'январ': 1, 'феврал': 2, 'март': 3, 'апрел': 4,
    'мая': 5, 'май': 5,
    'июн': 6, 'июл': 7, 'август': 8,
    'сентябр': 9, 'октябр': 10, 'ноябр': 11, 'декабр': 12,
    # Сокращения 4 буквы (для коротких форматов «3 сент»)
    'сент': 9, 'окт': 10, 'нояб': 11, 'дек': 12,
}

# Строки, которые НЕ являются предметами
SKIP_SUBJECTS = {'посещаемость', '0', ''}


def _parse_russian_day_month(date_str: str) -> Optional[Tuple[int, int]]:
    """Парсит «2 сентября» / «14 март Сб» → (day, month) без года. None если не дата."""
    if not date_str:
        return None

    # Убираем день недели и лишние пробелы
    clean = re.sub(r'\s+(пн|вт|ср|чт|пт|сб|вс|Пн|Вт|Ср|Чт|Пт|Сб|Вс)\.?$', '', date_str.strip(), flags=re.IGNORECASE)
    clean = clean.strip()

    # Ищем число и месяц
    match = re.match(r'^(\d{1,2})\s+(\S+)', clean)
    if not match:
        return None

    day = int(match.group(1))
    month_text = match.group(2).lower()
    # Раньше тут было rstrip('яьа') и fallback на prefix.startswith(month_text).
    # Это создавало fake-match: «мая» → rstrip → «м» → 'март'.startswith('м')=True
    # → парсил как март. Сейчас MONTH_MAP содержит явные алиасы (мая/май, сент/сентябр)
    # и матчим только в одну сторону: month_text начинается с известного префикса.
    month = None
    for prefix, m in MONTH_MAP.items():
        if month_text.startswith(prefix):
            month = m
            break

    if month is None:
        return None
    return day, month


def _parse_russian_date(
    date_str: str,
    now: Optional[datetime] = None,
    academic_year: Optional[int] = None,
) -> Optional[datetime]:
    """
    Парсит русскую дату вида '2 сентября', '14 март Сб', '1 октября' и т.д.
    Возвращает datetime или None.

    Год в шапке отсутствует, поэтому:
      • `academic_year` (год начала уч. года таблицы, `students.academic_year`)
        — основной способ: сентябрь–декабрь → academic_year, январь–август →
        academic_year+1. Не зависит от текущей даты → прошлогодняя таблица
        никогда не «съезжает» на текущий год (инцидент 2026-09-02).
      • Fallback (academic_year=None): учебный год, в который попадает `now`
        (default: сейчас по Ташкенту, UTC+5 — не локальное/UTC время сервера,
        иначе на границе уч. года дата уехала бы в соседний год, B13).
    """
    parsed = _parse_russian_day_month(date_str)
    if parsed is None:
        return None
    day, month = parsed

    if academic_year is None:
        if now is None:
            now = _tashkent_now()
        academic_year = current_academic_year(now.date() if isinstance(now, datetime) else now)
    year = year_for_month(month, academic_year)

    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _warn_if_header_dates_unparsed(date_row: List[Any], parsed_ok: int, context: str = "") -> None:
    """Наблюдаемость (B13): лист ПОЛУЧЕН (не сетевой сбой), но при непустой шапке
    ни одна колонка-дата не распозналась → это «тихий пропуск» оценок. Логируем
    WARNING с тегом `[DATE_PARSE_FAIL]` (для отдельного грепа), один раз на проход.

    NB: только логирование — Bot API из hot-path парсинга НЕ дёргаем.
    """
    non_empty = [str(c).strip() for c in date_row[1:] if c is not None and str(c).strip()]
    if non_empty and parsed_ok == 0:
        logger.warning(
            f"[DATE_PARSE_FAIL] Шапка листа содержит {len(non_empty)} непустых "
            f"дата-колонок, но НИ ОДНА не распозналась как дата"
            f"{(' (' + context + ')') if context else ''}. "
            f"Оценки за сегодня могут молча не записаться. "
            f"Примеры шапки: {non_empty[:5]}"
        )


def _parse_all_grades_sheet(
    data: List[List[str]], context: str = "", academic_year: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Парсит данные листа "Все оценки" в список записей.

    `academic_year` — учебный год таблицы для восстановления года в датах шапки
    (см. _parse_russian_date). None → fallback от текущей даты.

    Returns:
        Список словарей: {subject, grade_value, raw_text, date, col_index}
    """
    if not data or len(data) < 3:
        return []

    # Строка 2 (index 1) — заголовки дат
    date_row = data[1]
    dates = []
    parsed_ok = 0
    for col_idx, cell in enumerate(date_row):
        if col_idx == 0:
            continue  # Первый столбец — "Оценки"
        parsed = _parse_russian_date(str(cell).strip(), academic_year=academic_year)
        if parsed:
            parsed_ok += 1
        dates.append((col_idx, parsed))

    _warn_if_header_dates_unparsed(date_row, parsed_ok, context)

    records = []
    # Строки 3+ (index 2+) — предметы и оценки
    for row_idx, row in enumerate(data[2:], start=3):
        if not row:
            continue

        subject = str(row[0]).strip()
        if not subject or subject.lower() in SKIP_SUBJECTS:
            continue

        # Пропускаем строки с числами (0, 1, 2) в первом столбце — служебные
        try:
            int(subject)
            continue
        except ValueError:
            pass

        for col_idx, date_val in dates:
            if col_idx >= len(row):
                continue

            cell_value = str(row[col_idx]).strip()
            if not cell_value:
                continue

            # Парсим ячейку как список (поддержка X/Y: «2/5» → две оценки)
            cell_grades = sanitize_cell(cell_value)
            if not cell_grades:
                continue  # Мусор / спец-токены, которые мы не пишем в историю

            raw_text = "/".join(t for _, t in cell_grades)
            nums = [g for g, _ in cell_grades if g is not None]
            grade_value = (sum(nums) / len(nums)) if nums else None

            records.append({
                'subject': subject,
                'grade_value': grade_value,
                'raw_text': raw_text,
                'date': date_val,
                'col_index': col_idx,
                'row_index': row_idx,
            })

    # Фикс B: дедуп внутри листа. Учитель иногда повторяет один и тот же день
    # в нескольких столбцах «Все оценки» (наблюдалось в проде: GE6/IN6, GD6/IM6).
    # Берём первое появление (subject, day, raw_text), остальное молча отбрасываем.
    seen: set = set()
    deduped: List[Dict[str, Any]] = []
    for rec in records:
        key = (
            rec['subject'],
            rec['date'].date().isoformat() if rec['date'] else None,
            rec['raw_text'],
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(rec)
    return deduped


def _import_from_sheet(
    student_id: int,
    spreadsheet_id: str,
    range_name: str,
    sheet_label: str,
    academic_year: Optional[int] = None,
    data: Optional[List[List[Any]]] = None,
) -> Dict[str, int]:
    """Generic чтение оценок из любого листа со структурой «предметы × даты».

    Подходит для «Все оценки» (master) и «Неделя» (свежий рабочий лист).

    Дедуп по содержимому (student_id, subject, date_added, raw_text) — если
    та же оценка уже в БД из другого листа, не дублируем.

    sheet_label попадает в cell_reference как префикс ("Все оценки!" / "Неделя!")
    для дебага и уникальности SQL-вставки.

    `academic_year` — учебный год таблицы (для года в датах шапки).
    `data` — уже загруженный лист (чтобы не читать Sheets дважды); None → fetch.
    """
    if data is None:
        try:
            data = get_sheet_data(spreadsheet_id, range_name)
        except Exception as e:
            logger.error(f"Failed to fetch '{range_name}' for student {student_id}: {e}")
            return {'imported': 0, 'skipped': 0, 'total': 0}

    if not data:
        return {'imported': 0, 'skipped': 0, 'total': 0}

    records = _parse_all_grades_sheet(
        data, context=f"student={student_id}, sheet={sheet_label}", academic_year=academic_year
    )
    imported = 0
    skipped = 0
    today = _tashkent_today_date()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for rec in records:
            # Фикс A: сегодня и будущие даты — зона monitor'а. Не пишем из истории.
            #
            # Раньше (== today) был баг: учитель проставлял в «Все оценки» оценку
            # на завтра, importer записывал её с datedate > today, а когда «завтра»
            # становилось «сегодня», monitor видел запись в БД через
            # get_existing_grade_by_content → пропускал → уведомление терялось.
            # Инцидент 22.05.2026 (Умарбек, Английский язык, JD6).
            #
            # Сейчас (>= today) importer импортирует ТОЛЬКО исторические оценки
            # (вчера и раньше). Сегодняшние и будущие — всегда через monitor → notify.
            if rec['date'] and rec['date'].date() >= today:
                skipped += 1
                continue

            grade_date = rec['date'].date().isoformat() if rec['date'] else None
            date_added = rec['date'].strftime('%Y-%m-%d 12:00:00') if rec['date'] else None
            cell_ref = f"{sheet_label}{_col_letter(rec['col_index'])}{rec['row_index']}"

            # Дедуп по содержимому: если в БД уже есть та же оценка по
            # (предмет, ДЕНЬ, значение) — пропускаем. Сравниваем именно
            # grade_date с fallback на date(date_added, '+5h') для legacy-записей.
            cursor.execute('''
                SELECT 1 FROM grade_history
                WHERE student_id = %s AND subject = %s
                  AND COALESCE(
                        grade_date::text,
                        (date_added::timestamp + interval '5 hours')::date::text)
                      = COALESCE(%s, '')
                  AND raw_text = %s
                LIMIT 1
            ''', (student_id, rec['subject'], grade_date, rec['raw_text']))
            if cursor.fetchone():
                skipped += 1
                continue

            # ON CONFLICT DO NOTHING вместо try/except: в PG любая ошибка
            # (UNIQUE constraint на cell_reference того же листа — повторный
            # импорт после ручного редактирования) аборти́т ВСЮ транзакцию, и
            # следующий execute в цикле упал бы. Дедуп выше должен ловить такие
            # случаи раньше, но ON CONFLICT — надёжный safety net без abort'а.
            # rowcount=0 → конфликт → считаем как skipped.
            if date_added:
                cursor.execute('''
                    INSERT INTO grade_history
                      (student_id, subject, grade_value, raw_text,
                       cell_reference, date_added, grade_date)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                ''', (student_id, rec['subject'], rec['grade_value'],
                      rec['raw_text'], cell_ref, date_added, grade_date))
            else:
                cursor.execute('''
                    INSERT INTO grade_history
                      (student_id, subject, grade_value, raw_text, cell_reference)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                ''', (student_id, rec['subject'], rec['grade_value'],
                      rec['raw_text'], cell_ref))
            if cursor.rowcount:
                imported += 1
            else:
                skipped += 1

    return {'imported': imported, 'skipped': skipped, 'total': len(records)}


def import_history_for_student(student_id: int, spreadsheet_id: str) -> Dict[str, int]:
    """
    Импортирует оценки студента из обоих листов: «Все оценки» (master со 2 сент)
    + «Неделя» (свежие оценки текущей недели, ещё не перенесённые в master).

    Дедуп по (subject, date, raw_text) гарантирует что одна и та же оценка
    из обоих листов не задвоится в БД.

    Учебный год таблицы берётся из `students.academic_year`; если ещё не
    определён (новая привязка / смена ссылки) — выводится по содержимому
    master-листа (`infer_sheet_academic_year`) и записывается в БД, чтобы
    монитор и следующие импорты использовали тот же год.
    """
    from src.database_manager import get_student_academic_year, set_student_academic_year

    academic_year = get_student_academic_year(student_id)
    master_data = None
    if academic_year is None:
        try:
            master_data = get_sheet_data(spreadsheet_id, MASTER_SHEET_RANGE)
        except Exception as e:
            logger.error(f"Failed to fetch master sheet for student {student_id}: {e}")
            master_data = None
        if master_data:
            academic_year = resolve_academic_year_from_sheet(master_data, context=f"student={student_id}")
            set_student_academic_year(student_id, academic_year)
            logger.info(f"[ACADEMIC_YEAR] student {student_id}: inferred {academic_year} from sheet content")

    r_master = _import_from_sheet(
        student_id, spreadsheet_id, MASTER_SHEET_RANGE, "Все оценки!",
        academic_year=academic_year, data=master_data,
    )
    r_week = _import_from_sheet(
        student_id, spreadsheet_id, "Неделя!A1:I50", "Неделя!", academic_year=academic_year,
    )

    result = {
        'imported': r_master['imported'] + r_week['imported'],
        'skipped': r_master['skipped'] + r_week['skipped'],
        'total': r_master['total'] + r_week['total'],
    }
    logger.info(
        f"History import for student {student_id}: "
        f"master={r_master['imported']}/{r_master['total']}, "
        f"week={r_week['imported']}/{r_week['total']}"
    )
    return result


def import_quarters_for_student(student_id: int, spreadsheet_id: str) -> Dict[str, int]:
    """
    Импортирует четвертные оценки из листа "Четверти".

    Структура листа:
      - Строка 1: заголовок (пропускаем)
      - Строка 2: "Предметы" | "1 Четверть" | "2 Четверть" | "3 Четверть" | "4 Четверть" | "Год"
      - Строки 3+: предмет | оценка | оценка | ...

    Returns:
        {imported: int, skipped: int, total: int}
    """
    from src.database_manager import upsert_quarter_grade

    RANGE_NAME = "Четверти!A1:G50"

    try:
        data = get_sheet_data(spreadsheet_id, RANGE_NAME)
    except Exception as e:
        logger.error(f"Failed to fetch 'Четверти' for student {student_id}: {e}")
        return {'imported': 0, 'skipped': 0, 'total': 0}

    if not data or len(data) < 3:
        logger.warning(f"No data in 'Четверти' for student {student_id}")
        return {'imported': 0, 'skipped': 0, 'total': 0}

    # Столбцы B-F = четверти 1-4 + год (quarter=5 для годовой)
    imported = 0
    skipped = 0
    total = 0

    for row in data[1:]:  # Пропускаем заголовок
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

        # Столбцы 1-5: четверти 1-4 + год
        for col_idx in range(1, min(len(row), 7)):
            cell_value = str(row[col_idx]).strip()
            if not cell_value:
                continue

            quarter = col_idx  # 1=1ч, 2=2ч, 3=3ч, 4=4ч, 5=год

            grade_value, clean_text = sanitize_grade(cell_value)
            if clean_text is None:
                continue

            total += 1
            changed = upsert_quarter_grade(student_id, subject, quarter, grade_value, clean_text)
            if changed:
                imported += 1
            else:
                skipped += 1

    result = {'imported': imported, 'skipped': skipped, 'total': total}
    logger.info(f"Quarter import for student {student_id}: {result}")
    return result


def import_history_for_all_students(force: bool = False):
    """
    Импорт истории для всех студентов из листа «Все оценки».

    Если force=False (default): пропускает студентов у которых УЖЕ есть
    исторические записи — это поведение для одноразового первоначального
    импорта при старте бота.

    Если force=True: всегда вызывает import_history_for_student. UNIQUE
    constraint на cell_reference защитит от дубликатов, но НОВЫЕ оценки
    (которые учитель добавил после последнего импорта) подтянутся.
    Используется регулярным sync'ом из monitor_engine раз в час.
    """
    from src.database_manager import get_active_spreadsheets

    students = get_active_spreadsheets()
    if not students:
        logger.info("No active students for history import.")
        return

    for student in students:
        student_id = student['student_id']
        spreadsheet_id = student['spreadsheet_id']

        if not force:
            # Первоначальный импорт: пропускаем уже импортированных
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT COUNT(*) as c FROM grade_history
                    WHERE student_id = %s AND cell_reference LIKE 'Все оценки!%%'
                ''', (student_id,))
                count = cursor.fetchone()['c']

            if count > 0:
                logger.info(f"Student {student_id} already has {count} historical records, skipping.")
                continue

        logger.info(f"Importing history for student {student_id} ({student['fio']})...")
        result = import_history_for_student(student_id, spreadsheet_id)
        logger.info(f"Student {student_id} history: imported={result['imported']}, skipped={result['skipped']}")

        if not force:
            # Четвертные импортируем только при первоначальном (force=True вызывается
            # регулярно — quarter_grades имеет UPSERT logic, можно дёргать тоже,
            # но это лишний трафик; четверти меняются раз в неделю-две)
            q_result = import_quarters_for_student(student_id, spreadsheet_id)
            logger.info(f"Student {student_id} quarters: imported={q_result['imported']}, skipped={q_result['skipped']}")


def _col_letter(col_index: int) -> str:
    """Конвертирует индекс столбца (0-based) в буквенное обозначение (A, B, ..., Z, AA, AB...)."""
    result = ''
    idx = col_index
    while True:
        result = chr(ord('A') + idx % 26) + result
        idx = idx // 26 - 1
        if idx < 0:
            break
    return result


# ─── MONOSOURCE_GRADES (этап 4 RFC, shadow run 2026-05-21) ───────────
# Чтение «сегодняшней колонки» из листа «Все оценки» — для shadow-сравнения
# с тем что monitor читает из «Сегодня!». После окончания shadow run и
# GO-решения — заменяет логику «Сегодня!» в monitor_engine полностью.
MASTER_SHEET_RANGE = "Все оценки!A1:ZZ50"


def resolve_academic_year_from_sheet(data: List[List[Any]], context: str = "", today=None) -> int:
    """Учебный год таблицы по её содержимому: месяцы колонок, где есть оценки.
    Первый проход парсится с fallback-годом — для вывода важны только месяцы."""
    records = _parse_all_grades_sheet(data, context=context)
    months = {rec['date'].month for rec in records if rec.get('date')}
    return infer_sheet_academic_year(months, today=today)


def _parse_master_sheet_for_date(
    data: List[List[Any]], target_date, context: str = "", academic_year: Optional[int] = None
) -> List[Tuple[str, str]]:
    """Pure-функция (для тестов): находит колонку с `target_date` в шапке (row 2)
    и возвращает [(subject, raw_grade)] из этой колонки.

    `academic_year` — учебный год таблицы: колонка «2 сентября» таблицы 2025/26
    = 2025-09-02 и НЕ совпадёт с target_date 2026-09-02 (инцидент 2026-09-02).
    None → год от текущей даты (legacy fallback).

    Возвращает только непустые значения. Пропускает служебные строки
    («Посещаемость», числовые заголовки).

    Наблюдаемость (B13): если шапка непустая, но НИ ОДНА колонка не распозналась
    как дата — логируем WARNING (`[DATE_PARSE_FAIL]`), иначе тихий пропуск оценок
    в monitor'е был бы невидим. `context` — для идентификации студента в логе.
    """
    if not data or len(data) < 3:
        return []

    # row 2 (index 1) — даты в шапке. Ищем колонку для target_date.
    # Сканируем всю шапку (не break на первом матче), чтобы посчитать сколько
    # колонок вообще распозналось — для [DATE_PARSE_FAIL] наблюдаемости.
    date_row = data[1]
    target_col = None
    parsed_ok = 0
    for col_idx, cell in enumerate(date_row):
        if col_idx == 0:
            continue  # первый столбец — «Оценки»
        parsed = _parse_russian_date(str(cell).strip(), academic_year=academic_year) if cell else None
        if parsed:
            parsed_ok += 1
            if parsed.date() == target_date and target_col is None:
                target_col = col_idx

    _warn_if_header_dates_unparsed(date_row, parsed_ok, context)

    if target_col is None:
        return []

    # row 3+ — предметы и их оценки в target_col.
    grades: List[Tuple[str, str]] = []
    for row in data[2:]:
        if not row or len(row) <= target_col:
            continue
        subject = str(row[0]).strip() if row[0] is not None else ""
        if not subject or subject.lower() in SKIP_SUBJECTS:
            continue
        # Пропускаем служебные числовые заголовки (0, 1, 2…)
        try:
            int(subject)
            continue
        except ValueError:
            pass

        cell_val = row[target_col]
        raw = str(cell_val).strip() if cell_val is not None else ""
        if not raw:
            continue
        grades.append((subject, raw))

    return grades


def read_master_sheet_today_grades(spreadsheet_id: str) -> List[Tuple[str, str]]:
    """Читает «Все оценки!» и возвращает [(subject, raw_grade)] для сегодняшней
    даты по Ташкенту. Пустой список — нет такой даты в шапке или нет данных.

    Используется в monitor'е в shadow-режиме (этап 4 RFC). После GO-решения
    эта функция заменит чтение «Сегодня!» полностью."""
    try:
        data = get_sheet_data(spreadsheet_id, MASTER_SHEET_RANGE)
    except Exception as e:
        logger.warning(f"[SHADOW] Failed to fetch master sheet for {spreadsheet_id}: {e}")
        return []
    if data is None:
        return []
    today = _tashkent_today_date()  # уже date, не datetime
    return _parse_master_sheet_for_date(
        data, today, context=f"spreadsheet={spreadsheet_id}",
        academic_year=current_academic_year(today),
    )
