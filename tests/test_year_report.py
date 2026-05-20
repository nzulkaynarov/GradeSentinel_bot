"""Tests for compute_year_report — end-of-year dashboard view.

Pure-функция в webapp/app.py: получает list оценок, возвращает агрегаты
за учебный год (avg, monthly trend, best/worst месяц, top/problem subjects,
streaks, growth).
"""
import os
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Stub minimal env before importing webapp.app
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")

from webapp.app import compute_year_report  # noqa: E402


def _g(subject, grade_value, grade_date, raw_text=None):
    return {
        "subject": subject,
        "grade_value": grade_value,
        "grade_date": grade_date,
        "raw_text": raw_text or (str(grade_value) if grade_value else ""),
        "date_added": f"{grade_date} 12:00:00",
    }


# ─── Empty / minimal ─────────────────────────────────────────
def test_empty_returns_zero_report():
    r = compute_year_report([])
    assert r["total_grades"] == 0
    assert r["numeric_count"] == 0
    assert r["year_avg"] is None
    assert r["monthly_trend"] == []
    assert r["best_month"] is None
    assert r["best_streak"] == 0


def test_single_grade_no_growth():
    r = compute_year_report([_g("Алгебра", 4, "2026-03-15")])
    assert r["numeric_count"] == 1
    assert r["year_avg"] == 4.0
    # Один grade не даёт growth (нужно >= 6 для growth calculation)
    assert r["growth"] is None


# ─── Год с реалистичным набором оценок ────────────────────────
def _make_year_grades():
    """Имитация: сентябрь-май, разные предметы, есть рост."""
    return [
        # Сентябрь — старт, средние оценки
        _g("Алгебра", 3, "2025-09-05"),
        _g("Алгебра", 4, "2025-09-15"),
        _g("Литература", 3, "2025-09-10"),
        # Октябрь
        _g("Алгебра", 4, "2025-10-10"),
        _g("Литература", 4, "2025-10-12"),
        # Декабрь — слабый месяц
        _g("Алгебра", 3, "2025-12-05"),
        _g("Физика", 2, "2025-12-10"),
        # Март — улучшение
        _g("Алгебра", 5, "2026-03-10"),
        _g("Алгебра", 5, "2026-03-15"),
        _g("Литература", 5, "2026-03-20"),
        # Май — продолжение успехов + streak
        _g("Алгебра", 5, "2026-05-05"),
        _g("Алгебра", 5, "2026-05-08"),
        _g("Литература", 5, "2026-05-12"),
        _g("Литература", 5, "2026-05-15"),
        # Низкий предмет (для problem_subjects)
        _g("Физика", 3, "2026-04-01"),
        _g("Физика", 3, "2026-04-15"),
        _g("Физика", 3, "2026-05-01"),
    ]


def test_year_avg_realistic():
    r = compute_year_report(_make_year_grades())
    assert r["numeric_count"] == 17
    assert r["year_avg"] is not None
    assert 3.5 < r["year_avg"] < 4.5  # realistic spread


def test_monthly_trend_sorted_chronologically():
    r = compute_year_report(_make_year_grades())
    months = [m["month"] for m in r["monthly_trend"]]
    assert months == sorted(months)
    assert "2025-09" in months
    assert "2026-05" in months


def test_best_worst_month_identified():
    r = compute_year_report(_make_year_grades())
    # Лучший — март или май (все 5)
    assert r["best_month"]["avg"] >= 4.5
    # Худший — декабрь (2 и 3)
    assert r["worst_month"]["avg"] <= 3.0


def test_top_subjects_have_min_3_grades():
    r = compute_year_report(_make_year_grades())
    # Все top должны иметь >= 3 оценок
    for s in r["top_subjects"]:
        assert s["count"] >= 3


def test_problem_subjects_include_physics():
    """Физика — 4 оценки со средним 2.75 — должна попасть в problem."""
    r = compute_year_report(_make_year_grades())
    problem_names = [s["name"] for s in r["problem_subjects"]]
    assert "Физика" in problem_names


def test_growth_positive_for_improving_student():
    r = compute_year_report(_make_year_grades())
    # Учебный год начался с 3-4, закончился пятёрками → рост положительный
    assert r["growth"] is not None
    assert r["growth"] > 0


def test_best_streak_counts_consecutive_fives():
    """Хронологически (по grade_date asc) идут 5,5,5,5,5,5 в марте-мае."""
    r = compute_year_report(_make_year_grades())
    # В марте 3 пятёрки, в мае 4 пятёрки — но между ними физика 3 → разрыв.
    # Sort by grade_date: март 10, март 15, март 20, апрель 1 (физика 3) — streak ломается.
    # Реальный max streak — 3 (март: алгебра-5, алгебра-5, литература-5).
    assert r["best_streak"] >= 3


def test_growth_skipped_when_few_grades():
    """< 6 оценок — growth не считаем (статистически не валидно)."""
    grades = [_g("Алгебра", 4, "2025-09-01"), _g("Литература", 5, "2025-10-01")]
    r = compute_year_report(grades)
    assert r["growth"] is None


def test_non_numeric_grades_ignored_in_avg():
    """«н» (отсутствие) не должно влиять на средний."""
    grades = [
        _g("Алгебра", 5, "2026-04-01"),
        _g("Алгебра", None, "2026-04-02", raw_text="н"),
        _g("Алгебра", 5, "2026-04-03"),
    ]
    r = compute_year_report(grades)
    # numeric_count считает только числовые
    assert r["numeric_count"] == 2
    assert r["year_avg"] == 5.0
    # total_grades — все включая «н»
    assert r["total_grades"] == 3
