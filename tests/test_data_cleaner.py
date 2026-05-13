"""Тесты для sanitize_grade / sanitize_cell — критичные функции в polling-цикле."""
import pytest
from src.data_cleaner import sanitize_grade, sanitize_cell


# ─── sanitize_grade: backward-compat одиночные оценки ───────────────
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
    # Multi-grade: sanitize_grade возвращает (None, None) — caller обязан
    # использовать sanitize_cell. Эту семантику ловит тест отдельно.
    ("2/5", (None, None)),
    ("2/2", (None, None)),
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


# ─── sanitize_cell: multi-grade поддержка ───────────────────────────
@pytest.mark.parametrize("raw,expected", [
    # Одиночные — список из 1 элемента
    ("5", [(5.0, "5")]),
    ("3", [(3.0, "3")]),
    ("5-", [(5.0, "5-")]),
    ("4+", [(4.0, "4+")]),
    ("н", [(None, "н")]),
    # Multi-grade — это то ради чего вся эта работа
    ("2/5", [(2.0, "2"), (5.0, "5")]),
    ("5/5", [(5.0, "5"), (5.0, "5")]),
    ("2/2", [(2.0, "2"), (2.0, "2")]),
    ("4/3/2", [(4.0, "4"), (3.0, "3"), (2.0, "2")]),
    # Спец-слова со слэшем НЕ ломаются на части
    ("н/а", [(None, "н/а")]),
    ("б/ф", [(None, "б/ф")]),
    # Пустые / мусор → пустой список
    ("", []),
    ("  ", []),
    ("XYZ", []),
    ("13 мая ср", []),     # дата — не оценка
    ("отлично", []),
    # Один сегмент мусор → весь cell мусор (безопаснее)
    ("5/X", []),
    ("/5", []),
    ("5/", []),
    ("2//5", []),
    # Пробелы вокруг сегментов
    (" 2 / 5 ", [(2.0, "2"), (5.0, "5")]),
])
def test_sanitize_cell(raw, expected):
    assert sanitize_cell(raw) == expected


def test_sanitize_cell_non_string():
    """Числовой ввод обрабатывается."""
    assert sanitize_cell(4) == [(4.0, "4")]


def test_sanitize_cell_preserves_modifier():
    """Модификаторы (- + = .N) сохраняются в raw_text."""
    result = sanitize_cell("4+/5")
    assert result == [(4.0, "4+"), (5.0, "5")]
