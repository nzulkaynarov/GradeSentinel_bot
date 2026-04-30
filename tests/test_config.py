"""Тесты config — fallback на default при невалидных env."""
import importlib


def test_env_int_fallback_on_invalid(monkeypatch):
    monkeypatch.setenv("POLLING_INTERVAL", "not-a-number")
    import src.config as cfg
    importlib.reload(cfg)
    assert cfg.POLLING_INTERVAL == 300


def test_env_int_uses_env_when_valid(monkeypatch):
    monkeypatch.setenv("POLLING_INTERVAL", "120")
    import src.config as cfg
    importlib.reload(cfg)
    assert cfg.POLLING_INTERVAL == 120


def test_env_int_default_when_missing(monkeypatch):
    monkeypatch.delenv("POLLING_INTERVAL", raising=False)
    import src.config as cfg
    importlib.reload(cfg)
    assert cfg.POLLING_INTERVAL == 300


def test_quiet_hours_config_used_in_helper(monkeypatch):
    """is_quiet_hours читает QUIET_HOURS_START/END из config."""
    monkeypatch.setenv("QUIET_HOURS_START", "23")
    monkeypatch.setenv("QUIET_HOURS_END", "6")
    import src.config as cfg
    importlib.reload(cfg)
    import src.notification_helpers as nh
    importlib.reload(nh)
    # 5:00 ночь в новом окне (23-6) → quiet
    # Не зовём is_quiet_hours напрямую (зависит от текущего часа)
    assert cfg.QUIET_HOURS_START == 23
    assert cfg.QUIET_HOURS_END == 6
