"""Тесты для compute_dashboard_insight — кэш 6h и graceful degradation."""

import os
import sys
import json
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _summary(avg=4.2, delta=0.3, problems=None, tops=None):
    """Helper для конструирования summary с None-safe arithmetic."""
    if avg is None:
        return {
            "current_avg": None, "previous_avg": None, "delta": None,
            "trend": "stable", "status": "stable",
            "problem_subjects": [], "top_subjects": [],
        }
    delta = delta if delta is not None else 0
    if delta > 0.2:
        trend = "up"
    elif delta < -0.2:
        trend = "down"
    else:
        trend = "stable"
    return {
        "current_avg": avg,
        "previous_avg": avg - delta,
        "delta": delta,
        "trend": trend,
        "status": "stable",
        "problem_subjects": problems or [],
        "top_subjects": tops or [],
    }


@pytest.fixture
def fresh_db(temp_db):
    """temp_db фикстура из conftest подменяет DB_PATH."""
    yield temp_db


def test_returns_none_when_no_grades(fresh_db):
    """current_avg=None → нет смысла дёргать AI, возвращаем None."""
    from src.analytics_engine import compute_dashboard_insight
    summary = _summary(avg=None)
    assert compute_dashboard_insight(1, summary, lang="ru") is None


def test_returns_none_without_api_key(fresh_db, monkeypatch):
    """Если ANTHROPIC_API_KEY не задан — graceful None, не падаем."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Сбросить кэшированный client
    from src import analytics_engine
    analytics_engine._client = None

    from src.analytics_engine import compute_dashboard_insight
    assert compute_dashboard_insight(1, _summary(), lang="ru") is None


def test_cache_hit_skips_api_call(fresh_db, monkeypatch):
    """Если в settings лежит свежий insight — НЕ дёргаем Claude."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from src import analytics_engine
    analytics_engine._client = None  # сброс

    from src.database_manager import set_setting
    cache_key = "insight:1:7:ru"
    cached_payload = json.dumps({
        "text": "Тест-кэш",
        "generated_at": datetime.now().isoformat(),
    })
    set_setting(cache_key, cached_payload)

    # Mock anthropic — должен НЕ вызываться
    with patch("src.analytics_engine._get_client") as mock_client:
        mock_client.return_value = MagicMock()
        from src.analytics_engine import compute_dashboard_insight
        result = compute_dashboard_insight(1, _summary(), lang="ru", days=7)
        assert result == "Тест-кэш"
        # _get_client не должен был вызываться так как cache hit
        mock_client.assert_not_called()


def test_cache_expired_triggers_refresh(fresh_db, monkeypatch):
    """Если кэш протух (>6h) — дёргаем API заново."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from src import analytics_engine
    analytics_engine._client = None

    from src.database_manager import set_setting
    cache_key = "insight:1:7:ru"
    # Кэш на 7 часов назад — протух
    expired_payload = json.dumps({
        "text": "Старый кэш",
        "generated_at": (datetime.now() - timedelta(hours=7)).isoformat(),
    })
    set_setting(cache_key, expired_payload)

    # Mock client возвращает новый текст
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="Новый ответ")]
    mock_client.messages.create.return_value = mock_response

    with patch("src.analytics_engine._get_client", return_value=mock_client):
        from src.analytics_engine import compute_dashboard_insight
        result = compute_dashboard_insight(1, _summary(), lang="ru", days=7)
        assert result == "Новый ответ"
        mock_client.messages.create.assert_called_once()


def test_api_timeout_returns_none_no_crash(fresh_db, monkeypatch):
    """Если Claude таймаутит — НЕ падаем, возвращаем None (кэш не пишем)."""
    import anthropic
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from src import analytics_engine
    analytics_engine._client = None

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = anthropic.APITimeoutError(request=MagicMock())

    with patch("src.analytics_engine._get_client", return_value=mock_client):
        from src.analytics_engine import compute_dashboard_insight
        result = compute_dashboard_insight(1, _summary(), lang="ru", days=7)
        assert result is None


def test_strips_quotes_from_response(fresh_db, monkeypatch):
    """Claude иногда возвращает ответ в кавычках — чистим."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from src import analytics_engine
    analytics_engine._client = None

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='"Текст в кавычках"')]
    mock_client.messages.create.return_value = mock_response

    with patch("src.analytics_engine._get_client", return_value=mock_client):
        from src.analytics_engine import compute_dashboard_insight
        result = compute_dashboard_insight(1, _summary(), lang="ru", days=7)
        assert result == "Текст в кавычках"


def test_separate_cache_per_lang(fresh_db, monkeypatch):
    """ru и en кэшируются независимо."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    from src import analytics_engine
    analytics_engine._client = None

    from src.database_manager import set_setting
    set_setting("insight:1:7:ru", json.dumps({
        "text": "RU-cache",
        "generated_at": datetime.now().isoformat(),
    }))
    set_setting("insight:1:7:en", json.dumps({
        "text": "EN-cache",
        "generated_at": datetime.now().isoformat(),
    }))

    from src.analytics_engine import compute_dashboard_insight
    assert compute_dashboard_insight(1, _summary(), lang="ru") == "RU-cache"
    assert compute_dashboard_insight(1, _summary(), lang="en") == "EN-cache"
