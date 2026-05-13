"""Одноразовый backfill `grade_history.grade_date` для существующих записей.

Этап 1B RFC (Docs/rfc-grades-source-of-truth.md). Запускается админом
явно после деплоя этапа 1A. НЕ часть init_db — мы хотим контролируемое
поведение с DRY-RUN и репортом.

Источники даты по приоритету:
1. cell_reference вида "Сегодня!{subject}:{YYYY-MM-DD}" — дата из самого ключа,
   её ставил monitor_engine в момент INSERT.
2. cell_reference вида "Все оценки!{COL}{ROW}" или "Неделя!{COL}{ROW}" — берём
   заголовок столбца из шапки Sheets (best current knowledge) и парсим через
   src.history_importer._parse_russian_date.
3. Fallback: date(date_added). Логируется как WARN — означает что у записи нет
   способа восстановить «правильную» дату, используем технический timestamp.

Запуск:
    python -m scripts.backfill_grade_date              # DRY-RUN отчёт
    python -m scripts.backfill_grade_date --apply      # UPDATE на месте
"""
import argparse
import logging
import os
import re
import sys
from datetime import date as date_cls
from typing import Dict, List, Optional, Tuple

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# Cell-reference форматы. Берём с захватом дат / координат.
_TODAY_RE = re.compile(r'^Сегодня!(?P<subject>.+):(?P<date>\d{4}-\d{2}-\d{2})$')
_GRID_RE = re.compile(r'^(?P<sheet>Все оценки|Неделя)!(?P<col>[A-Z]+)(?P<row>\d+)$')


def col_letter_to_index_0based(letters: str) -> int:
    """'A' → 0, 'B' → 1, 'CI' → 86. Совпадает с обратной функцией
    `src.history_importer._col_letter`, которая использует 0-based индексы."""
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - ord('A') + 1)
    return idx - 1


def resolve_grade_date(
    cell_reference: str,
    date_added: Optional[str],
    headers_by_sheet: Dict[str, List[str]],
) -> Tuple[Optional[date_cls], str]:
    """Чистая функция: возвращает (grade_date | None, source_label).

    headers_by_sheet — словарь {sheet_name: [headers_0based]} где headers[i]
    это значение колонки в `_col_letter(i)`. Например headers['Все оценки'][0]
    = "Оценки", headers['Все оценки'][1] = "2 сентября".

    source_label: одно из 'cell_ref_today', 'sheet_header', 'fallback_date_added',
    'unresolved'.
    """
    from src.history_importer import _parse_russian_date

    m_today = _TODAY_RE.match(cell_reference)
    if m_today:
        try:
            d = date_cls.fromisoformat(m_today.group('date'))
            return d, 'cell_ref_today'
        except ValueError:
            pass  # fall-through на fallback

    m_grid = _GRID_RE.match(cell_reference)
    if m_grid:
        sheet = m_grid.group('sheet')
        col_idx = col_letter_to_index_0based(m_grid.group('col'))
        headers = headers_by_sheet.get(sheet, [])
        if 0 <= col_idx < len(headers):
            header = headers[col_idx]
            if header and header.strip():
                parsed = _parse_russian_date(header)
                if parsed is not None:
                    return parsed.date(), 'sheet_header'

    # Fallback: технический timestamp вставки. Не идеал но лучше чем NULL.
    if date_added:
        try:
            return date_cls.fromisoformat(date_added[:10]), 'fallback_date_added'
        except ValueError:
            pass

    return None, 'unresolved'


def load_headers_for_student(spreadsheet_id: str) -> Dict[str, List[str]]:
    """Грузит шапки «Все оценки!A2:JZ2» и «Неделя!A2:Z2» через Google Sheets API.
    Возвращает {sheet_name: [headers_0based]}."""
    from src.google_sheets import get_sheet_data
    out: Dict[str, List[str]] = {}
    for sheet, rng in [("Все оценки", "Все оценки!A2:JZ2"), ("Неделя", "Неделя!A2:Z2")]:
        try:
            data = get_sheet_data(spreadsheet_id, rng)
        except Exception as e:
            logger.warning(f"Не смог прочитать {rng} для {spreadsheet_id}: {e}")
            out[sheet] = []
            continue
        if not data or not data[0]:
            out[sheet] = []
            continue
        out[sheet] = [str(c) if c is not None else "" for c in data[0]]
    return out


def backfill(
    headers_by_student: Dict[int, Dict[str, List[str]]],
    apply: bool = False,
) -> Dict[str, int]:
    """Главная функция. headers_by_student — для каждого student_id шапки его листов.

    Возвращает счётчики по source_label плюс 'updated' (сколько UPDATE'ов).
    """
    from src.database_manager import get_db_connection

    counters = {
        'cell_ref_today': 0,
        'sheet_header': 0,
        'fallback_date_added': 0,
        'unresolved': 0,
        'updated': 0,
        'skipped_already_set': 0,
    }
    plan: List[Tuple[int, str]] = []  # (id, grade_date.isoformat())

    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT id, student_id, cell_reference, date_added, grade_date
            FROM grade_history
            ORDER BY id
        """)
        rows = cur.fetchall()

    for r in rows:
        if r['grade_date']:
            counters['skipped_already_set'] += 1
            continue
        headers = headers_by_student.get(r['student_id'], {})
        gd, source = resolve_grade_date(r['cell_reference'], r['date_added'], headers)
        counters[source] += 1
        if gd is not None:
            plan.append((r['id'], gd.isoformat()))

    if apply and plan:
        with get_db_connection() as conn:
            cur = conn.cursor()
            for rid, gd in plan:
                cur.execute(
                    "UPDATE grade_history SET grade_date = ? WHERE id = ?",
                    (gd, rid),
                )
            counters['updated'] = len(plan)

    counters['_plan_size'] = len(plan)
    return counters


def _samples(rows, source_label, headers_by_student, n=5):
    """Несколько образцов для отчёта — посмотреть руками что бы поставили."""
    out = []
    for r in rows:
        if r['grade_date']:
            continue
        headers = headers_by_student.get(r['student_id'], {})
        gd, source = resolve_grade_date(r['cell_reference'], r['date_added'], headers)
        if source != source_label:
            continue
        out.append((r['id'], r['cell_reference'], r['date_added'], gd))
        if len(out) >= n:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true',
                    help='Применить UPDATE (по умолчанию DRY-RUN)')
    ap.add_argument('--no-sheets', action='store_true',
                    help='Не читать Sheets API (только cell_ref_today + fallback)')
    args = ap.parse_args()

    from src.database_manager import get_active_spreadsheets, get_db_connection

    headers_by_student: Dict[int, Dict[str, List[str]]] = {}
    if not args.no_sheets:
        for s in get_active_spreadsheets():
            sid = s['student_id']
            logger.info(f"Читаю Sheets для student {sid}...")
            headers_by_student[sid] = load_headers_for_student(s['spreadsheet_id'])

    # Для отчёта — образцы из каждой категории
    with get_db_connection() as conn:
        rows = conn.cursor().execute(
            "SELECT id, student_id, cell_reference, date_added, grade_date "
            "FROM grade_history ORDER BY id"
        ).fetchall()

    counters = backfill(headers_by_student, apply=args.apply)

    print("\n=== BACKFILL REPORT ===")
    for k in ['cell_ref_today', 'sheet_header', 'fallback_date_added',
              'unresolved', 'skipped_already_set']:
        print(f"  {k:25} {counters[k]}")
    print(f"  {'_plan_size':25} {counters['_plan_size']}")
    print(f"  {'updated':25} {counters['updated']}")
    if not args.apply:
        print("\n(DRY-RUN — для применения добавь --apply)")

    for label in ['cell_ref_today', 'sheet_header', 'fallback_date_added', 'unresolved']:
        samples = _samples(rows, label, headers_by_student, n=5)
        if samples:
            print(f"\n  образцы {label}:")
            for rid, ref, da, gd in samples:
                print(f"    id={rid:5} ref={ref:35} da={da} → {gd}")


if __name__ == '__main__':
    main()
