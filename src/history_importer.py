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
from src.data_cleaner import sanitize_grade
from src.database_manager import get_db_connection

logger = logging.getLogger(__name__)

# Маппинг русских названий месяцев (в родительном падеже и сокращениях)
MONTH_MAP = {
    'январ': 1, 'феврал': 2, 'март': 3, 'апрел': 4,
    'ма': 5, 'июн': 6, 'июл': 7, 'август': 8,
    'сентябр': 9, 'октябр': 10, 'ноябр': 11, 'декабр': 12,
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
    month_text = match.group(2).lower().rstrip('яьа')  # убираем окончания: сентября -> сентябр

    month = None
    for prefix, m in MONTH_MAP.items():
        if month_text.startswith(prefix) or prefix.startswith(month_text):
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

            grade_value, clean_text = sanitize_grade(cell_value)
            if clean_text is None:
                continue  # Мусор — пропускаем

            records.append({
                'subject': subject,
                'grade_value': grade_value,
                'raw_text': clean_text,
                'date': date_val,
                'col_index': col_idx,
                'row_index': row_idx,
            })

    return records


def import_history_for_student(student_id: int, spreadsheet_id: str) -> Dict[str, int]:
    """
    Импортирует все исторические оценки из листа "Все оценки" для студента.

    Returns:
        Словарь {imported: int, skipped: int, total: int}
    """
    RANGE_NAME = "Все оценки!A1:ZZ50"

    try:
        data = get_sheet_data(spreadsheet_id, RANGE_NAME)
    except Exception as e:
        logger.error(f"Failed to fetch 'Все оценки' for student {student_id}: {e}")
        return {'imported': 0, 'skipped': 0, 'total': 0}

    if not data:
        logger.warning(f"No data in 'Все оценки' for student {student_id}")
        return {'imported': 0, 'skipped': 0, 'total': 0}

    records = _parse_all_grades_sheet(data)
    logger.info(f"Parsed {len(records)} grade records from 'Все оценки' for student {student_id}")

    imported = 0
    skipped = 0

    with get_db_connection() as conn:
        cursor = conn.cursor()
        for rec in records:
            # cell_reference уникальный: "Все оценки!{col}{row}"
            cell_ref = f"Все оценки!{_col_letter(rec['col_index'])}{rec['row_index']}"

            # date_added берём из распарсенной даты (если удалось парсить)
            date_added = rec['date'].strftime('%Y-%m-%d 12:00:00') if rec['date'] else None

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
                # UNIQUE constraint — запись уже существует
                skipped += 1

    result = {'imported': imported, 'skipped': skipped, 'total': len(records)}
    logger.info(f"History import for student {student_id}: {result}")
    return result


def import_history_for_all_students():
    """
    Одноразовый импорт истории для всех студентов, у которых ещё нет исторических данных.
    Проверяет наличие записей с cell_reference вида 'Все оценки!%'.
    """
    from src.database_manager import get_active_spreadsheets

    students = get_active_spreadsheets()
    if not students:
        logger.info("No active students for history import.")
        return

    for student in students:
        student_id = student['student_id']
        spreadsheet_id = student['spreadsheet_id']

        # Проверяем, был ли уже импорт
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
        logger.info(f"Student {student_id}: imported={result['imported']}, skipped={result['skipped']}")


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
