"""Тесты pure-функций агрегации метрик дашборда из webapp/app.py.

Проверяем компьютацию summary, trend_by_day, by_subject — без БД и без HTTP.
Эти функции — ядро WAU дашборда, они должны работать корректно для всех
edge-cases (пустой период, один предмет, отрицательная дельта, и т.д.).
"""

import os
import sys
from datetime import datetime, timedelta

# Делаем webapp/ импортируемым
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# webapp/app.py выполняет init_db() на module load — нужно чтоб это не падало
# в тестах. conftest.py фиксура temp_db уже делает monkeypatch DB_PATH.
# Но webapp/app.py импортируется как модуль до фикстуры → проще выставить
# DATABASE_PATH в env до import.
os.environ.setdefault("DATABASE_PATH", "/tmp/gs_test_dashboard.db")
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")

from webapp.app import compute_summary, compute_trend_by_day, compute_by_subject  # noqa: E402


def _grade(subject, value, days_ago=0, raw_text=None):
    """Helper: формирует grade dict в формате get_grade_history_for_student_all."""
    date_added = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    return {
        "subject": subject,
        "grade_value": value,
        "raw_text": raw_text or (str(value) if value is not None else ""),
        "date_added": date_added,
    }


# ════════════════════════════════════════════════════════════
#  compute_summary
# ════════════════════════════════════════════════════════════

class TestComputeSummary:
    def test_empty_returns_none_avg(self):
        s = compute_summary([], [], 7)
        assert s["current_avg"] is None
        assert s["trend"] == "stable"
        assert s["status"] == "stable"
        assert s["new_count"] == 0
        assert s["problem_subjects"] == []
        assert s["top_subjects"] == []

    def test_simple_average(self):
        grades = [_grade("Math", 5), _grade("Math", 4), _grade("Phys", 3)]
        s = compute_summary(grades, [], 7)
        assert s["current_avg"] == 4.0
        assert s["new_count"] == 3

    def test_problem_subject_detected_at_threshold(self):
        # avg = 3.0, ниже 3.5 порога → problem
        grades = [_grade("History", 3), _grade("History", 3)]
        s = compute_summary(grades, [], 7)
        assert len(s["problem_subjects"]) == 1
        assert s["problem_subjects"][0]["name"] == "History"
        assert s["problem_subjects"][0]["avg"] == 3.0
        assert s["status"] == "concern"

    def test_top_subject_detected(self):
        grades = [_grade("Math", 5), _grade("Math", 5)]
        s = compute_summary(grades, [], 7)
        assert len(s["top_subjects"]) == 1
        assert s["top_subjects"][0]["name"] == "Math"

    def test_delta_positive_means_up_trend(self):
        current = [_grade("Math", 5), _grade("Math", 5)]
        previous = [_grade("Math", 3), _grade("Math", 3)]
        s = compute_summary(current, previous, 7)
        assert s["delta"] == 2.0
        assert s["trend"] == "up"
        # status: проблем нет, тренд up → improving
        assert s["status"] == "improving"

    def test_delta_negative_means_down_trend(self):
        current = [_grade("Math", 4), _grade("Math", 4)]
        previous = [_grade("Math", 5), _grade("Math", 5)]
        s = compute_summary(current, previous, 7)
        assert s["delta"] == -1.0
        assert s["trend"] == "down"
        assert s["status"] == "declining"

    def test_small_delta_is_stable(self):
        # delta = 0.1, ниже DELTA_SIGNIFICANT (0.2) → stable
        current = [_grade("Math", 4.1)]
        previous = [_grade("Math", 4.0)]
        s = compute_summary(current, previous, 7)
        assert s["trend"] == "stable"

    def test_problems_take_priority_over_improving(self):
        # У нас тренд up (delta=+0.5), но также есть проблемная тема → concern
        current = [_grade("Math", 5), _grade("History", 3)]
        previous = [_grade("Math", 4), _grade("History", 3)]
        s = compute_summary(current, previous, 7)
        assert s["status"] == "concern"

    def test_period_metadata_set(self):
        s = compute_summary([_grade("X", 4)], [], 14)
        assert s["period_days"] == 14
        # period_start = today - 14d, period_end = today
        end = datetime.fromisoformat(s["period_end"]).date()
        start = datetime.fromisoformat(s["period_start"]).date()
        assert (end - start).days == 14

    def test_subject_delta_computed(self):
        current = [_grade("Math", 5), _grade("Math", 5)]
        previous = [_grade("Math", 3), _grade("Math", 3)]
        s = compute_summary(current, previous, 7)
        # Top subject Math должен иметь delta = +2
        top_math = next(t for t in s["top_subjects"] if t["name"] == "Math")
        assert top_math["delta"] == 2.0

    def test_skips_grades_with_none_value(self):
        # Текстовые "оценки" (н/а, зачёт) приходят с grade_value=None — игнорируем
        grades = [_grade("Math", 5), _grade("Math", None, raw_text="зачёт")]
        s = compute_summary(grades, [], 7)
        assert s["current_avg"] == 5.0

    def test_problem_sorted_by_avg_ascending(self):
        # Худшие первыми
        grades = [
            _grade("A", 2), _grade("A", 2),  # avg 2 — самый плохой
            _grade("B", 3), _grade("B", 3),  # avg 3
        ]
        s = compute_summary(grades, [], 7)
        assert s["problem_subjects"][0]["name"] == "A"
        assert s["problem_subjects"][1]["name"] == "B"


# ════════════════════════════════════════════════════════════
#  compute_trend_by_day
# ════════════════════════════════════════════════════════════

class TestComputeTrendByDay:
    def test_groups_by_date(self):
        # Две оценки в один день → одна точка с avg
        grades = [_grade("Math", 5, days_ago=2), _grade("Math", 3, days_ago=2)]
        trend = compute_trend_by_day(grades, 7)
        assert len(trend) == 1
        assert trend[0]["avg"] == 4.0
        assert trend[0]["count"] == 2

    def test_skips_days_without_grades(self):
        # Если нет оценок в день — нет точки. Не вставляем None'ы.
        grades = [_grade("Math", 5, days_ago=0), _grade("Math", 4, days_ago=3)]
        trend = compute_trend_by_day(grades, 7)
        assert len(trend) == 2

    def test_sorted_by_date_ascending(self):
        # Сначала старые, потом новые — для line chart слева направо
        grades = [_grade("Math", 5, days_ago=1), _grade("Math", 4, days_ago=3)]
        trend = compute_trend_by_day(grades, 7)
        assert trend[0]["date"] < trend[1]["date"]

    def test_skips_none_grades(self):
        grades = [_grade("Math", None, days_ago=0)]
        trend = compute_trend_by_day(grades, 7)
        assert trend == []


# ════════════════════════════════════════════════════════════
#  compute_by_subject
# ════════════════════════════════════════════════════════════

class TestComputeBySubject:
    def test_groups_and_averages(self):
        grades = [
            _grade("Math", 5), _grade("Math", 3),
            _grade("Phys", 4),
        ]
        result = compute_by_subject(grades)
        names = [s["name"] for s in result]
        assert set(names) == {"Math", "Phys"}
        math = next(s for s in result if s["name"] == "Math")
        assert math["avg"] == 4.0
        assert math["count"] == 2

    def test_sorted_by_avg_descending(self):
        grades = [_grade("A", 3), _grade("B", 5), _grade("C", 4)]
        result = compute_by_subject(grades)
        assert [s["name"] for s in result] == ["B", "C", "A"]

    def test_empty_returns_empty(self):
        assert compute_by_subject([]) == []
