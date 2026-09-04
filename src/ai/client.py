"""Singleton Anthropic-клиент для AI-аналитики.

Выделено из `src/analytics_engine.py` (PR-M1). `_get_client` — единственная
точка создания клиента; кэшируется в модульной глобали `_client`. Тесты мокают
клиент через `monkeypatch.setattr("src.analytics_engine._get_client", ...)` —
это продолжает работать, потому что оркестрационные функции в analytics_engine
вызывают `_get_client` из своего namespace (re-export из этого модуля).
"""
import os
import logging
from typing import Optional
import anthropic

logger = logging.getLogger(__name__)

_client = None

# Короткий таймаут: пользователь не должен ждать 10 минут (SDK дефолт), если
# Anthropic тормозит или сеть моргает. 30 сек хватает для max_tokens=800.
_API_TIMEOUT_SECONDS = 30.0

# Ретраи SDK по умолчанию — 2, то есть до 3 попыток по 30 сек на ОДИН вызов.
# Вместе с tool-use циклом (до 6 вызовов) это давало теоретические 9 минут на
# один HTTP-запрос, при том что у веб-приложения всего 8 слотов на всё. Одна
# повторная попытка — достаточный компромисс (аудит 2026-09-03).
_API_MAX_RETRIES = 1


def _get_client() -> Optional[anthropic.Anthropic]:
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set. AI analytics disabled.")
        return None

    _client = anthropic.Anthropic(api_key=api_key, timeout=_API_TIMEOUT_SECONDS,
                                  max_retries=_API_MAX_RETRIES)
    return _client
