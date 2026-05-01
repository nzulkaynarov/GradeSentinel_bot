"""Тесты error_reporter — что без SENTRY_DSN всё работает (no-op),
и что report() не глотает ошибки сам."""
import logging
import pytest


@pytest.fixture(autouse=True)
def reset_sentry_state(monkeypatch):
    """Каждый тест начинает с чистого состояния — Sentry не инициализирован."""
    import src.error_reporter as er
    monkeypatch.setattr(er, '_sentry_inited', False)
    monkeypatch.setattr(er, '_sentry_module', None)


def test_report_logs_exception_without_sentry(caplog, monkeypatch):
    """Без SENTRY_DSN report должен просто залогировать ошибку с stack trace."""
    monkeypatch.setattr('src.error_reporter.SENTRY_DSN', '')
    from src.error_reporter import report

    with caplog.at_level(logging.ERROR):
        try:
            raise ValueError("test boom")
        except ValueError as e:
            report("test.scope", e, user_id=42)

    assert any("test.scope" in r.message for r in caplog.records)
    assert any("test boom" in r.message for r in caplog.records)
    assert any("user_id=42" in r.message for r in caplog.records)


def test_report_does_not_raise_without_sentry(monkeypatch):
    """report() сам не должен бросать — это catch-all."""
    monkeypatch.setattr('src.error_reporter.SENTRY_DSN', '')
    from src.error_reporter import report

    # Не должно быть исключений
    try:
        raise RuntimeError("irrelevant")
    except RuntimeError as e:
        report("any", e)


def test_warn_logs_message(caplog, monkeypatch):
    monkeypatch.setattr('src.error_reporter.SENTRY_DSN', '')
    from src.error_reporter import warn

    with caplog.at_level(logging.WARNING):
        warn("my.scope", "something weird", count=7)

    assert any("my.scope" in r.message and "something weird" in r.message
               for r in caplog.records)


def test_init_sentry_skipped_when_dsn_empty(monkeypatch):
    monkeypatch.setattr('src.error_reporter.SENTRY_DSN', '')
    from src.error_reporter import _try_init_sentry
    assert _try_init_sentry() is False


def test_init_sentry_handles_missing_package(monkeypatch):
    """Если SENTRY_DSN задан, но пакета нет — не падать."""
    monkeypatch.setattr('src.error_reporter.SENTRY_DSN', 'https://fake@example.com/1')

    # Эмулируем отсутствие sentry_sdk
    import sys
    monkeypatch.setitem(sys.modules, 'sentry_sdk', None)  # ImportError при import

    from src.error_reporter import _try_init_sentry
    # Не падает, возвращает False
    result = _try_init_sentry()
    assert result is False
