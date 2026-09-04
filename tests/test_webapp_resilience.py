"""Устойчивость веб-приложения (аудит 2026-09-03).

Три места, где HTTP-поток отдавался внешней системе без предела, и один
эндпоинт, который врал мониторингу:

  • `/health` не трогал БД вообще и отдавал 200 при мёртвой базе;
  • `_get_bot_username` кэшировал успех, но не неудачу — пока Telegram отвечает
    5xx, каждое открытие дашборда делало свежий `get_me()` и занимало поток;
  • таблица полной истории в PDF строилась без лимита строк при `MemoryMax`,
    оставлявшем ~60 МБ свободных.
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import webapp.app as wa  # noqa: E402


@pytest.fixture
def client():
    wa.app.config.update(TESTING=True)
    return wa.app.test_client()


# ─── /health честно отражает состояние БД ────────────────────────────
def test_health_ok_when_db_reachable(client):
    wa._HEALTH_CACHE.update(at=0.0, ok=True)

    res = client.get("/health")

    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"


def test_health_reports_degraded_when_db_down(client):
    """Мониторинг должен видеть проблему, а не «здоров» при мёртвой базе."""
    wa._HEALTH_CACHE.update(at=0.0, ok=True)

    with patch.object(wa, "get_db_connection", side_effect=RuntimeError("db is down")):
        res = client.get("/health")

    assert res.status_code == 503
    assert res.get_json()["status"] == "degraded"


def test_health_result_is_cached(client):
    """Проверка кэшируется: healthcheck не должен сам стать нагрузкой."""
    wa._HEALTH_CACHE.update(at=0.0, ok=True)

    with patch.object(wa, "get_db_connection") as conn:
        client.get("/health")
        client.get("/health")

    assert conn.call_count == 1


# ─── Имя бота: неудача тоже кэшируется ───────────────────────────────
@pytest.fixture(autouse=True)
def _reset_bot_cache():
    wa._BOT_USERNAME_CACHE = None
    wa._BOT_USERNAME_FAILED_AT = None
    yield
    wa._BOT_USERNAME_CACHE = None
    wa._BOT_USERNAME_FAILED_AT = None


def test_bot_username_failure_is_cached(monkeypatch):
    """Пока Telegram недоступен, get_me() зовётся один раз, а не на каждый
    запрос: иначе восемь открытий дашборда занимали все слоты."""
    monkeypatch.delenv("BOT_USERNAME", raising=False)
    bot = MagicMock()
    bot.get_me.side_effect = RuntimeError("telegram 502")

    with patch.object(wa, "_get_webapp_bot", return_value=bot):
        assert wa._get_bot_username() is None
        assert wa._get_bot_username() is None
        assert wa._get_bot_username() is None

    assert bot.get_me.call_count == 1


def test_bot_username_no_failure_means_no_cooldown(monkeypatch):
    """Регрессия: отсчёт «последней неудачи» от нуля на свежезагруженной машине
    блокировал самый первый вызов (time.monotonic() там близок к нулю)."""
    monkeypatch.delenv("BOT_USERNAME", raising=False)
    bot = MagicMock()
    bot.get_me.return_value = MagicMock(username="GradeSentinelBot")

    with patch.object(wa, "_get_webapp_bot", return_value=bot), \
         patch.object(wa.time, "monotonic", return_value=1.0):
        assert wa._get_bot_username() == "GradeSentinelBot"

    assert bot.get_me.call_count == 1


def test_bot_username_retries_after_cooldown(monkeypatch):
    """Через cooldown попытка повторяется — сбой не вечен."""
    monkeypatch.delenv("BOT_USERNAME", raising=False)
    bot = MagicMock()
    bot.get_me.side_effect = RuntimeError("telegram 502")

    with patch.object(wa, "_get_webapp_bot", return_value=bot):
        wa._get_bot_username()
        wa._BOT_USERNAME_FAILED_AT -= wa._BOT_USERNAME_RETRY_SECONDS + 1
        wa._get_bot_username()

    assert bot.get_me.call_count == 2


def test_bot_username_from_env_skips_telegram(monkeypatch):
    """Имя бота не меняется — переменная окружения снимает вызов вовсе."""
    monkeypatch.setenv("BOT_USERNAME", "@GradeSentinelBot")
    bot = MagicMock()

    with patch.object(wa, "_get_webapp_bot", return_value=bot):
        assert wa._get_bot_username() == "GradeSentinelBot"

    bot.get_me.assert_not_called()


# ─── PDF: таблица истории ограничена ─────────────────────────────────
def _grades(n):
    return [{"subject": "Алгебра", "raw_text": "4", "grade_value": 4.0,
             "grade_date": f"2026-{1 + i // 400:02d}-{1 + i % 28:02d}"} for i in range(n)]


def test_pdf_history_table_is_capped():
    """Отчёт на тысячи строк не должен уносить оба воркера по памяти."""
    from webapp.pdf_export import _MAX_HISTORY_ROWS, _full_history_table

    tbl = _full_history_table(_grades(_MAX_HISTORY_ROWS + 500), "ru", {})
    # header + cap + строка примечания
    assert len(tbl._cellvalues) == _MAX_HISTORY_ROWS + 2


def test_pdf_history_notes_truncation():
    """Усечение подписано числами, а не спрятано."""
    from webapp.pdf_export import _MAX_HISTORY_ROWS, _full_history_table

    total = _MAX_HISTORY_ROWS + 123
    tbl = _full_history_table(_grades(total), "ru", {})
    note = tbl._cellvalues[-1][0]

    assert str(total) in note and str(_MAX_HISTORY_ROWS) in note


def test_pdf_history_keeps_everything_below_cap():
    """Обычный отчёт не режется и примечания не получает."""
    from webapp.pdf_export import _full_history_table

    tbl = _full_history_table(_grades(50), "ru", {})

    assert len(tbl._cellvalues) == 51


def test_pdf_truncation_label_localized():
    """Подпись есть на всех трёх языках."""
    from webapp.pdf_export import _localize

    for lang in ("ru", "uz", "en"):
        text = _localize("history_truncated", lang)
        assert "{shown}" in text and "{total}" in text


# ─── Бюджет времени AI-чата ──────────────────────────────────────────
def test_chat_has_time_budget():
    """У ответа есть общий предел, а не только таймаут одного вызова."""
    from src.analytics_engine import _CHAT_TIME_BUDGET_SECONDS
    from src.ai.client import _API_MAX_RETRIES, _API_TIMEOUT_SECONDS

    assert 0 < _CHAT_TIME_BUDGET_SECONDS <= 120
    assert _API_MAX_RETRIES <= 1          # три попытки по 30 с на вызов — слишком
    assert _API_TIMEOUT_SECONDS <= 30
