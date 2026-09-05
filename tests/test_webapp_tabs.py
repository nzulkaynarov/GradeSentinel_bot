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


def test_today_hero_states_period_and_sample_size(html, app_js):
    """Средний балл на «Сегодня» подписан числом оценок и периодом.

    Балл без размера выборки и срока выглядит точнее, чем он есть, — это
    правило дашборда, а не косметика.
    """
    assert 'id="kpi-period-hint"' in html, "нет подписи под средним баллом"
    tpl_use = re.search(r't\("today_sample_tpl"\)(.*?);', app_js, re.S)
    assert tpl_use, "подпись не собирается из today_sample_tpl"
    for lang in LANGS:
        data = json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        tpl = data["today_sample_tpl"]
        assert "{n}" in tpl and "{days}" in tpl, f"{lang}: в подписи потерялся плейсхолдер"


def test_extreme_subjects_moved_to_subjects_tab(html):
    """«Лучший» и «слабее всего» лежат в «Предметах», а не в ленте дня."""
    subjects_panel = html.split('id="view-subjects"')[1].split('id="view-year"')[0]
    assert 'id="kpi-top-name"' in subjects_panel
    assert 'id="kpi-worst-name"' in subjects_panel


def test_truncation_is_disclosed(html, app_js):
    """Урезанный сервером срез не выдаётся за полную историю."""
    assert 'id="recent-truncated"' in html
    assert 'grades_truncated_tpl' in app_js
    for lang in LANGS:
        data = json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        tpl = data["grades_truncated_tpl"]
        assert "{n}" in tpl and "{total}" in tpl, f"{lang}: потерян плейсхолдер"


def test_quarters_moved_to_results_tab(html):
    """Четвертные лежат в «Итогах», а не поверх текущей успеваемости."""
    subjects_panel = html.split('id="view-subjects"')[1].split('id="view-year"')[0]
    results_panel = html.split('id="view-year"')[1].split('id="view-chat"')[0]
    assert 'id="quarters-section"' not in subjects_panel
    assert 'id="quarters-section"' in results_panel


def test_subjects_list_is_rendered_not_dead_code(app_js, html):
    """renderSubjectsList вызывается: до редизайна функция висела мёртвой."""
    assert 'id="subjects-list"' in html
    calls = re.findall(r"^\s*renderSubjectsList\(", app_js, re.M)
    assert calls, "renderSubjectsList не вызывается ниоткуда"


def test_subjects_header_states_sample_size(html, app_js):
    """Список предметов подписан числом предметов и размером выборки.

    Средний по двум оценкам и по двадцати читается одинаково, если не
    написать, сколько их было.
    """
    assert 'id="subjects-count"' in html and 'id="subjects-sample"' in html
    assert 'subjects_count_tpl' in app_js and 'today_sample_tpl' in app_js
    for lang in LANGS:
        data = json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        assert "{n}" in data["subjects_count_tpl"], f"{lang}: потерян плейсхолдер"
        assert "{n}" in data["subjects_grades_tpl"], f"{lang}: потерян плейсхолдер"


def test_subjects_sorted_by_average(app_js):
    """Список идёт от сильного предмета к слабому — как в макете."""
    body = app_js.split("function renderSubjectsList")[1]
    assert "sort((a, b) => b.avg - a.avg)" in body


def test_quarter_trend_is_a_word_not_an_arrow(app_js):
    """Тренд четвертей подписан словом.

    «↓» родитель читает как «плохо вообще», а «снижается» — как «от четверти
    к четверти», что и имеется в виду.
    """
    # Только тело функции: в drill-down стрелка «Тренд» остаётся законно.
    body = app_js.split("function renderQuartersBlock")[1].split("\nfunction ")[0]
    assert "quarters_trend_up" in body and "quarters_trend_down" in body
    assert "'↑'" not in body and "'↓'" not in body
    for lang in LANGS:
        data = json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        for key in ("quarters_trend_up", "quarters_trend_down", "quarters_trend_flat"):
            assert data.get(key), f"{lang}: нет перевода {key}"


def test_forecast_stays_labelled(app_js, html):
    """Прогноз годовой остаётся помеченным как прогноз.

    Число без пометки читается как выставленная оценка — это ровно та ложь,
    ради которой badge и вводили.
    """
    body = app_js.split("function renderQuartersBlock")[1].split("\nfunction ")[0]
    assert "quarters_forecast_badge" in body
    assert "year_is_forecast" in body
    assert 'data-i18n="quarters_empty"' in html


def test_period_stats_not_duplicated_on_two_tabs(app_js):
    """Средний за период живёт только во вкладке «Предметы».

    На двух вкладках одно и то же число расходилось бы при разных периодах.
    """
    body = app_js.split("function renderQuartersBlock")[1].split("function ")[0]
    assert "qc-footer" not in body
    assert "_sparklineSvg" not in body


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
