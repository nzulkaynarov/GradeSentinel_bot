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


def _get_client() -> Optional[anthropic.Anthropic]:
    global _client
    if _client is not None:
        return _client

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY not set. AI analytics disabled.")
        return None

    _client = anthropic.Anthropic(api_key=api_key, timeout=_API_TIMEOUT_SECONDS)
    return _client
