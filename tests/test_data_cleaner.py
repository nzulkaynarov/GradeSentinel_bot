"""Тесты для sanitize_grade — критичная функция в polling-цикле."""
import pytest
from src.data_cleaner import sanitize_grade


@pytest.mark.parametrize("raw,expected", [
    ("5-", (5.0, "5-")),
    ("4+", (4.0, "4+")),
    ("4=", (4.0, "4=")),
    ("3", (3.0, "3")),
    (" 2 ", (2.0, "2")),
    ("н", (None, "н")),
    ("болел", (None, "болел")),
    ("болела", (None, "болела")),
    ("5.0", (5.0, "5.0")),
    ("4.5", (4.0, "4.5")),
    ("осв", (None, "осв")),
    ("ув", (None, "ув")),
    ("отлично", (None, None)),
    ("", (None, "")),
])
def test_sanitize_grade(raw, expected):
    assert sanitize_grade(raw) == expected


def test_sanitize_grade_non_string():
    """Числовые значения тоже должны парситься."""
    assert sanitize_grade(5)[0] == 5.0


def test_sanitize_grade_garbage():
    """Случайный мусор не должен распознаваться как оценка."""
    grade, text = sanitize_grade("XYZ123")
    assert grade is None
