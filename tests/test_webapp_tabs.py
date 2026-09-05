"""Каркас Mini App: четыре вкладки и нижняя навигация.

Разметка дашборда тестами раньше не покрывалась, поэтому опечатка в id панели
или забытый ключ перевода проявлялись только на живом телефоне родителя.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
TEMPLATE = ROOT / "webapp" / "templates" / "dashboard.html"
APP_JS = ROOT / "webapp" / "static" / "app.js"
LOCALES_DIR = ROOT / "webapp" / "static" / "locales"
LANGS = ["ru", "uz", "en"]

# data-view кнопки → id панели. Должно совпадать с TAB_VIEWS в app.js.
TABS = {
    "today": "view-today",
    "subjects": "view-subjects",
    "year": "view-year",
    "chat": "view-chat",
}


@pytest.fixture(scope="module")
def html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_js() -> str:
    return APP_JS.read_text(encoding="utf-8")


@pytest.mark.parametrize("view,panel_id", sorted(TABS.items()))
def test_tab_has_button_and_panel(html, view, panel_id):
    """У каждой вкладки есть и кнопка в навигации, и панель с контентом."""
    assert f'data-view="{view}"' in html, f"нет кнопки вкладки {view}"
    assert f'id="{panel_id}"' in html, f"нет панели {panel_id}"


def test_only_first_tab_visible_initially(html):
    """До инициализации JS открыта ровно одна вкладка — «Сегодня».

    Иначе при медленной загрузке родитель на мгновение видит все четыре
    экрана сразу.
    """
    for view, panel_id in TABS.items():
        m = re.search(rf'<div id="{panel_id}"[^>]*>', html)
        assert m, f"панель {panel_id} не найдена"
        hidden = "hidden" in m.group(0)
        assert hidden == (view != "today"), (
            f"панель {panel_id}: hidden={hidden}, ожидалось {view != 'today'}"
        )


def test_app_js_knows_same_tabs(app_js):
    """TAB_VIEWS в app.js не разъехался с разметкой."""
    block = re.search(r"const TAB_VIEWS = \{(.*?)\}", app_js, re.S)
    assert block, "TAB_VIEWS не найден в app.js"
    assert dict(re.findall(r'(\w+):\s*"([\w-]+)"', block.group(1))) == TABS


def test_period_toggle_hidden_where_meaningless(app_js):
    """Период осмыслен только на вкладках со скользящим окном.

    «Итоги» разрезаны по учебным годам — переключатель дней там врал бы.
    """
    block = re.search(r"VIEWS_WITH_PERIOD = new Set\(\[(.*?)\]\)", app_js, re.S)
    assert block, "VIEWS_WITH_PERIOD не найден"
    assert set(re.findall(r'"(\w+)"', block.group(1))) == {"today", "subjects"}


def test_removed_controls_are_gone(html):
    """Верхние вкладки и кнопка «Спросить AI» удалены вместе с их ключами."""
    assert 'class="view-tabs"' not in html
    assert 'id="btn-open-ai"' not in html
    for lang in LANGS:
        data = json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        assert "tab_dashboard" not in data, f"{lang}: остался мёртвый ключ"
        assert "action_open_ai" not in data, f"{lang}: остался мёртвый ключ"


def test_every_template_i18n_key_is_translated(html):
    """Каждый data-i18n из шаблона есть во всех трёх локалях.

    Забытый ключ показывает родителю русский текст интерфейса на узбекском.
    """
    keys = set(re.findall(r'data-i18n(?:-placeholder)?="([\w_]+)"', html))
    assert keys, "в шаблоне не нашлось ни одного data-i18n"
    for lang in LANGS:
        data = json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        missing = keys - set(data)
        assert not missing, f"{lang}.json не хватает ключей: {sorted(missing)}"


def test_tabbar_accounts_for_safe_area():
    """Бар закреплён внизу — контент и сам бар обязаны учитывать safe area.

    Без этого на iPhone нижняя навигация уезжает под системную полосу, а
    последняя карточка прячется за баром.
    """
    css = (ROOT / "webapp" / "static" / "style.css").read_text(encoding="utf-8")
    tabbar = re.search(r"\.tabbar \{(.*?)\}", css, re.S)
    assert tabbar and "safe-area-inset-bottom" in tabbar.group(1)
    content = re.search(r"#content \{(.*?)\}", css, re.S)
    assert content and "--gs-tabbar-h" in content.group(1)
