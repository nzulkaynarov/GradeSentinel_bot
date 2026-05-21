"""NAV-010: tracking AI scheduler failures + admin alert.

Покрываем:
- success после fail'ов резетит счётчик
- N подряд fails (где N=_AI_FAIL_THRESHOLD) → notify admin
- Cooldown 24h: повторный fail в течение 24h НЕ триггерит notify
- Нет ADMIN_ID env → silent (не падает)
- Нет _bot → silent
"""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "12345:test")
os.environ.setdefault("ADMIN_GROUP_ID", "0")
os.environ["ADMIN_ID"] = "555000"

import src.database_manager as dbm
import src.schedulers as sched


@pytest.fixture
def fresh_settings(temp_db, monkeypatch):
    """Мокаем _bot для перехвата send_message. ADMIN_ID overridden внутри теста
    (conftest temp_db ставит '0' — надо вернуть валидный для notification)."""
    monkeypatch.setenv("ADMIN_ID", "555000")
    sent = []

    class FakeBot:
        def send_message(self, *args, **kwargs):
            sent.append((args, kwargs))

    monkeypatch.setattr(sched, "_bot", FakeBot())
    return sent


def _settings_key(job):
    return f"ai_consec_fails_{job}"


def test_success_resets_counter(fresh_settings):
    dbm.set_setting(_settings_key('test_job'), "5")
    sched._track_ai_outcome('test_job', success=True)
    assert dbm.get_setting(_settings_key('test_job')) == "0"


def test_fail_increments_counter(fresh_settings):
    sched._track_ai_outcome('test_job', success=False)
    assert dbm.get_setting(_settings_key('test_job')) == "1"
    sched._track_ai_outcome('test_job', success=False)
    assert dbm.get_setting(_settings_key('test_job')) == "2"


def test_below_threshold_does_not_notify(fresh_settings):
    """1 или 2 подряд fails — НЕ беспокоим admin'а."""
    sent = fresh_settings
    sched._track_ai_outcome('test_job', success=False)
    sched._track_ai_outcome('test_job', success=False)
    assert len(sent) == 0, f"Should not notify after 2 fails, sent={sent}"


def test_threshold_reached_notifies_admin(fresh_settings):
    """3 подряд fails → одно сообщение admin'у."""
    sent = fresh_settings
    sched._track_ai_outcome('test_job', success=False)
    sched._track_ai_outcome('test_job', success=False)
    sched._track_ai_outcome('test_job', success=False)
    assert len(sent) == 1
    # Адресат — ADMIN_ID
    assert sent[0][0][0] == 555000
    # Текст содержит job name
    assert 'test_job' in sent[0][0][1]


def test_cooldown_prevents_repeat_notify(fresh_settings):
    """После notify, дальнейшие fails в течение 24h НЕ триггерят повторно."""
    sent = fresh_settings
    for _ in range(5):
        sched._track_ai_outcome('test_job', success=False)
    # Threshold=3, fail #3 notified, fails #4,5 — cooldown
    assert len(sent) == 1


def test_success_after_fails_logs_recovery(fresh_settings, caplog):
    import logging
    caplog.set_level(logging.INFO)
    # Симулируем 4 fails (1 notify уже отправлен на 3-м)
    for _ in range(4):
        sched._track_ai_outcome('test_job', success=False)
    # Recovery
    sched._track_ai_outcome('test_job', success=True)
    # Counter reset to 0
    assert dbm.get_setting(_settings_key('test_job')) == "0"
    # Recovery log
    assert any('recovered' in r.getMessage().lower() for r in caplog.records)


def test_isolated_per_job(fresh_settings):
    """Разные job names — независимые счётчики."""
    sent = fresh_settings
    sched._track_ai_outcome('job_a', success=False)
    sched._track_ai_outcome('job_a', success=False)
    sched._track_ai_outcome('job_b', success=False)
    sched._track_ai_outcome('job_b', success=False)
    sched._track_ai_outcome('job_b', success=False)  # 3rd → notify

    assert len(sent) == 1
    assert 'job_b' in sent[0][0][1]
    assert 'job_a' not in sent[0][0][1]


def test_no_bot_silent(temp_db, monkeypatch):
    """Если _bot не задан (тестовая среда без бота) — _track_ai_outcome не падает."""
    monkeypatch.setattr(sched, "_bot", None)
    for _ in range(5):
        sched._track_ai_outcome('test_job', success=False)
    # Просто не упало


def test_no_admin_id_silent(fresh_settings, monkeypatch):
    """Без ADMIN_ID env — не шлём (но и не падаем)."""
    monkeypatch.delenv("ADMIN_ID", raising=False)
    sent = fresh_settings
    for _ in range(5):
        sched._track_ai_outcome('test_job', success=False)
    assert len(sent) == 0
