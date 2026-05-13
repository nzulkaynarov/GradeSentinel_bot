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
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

from src.google_sheets import get_sheet_data
from src.data_cleaner import sanitize_grade, sanitize_cell
from src.database_manager import get_db_connection

logger = logging.getLogger(__name__)

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

# Текущий учебный год: сентябрь-декабрь = текущий/прошлый год, январь-август = следующий/текущий
CURRENT_YEAR = datetime.now().year


def _parse_russian_date(date_str: str) -> Optional[datetime]:
    """
    Парсит русскую дату вида '2 сентября', '14 март Сб', '1 октября' и т.д.
    Возвращает datetime или None.
    """
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

    # Определяем год по учебному году
    # Сентябрь-декабрь → год начала учебного года
    # Январь-август → следующий год
    now = datetime.now()
    if month >= 9:
        # Если сейчас январь-август, учебный год начался в прошлом году
        year = now.year if now.month >= 9 else now.year - 1
    else:
        # Январь-август: год окончания учебного года
        year = now.year if now.month <= 8 else now.year + 1

    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _parse_all_grades_sheet(data: List[List[str]]) -> List[Dict[str, Any]]:
    """
    Парсит данные листа "Все оценки" в список записей.

    Returns:
        Список словарей: {subject, grade_value, raw_text, date, col_index}
    """
    if not data or len(data) < 3:
        return []

    # Строка 2 (index 1) — заголовки дат
    date_row = data[1]
    dates = []
    for col_idx, cell in enumerate(date_row):
        if col_idx == 0:
            continue  # Первый столбец — "Оценки"
        parsed = _parse_russian_date(str(cell).strip())
        dates.append((col_idx, parsed))

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

    return records


def _import_from_sheet(
    student_id: int,
    spreadsheet_id: str,
    range_name: str,
    sheet_label: str,
) -> Dict[str, int]:
    """Generic чтение оценок из любого листа со структурой «предметы × даты».

    Подходит для «Все оценки» (master) и «Неделя» (свежий рабочий лист).

    Дедуп по содержимому (student_id, subject, date_added, raw_text) — если
    та же оценка уже в БД из другого листа, не дублируем.

    sheet_label попадает в cell_reference как префикс ("Все оценки!" / "Неделя!")
    для дебага и уникальности SQL-вставки.
    """
    try:
        data = get_sheet_data(spreadsheet_id, range_name)
    except Exception as e:
        logger.error(f"Failed to fetch '{range_name}' for student {student_id}: {e}")
        return {'imported': 0, 'skipped': 0, 'total': 0}

    if not data:
        return {'imported': 0, 'skipped': 0, 'total': 0}

    records = _parse_all_grades_sheet(data)
    imported = 0
    skipped = 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for rec in records:
            date_added = rec['date'].strftime('%Y-%m-%d 12:00:00') if rec['date'] else None
            cell_ref = f"{sheet_label}{_col_letter(rec['col_index'])}{rec['row_index']}"

            # Дедуп по содержимому: если в БД уже есть та же оценка по
            # (предмет, ДЕНЬ без времени, значение) — пропускаем.
            # Сравнение через date() важно: monitor пишет «Сегодня» с реальным
            # timestamp (HH:MM:SS), а импорт из «Все оценки»/«Неделя» — с 12:00.
            # Без date() они считались бы разными.
            cursor.execute('''
                SELECT 1 FROM grade_history
                WHERE student_id = ? AND subject = ?
                  AND COALESCE(date(date_added), '') = COALESCE(date(?), '')
                  AND raw_text = ?
                LIMIT 1
            ''', (student_id, rec['subject'], date_added, rec['raw_text']))
            if cursor.fetchone():
                skipped += 1
                continue

            try:
                if date_added:
                    cursor.execute('''
                        INSERT INTO grade_history (student_id, subject, grade_value, raw_text, cell_reference, date_added)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (student_id, rec['subject'], rec['grade_value'], rec['raw_text'], cell_ref, date_added))
                else:
                    cursor.execute('''
                        INSERT INTO grade_history (student_id, subject, grade_value, raw_text, cell_reference)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (student_id, rec['subject'], rec['grade_value'], rec['raw_text'], cell_ref))
                imported += 1
            except Exception:
                # UNIQUE constraint на cell_reference (того же листа) — повторный
                # импорт того же листа после ручного редактирования. Дедуп выше
                # должен ловить такие случаи раньше, но safety net не помешает.
                skipped += 1

    return {'imported': imported, 'skipped': skipped, 'total': len(records)}


def import_history_for_student(student_id: int, spreadsheet_id: str) -> Dict[str, int]:
    """
    Импортирует оценки студента из обоих листов: «Все оценки» (master со 2 сент)
    + «Неделя» (свежие оценки текущей недели, ещё не перенесённые в master).

    Дедуп по (subject, date, raw_text) гарантирует что одна и та же оценка
    из обоих листов не задвоится в БД.
    """
    r_master = _import_from_sheet(student_id, spreadsheet_id, "Все оценки!A1:ZZ50", "Все оценки!")
    r_week = _import_from_sheet(student_id, spreadsheet_id, "Неделя!A1:I50", "Неделя!")

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
                    WHERE student_id = ? AND cell_reference LIKE 'Все оценки!%'
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
