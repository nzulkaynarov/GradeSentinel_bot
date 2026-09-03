"""Дашборд показывает то, что подписано (аудит 2026-09-03).

Жалоба владельца: «в дашборде частично отображаются старые или кешированные
данные». Разбор нашёл несколько независимых причин; здесь закреплены серверные.

Главная: `recent_grades` отдавались фронту сырыми объектами psycopg, а Flask 3
сериализует `date`/`datetime` через `http_date()` — «Wed, 02 Sep 2026 00:00:00
GMT». Фронт сравнивает и режет эти значения как 'YYYY-MM-DD', поэтому группы дат
сортировались по названию дня недели, «Сегодня» вставало над вчерашними
оценками, а подписи оси графика выглядели как «02 Se».
"""
import json
import os
import re
import sys
from datetime import date, datetime

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from webapp.app import (  # noqa: E402
    MIN_SAMPLE,
    _serialize_grades,
    compute_summary,
)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ─── Формат дат в ответе API ─────────────────────────────────────────
def test_recent_grades_dates_are_iso_strings():
    """Регрессия: psycopg отдаёт объекты, Flask превратил бы их в HTTP-date."""
    rows = [{
        "subject": "Алгебра",
        "grade_value": 4.0,
        "raw_text": "4",
        "cell_reference": "Все оценки!B7",
        "grade_date": date(2026, 9, 2),
        "date_added": datetime(2026, 9, 2, 19, 7, 39),
    }]

    out = _serialize_grades(rows)

    assert _ISO_DATE.match(out[0]["grade_date"]), out[0]["grade_date"]
    assert out[0]["grade_date"] == "2026-09-02"
    assert out[0]["date_added"].startswith("2026-09-02T")
    # Поле для отладки фронту не нужно и раньше зря ехало по сети
    assert "cell_reference" not in out[0]


def test_serialized_grades_survive_json_round_trip():
    """Именно то, что уйдёт клиенту: json.dumps не должен ломать формат."""
    rows = [{"subject": "Химия", "grade_value": None, "raw_text": "н",
             "grade_date": date(2026, 5, 21), "date_added": datetime(2026, 5, 21, 12, 0)}]

    decoded = json.loads(json.dumps(_serialize_grades(rows)))

    assert decoded[0]["grade_date"] == "2026-05-21"
    assert decoded[0]["raw_text"] == "н"
    assert decoded[0]["grade_value"] is None


def test_serialized_dates_sort_chronologically():
    """Строковая сортировка ISO = хронологическая. С HTTP-date «Fri» < «Mon»
    давало порядок по названию дня недели."""
    rows = [
        {"subject": "A", "grade_value": 4, "raw_text": "4",
         "grade_date": date(2026, 9, 2), "date_added": datetime(2026, 9, 2, 10, 0)},
        {"subject": "B", "grade_value": 5, "raw_text": "5",
         "grade_date": date(2026, 8, 31), "date_added": datetime(2026, 8, 31, 10, 0)},
        {"subject": "C", "grade_value": 3, "raw_text": "3",
         "grade_date": date(2026, 9, 10), "date_added": datetime(2026, 9, 10, 10, 0)},
    ]

    out = _serialize_grades(rows)
    order = sorted({g["grade_date"] for g in out}, reverse=True)

    assert order == ["2026-09-10", "2026-09-02", "2026-08-31"]


def test_legacy_string_dates_pass_through():
    """Тестовые и legacy-данные приходят строками — формат сохраняется."""
    rows = [{"subject": "A", "grade_value": 4, "raw_text": "4",
             "grade_date": "2026-09-02", "date_added": "2026-09-02 19:07:39"}]

    assert _serialize_grades(rows)[0]["grade_date"] == "2026-09-02"


# ─── Метрики не тревожат родителя на шуме ────────────────────────────
def _g(subject, value, day="2026-09-02"):
    return {"subject": subject, "grade_value": value, "raw_text": str(value),
            "grade_date": day, "date_added": f"{day} 12:00:00"}


def test_delta_needs_sample_in_both_periods():
    """На проде «Год» сравнивался с ОДНОЙ сентябрьской оценкой и рисовал
    родителю падение на 1.49 балла."""
    current = [_g("Алгебра", 3.0), _g("Химия", 3.5), _g("Физика", 4.0)]
    previous = [_g("Алгебра", 5.0, "2025-09-02")]

    summary = compute_summary(current, previous, 365)

    assert summary["delta"] is None
    assert summary["trend"] == "stable"


def test_delta_computed_when_both_periods_have_data():
    current = [_g("Алгебра", 3.0), _g("Химия", 3.0), _g("Физика", 3.0)]
    previous = [_g("Алгебра", 4.0, "2026-06-02"), _g("Химия", 4.0, "2026-06-03"),
                _g("Физика", 4.0, "2026-06-04")]

    summary = compute_summary(current, previous, 90)

    assert summary["delta"] == -1.0
    assert summary["trend"] == "down"


def test_single_bad_grade_does_not_raise_concern():
    """Одна двойка больше не включает статус «есть на что обратить внимание»,
    пока KPI на том же экране пишет «недостаточно данных»."""
    summary = compute_summary([_g("Английский", 2.0)], [], 7)

    assert summary["problem_subjects"] == []
    assert summary["status"] != "concern"


def test_problem_subject_reported_with_enough_data():
    grades = [_g("Английский", 2.0), _g("Английский", 3.0), _g("Английский", 3.0)]

    summary = compute_summary(grades, [], 30)

    assert [s["name"] for s in summary["problem_subjects"]] == ["Английский"]
    assert summary["status"] == "concern"


def test_top_subject_requires_sample_too():
    """Симметрично: одна пятёрка не делает предмет «лучшим»."""
    assert compute_summary([_g("Музыка", 5.0)], [], 7)["top_subjects"] == []
    enough = [_g("Музыка", 5.0), _g("Музыка", 5.0), _g("Музыка", 4.5)]
    assert [s["name"] for s in compute_summary(enough, [], 30)["top_subjects"]] == ["Музыка"]


def test_min_sample_is_shared_by_kpi_and_summary():
    """Один порог на весь дашборд — иначе подписи противоречат друг другу."""
    from webapp.app import KPI_MIN_SAMPLE

    assert KPI_MIN_SAMPLE == MIN_SAMPLE == 3


# ─── Мёртвое поле убрано из ответа ───────────────────────────────────
def test_trend_by_day_no_longer_shipped():
    """Поле отдавалось «на пару дней» с 21 мая и не читалось фронтом."""
    import webapp.app as wa

    source = open(os.path.join(ROOT, "webapp", "app.py"), encoding="utf-8").read()
    assert '"trend_by_day"' not in source
    assert hasattr(wa, "compute_trend_by_day")   # функция и её тесты остаются
