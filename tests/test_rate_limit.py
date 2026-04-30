"""Тесты per-user rate limiter'а.

Импортируем напрямую src.rate_limiter — он не имеет зависимостей от bot_instance,
поэтому работает в CI без BOT_TOKEN.
"""
import time
from src.rate_limiter import is_rate_limited, reset
from src.config import RATE_LIMIT_MAX, RATE_LIMIT_WINDOW


def setup_function(_func):
    reset()


def test_blocks_after_threshold():
    user_id = 999_888_777
    for _ in range(RATE_LIMIT_MAX):
        assert is_rate_limited(user_id) is False
    assert is_rate_limited(user_id) is True


def test_window_expiry_unblocks(monkeypatch):
    """Через RATE_LIMIT_WINDOW секунд старые тики выпадают и пользователь снова может."""
    user_id = 111_222_333
    fake_time = [1000.0]
    monkeypatch.setattr('src.rate_limiter.time.time', lambda: fake_time[0])

    for _ in range(RATE_LIMIT_MAX):
        assert is_rate_limited(user_id) is False
    assert is_rate_limited(user_id) is True

    # Прыгаем на RATE_LIMIT_WINDOW + 1 секунду вперёд
    fake_time[0] += RATE_LIMIT_WINDOW + 1
    assert is_rate_limited(user_id) is False


def test_independent_users():
    a, b = 11, 22
    for _ in range(RATE_LIMIT_MAX):
        assert is_rate_limited(a) is False
    assert is_rate_limited(a) is True
    # Другой пользователь не затронут
    assert is_rate_limited(b) is False
