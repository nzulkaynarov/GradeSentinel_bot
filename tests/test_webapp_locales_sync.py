"""Проверка что webapp/static/locales/{ru,uz,en}.json синхронны по ключам."""

import json
from pathlib import Path

import pytest

LOCALES_DIR = Path(__file__).parent.parent / "webapp" / "static" / "locales"
LANGS = ["ru", "uz", "en"]


def _load(lang: str) -> dict:
    with open(LOCALES_DIR / f"{lang}.json", encoding="utf-8") as f:
        return json.load(f)


def test_locales_have_same_keys():
    """Все три файла должны иметь одинаковый набор ключей."""
    locales = {lang: _load(lang) for lang in LANGS}
    ref_keys = set(locales["ru"].keys())

    for lang, data in locales.items():
        keys = set(data.keys())
        missing = ref_keys - keys
        extra = keys - ref_keys
        assert not missing, f"{lang}.json missing keys: {missing}"
        assert not extra, f"{lang}.json has extra keys: {extra}"


def test_locales_no_empty_values():
    """Ни в одном переводе не должно быть пустых строк (typo при копи-пасте)."""
    for lang in LANGS:
        data = _load(lang)
        empty = [k for k, v in data.items() if not str(v).strip()]
        assert not empty, f"{lang}.json has empty values for: {empty}"


@pytest.mark.parametrize("lang", LANGS)
def test_placeholder_consistency(lang):
    """Если в ru.json есть {placeholder} — он должен быть в других языках."""
    import re
    ref = _load("ru")
    target = _load(lang)
    for key, ru_val in ref.items():
        ru_placeholders = set(re.findall(r"\{(\w+)\}", str(ru_val)))
        if not ru_placeholders:
            continue
        target_val = target.get(key, "")
        target_placeholders = set(re.findall(r"\{(\w+)\}", str(target_val)))
        assert ru_placeholders == target_placeholders, (
            f"{lang}.json key '{key}': placeholders mismatch "
            f"(ru: {ru_placeholders}, {lang}: {target_placeholders})"
        )
