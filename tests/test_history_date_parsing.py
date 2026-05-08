"""Тесты парсера русских дат — регрессия по багу март/мая.

Раньше `_parse_russian_date('3 мая')` возвращал март (3) вместо мая (5),
потому что rstrip('яьа') обрезал до «м», а fallback prefix.startswith
матчил с «март». Теперь — явные алиасы и однонаправленный match.
"""

import os
import sys
from datetime import datetime

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.history_importer import _parse_russian_date


def test_short_may_format():
    """«3 мая» — короткая форма, главный регрессионный случай."""
    d = _parse_russian_date("3 мая")
    assert d is not None
    assert d.month == 5
    assert d.day == 3


def test_short_may_with_weekday():
    """«4 мая пн» — мая с днём недели в конце."""
    d = _parse_russian_date("4 мая пн")
    assert d is not None
    assert d.month == 5
    assert d.day == 4


def test_full_september():
    """«2 сентября» — длинная форма родительного падежа."""
    d = _parse_russian_date("2 сентября")
    assert d is not None
    assert d.month == 9
    assert d.day == 2


def test_full_march():
    """«14 марта» — март не должен путаться с мая."""
    d = _parse_russian_date("14 марта")
    assert d is not None
    assert d.month == 3
    assert d.day == 14


def test_full_october():
    d = _parse_russian_date("1 октября")
    assert d is not None
    assert d.month == 10
    assert d.day == 1


def test_short_september_alias():
    """«5 сент» — короткая форма должна матчиться через alias."""
    d = _parse_russian_date("5 сент")
    assert d is not None
    assert d.month == 9
    assert d.day == 5


def test_short_october_alias():
    d = _parse_russian_date("12 окт")
    assert d is not None
    assert d.month == 10


def test_february():
    d = _parse_russian_date("28 февраля")
    assert d is not None
    assert d.month == 2


def test_january():
    d = _parse_russian_date("15 января")
    assert d is not None
    assert d.month == 1


def test_with_dot_after_weekday():
    """«4 мая пн.» — с точкой после дня недели."""
    d = _parse_russian_date("4 мая пн.")
    assert d is not None
    assert d.month == 5
    assert d.day == 4


def test_invalid_returns_none():
    """Мусор не должен крэшить."""
    assert _parse_russian_date("") is None
    assert _parse_russian_date("abc") is None
    assert _parse_russian_date("123") is None
    assert _parse_russian_date("32 какой-то") is None


def test_all_months():
    """Все 12 месяцев должны парситься в длинной форме."""
    months_full = [
        ("января", 1), ("февраля", 2), ("марта", 3), ("апреля", 4),
        ("мая", 5), ("июня", 6), ("июля", 7), ("августа", 8),
        ("сентября", 9), ("октября", 10), ("ноября", 11), ("декабря", 12),
    ]
    for month_word, expected in months_full:
        d = _parse_russian_date(f"15 {month_word}")
        assert d is not None, f"Failed to parse '15 {month_word}'"
        assert d.month == expected, (
            f"'15 {month_word}' parsed as month {d.month}, expected {expected}"
        )
