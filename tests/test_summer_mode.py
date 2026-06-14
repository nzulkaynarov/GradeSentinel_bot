"""«Летний режим» этап 1: слабейший предмет + календарь каникул.

Lock-парити ('summer_mode' в _job_locks) проверяется
test_prod_stability_regressions; sync новых i18n-ключей — test_locales_sync.
"""
import os
import sys
import json
from datetime import datetime, timezone, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm
import src.schedulers as sched


_TASHKENT_TODAY = (datetime.now(timezone.utc) + timedelta(hours=5)).date()


def _today_iso():
    return _TASHKENT_TODAY.isoformat()


_seed_counter = {"n": 0}


def _seed(sid, subject, value, _date_iso=None):
    """Сидит оценку с УНИКАЛЬНОЙ датой (UNIQUE на student+subject+date+raw_text)."""
    _seed_counter["n"] += 1
    grade_date = (_TASHKENT_TODAY - timedelta(days=_seed_counter["n"])).isoformat()
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO grade_history "
            "(student_id, subject, grade_value, raw_text, cell_reference, grade_date) "
            "VALUES (?,?,?,?,?,?)",
            (sid, subject, value, str(value), "X", grade_date),
        )


# ─────────────────────── weakest subject ───────────────────────

def test_weakest_subject_picks_lowest_avg(temp_db):
    sid = dbm.add_student("Kid", "ss")
    today = _today_iso()
    for v in (3, 3, 3):
        _seed(sid, "Математика", v, today)
    for v in (5, 5, 5):
        _seed(sid, "История", v, today)
    w = dbm.get_weakest_subject(sid, days=30)
    assert w is not None
    assert w["subject"] == "Математика"
    assert w["count"] == 3
    assert w["avg"] == 3.0


def test_weakest_subject_respects_min_count(temp_db):
    """Предмет с 1 двойкой (count<3) — шум, игнор; берём предмет с ≥3 оценок."""
    sid = dbm.add_student("Kid", "ss")
    today = _today_iso()
    _seed(sid, "ИЗО", 2, today)            # одна оценка → ниже порога
    for v in (4, 4, 4):
        _seed(sid, "Физкультура", v, today)
    w = dbm.get_weakest_subject(sid, days=30, min_count=3)
    assert w is not None
    assert w["subject"] == "Физкультура"


def test_weakest_subject_none_when_insufficient(temp_db):
    sid = dbm.add_student("Kid", "ss")
    _seed(sid, "ИЗО", 2, _today_iso())
    assert dbm.get_weakest_subject(sid, min_count=3) is None


# ─────────────────────── holiday calendar ───────────────────────

def test_is_holiday_now_true_when_today_in_range(temp_db):
    today = _today_iso()
    dbm.set_setting('summer_mode_holidays', json.dumps([[today, today]]))
    assert sched._is_holiday_now() is True


def test_is_holiday_now_false_outside_range(temp_db):
    dbm.set_setting('summer_mode_holidays', json.dumps([["2000-01-01", "2000-01-10"]]))
    assert sched._is_holiday_now() is False


def test_holiday_bad_json_falls_back_to_default(temp_db):
    dbm.set_setting('summer_mode_holidays', "{not valid json")
    assert sched._get_holiday_periods() == sched._DEFAULT_HOLIDAY_PERIODS


# ─────────────────── stage 2: rotation + opt-out ───────────────────

def test_weak_subjects_ordered_worst_first(temp_db):
    sid = dbm.add_student("Kid", "ss")
    for v in (2, 2, 2):
        _seed(sid, "Слабый", v)
    for v in (3, 3, 3):
        _seed(sid, "Средний", v)
    for v in (5, 5, 5):
        _seed(sid, "Сильный", v)
    subs = dbm.get_weak_subjects(sid, days=60)
    assert [s["subject"] for s in subs] == ["Слабый", "Средний", "Сильный"]


def test_rotated_subject_cycles_by_week(temp_db):
    sid = dbm.add_student("Kid", "ss")
    for v in (2, 2, 2):
        _seed(sid, "A", v)
    for v in (3, 3, 3):
        _seed(sid, "B", v)
    # 2 предмета → чередование по чётности недели
    w0 = dbm.get_rotated_weak_subject(sid, 0, days=60)
    w1 = dbm.get_rotated_weak_subject(sid, 1, days=60)
    w2 = dbm.get_rotated_weak_subject(sid, 2, days=60)
    assert w0["subject"] == "A"      # worst, index 0
    assert w1["subject"] == "B"      # index 1
    assert w2["subject"] == "A"      # wrap (2 % 2 == 0)


def test_rotated_subject_none_without_data(temp_db):
    sid = dbm.add_student("Kid", "ss")
    assert dbm.get_rotated_weak_subject(sid, 0) is None


def test_summer_optout_roundtrip(temp_db):
    assert dbm.is_summer_opted_out(777) is False
    dbm.set_summer_opted_out(777, True)
    assert dbm.is_summer_opted_out(777) is True
    dbm.set_summer_opted_out(777, False)
    assert dbm.is_summer_opted_out(777) is False


def test_summer_mode_skips_when_not_holiday(temp_db, monkeypatch):
    """Если не каникулы — джоба выходит сразу, без обращения к students."""
    dbm.set_setting('summer_mode_holidays', json.dumps([["2000-01-01", "2000-01-02"]]))
    called = {"n": 0}

    def _boom():
        called["n"] += 1
        raise AssertionError("не должно вызываться вне каникул")

    monkeypatch.setattr(
        "src.db.families.get_active_spreadsheets_with_subscription", _boom)
    sched._check_summer_mode()   # не должно бросить
    assert called["n"] == 0
