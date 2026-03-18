"""
Модуль интернационализации (i18n).
Поддержка: ru (русский), uz (узбекский), en (английский).
"""
import json
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_translations = {}
SUPPORTED_LANGS = ['ru', 'uz', 'en']
DEFAULT_LANG = 'ru'

# Маппинг: локализованный текст кнопки -> action key
BUTTON_ACTIONS = {}


def load_translations():
    """Загружает все файлы локализации и строит маппинг кнопок."""
    locales_dir = os.path.join(os.path.dirname(__file__), 'locales')
    for lang in SUPPORTED_LANGS:
        path = os.path.join(locales_dir, f'{lang}.json')
        try:
            with open(path, 'r', encoding='utf-8') as f:
                _translations[lang] = json.load(f)
            logger.info(f"Loaded locale: {lang} ({len(_translations[lang])} keys)")
        except FileNotFoundError:
            logger.warning(f"Locale file not found: {path}")
            _translations[lang] = {}

    _build_button_actions()


def _build_button_actions():
    """Строит маппинг текстов кнопок на action-ключи для всех языков."""
    global BUTTON_ACTIONS
    BUTTON_ACTIONS.clear()

    button_keys = {
        'btn_status': 'status',
        'btn_families': 'families',
        'btn_new_family': 'new_family',
        'btn_my_family': 'my_family',
        'btn_grades': 'grades',
        'btn_ai_analysis': 'ai_analysis',
        'btn_support': 'support',
        'btn_broadcast': 'broadcast',
        'btn_settings': 'settings',
        'btn_subscription': 'subscription',
        'btn_admin_panel': 'admin_panel',
        'btn_user_menu': 'user_menu',
    }

    for lang in SUPPORTED_LANGS:
        for key, action in button_keys.items():
            text = t(key, lang)
            if text != key:  # Не добавляем ключ если перевод не найден
                BUTTON_ACTIONS[text] = action


def t(key: str, lang: str = DEFAULT_LANG, **kwargs) -> str:
    """
    Возвращает переведённую строку по ключу.
    Если перевода нет для данного языка, использует DEFAULT_LANG.
    Если ключа нет вообще — возвращает сам ключ.
    """
    text = _translations.get(lang, {}).get(key)
    if text is None:
        text = _translations.get(DEFAULT_LANG, {}).get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text


def get_lang_name(lang: str) -> str:
    """Возвращает название языка на нём самом."""
    names = {'ru': 'Русский', 'uz': "O'zbek", 'en': 'English'}
    return names.get(lang, lang)
