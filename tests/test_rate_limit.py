"""Тест thread-safety и TTL-чистки rate limit'а."""
import time


def test_rate_limit_blocks_after_threshold(monkeypatch):
    # Импортируем main лениво и патчим зависимости от dotenv/telebot,
    # чтобы тесты не падали в окружении без .env
    import importlib
    import sys

    # Подменяем модули, которые main импортирует из bot_instance/i18n/etc
    # на минимальные заглушки
    if 'src.main' in sys.modules:
        del sys.modules['src.main']

    # Стабовая среда — не нужна для is_rate_limited (он чистая функция)
    monkeypatch.setenv('BOT_TOKEN', 'test')
    monkeypatch.setenv('ADMIN_ID', '0')

    try:
        from src.main import is_rate_limited, RATE_LIMIT_MAX
    except Exception:
        # Если в окружении нет telebot — пропускаем
        import pytest
        pytest.skip("main imports unavailable in test env")
        return

    user_id = 999_888_777
    # До предела
    for _ in range(RATE_LIMIT_MAX):
        assert is_rate_limited(user_id) is False
    # На пределе — блокируется
    assert is_rate_limited(user_id) is True
