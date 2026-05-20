"""Парсер «Все оценки» для сегодняшней колонки — этап 4 RFC MONOSOURCE_GRADES.

Pure-функция `_parse_master_sheet_for_date`: получает на вход data (list of
lists, как возвращает get_sheet_data) и target_date, возвращает [(subject, raw)]
из соответствующей колонки.
"""
from datetime import date
from unittest.mock import patch

from src.history_importer import _parse_master_sheet_for_date, read_master_sheet_today_grades


# ─── Структура листа из реальных xlsx (выгрузка 21.05.2026) ──────────
def _make_sheet(date_headers, subject_rows):
    """Помощник: data[0]=header, data[1]=date row, data[2:]=subject rows.

    date_headers — список строк дат (col B+). col A — 'Оценки'.
    subject_rows — [(subject, [val_in_each_date_col, ...])]
    """
    data = [
        ['Оценки все даты'] + [f'Столбец {i+2}' for i in range(len(date_headers))],
        ['Оценки'] + list(date_headers),
    ]
    for subj, vals in subject_rows:
        data.append([subj] + list(vals))
    return data


def test_finds_today_column_and_extracts_grades():
    data = _make_sheet(
        ['20 мая', '21 мая', '22 мая'],
        [
            ('Алгебра', [None, '4', None]),
            ('Литература', ['3', '5', None]),
            ('Геометрия', [None, None, None]),  # пустая — не попадает в результат
        ],
    )
    out = _parse_master_sheet_for_date(data, date(2026, 5, 21))
    assert out == [('Алгебра', '4'), ('Литература', '5')]


def test_returns_empty_when_date_not_in_header():
    data = _make_sheet(
        ['20 мая', '22 мая'],
        [('Алгебра', ['3', '4'])],
    )
    # Сегодня — 21 мая, такой колонки нет → пустой результат, не падение
    assert _parse_master_sheet_for_date(data, date(2026, 5, 21)) == []


def test_skips_attendance_and_numeric_header_rows():
    data = _make_sheet(
        ['21 мая'],
        [
            ('Алгебра', ['5']),
            ('Посещаемость', ['100%']),  # SKIP_SUBJECTS
            ('0', ['какой-то служебный']),  # числовой — skip
            ('Литература', ['4']),
        ],
    )
    out = _parse_master_sheet_for_date(data, date(2026, 5, 21))
    assert out == [('Алгебра', '5'), ('Литература', '4')]


def test_parses_date_with_weekday_suffix():
    """Реальный формат в листе «Неделя!»: «14 март Сб». _parse_russian_date
    обрезает день недели. Здесь то же должно работать через ту же утилиту."""
    data = _make_sheet(
        ['21 мая чт', '22 мая пт'],
        [('Алгебра', ['4', '5'])],
    )
    out = _parse_master_sheet_for_date(data, date(2026, 5, 21))
    assert out == [('Алгебра', '4')]


def test_treats_whitespace_only_as_empty():
    """В реальных листах часто стоят ' ' (пробел) вместо None — нужно скипать."""
    data = _make_sheet(
        ['21 мая'],
        [
            ('Алгебра', [' ']),  # whitespace = пусто после strip
            ('Литература', ['5']),
        ],
    )
    out = _parse_master_sheet_for_date(data, date(2026, 5, 21))
    assert out == [('Литература', '5')]


def test_handles_short_row_shorter_than_target_column():
    """Если строка короче чем target_col — НЕ падать, а пропустить."""
    data = [
        ['Оценки все даты', 'Столбец 2', 'Столбец 3', 'Столбец 4'],
        ['Оценки', '20 мая', '21 мая', '22 мая'],
        ['Алгебра', '3'],  # короткая строка, нет col для 21 мая
        ['Литература', '3', '5', None],  # полная — найдём
    ]
    out = _parse_master_sheet_for_date(data, date(2026, 5, 21))
    assert out == [('Литература', '5')]


def test_empty_data_returns_empty():
    assert _parse_master_sheet_for_date([], date(2026, 5, 21)) == []
    assert _parse_master_sheet_for_date([[]], date(2026, 5, 21)) == []
    # Только header + date row, без subject rows — тоже OK
    assert _parse_master_sheet_for_date([['h'], ['Оценки', '21 мая']], date(2026, 5, 21)) == []


# ─── Integration: read_master_sheet_today_grades с моком get_sheet_data ─
def test_read_master_sheet_uses_tashkent_today(monkeypatch):
    """read_master_sheet_today_grades использует tashkent today из
    _tashkent_today_date, мокаем чтобы тест был детерминированным."""
    fake_data = _make_sheet(
        ['20 мая', '21 мая'],
        [('Алгебра', [None, '4/5'])],
    )

    monkeypatch.setattr('src.history_importer.get_sheet_data', lambda *a, **k: fake_data)
    monkeypatch.setattr('src.history_importer._tashkent_today_date', lambda: date(2026, 5, 21))

    out = read_master_sheet_today_grades('ss-test')
    assert out == [('Алгебра', '4/5')]


def test_read_master_sheet_graceful_on_fetch_failure(monkeypatch):
    """Sheets API упало — возвращаем [] и логируем, не валим monitor."""
    def _raise(*a, **k):
        raise RuntimeError("Sheets API down")
    monkeypatch.setattr('src.history_importer.get_sheet_data', _raise)
    monkeypatch.setattr('src.history_importer._tashkent_today_date', lambda: date(2026, 5, 21))

    assert read_master_sheet_today_grades('ss-test') == []
