"""Тесты для PR_H1: bot ai_chat handler использует conversation history.

Webapp уже использует prev_messages с PR_D R6 — этот PR подтягивает
ту же логику в `src/handlers/ai_chat.py:_ask_ai`. Тесты проверяют:
- get_recent_chat_history вызывается с (telegram_id, student_id)
- user-message сохраняется ДО вызова AI (чтобы не потерять при race)
- assistant-message сохраняется только при УСПЕХЕ (не сохраняем None)
- prev_messages передаётся в answer_parent_question
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# bot_instance валидирует ":" в BOT_TOKEN — формат должен быть похож на реальный
os.environ.setdefault("BOT_TOKEN", "12345:test-token-for-handler-import")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")

import json
import pytest


def _setup_common_mocks(monkeypatch, ai_answer="AI ответ"):
    """Подменяет всё кроме того что тестируем. Возвращает captured dict.

    NAV-001: family-scoped. Тестовая семья family_id=10 с одним ребёнком
    student_id=5. Mocks отражают новый flow (get_families_for_user,
    get_family_students, save_family_chat_message и т.п.)."""
    import src.handlers.ai_chat as ai_chat_mod

    captured = {
        'history_calls': [],
        'save_calls': [],
        'ai_calls': [],
        'bot_sends': [],
    }

    monkeypatch.setattr(ai_chat_mod, "get_families_for_user",
                        lambda _: [{"id": 10, "family_name": "Test"}])
    monkeypatch.setattr(ai_chat_mod, "get_family_students",
                        lambda fid: [{"id": 5, "fio": "Заур", "display_name": "Заур"}])
    monkeypatch.setattr(ai_chat_mod, "get_grade_history_for_student_all",
                        lambda *a, **kw: [{"subject": "Алгебра", "grade_value": 5.0,
                                            "raw_text": "5", "grade_date": "2026-05-21"}])
    monkeypatch.setattr(ai_chat_mod, "get_user_lang", lambda _: 'ru')

    def fake_get_history(tg, fid):
        captured['history_calls'].append((tg, fid))
        return [{"role": "user", "content": "Прошлый Q"},
                {"role": "assistant", "content": "Прошлый A"}]
    monkeypatch.setattr(ai_chat_mod, "get_recent_family_chat_history", fake_get_history)

    def fake_save(tg, fid, role, content):
        captured['save_calls'].append((tg, fid, role, content))
        return 42  # mock msg_id
    monkeypatch.setattr(ai_chat_mod, "save_family_chat_message", fake_save)

    def fake_ai(**kwargs):
        captured['ai_calls'].append(kwargs)
        return ai_answer
    # Inline import в _ask_ai → patch atrib на analytics_engine модуле
    import src.analytics_engine as ae
    monkeypatch.setattr(ae, "answer_parent_question", fake_ai)

    # Мокаем bot методы
    monkeypatch.setattr(ai_chat_mod.bot, "send_chat_action",
                        lambda *a, **kw: None, raising=False)

    # placeholder.message_id mock — send_message возвращает объект с msg_id
    class _SentMsg:
        message_id = 777

    def fake_send(*args, **kwargs):
        captured['bot_sends'].append((args, kwargs))
        return _SentMsg()
    monkeypatch.setattr(ai_chat_mod.bot, "send_message", fake_send, raising=False)
    monkeypatch.setattr(ai_chat_mod.bot, "edit_message_text",
                        lambda *a, **kw: None, raising=False)

    return captured


def test_ask_ai_loads_history_and_passes_to_answer(monkeypatch):
    """История подгружается per family_id и передаётся в answer_parent_question."""
    captured = _setup_common_mocks(monkeypatch)

    from src.handlers.ai_chat import _ask_ai
    state = {"state": "ai_chat_mode", "data": json.dumps({"family_id": 10})}
    _ask_ai(user_id=123, question="Новый Q", lang='ru', state=state)

    assert captured['history_calls'] == [(123, 10)], (
        f"get_recent_family_chat_history called wrong: {captured['history_calls']}"
    )
    ai_call = captured['ai_calls'][0]
    assert ai_call['prev_messages'] == [
        {"role": "user", "content": "Прошлый Q"},
        {"role": "assistant", "content": "Прошлый A"},
    ]
    assert ai_call['question'] == "Новый Q"
    assert ai_call['family_id'] == 10
    assert ai_call['student_id'] is None  # NAV-001: family-scope, не student-scope


def test_ask_ai_saves_user_before_ai_call_and_assistant_after(monkeypatch):
    """Порядок: save user → call AI → save assistant.
    Гарантирует что юзер не теряет свой вопрос при AI-фейле."""
    captured = _setup_common_mocks(monkeypatch, ai_answer="Конкретный ответ")

    from src.handlers.ai_chat import _ask_ai
    state = {"state": "ai_chat_mode", "data": json.dumps({"family_id": 10})}
    _ask_ai(user_id=123, question="Q?", lang='ru', state=state)

    assert len(captured['save_calls']) == 2
    assert captured['save_calls'][0] == (123, 10, 'user', "Q?")
    assert captured['save_calls'][1] == (123, 10, 'assistant', "Конкретный ответ")


def test_ask_ai_does_not_save_assistant_when_ai_returns_none(monkeypatch):
    """AI вернул None → ai_chat_error отправлен → assistant НЕ сохраняется."""
    captured = _setup_common_mocks(monkeypatch, ai_answer=None)

    from src.handlers.ai_chat import _ask_ai
    state = {"state": "ai_chat_mode", "data": json.dumps({"family_id": 10})}
    _ask_ai(user_id=123, question="Q?", lang='ru', state=state)

    # Только user message сохранён, assistant нет
    assert len(captured['save_calls']) == 1
    assert captured['save_calls'][0][2] == 'user'
    # PR_H4: placeholder отправлен, потом edit_message_text заменяет на error.
    # bot.send_message вызван 1 раз (placeholder), edit заменил на ошибку —
    # send_message больше не вызывается. Допускаем 1-2 sends.
    assert 1 <= len(captured['bot_sends']) <= 2


def test_ask_ai_does_not_save_assistant_when_ai_raises(monkeypatch):
    """AI raise → assistant НЕ сохраняется. Тот же контракт что и при None."""
    captured = _setup_common_mocks(monkeypatch)

    import src.analytics_engine as ae
    def raising_ai(**kwargs):
        raise RuntimeError("Anthropic timeout")
    monkeypatch.setattr(ae, "answer_parent_question", raising_ai)

    from src.handlers.ai_chat import _ask_ai
    state = {"state": "ai_chat_mode", "data": json.dumps({"family_id": 10})}
    _ask_ai(user_id=123, question="Q?", lang='ru', state=state)

    # User saved, assistant НЕ saved
    assert len(captured['save_calls']) == 1
    assert captured['save_calls'][0][2] == 'user'


def test_ask_ai_skip_save_user_does_not_double_save(monkeypatch):
    """NAV-007 retry: skip_save_user=True пропускает save user, но
    assistant сохраняется на успех."""
    captured = _setup_common_mocks(monkeypatch, ai_answer="Retry answer")

    from src.handlers.ai_chat import _ask_ai
    state = {"state": "ai_chat_mode", "data": json.dumps({"family_id": 10})}
    _ask_ai(user_id=123, question="Прошлый Q", lang='ru', state=state,
            skip_save_user=True)

    # Только assistant сохранён (user пропущен)
    assert len(captured['save_calls']) == 1
    assert captured['save_calls'][0][2] == 'assistant'
    assert captured['save_calls'][0][3] == "Retry answer"


def test_ask_ai_fail_includes_retry_markup(monkeypatch):
    """NAV-007: при AI fail отправляется error с inline retry-кнопкой."""
    captured = _setup_common_mocks(monkeypatch, ai_answer=None)

    # Перехватываем edit_message_text чтобы убедиться что retry_markup приложен
    import src.handlers.ai_chat as ai_chat_mod
    edits = []
    monkeypatch.setattr(ai_chat_mod.bot, "edit_message_text",
                        lambda *a, **kw: edits.append(kw) or None,
                        raising=False)

    from src.handlers.ai_chat import _ask_ai
    state = {"state": "ai_chat_mode", "data": json.dumps({"family_id": 10})}
    _ask_ai(user_id=123, question="Q?", lang='ru', state=state)

    # error edit должен иметь reply_markup (retry-кнопка)
    error_edits = [e for e in edits if 'не получилось' in (e.get('text') or '').lower()]
    assert len(error_edits) >= 1
    assert error_edits[0].get('reply_markup') is not None


def test_ask_ai_empty_history_passes_empty_list(monkeypatch):
    """Первый вопрос родителя — пустая история, prev_messages=[]."""
    captured = _setup_common_mocks(monkeypatch)

    import src.handlers.ai_chat as ai_chat_mod
    monkeypatch.setattr(ai_chat_mod, "get_recent_family_chat_history",
                        lambda tg, fid: [])

    from src.handlers.ai_chat import _ask_ai
    state = {"state": "ai_chat_mode", "data": json.dumps({"family_id": 10})}
    _ask_ai(user_id=123, question="Первый Q", lang='ru', state=state)

    assert captured['ai_calls'][0]['prev_messages'] == []
