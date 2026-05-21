"""Tests for tool use в AI чате (PR_E2).

Покрываем:
- dispatch_tool с разными tool names + edge cases (no family, unknown tool, DB error)
- resolve_family_id_for_student (есть семья / нет / multi-family — берём первую)
- TOOL_DEFINITIONS соответствуют Anthropic API contract
- answer_parent_question: tool-use loop правильно гоняет tools и финализирует
- MAX_TOOL_ITERATIONS cap не даёт зациклиться
"""
import os
import sys
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")

import pytest

from src.ai_tools import (
    TOOL_DEFINITIONS,
    MAX_TOOL_ITERATIONS,
    dispatch_tool,
    resolve_family_id_for_student,
    _labels,
)


# ─── TOOL_DEFINITIONS shape ────────────────────────────────────

def test_tool_definitions_have_three_tools():
    names = [t['name'] for t in TOOL_DEFINITIONS]
    assert 'get_subscription_status' in names
    assert 'get_family_members' in names
    assert 'get_family_pricing' in names


@pytest.mark.parametrize("tool", TOOL_DEFINITIONS)
def test_tool_definition_has_required_fields(tool):
    """Anthropic API требует name + description + input_schema."""
    assert 'name' in tool
    assert 'description' in tool
    assert 'input_schema' in tool
    assert tool['input_schema'].get('type') == 'object'
    # У нас все tools без args — schema должна быть пустой object
    assert tool['input_schema'].get('properties') == {}
    assert tool['input_schema'].get('required') == []


# ─── _labels coverage ─────────────────────────────────────────

@pytest.mark.parametrize("lang", ['ru', 'uz', 'en'])
def test_labels_have_all_keys(lang):
    """Все 3 языка должны иметь те же ключи (синхронность для UI)."""
    ru_keys = set(_labels('ru').keys())
    lang_keys = set(_labels(lang).keys())
    assert ru_keys == lang_keys, f"[{lang}] keys differ from ru: {ru_keys ^ lang_keys}"


def test_labels_unknown_lang_falls_back_to_ru():
    """Незнакомый язык должен fallback'ить в ru, не падать."""
    out = _labels('fr')
    assert out == _labels('ru')


# ─── dispatch_tool edge cases ──────────────────────────────────

def test_dispatch_tool_no_family_returns_fallback_for_family_tools():
    """Tools которым нужен family_id → fallback при None (не падают)."""
    result = dispatch_tool('get_subscription_status', {}, None, 'ru')
    assert 'оп' in result.lower() or 'manage_family' in result  # «определить»/«open»

    result = dispatch_tool('get_family_members', {}, None, 'ru')
    assert 'оп' in result.lower() or 'manage_family' in result


def test_dispatch_tool_pricing_does_not_need_family():
    """get_family_pricing не требует family_id (тарифы глобальны)."""
    with patch('src.ai_tools._format_family_pricing', return_value='STUB_PRICES'):
        result = dispatch_tool('get_family_pricing', {}, None, 'ru')
        assert result == 'STUB_PRICES'


def test_dispatch_tool_unknown_returns_fallback():
    """Unknown tool name → safe fallback, не raise."""
    result = dispatch_tool('foo_bar_baz', {}, 42, 'ru')
    assert 'недоступ' in result.lower()


def test_dispatch_tool_swallows_exceptions():
    """Любое исключение в tool → fallback, никогда не raise (сломает loop)."""
    with patch('src.ai_tools._format_subscription_status', side_effect=RuntimeError("DB down")):
        result = dispatch_tool('get_subscription_status', {}, 42, 'ru')
        assert 'недоступ' in result.lower()


@pytest.mark.parametrize("lang", ['ru', 'uz', 'en'])
def test_dispatch_tool_localizes_errors(lang):
    """Error message переведён на язык юзера."""
    result = dispatch_tool('foo_bar', {}, 42, lang)
    # Каждый язык имеет свой error label
    expected_marker = {
        'ru': 'недоступ',
        'uz': 'mavjud emas',
        'en': 'unavailable',
    }
    assert expected_marker[lang] in result.lower()


# ─── resolve_family_id_for_student ────────────────────────────

def test_resolve_family_id_no_families():
    with patch('src.database_manager.get_families_for_student', return_value=[]):
        assert resolve_family_id_for_student(123) is None


def test_resolve_family_id_returns_first():
    """Если ученик в нескольких семьях — берём первую (single-family типичный кейс)."""
    with patch('src.database_manager.get_families_for_student',
                return_value=[{'id': 7, 'family_name': 'A'},
                              {'id': 8, 'family_name': 'B'}]):
        assert resolve_family_id_for_student(123) == 7


# ─── answer_parent_question tool-use loop ─────────────────────

def _mock_text_response(text):
    """Возвращает «final text» response (stop_reason='end_turn')."""
    class _Block:
        type = 'text'
        def __init__(self, txt):
            self.text = txt
    class _Resp:
        stop_reason = 'end_turn'
        content = [_Block(text)]
    return _Resp()


def _mock_tool_use_response(tool_name, tool_input=None, tool_use_id='tu_1'):
    """Возвращает «tool_use» response (stop_reason='tool_use')."""
    class _ToolUseBlock:
        type = 'tool_use'
        def __init__(self):
            self.name = tool_name
            self.input = tool_input or {}
            self.id = tool_use_id
    class _Resp:
        stop_reason = 'tool_use'
        content = [_ToolUseBlock()]
    return _Resp()


def test_no_tool_use_returns_text_directly(monkeypatch):
    """Если Claude вернул text сразу (без tool_use) — возвращаем текст."""
    captured = {'calls': 0}

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                captured['calls'] += 1
                return _mock_text_response('Прямой ответ')

    monkeypatch.setattr("src.analytics_engine._get_client", lambda: FakeClient())
    monkeypatch.setattr("src.ai_tools.resolve_family_id_for_student", lambda _: 42)

    from src.analytics_engine import answer_parent_question
    result = answer_parent_question(
        student_id=1, student_name="Test",
        grades=[{"subject": "X", "grade_value": 5.0, "raw_text": "5",
                 "grade_date": "2026-05-21"}],
        question="Как дела?", lang='ru',
    )
    assert result == 'Прямой ответ'
    assert captured['calls'] == 1, "Should be single API call when no tool_use"


def test_tool_use_loop_executes_tool_and_finalizes(monkeypatch):
    """Claude: tool_use → dispatcher → второй вызов с tool_result → final text."""
    captured = {'calls': 0, 'last_messages': None}

    def make_response(**kwargs):
        captured['calls'] += 1
        captured['last_messages'] = kwargs.get('messages')
        if captured['calls'] == 1:
            return _mock_tool_use_response('get_family_pricing')
        return _mock_text_response('Тарифы: 29 900 UZS / мес.')

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                return make_response(**kwargs)

    monkeypatch.setattr("src.analytics_engine._get_client", lambda: FakeClient())
    monkeypatch.setattr("src.ai_tools.resolve_family_id_for_student", lambda _: 42)
    monkeypatch.setattr("src.ai_tools._format_family_pricing", lambda lang: 'STUB_PRICES_BLOB')

    from src.analytics_engine import answer_parent_question
    result = answer_parent_question(
        student_id=1, student_name="Test",
        grades=[{"subject": "X", "grade_value": 5.0, "raw_text": "5",
                 "grade_date": "2026-05-21"}],
        question="Сколько стоит?", lang='ru',
    )
    assert result == 'Тарифы: 29 900 UZS / мес.'
    assert captured['calls'] == 2

    # Проверяем что во второй вызов попал tool_result со STUB_PRICES_BLOB
    msgs = captured['last_messages']
    # Должно быть: [user (вопрос), assistant (tool_use), user (tool_result)]
    assert len(msgs) == 3
    assert msgs[-1]['role'] == 'user'
    tool_results = msgs[-1]['content']
    assert isinstance(tool_results, list)
    assert tool_results[0]['type'] == 'tool_result'
    assert tool_results[0]['content'] == 'STUB_PRICES_BLOB'


def test_tool_use_caps_at_max_iterations(monkeypatch):
    """Если Claude бесконечно вызывает tools — возвращаем None после cap'а."""
    captured = {'calls': 0}

    def make_response(**kwargs):
        captured['calls'] += 1
        # Всегда возвращаем tool_use — Claude никогда не сошёлся
        return _mock_tool_use_response('get_family_pricing',
                                         tool_use_id=f'tu_{captured["calls"]}')

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                return make_response(**kwargs)

    monkeypatch.setattr("src.analytics_engine._get_client", lambda: FakeClient())
    monkeypatch.setattr("src.ai_tools.resolve_family_id_for_student", lambda _: 42)
    monkeypatch.setattr("src.ai_tools._format_family_pricing", lambda lang: 'X')

    from src.analytics_engine import answer_parent_question
    result = answer_parent_question(
        student_id=1, student_name="Test",
        grades=[{"subject": "X", "grade_value": 5.0, "raw_text": "5",
                 "grade_date": "2026-05-21"}],
        question="Тарифы?", lang='ru',
    )
    assert result is None
    # MAX_TOOL_ITERATIONS + 1 итераций (+1 для финального шанса)
    assert captured['calls'] == MAX_TOOL_ITERATIONS + 1


def test_tool_use_loop_uses_provided_family_id(monkeypatch):
    """Если family_id передан явно — resolve_family_id_for_student НЕ вызывается."""
    resolve_called = {'count': 0}

    def fake_resolve(_):
        resolve_called['count'] += 1
        return 999

    monkeypatch.setattr("src.ai_tools.resolve_family_id_for_student", fake_resolve)

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                return _mock_text_response('ok')

    monkeypatch.setattr("src.analytics_engine._get_client", lambda: FakeClient())

    from src.analytics_engine import answer_parent_question
    answer_parent_question(
        student_id=1, student_name="Test", grades=[],
        question="?", lang='ru', family_id=77,
    )
    assert resolve_called['count'] == 0, "family_id provided — no resolve call"


# ─── System prompt mentions tools ─────────────────────────────

@pytest.mark.parametrize("lang", ['ru', 'uz', 'en'])
def test_system_prompt_mentions_tool_names(lang):
    """System prompt должен инструктировать AI про доступные tools.
    Без этого Claude может игнорить tools или дольше думать прежде чем вызвать."""
    from src.analytics_engine import _CHAT_SYSTEM_PROMPTS
    text = _CHAT_SYSTEM_PROMPTS[lang]
    assert 'get_subscription_status' in text, f"[{lang}] missing tool name"
    assert 'get_family_members' in text, f"[{lang}] missing tool name"
    assert 'get_family_pricing' in text, f"[{lang}] missing tool name"
