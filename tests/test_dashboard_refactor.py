"""Tests for dashboard radical refactor (NAV → analytical):
- compute_trend_by_subject (multi-line chart, заменил trend_by_day)
- compute_quarters_with_forecast (4 четверти + годовая или прогноз)
- compute_dashboard_kpis (4 KPI cards)
- by_subject enriched (last_grade, last_date, trend)
"""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")

from webapp.app import (
    compute_by_subject, compute_trend_by_subject,
    compute_quarters_with_forecast, compute_dashboard_kpis,
    compute_summary,
)


# ─── compute_by_subject (enriched) ─────────────────────────

def test_by_subject_includes_last_grade_and_trend():
    grades = [
        {"subject": "Алгебра", "grade_value": 5.0, "raw_text": "5", "grade_date": "2026-05-15"},
        {"subject": "Алгебра", "grade_value": 5.0, "raw_text": "5", "grade_date": "2026-05-16"},
        {"subject": "Алгебра", "grade_value": 3.0, "raw_text": "3", "grade_date": "2026-05-20"},
        {"subject": "Алгебра", "grade_value": 3.0, "raw_text": "3", "grade_date": "2026-05-21"},
    ]
    result = compute_by_subject(grades)
    assert len(result) == 1
    s = result[0]
    assert s['name'] == 'Алгебра'
    assert s['count'] == 4
    assert s['last_grade'] == '3'
    assert s['last_date'] == '2026-05-20' or s['last_date'] == '2026-05-21'
    # Trend: первая половина 5.0, вторая 3.0 → down
    assert s['trend'] == 'down'


def test_by_subject_trend_flat_for_stable():
    grades = [
        {"subject": "Лит", "grade_value": 4.0, "raw_text": "4", "grade_date": f"2026-05-{15+i:02d}"}
        for i in range(8)
    ]
    result = compute_by_subject(grades)
    assert result[0]['trend'] == 'flat'


def test_by_subject_trend_skips_when_too_few_data():
    grades = [
        {"subject": "Алгебра", "grade_value": 5.0, "raw_text": "5", "grade_date": "2026-05-15"},
        {"subject": "Алгебра", "grade_value": 3.0, "raw_text": "3", "grade_date": "2026-05-20"},
    ]
    result = compute_by_subject(grades)
    # 2 оценки — недостаточно для trend, остаётся flat
    assert result[0]['trend'] == 'flat'


# ─── compute_trend_by_subject (multi-line) ──────────────────

def test_trend_by_subject_groups_by_week():
    grades = [
        {"subject": "Алгебра", "grade_value": 5.0, "raw_text": "5", "grade_date": "2026-05-04"},  # пн
        {"subject": "Алгебра", "grade_value": 4.0, "raw_text": "4", "grade_date": "2026-05-05"},  # вт
        {"subject": "Алгебра", "grade_value": 3.0, "raw_text": "3", "grade_date": "2026-05-11"},  # сл. неделя
    ]
    result = compute_trend_by_subject(grades, period_days=14)
    assert len(result) == 1
    line = result[0]
    assert line['subject'] == 'Алгебра'
    # 2 недели → 2 точки
    assert len(line['points']) == 2
    # Первая неделя: средний 4.5 (5+4)/2
    assert line['points'][0]['avg'] == 4.5
    assert line['points'][1]['avg'] == 3.0


def test_trend_by_subject_caps_at_max_subjects():
    grades = []
    for i in range(15):
        for j in range(3):  # 3 оценки по каждому предмету
            grades.append({
                "subject": f"Предмет{i}", "grade_value": 4.0, "raw_text": "4",
                "grade_date": "2026-05-15",
            })
    result = compute_trend_by_subject(grades, period_days=30, max_subjects=8)
    assert len(result) == 8  # cap


def test_trend_by_subject_empty():
    assert compute_trend_by_subject([], period_days=7) == []


# ─── compute_quarters_with_forecast ─────────────────────────

def test_quarters_with_explicit_year_grade():
    """Если в БД есть quarter=5 (год) — используем как есть."""
    quarter_grades = [
        {"subject": "Физика", "quarter": 1, "raw_text": "5", "grade_value": 5.0},
        {"subject": "Физика", "quarter": 2, "raw_text": "5", "grade_value": 5.0},
        {"subject": "Физика", "quarter": 3, "raw_text": "4", "grade_value": 4.0},
        {"subject": "Физика", "quarter": 4, "raw_text": "5", "grade_value": 5.0},
        {"subject": "Физика", "quarter": 5, "raw_text": "5", "grade_value": 5.0},
    ]
    result = compute_quarters_with_forecast(quarter_grades)
    assert len(result) == 1
    row = result[0]
    assert row['year'] == '5'
    assert row['year_is_forecast'] is False
    assert row['year_value'] == 5.0


def test_quarters_forecast_when_no_year_grade():
    """Если нет q=5 — прогноз из avg 1-4ч."""
    quarter_grades = [
        {"subject": "Математика", "quarter": 1, "raw_text": "4", "grade_value": 4.0},
        {"subject": "Математика", "quarter": 2, "raw_text": "4", "grade_value": 4.0},
        {"subject": "Математика", "quarter": 3, "raw_text": "3", "grade_value": 3.0},
    ]
    result = compute_quarters_with_forecast(quarter_grades)
    assert len(result) == 1
    row = result[0]
    assert row['year_is_forecast'] is True
    assert row['year'].startswith('~')
    # Forecast: (4+4+3)/3 = 3.67
    assert abs(row['year_value'] - 3.67) < 0.01


def test_quarters_trend_detection():
    quarter_grades = [
        {"subject": "А", "quarter": 1, "raw_text": "5", "grade_value": 5.0},
        {"subject": "А", "quarter": 2, "raw_text": "4", "grade_value": 4.0},
        {"subject": "А", "quarter": 3, "raw_text": "3", "grade_value": 3.0},
    ]
    result = compute_quarters_with_forecast(quarter_grades)
    assert result[0]['trend'] == 'down'


def test_quarters_sorted_problems_first():
    """Предметы с year_value < 4 — сверху."""
    quarter_grades = [
        {"subject": "Литература", "quarter": 1, "raw_text": "5", "grade_value": 5.0},
        {"subject": "Литература", "quarter": 5, "raw_text": "5", "grade_value": 5.0},
        {"subject": "Математика", "quarter": 1, "raw_text": "3", "grade_value": 3.0},
        {"subject": "Математика", "quarter": 5, "raw_text": "3", "grade_value": 3.0},
    ]
    result = compute_quarters_with_forecast(quarter_grades)
    # Математика (year=3) сверху, Литература (year=5) снизу
    assert result[0]['subject'] == 'Математика'
    assert result[1]['subject'] == 'Литература'


def test_quarters_empty():
    assert compute_quarters_with_forecast([]) == []


# ─── compute_dashboard_kpis ─────────────────────────────────

def test_kpis_with_full_data():
    summary = {'current_avg': 4.3, 'delta': 0.2}
    by_subject = [
        {'name': 'Литература', 'avg': 5.0, 'count': 5},
        {'name': 'Алгебра', 'avg': 4.0, 'count': 3},
        {'name': 'Физика', 'avg': 3.0, 'count': 4},
    ]
    kpis = compute_dashboard_kpis(summary, by_subject, 12)
    assert kpis['current_avg'] == 4.3
    assert kpis['delta'] == 0.2
    assert kpis['total_grades'] == 12
    assert kpis['top_subject']['name'] == 'Литература'
    assert kpis['worst_subject']['name'] == 'Физика'


def test_kpis_empty_subjects():
    """Нет предметов — top/worst = None."""
    kpis = compute_dashboard_kpis({'current_avg': None, 'delta': None}, [], 0)
    assert kpis['top_subject'] is None
    assert kpis['worst_subject'] is None
