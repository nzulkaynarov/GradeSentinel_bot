"""Учебный год определяется по Ташкенту (UTC+5), а не по локальному/UTC времени
сервера (B13).

Баг: `_parse_russian_date` брал год из `datetime.now()` (наивное серверное время).
У сервера в UTC вечером (~19:00-23:59 UTC) по Ташкенту уже «завтра». На границе
учебного года (31 авг/1 сен) это уводило сентябрьскую колонку на год назад:
«1 сентября» парсилось в year-1 → monitor искал колонку для сегодня (уже сен) и
не находил → тихий пропуск оценки без алерта.

Плюс наблюдаемость: лист получен, шапка непустая, но ни одна колонка не
распозналась как дата → WARNING `[DATE_PARSE_FAIL]` вместо тихого пропуска.
"""
import logging
import os
import sys
from datetime import date, datetime
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.history_importer import (
    _parse_russian_date,
    _parse_all_grades_sheet,
    _parse_master_sheet_for_date,
)


# ─── Граница учебного года: сентябрь ────────────────────────────────
def test_september_date_at_year_boundary_uses_tashkent():
    """Сервер: 31 авг 23:00 UTC → Ташкент: 1 сен 04:00 (уже новый уч.год).
    Сентябрьская колонка должна парситься в ТЕКУЩИЙ год, а не year-1."""
    tashkent_now = datetime(2026, 9, 1, 4, 0)  # уже 1 сен по Ташкенту
    d = _parse_russian_date("1 сентября", now=tashkent_now)
    assert d is not None
    assert d.year == 2026, "сентябрь должен принадлежать текущему уч.году, не year-1"
    assert (d.month, d.day) == (9, 1)


def test_september_boundary_regression_would_break_with_server_utc():
    """Демонстрация бага: если бы год считался по серверному UTC (31 авг, month=8),
    сентябрьская дата уехала бы на год назад → mismatch с сегодня → тихий пропуск.

    Этот тест фиксирует, что при «правильном» ташкентском now поведение верное,
    а при «серверном» (Aug 31) — было бы year-1 (то, чего мы избегаем)."""
    server_utc_now = datetime(2026, 8, 31, 23, 0)  # month=8 — старый режим
    d_buggy = _parse_russian_date("1 сентября", now=server_utc_now)
    assert d_buggy.year == 2025  # именно этот off-by-one-year мы устранили

    tashkent_now = datetime(2026, 9, 1, 4, 0)  # month=9 — корректно по Ташкенту
    d_fixed = _parse_russian_date("1 сентября", now=tashkent_now)
    assert d_fixed.year == 2026


# ─── Граница учебного года: декабрь/январь ──────────────────────────
def test_december_january_boundary_semesters():
    """Сервер: 31 дек 23:00 UTC → Ташкент: 1 янв 04:00 следующего года.
    Декабрь = осенний семестр уч.года, начавшегося в прошлом сентябре;
    май = весенний семестр того же уч.года (текущий календарный год)."""
    tashkent_now = datetime(2027, 1, 1, 4, 0)  # уже 1 янв по Ташкенту

    d_dec = _parse_russian_date("28 декабря", now=tashkent_now)
    assert d_dec is not None
    assert d_dec.year == 2026, "декабрь принадлежит уч.году, начавшемуся в сен 2026"

    d_may = _parse_russian_date("3 мая", now=tashkent_now)
    assert d_may is not None
    assert d_may.year == 2027, "весна — календарный год окончания уч.года"


# ─── «3 мая» относится к правильному учебному году (не +1) ──────────
def test_may_in_spring_stays_current_year():
    """Во время весны «3 мая» — текущий календарный год, не следующий."""
    now_spring = datetime(2026, 5, 10)
    d = _parse_russian_date("3 мая", now=now_spring)
    assert d is not None
    assert d.year == 2026
    assert (d.month, d.day) == (5, 3)


def test_may_in_autumn_is_next_calendar_year():
    """Осенью «3 мая» — весна СЛЕДУЮЩЕГО календарного года того же уч.года."""
    now_autumn = datetime(2025, 10, 1)
    d = _parse_russian_date("3 мая", now=now_autumn)
    assert d is not None
    assert d.year == 2026


# ─── Default now = ташкентский, а не серверный локальный ────────────
def test_default_now_uses_tashkent_helper():
    """Без явного now функция должна брать «сейчас» из _tashkent_now, не datetime.now()."""
    fake_now = datetime(2026, 9, 1, 4, 0)
    with patch("src.history_importer._tashkent_now", return_value=fake_now):
        d = _parse_russian_date("1 сентября")
    assert d.year == 2026


# ─── Наблюдаемость: [DATE_PARSE_FAIL] при непарсящейся шапке ─────────
def test_master_sheet_warns_when_no_dates_parse(caplog):
    """Лист получен, шапка непустая, но ни одна колонка не распозналась как дата
    → WARNING (для грепа), а не тихий пропуск."""
    data = [
        ["Оценки все даты", "c2", "c3", "c4"],
        ["Оценки", "непонятно", "мусор", "xyz"],
        ["Математика", "5", "4", "3"],
    ]
    with caplog.at_level(logging.WARNING):
        result = _parse_master_sheet_for_date(data, date(2026, 9, 1), context="student=42")
    assert result == []
    assert any("[DATE_PARSE_FAIL]" in r.message for r in caplog.records)
    assert "student=42" in caplog.text


def test_master_sheet_no_warn_when_dates_parse(caplog):
    """Если хотя бы одна колонка — валидная дата (просто не сегодня), НЕ спамим."""
    data = [
        ["Оценки все даты", "c2", "c3"],
        ["Оценки", "1 сентября", "2 сентября"],
        ["Математика", "5", "4"],
    ]
    with caplog.at_level(logging.WARNING):
        # target — дата, которой нет в шапке
        result = _parse_master_sheet_for_date(data, date(2030, 3, 3), context="student=7")
    assert result == []
    assert "[DATE_PARSE_FAIL]" not in caplog.text


def test_import_parser_warns_when_no_dates_parse(caplog):
    """Тот же контроль в importer-пути (_parse_all_grades_sheet)."""
    data = [
        ["Оценки все даты", "c2", "c3"],
        ["Оценки", "???", "n/a"],
        ["Математика", "5", "4"],
    ]
    with caplog.at_level(logging.WARNING):
        records = _parse_all_grades_sheet(data, context="student=99, sheet=Все оценки!")
    # Записи с date=None всё равно создаются (importer позже отфильтрует по grade_date),
    # но факт «ни одна колонка не распозналась» уже виден в логе.
    assert all(r["date"] is None for r in records)
    assert "[DATE_PARSE_FAIL]" in caplog.text
    assert "student=99" in caplog.text
