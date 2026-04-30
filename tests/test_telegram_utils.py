"""Тесты обёртки send_with_retry — корректность обработки 429/RetryAfter."""
import pytest
from unittest.mock import MagicMock
from src.telegram_utils import send_with_retry


class FakeApiException(Exception):
    def __init__(self, code, retry_after=None):
        self.error_code = code
        if retry_after is not None:
            self.result_json = {'parameters': {'retry_after': retry_after}}


def test_success_first_try():
    func = MagicMock()
    ok, exc = send_with_retry(func, 1, 2, kw=3)
    assert ok is True
    assert exc is None
    func.assert_called_once_with(1, 2, kw=3)


def test_terminal_403_no_retry():
    func = MagicMock(side_effect=FakeApiException(403))
    ok, exc = send_with_retry(func)
    assert ok is False
    assert func.call_count == 1


def test_429_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr('time.sleep', lambda *_: None)
    call_count = {'n': 0}

    def func():
        call_count['n'] += 1
        if call_count['n'] == 1:
            raise FakeApiException(429, retry_after=1)
        return  # успех на втором

    ok, exc = send_with_retry(func, max_attempts=3)
    assert ok is True
    assert call_count['n'] == 2


def test_429_skips_when_retry_after_too_long(monkeypatch):
    monkeypatch.setattr('time.sleep', lambda *_: None)
    func = MagicMock(side_effect=FakeApiException(429, retry_after=999))
    ok, exc = send_with_retry(func, max_attempts=3, max_retry_after=30)
    assert ok is False
    func.assert_called_once()


def test_500_transient_retries(monkeypatch):
    monkeypatch.setattr('time.sleep', lambda *_: None)
    func = MagicMock(side_effect=[FakeApiException(500), FakeApiException(500), None])
    ok, exc = send_with_retry(func, max_attempts=3, base_delay=0.01)
    assert ok is True
    assert func.call_count == 3
