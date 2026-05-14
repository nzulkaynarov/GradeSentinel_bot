"""Этап 1B RFC: backfill grade_date через резолвер шапки Sheets / cell_ref."""
import os
import sys
from datetime import date

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm
from scripts.backfill_grade_date import (
    backfill,
    col_letter_to_index_0based,
    resolve_grade_date,
)


# ─── col_letter <-> index ──────────────────────────────────────────────
def test_col_letter_to_index_0based():
    """Должен совпадать с обратной функцией src.history_importer._col_letter."""
    from src.history_importer import _col_letter
    for i in [0, 1, 25, 26, 51, 52, 86, 87, 247, 264]:
        letters = _col_letter(i)
        back = col_letter_to_index_0based(letters)
        assert back == i, f"_col_letter({i})={letters!r} → back={back}, expected {i}"


# ─── resolve_grade_date — pure function ────────────────────────────────
def test_resolve_today_cell_ref():
    """monitor пишет cell_ref содержащий дату — берём её, не нужна шапка."""
    gd, src = resolve_grade_date(
        "Сегодня!Алгебра:2026-05-13",
        date_added="2026-05-13 14:47:10",
        headers_by_sheet={},
    )
    assert gd == date(2026, 5, 13)
    assert src == 'cell_ref_today'


def test_resolve_today_cell_ref_with_colons_in_subject():
    """Предмет может содержать любые символы; разделитель — последнее двоеточие
    перед YYYY-MM-DD. Сейчас regex жадно матчит subject; убеждаемся что не падаем."""
    gd, src = resolve_grade_date(
        "Сегодня!Литература (XIX в.):2026-05-13",
        date_added=None,
        headers_by_sheet={},
    )
    assert gd == date(2026, 5, 13)
    assert src == 'cell_ref_today'


def test_resolve_sheet_header_vse_ocenki():
    """cell_ref «Все оценки!IU5» → берём header[col(IU)] = «13 мая»."""
    # IU = 254 + 1 = 255 (1-based); в 0-based = 254. Но _col_letter использует 0-based.
    # IU = 254 in 0-based: 'I'(8) * 26 + 'U'(20) - смотря какой счёт.
    # Точнее: пусть headers — список с индексами 0..264. IU соответствует col_letter_to_index_0based('IU').
    iu_idx = col_letter_to_index_0based('IU')
    headers_all = ['Оценки'] + [f'fake-{i}' for i in range(264)]
    headers_all[iu_idx] = '13 мая'
    gd, src = resolve_grade_date(
        "Все оценки!IU5",
        date_added="2026-05-13 12:00:00",
        headers_by_sheet={'Все оценки': headers_all, 'Неделя': []},
    )
    assert gd == date(2026, 5, 13)
    assert src == 'sheet_header'


def test_resolve_sheet_header_for_nedelya():
    """То же для «Неделя»: cell_ref «Неделя!G6» → header[col(G)]."""
    g_idx = col_letter_to_index_0based('G')
    headers_week = [''] * 27
    headers_week[g_idx] = '10 мая вс'
    gd, src = resolve_grade_date(
        "Неделя!G6",
        date_added=None,
        headers_by_sheet={'Все оценки': [], 'Неделя': headers_week},
    )
    assert gd == date(2026, 5, 10)
    assert src == 'sheet_header'


def test_resolve_header_out_of_range_falls_back():
    """Если col_idx за пределы шапки — fallback на date_added."""
    gd, src = resolve_grade_date(
        "Все оценки!ZZZ5",  # очень далеко справа
        date_added="2026-04-01 12:00:00",
        headers_by_sheet={'Все оценки': ['Оценки'], 'Неделя': []},
    )
    assert gd == date(2026, 4, 1)
    assert src == 'fallback_date_added'


def test_resolve_empty_header_falls_back():
    """Header пуст — fallback."""
    gd, src = resolve_grade_date(
        "Все оценки!B5",
        date_added="2025-09-02 12:00:00",
        headers_by_sheet={'Все оценки': ['', '', 'не дата'], 'Неделя': []},
    )
    assert gd == date(2025, 9, 2)
    assert src == 'fallback_date_added'


def test_resolve_unparseable_header_falls_back():
    """Header есть но не парсится — fallback."""
    gd, src = resolve_grade_date(
        "Все оценки!B5",
        date_added="2025-09-02 12:00:00",
        headers_by_sheet={'Все оценки': ['Оценки', 'abracadabra'], 'Неделя': []},
    )
    assert gd == date(2025, 9, 2)
    assert src == 'fallback_date_added'


def test_resolve_no_match_no_date_added_unresolved():
    """Нет совпадения ни с одним паттерном, нет date_added → None / 'unresolved'."""
    gd, src = resolve_grade_date(
        "WHATEVER!X1",
        date_added=None,
        headers_by_sheet={},
    )
    assert gd is None
    assert src == 'unresolved'


# ─── backfill — оркестратор ────────────────────────────────────────────
def _seed_records(temp_db):
    """Несколько записей разных типов для проверки backfill в БД."""
    sid = dbm.add_student("Kid", "ss-bf")
    inserts = [
        # (subject, raw_text, cell_reference, date_added)
        ("Алгебра", "5", "Сегодня!Алгебра:2026-05-13", "2026-05-13 14:00:00"),
        ("Английский", "2", "Все оценки!IM6", "2026-03-05 12:00:00"),  # «битая дата»
        ("Геометрия", "4", "Все оценки!B8", "2025-09-02 12:00:00"),
        ("Литература", "3", "Неделя!G3", "2026-05-10 12:00:00"),
        # Запись с уже выставленным grade_date — skip
        ("История", "5", "Сегодня!История:2026-05-12", "2026-05-12 10:00:00"),
    ]
    with dbm.get_db_connection() as conn:
        cur = conn.cursor()
        for subj, raw, ref, da in inserts:
            cur.execute(
                "INSERT INTO grade_history (student_id, subject, raw_text, cell_reference, date_added) "
                "VALUES (?, ?, ?, ?, ?)",
                (sid, subj, raw, ref, da),
            )
        # Для последней — сразу выставим grade_date
        cur.execute(
            "UPDATE grade_history SET grade_date = ? WHERE cell_reference = ?",
            ("2026-05-12", "Сегодня!История:2026-05-12"),
        )
    return sid


def test_backfill_dry_run_does_not_change(legacy_temp_db):
    sid = _seed_records(legacy_temp_db)
    # Шапка: для записи IM6 (Все оценки!IM6) проставим «5 мая»
    headers_all = ['Оценки'] + [''] * 264
    headers_all[col_letter_to_index_0based('IM')] = '5 мая'
    headers_all[col_letter_to_index_0based('B')] = '2 сентября'
    headers_week = [''] * 27
    headers_week[col_letter_to_index_0based('G')] = '10 мая вс'

    counters = backfill(
        {sid: {'Все оценки': headers_all, 'Неделя': headers_week}},
        apply=False,
    )
    assert counters['cell_ref_today'] == 1
    assert counters['sheet_header'] == 3
    assert counters['fallback_date_added'] == 0
    assert counters['unresolved'] == 0
    assert counters['skipped_already_set'] == 1
    assert counters['updated'] == 0
    assert counters['_plan_size'] == 4

    # БД не тронута
    with dbm.get_db_connection() as conn:
        rows = conn.cursor().execute(
            "SELECT cell_reference, grade_date FROM grade_history WHERE student_id=?",
            (sid,),
        ).fetchall()
    null_count = sum(1 for r in rows if r['grade_date'] is None)
    assert null_count == 4


def test_backfill_apply_sets_grade_date(legacy_temp_db):
    sid = _seed_records(legacy_temp_db)
    headers_all = ['Оценки'] + [''] * 264
    headers_all[col_letter_to_index_0based('IM')] = '5 мая'
    headers_all[col_letter_to_index_0based('B')] = '2 сентября'
    headers_week = [''] * 27
    headers_week[col_letter_to_index_0based('G')] = '10 мая вс'

    counters = backfill(
        {sid: {'Все оценки': headers_all, 'Неделя': headers_week}},
        apply=True,
    )
    assert counters['updated'] == 4

    with dbm.get_db_connection() as conn:
        rows = {
            r['cell_reference']: r['grade_date']
            for r in conn.cursor().execute(
                "SELECT cell_reference, grade_date FROM grade_history WHERE student_id=?",
                (sid,),
            ).fetchall()
        }
    assert rows['Сегодня!Алгебра:2026-05-13'] == '2026-05-13'
    assert rows['Все оценки!IM6'] == '2026-05-05'           # ← битая дата восстановлена!
    assert rows['Все оценки!B8'] == '2025-09-02'
    assert rows['Неделя!G3'] == '2026-05-10'
    # Запись со старым grade_date не перезаписалась
    assert rows['Сегодня!История:2026-05-12'] == '2026-05-12'
