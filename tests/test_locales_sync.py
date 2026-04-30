"""Тесты синхронности локалей: ru/uz/en должны иметь одинаковые ключи.

CLAUDE.md гарантирует, что 265+ ключей синхронны. Если кто-то добавил ключ
только в один файл, эта проверка поймает регрессию ещё в CI.
"""
import json
import os
import pytest


LOCALES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src', 'locales'
)
LANGS = ('ru', 'uz', 'en')


def _load(lang: str) -> dict:
    with open(os.path.join(LOCALES_DIR, f'{lang}.json'), encoding='utf-8') as f:
        return json.load(f)


def test_all_locales_load_as_valid_json():
    for lang in LANGS:
        data = _load(lang)
        assert isinstance(data, dict)
        assert len(data) > 0


def test_locale_keys_in_sync():
    """Все три локали должны иметь идентичный набор ключей."""
    locales = {lang: _load(lang) for lang in LANGS}
    keys = {lang: set(data.keys()) for lang, data in locales.items()}

    ru_keys = keys['ru']
    diffs = []
    for lang in ('uz', 'en'):
        missing = ru_keys - keys[lang]
        extra = keys[lang] - ru_keys
        if missing:
            diffs.append(f"{lang} missing keys (vs ru): {sorted(missing)}")
        if extra:
            diffs.append(f"{lang} has extra keys (vs ru): {sorted(extra)}")

    assert not diffs, "\n".join(diffs)


def test_no_empty_translation_values():
    """Пустые строки в локали — обычно недоделанный перевод."""
    for lang in LANGS:
        data = _load(lang)
        empty_keys = [k for k, v in data.items() if isinstance(v, str) and not v.strip()]
        assert not empty_keys, f"{lang}.json has empty values for: {empty_keys}"


def test_format_placeholders_match_across_locales():
    """Если в ru есть {placeholder}, в uz/en для того же ключа должен быть тот же набор плейсхолдеров.
    Иначе t() упадёт на KeyError при подстановке."""
    import re
    placeholder_re = re.compile(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}')

    ru = _load('ru')
    mismatches = []
    for lang in ('uz', 'en'):
        other = _load(lang)
        for key, ru_val in ru.items():
            if not isinstance(ru_val, str):
                continue
            other_val = other.get(key)
            if not isinstance(other_val, str):
                continue
            ru_ph = set(placeholder_re.findall(ru_val))
            other_ph = set(placeholder_re.findall(other_val))
            if ru_ph != other_ph:
                mismatches.append(
                    f"{lang}.{key}: ru={sorted(ru_ph)} vs {lang}={sorted(other_ph)}"
                )

    assert not mismatches, "\n".join(mismatches[:20])  # первые 20 для краткости
