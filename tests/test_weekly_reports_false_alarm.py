"""Регресс: летом (нет свежих оценок) weekly_reports НЕ должен слать ложный
admin-алерт «Check ANTHROPIC_API_KEY».

Инцидент 14.06.2026: 3 пустых воскресенья подряд (31.05/07.06/14.06) →
ai_successes=0 при ai_calls>0 → _track_ai_outcome('weekly_reports', False) ×3 →
порог → ложный алерт. Anthropic при этом работал. Фикс: пропускать учеников без
свежих данных ДО счётчика ai_calls (зеркалит guard analyze_student_grades).
"""
import os
import sys
from datetime import datetime, timezone, timedelta

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm
import src.handlers.analytics as an
import src.schedulers as sched


def _seed_recent(sid, subject, value, days_ago):
    gd = (datetime.now(timezone.utc) + timedelta(hours=5) - timedelta(days=days_ago)).date().isoformat()
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO grade_history "
            "(student_id, subject, grade_value, raw_text, cell_reference, grade_date) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (sid, subject, value, str(value), "X", gd),
        )


def _wire(monkeypatch, sid, gen_return):
    track = []
    gen = {"n": 0}
    monkeypatch.setattr(an, "get_active_spreadsheets",
                        lambda: [{"student_id": sid, "fio": "Kid", "display_name": "Kid"}])
    monkeypatch.setattr(an, "get_parents_for_student", lambda s: [111])
    monkeypatch.setattr(an, "get_user_lang", lambda t: "ru")

    def _fake_gen(*a, **k):
        gen["n"] += 1
        return gen_return
    monkeypatch.setattr(an, "generate_weekly_summary", _fake_gen)
    monkeypatch.setattr(sched, "_track_ai_outcome",
                        lambda job, success: track.append((job, success)))
    return track, gen


def test_no_recent_data_does_not_track_or_call_ai(temp_db, monkeypatch):
    """Лето: 0 свежих оценок → ученик пропущен, AI не зовётся, трекинга нет."""
    sid = dbm.add_student("Kid", "ss")  # без оценок
    track, gen = _wire(monkeypatch, sid, gen_return=None)

    an._send_weekly_reports()

    assert track == []      # ← НЕ слать ложный алерт
    assert gen["n"] == 0    # ← даже не дёргать AI


def test_with_data_but_ai_fail_does_track_failure(temp_db, monkeypatch):
    """Есть свежие данные, но AI вернул None → это реальный fail, трекаем."""
    sid = dbm.add_student("Kid", "ss")
    _seed_recent(sid, "Математика", 4, days_ago=1)
    _seed_recent(sid, "История", 3, days_ago=2)
    track, gen = _wire(monkeypatch, sid, gen_return=None)

    an._send_weekly_reports()

    assert gen["n"] == 1
    assert track == [("weekly_reports", False)]


def test_with_data_and_ai_ok_tracks_success(temp_db, monkeypatch):
    sid = dbm.add_student("Kid", "ss")
    _seed_recent(sid, "Математика", 4, days_ago=1)
    _seed_recent(sid, "История", 3, days_ago=2)
    track, gen = _wire(monkeypatch, sid, gen_return="Отличная неделя!")
    monkeypatch.setattr(an.bot, "send_message", lambda *a, **k: None)

    an._send_weekly_reports()

    assert track == [("weekly_reports", True)]
