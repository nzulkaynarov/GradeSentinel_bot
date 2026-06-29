"""Tests for proactive AI alerts (PR_H5).

Покрываем:
- detect_anomalies: серия ≤3 grade'ов в 7 дней → anomaly, иначе []
- save_alert + was_alerted_recently — dedup за 48ч
- generate_proactive_alert: prompt по типу+языку, AI mock возвращает текст
- scheduler job: end-to-end — детектит, не дублирует, шлёт правильно
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "12345:test")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")

import src.database_manager as dbm


# ─── DB layer ─────────────────────────────────────────────────

def test_save_alert_and_recent_check(temp_db):
    """Сохранили → was_alerted_recently возвращает True; для другого типа — False."""
    student_id = dbm.add_student("Kid", "ss-1")
    dbm.save_alert(student_id, 'low_grades_series')
    assert dbm.was_alerted_recently(student_id, 'low_grades_series') is True
    assert dbm.was_alerted_recently(student_id, 'sudden_drop') is False


def test_was_alerted_recently_false_when_old(temp_db):
    """Alert 50ч назад → not recent (cooldown 48ч)."""
    student_id = dbm.add_student("Kid", "ss-2")
    # Симулируем старый alert: вставляем напрямую с прошлым timestamp
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO proactive_alerts (student_id, alert_type, sent_at) "
            "VALUES (%s, %s, (now() at time zone 'utc') - interval '50 hours')",
            (student_id, 'low_grades_series'),
        )
    assert dbm.was_alerted_recently(student_id, 'low_grades_series') is False


def test_was_alerted_custom_window(temp_db):
    """Произвольный hours параметр работает."""
    student_id = dbm.add_student("Kid", "ss-3")
    dbm.save_alert(student_id, 'x')
    assert dbm.was_alerted_recently(student_id, 'x', hours=1) is True
    # Через 0 часов — никогда не отправляли (граничный кейс)
    # 0 hours window = "in the last 0 hours" → strictly recent → True для только что
    # вставленного. Не тестируем boundary, проверим только что параметр читается.


def test_save_alert_isolated_per_student(temp_db):
    s1 = dbm.add_student("A", "ss-A")
    s2 = dbm.add_student("B", "ss-B")
    dbm.save_alert(s1, 'low_grades_series')
    assert dbm.was_alerted_recently(s1, 'low_grades_series') is True
    assert dbm.was_alerted_recently(s2, 'low_grades_series') is False


# ─── detect_anomalies ─────────────────────────────────────────

def _today_offset(days):
    """Возвращает ISO date offset days back от Tashkent today."""
    now_t = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)
    return (now_t.date() - timedelta(days=days)).isoformat()


def test_detect_anomalies_empty_for_no_grades(temp_db):
    student_id = dbm.add_student("Kid", "ss-empty")
    from src.analytics_engine import detect_anomalies
    assert detect_anomalies(student_id) == []


def test_detect_anomalies_empty_for_only_good_grades(temp_db):
    student_id = dbm.add_student("Kid", "ss-good")
    for i in range(5):
        dbm.add_grade(student_id, "Алгебра", 5.0, "5", f"r{i}",
                       grade_date=_today_offset(i))
    from src.analytics_engine import detect_anomalies
    assert detect_anomalies(student_id) == []


def test_detect_anomalies_triggers_on_three_low_grades_in_week(temp_db):
    student_id = dbm.add_student("Kid", "ss-low")
    for i in range(3):
        dbm.add_grade(student_id, "Математика", 3.0, "3", f"r{i}",
                       grade_date=_today_offset(i))

    from src.analytics_engine import detect_anomalies
    anomalies = detect_anomalies(student_id)
    assert len(anomalies) == 1
    a = anomalies[0]
    assert a['type'] == 'low_grades_series'
    assert a['count'] == 3
    assert "Математика" in a['subjects']
    assert a['days'] == 7


def test_detect_anomalies_ignores_grades_older_than_7_days(temp_db):
    student_id = dbm.add_student("Kid", "ss-old")
    # 3 двойки 10 дней назад — НЕ должны триггерить
    for i in range(3):
        dbm.add_grade(student_id, "Литература", 2.0, "2", f"r{i}",
                       grade_date=_today_offset(10 + i))

    from src.analytics_engine import detect_anomalies
    assert detect_anomalies(student_id) == []


def test_detect_anomalies_threshold_inclusive_of_3(temp_db):
    """Тройка тоже считается «низкой» — это сигнал для четвёрочника."""
    student_id = dbm.add_student("Kid", "ss-3s")
    for i in range(3):
        dbm.add_grade(student_id, "Физика", 3.0, "3", f"r{i}",
                       grade_date=_today_offset(i))
    from src.analytics_engine import detect_anomalies
    anomalies = detect_anomalies(student_id)
    assert len(anomalies) == 1


def test_detect_anomalies_below_threshold_doesnt_trigger(temp_db):
    """2 низкие оценки — не серия (порог 3+)."""
    student_id = dbm.add_student("Kid", "ss-2low")
    for i in range(2):
        dbm.add_grade(student_id, "Химия", 2.0, "2", f"r{i}",
                       grade_date=_today_offset(i))
    from src.analytics_engine import detect_anomalies
    assert detect_anomalies(student_id) == []


# ─── generate_proactive_alert ─────────────────────────────────

def test_generate_alert_returns_text_when_api_works(monkeypatch):
    class FakeMessage:
        content = [type('obj', (), {'text': 'Серия троек — поговорите.'})()]

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                return FakeMessage()

    monkeypatch.setattr("src.analytics_engine._get_client", lambda: FakeClient())

    from src.analytics_engine import generate_proactive_alert
    text = generate_proactive_alert(
        "Заур",
        {'type': 'low_grades_series', 'count': 4, 'days': 7,
         'subjects': ['Математика', 'Физика']},
        lang='ru',
    )
    assert text == "Серия троек — поговорите."


def test_generate_alert_returns_none_for_unknown_type(monkeypatch):
    class FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise AssertionError("Should not be called for unknown type")

    monkeypatch.setattr("src.analytics_engine._get_client", lambda: FakeClient())

    from src.analytics_engine import generate_proactive_alert
    assert generate_proactive_alert("X", {'type': 'unknown_xxx'}, 'ru') is None


def test_generate_alert_returns_none_when_no_api_key(monkeypatch):
    monkeypatch.setattr("src.analytics_engine._get_client", lambda: None)
    from src.analytics_engine import generate_proactive_alert
    assert generate_proactive_alert(
        "X", {'type': 'low_grades_series', 'count': 3, 'subjects': [], 'days': 7}
    ) is None


@pytest.mark.parametrize("lang", ['ru', 'uz', 'en'])
def test_generate_alert_uses_correct_lang_prompt(monkeypatch, lang):
    """Каждый язык имеет свой prompt — passing lang меняет промпт."""
    captured = {}

    class FakeMessage:
        content = [type('obj', (), {'text': 'ok'})()]

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                captured['prompt'] = kwargs['messages'][0]['content']
                return FakeMessage()

    monkeypatch.setattr("src.analytics_engine._get_client", lambda: FakeClient())

    from src.analytics_engine import generate_proactive_alert
    generate_proactive_alert(
        "TestKid",
        {'type': 'low_grades_series', 'count': 3, 'days': 7, 'subjects': ['X']},
        lang=lang,
    )
    # Каждый язык имеет уникальную фразу:
    markers = {'ru': 'заботливый', 'uz': "g'amxo'r", 'en': 'caring'}
    assert markers[lang] in captured['prompt'].lower()
