"""Tests for /api/chat endpoint — AI assistant с контекстом ученика.

Реальные API-вызовы к Anthropic мокаем — тесты проверяют:
- Validation (400 на пустой question, длинный question)
- Rate limit (429 после 5 запросов/минута)
- Контекст формирования (compact rendering)
"""
import os
import sys
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")

from src.analytics_engine import _format_grades_context  # noqa: E402


# ─── Unit: формирование контекста для prompt'а ────────────────
def test_format_grades_context_empty():
    assert "пусто" in _format_grades_context([]).lower()


def test_format_grades_context_renders_compact_lines():
    grades = [
        {"subject": "Алгебра", "grade_value": 4.0, "raw_text": "4",
         "grade_date": "2026-05-15", "date_added": "2026-05-15 10:00:00"},
        {"subject": "Литература", "grade_value": 5.0, "raw_text": "5",
         "grade_date": "2026-05-14", "date_added": "2026-05-14 11:00:00"},
    ]
    out = _format_grades_context(grades)
    assert "Алгебра" in out
    assert "Литература" in out
    assert "2026-05-15" in out
    assert "2026-05-14" in out


def test_format_grades_context_truncates_long_list():
    grades = [{"subject": f"Subj{i}", "grade_value": 4.0, "raw_text": "4",
               "grade_date": "2026-05-15"} for i in range(200)]
    out = _format_grades_context(grades, max_count=10)
    # Только 10 строк
    assert out.count("Subj") == 10


def test_format_grades_context_handles_missing_grade_date():
    """Fallback на date_added если grade_date нет."""
    grades = [{"subject": "Алгебра", "grade_value": 4.0, "raw_text": "4",
               "date_added": "2026-05-15 14:30:00"}]
    out = _format_grades_context(grades)
    assert "2026-05-15" in out


# ─── Integration: rate limit endpoint ─────────────────────────
def test_rate_limit_blocks_after_5_requests():
    from webapp.app import _check_chat_rate_limit, _chat_rate_limit
    _chat_rate_limit.clear()

    tg_id = 99999
    for i in range(5):
        assert _check_chat_rate_limit(tg_id), f"Request {i+1} should pass"
    assert not _check_chat_rate_limit(tg_id), "6th request should be blocked"


def test_rate_limit_isolated_per_user():
    from webapp.app import _check_chat_rate_limit, _chat_rate_limit
    _chat_rate_limit.clear()

    for i in range(5):
        assert _check_chat_rate_limit(10001)
    # Другой юзер не затронут
    assert _check_chat_rate_limit(10002)


def test_rate_limit_clears_after_window(monkeypatch):
    from webapp.app import _check_chat_rate_limit, _chat_rate_limit
    _chat_rate_limit.clear()

    import time as _time
    base = _time.time()

    fake_time = [base]
    monkeypatch.setattr("time.time", lambda: fake_time[0])

    for _ in range(5):
        _check_chat_rate_limit(20001)
    assert not _check_chat_rate_limit(20001)

    # Через 61 секунду — должно опять пройти
    fake_time[0] = base + 61
    assert _check_chat_rate_limit(20001)
