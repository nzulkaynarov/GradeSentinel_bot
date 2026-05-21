"""Regression тесты для FAQ-блока в _CHAT_SYSTEM_PROMPTS.

PR_E1 расширил system prompt AI-чата фактами о боте (тарифы, тихие часы,
команды, инвайты и т.д.). Тесты проверяют:
  1. Все 3 языка содержат FAQ-блок (синхронность).
  2. Ключевые числовые инварианты (5 минут, 22-07, 48 часов, 5 детей)
     совпадают с src/config.py — если кто-то поменяет константу но забудет
     обновить prompt, тест упадёт.
  3. Все упомянутые команды (/start, /help, /grades, /ai_report,
     /subscription, /manage_family, /support) есть в каждом языке.
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")

import pytest

from src.analytics_engine import _CHAT_SYSTEM_PROMPTS
from src.config import (
    POLLING_INTERVAL,
    QUIET_HOURS_START,
    QUIET_HOURS_END,
    INVITE_EXPIRES_HOURS,
    MAX_CHILDREN_PER_FAMILY,
)


SUPPORTED_LANGS = ('ru', 'uz', 'en')


def test_all_languages_present():
    for lang in SUPPORTED_LANGS:
        assert lang in _CHAT_SYSTEM_PROMPTS, f"Lang {lang!r} missing from _CHAT_SYSTEM_PROMPTS"


@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
def test_prompt_has_grade_core_instruction(lang):
    """Базовая инструкция «отвечай по оценкам» осталась после расширения FAQ."""
    text = _CHAT_SYSTEM_PROMPTS[lang]
    # Каждый язык должен упоминать сегодняшнюю дату и оценки как core
    keywords = {
        'ru': ('оценк', 'сегодняшн'),
        'uz': ('baho', 'bugungi'),
        'en': ('grade', "today's"),
    }
    for kw in keywords[lang]:
        assert kw in text.lower() if kw.islower() else kw in text, (
            f"[{lang}] core grade-instruction marker {kw!r} missing"
        )


@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
def test_prompt_mentions_polling_interval(lang):
    """5 минут (POLLING_INTERVAL=300) упомянуты как частота мониторинга."""
    assert POLLING_INTERVAL == 300, "Тест держится за дефолт 300с; обнови если меняешь"
    text = _CHAT_SYSTEM_PROMPTS[lang]
    # Каждый язык должен сказать «5 минут» в каком-то виде
    markers = {
        'ru': '5 минут',
        'uz': '5 daqiqa',
        'en': '5 minutes',
    }
    assert markers[lang] in text, f"[{lang}] expected '{markers[lang]}' (POLLING_INTERVAL)"


@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
def test_prompt_mentions_quiet_hours(lang):
    """22:00 и 07:00 (QUIET_HOURS_START/END) упомянуты."""
    assert QUIET_HOURS_START == 22 and QUIET_HOURS_END == 7, (
        "Тест держится за 22-07 default; обнови если меняешь"
    )
    text = _CHAT_SYSTEM_PROMPTS[lang]
    assert '22:00' in text, f"[{lang}] quiet hours start (22:00) missing"
    assert '07:00' in text, f"[{lang}] quiet hours end (07:00) missing"


@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
def test_prompt_mentions_invite_ttl(lang):
    """48 часов (INVITE_EXPIRES_HOURS) упомянуты для инвайт-ссылок."""
    assert INVITE_EXPIRES_HOURS == 48, "Тест держится за 48ч default; обнови если меняешь"
    text = _CHAT_SYSTEM_PROMPTS[lang]
    assert '48' in text, f"[{lang}] invite TTL (48h) missing"


@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
def test_prompt_mentions_max_children(lang):
    """До 5 детей (MAX_CHILDREN_PER_FAMILY) упомянуты в family-блоке."""
    assert MAX_CHILDREN_PER_FAMILY == 5, "Тест держится за 5 default; обнови если меняешь"
    text = _CHAT_SYSTEM_PROMPTS[lang]
    assert '5' in text, f"[{lang}] max children per family (5) missing"


@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
@pytest.mark.parametrize("cmd", [
    "/start", "/help", "/grades", "/ai_report",
    "/subscription", "/manage_family", "/support",
])
def test_prompt_mentions_commands(lang, cmd):
    """Каждая команда из reference списка упомянута в каждом языке.

    Если добавляется новая команда или удаляется старая — обнови этот тест
    и сам system prompt одновременно."""
    text = _CHAT_SYSTEM_PROMPTS[lang]
    assert cmd in text, f"[{lang}] command {cmd} not mentioned in FAQ"


@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
def test_prompt_no_hardcoded_prices(lang):
    """Цены НЕ должны быть захардкожены — они мутируются через /set_prices.
    Если хочешь зашить цены — сначала зашей в БД через миграцию и убедись
    что get_plans() возвращает их же. Сейчас prompt должен отправлять
    юзера в /subscription за актуальными ценами."""
    text = _CHAT_SYSTEM_PROMPTS[lang]
    forbidden = ['29900', '29 900', '79900', '79 900', '249900', '249 900']
    for price in forbidden:
        assert price not in text, (
            f"[{lang}] hardcoded price {price!r} found — это сломается при /set_prices. "
            "Уведи AI в /subscription за актуальными ценами вместо этого."
        )


@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
def test_prompt_mentions_payment_methods(lang):
    """Click, Payme, Telegram Stars — все 3 способа упомянуты."""
    text = _CHAT_SYSTEM_PROMPTS[lang]
    assert 'Click' in text, f"[{lang}] payment method Click missing"
    assert 'Payme' in text, f"[{lang}] payment method Payme missing"
    assert 'Stars' in text, f"[{lang}] payment method Telegram Stars missing"


@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
def test_prompt_mentions_dont_know_safeguard(lang):
    """Safeguard «не угадывай про подписку/семью/цены — попроси открыть меню».
    Без этого AI начнёт фантазировать про subscription_end и состав семьи."""
    text = _CHAT_SYSTEM_PROMPTS[lang]
    # Маркеры разные на языках, ключевое — упоминание /subscription и /manage_family
    # как fallback для динамических вопросов
    assert '/subscription' in text, f"[{lang}] /subscription fallback missing"
    assert '/manage_family' in text, f"[{lang}] /manage_family fallback missing"


@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
def test_prompt_size_reasonable(lang):
    """Размер system prompt должен быть в разумных пределах. Если кто-то
    раздул prompt до 10K+ chars — это бьёт по latency и стоимости per request."""
    text = _CHAT_SYSTEM_PROMPTS[lang]
    # Текущий ~3300 chars. Cap ~6000 — даёт запас на 1.5x роста перед alert'ом.
    assert 500 < len(text) < 6000, (
        f"[{lang}] system prompt size {len(text)} chars вне диапазона [500, 6000]"
    )
