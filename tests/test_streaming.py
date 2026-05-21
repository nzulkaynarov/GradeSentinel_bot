"""Tests for streaming AI response (PR_H4, bot only).

MVP: stream_callback в answer_parent_question — для bot edit_message
прогрессии. Webapp SSE отложен на follow-up.

Покрываем:
- stream_callback вызывается с накопленным текстом
- без stream_callback — старое поведение (create, не stream)
- callback exception не ломает streaming
- bot handler корректно throttle'ит edit_message_text
"""
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "12345:test")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")


# ─── analytics_engine.answer_parent_question streaming ────────

class _FakeFinalMessage:
    """Эмулирует ответ stream.get_final_message() — нужен после tool_use loop'а
    в PR_E2 (analytics_engine проверяет stop_reason для continue/return)."""
    def __init__(self, text):
        self.stop_reason = 'end_turn'
        self.content = [type('TextBlock', (), {'type': 'text', 'text': text})()]


class _FakeStream:
    """Эмулирует Anthropic stream context manager."""
    def __init__(self, chunks):
        self._chunks = chunks
        self.text_stream = iter(chunks)
        self._joined = "".join(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get_final_message(self):
        return _FakeFinalMessage(self._joined)


def test_stream_callback_called_with_accumulated_text(monkeypatch):
    """callback получает РАСТУЩИЙ текст (не deltas)."""
    chunks = ["Привет", ", ", "родитель!"]
    received = []

    class FakeClient:
        class messages:
            @staticmethod
            def stream(**kwargs):
                return _FakeStream(chunks)

            @staticmethod
            def create(**kwargs):
                raise AssertionError("create should NOT be called when stream_callback set")

    monkeypatch.setattr("src.analytics_engine._get_client", lambda: FakeClient())

    from src.analytics_engine import answer_parent_question
    result = answer_parent_question(
        student_id=1, student_name="Test",
        grades=[{"subject": "X", "grade_value": 5.0, "raw_text": "5",
                 "grade_date": "2026-05-21"}],
        question="?", lang='ru',
        stream_callback=lambda txt: received.append(txt),
    )

    # Callback вызван 3 раза с растущим контекстом
    assert received == ["Привет", "Привет, ", "Привет, родитель!"]
    # Финальный результат — полный текст
    assert result == "Привет, родитель!"


def test_no_callback_uses_create_not_stream(monkeypatch):
    """Без stream_callback — старое поведение через client.messages.create."""

    class FakeMessage:
        content = [type('obj', (), {'text': 'ok'})()]

    class FakeClient:
        class messages:
            @staticmethod
            def stream(**kwargs):
                raise AssertionError("stream should NOT be called without callback")

            @staticmethod
            def create(**kwargs):
                return FakeMessage()

    monkeypatch.setattr("src.analytics_engine._get_client", lambda: FakeClient())

    from src.analytics_engine import answer_parent_question
    result = answer_parent_question(
        student_id=1, student_name="Test",
        grades=[{"subject": "X", "grade_value": 5.0, "raw_text": "5",
                 "grade_date": "2026-05-21"}],
        question="?", lang='ru',
    )
    assert result == 'ok'


def test_callback_exception_does_not_break_stream(monkeypatch):
    """callback raise — stream продолжается, финальный текст возвращается."""
    chunks = ["A", "BC", "DEF"]

    class FakeClient:
        class messages:
            @staticmethod
            def stream(**kwargs):
                return _FakeStream(chunks)

    monkeypatch.setattr("src.analytics_engine._get_client", lambda: FakeClient())

    def bad_callback(txt):
        raise RuntimeError("UI broken")

    from src.analytics_engine import answer_parent_question
    result = answer_parent_question(
        student_id=1, student_name="Test",
        grades=[{"subject": "X", "grade_value": 5.0, "raw_text": "5",
                 "grade_date": "2026-05-21"}],
        question="?", lang='ru',
        stream_callback=bad_callback,
    )
    assert result == "ABCDEF"


def test_empty_stream_returns_none(monkeypatch):
    """Stream без chunks → пустой текст → None."""

    class FakeClient:
        class messages:
            @staticmethod
            def stream(**kwargs):
                return _FakeStream([])

    monkeypatch.setattr("src.analytics_engine._get_client", lambda: FakeClient())

    from src.analytics_engine import answer_parent_question
    result = answer_parent_question(
        student_id=1, student_name="Test",
        grades=[{"subject": "X", "grade_value": 5.0, "raw_text": "5",
                 "grade_date": "2026-05-21"}],
        question="?", lang='ru',
        stream_callback=lambda _: None,
    )
    assert result is None


# ─── bot handler throttle ─────────────────────────────────────

def test_handler_streaming_throttles_edits(monkeypatch):
    """edit_message_text должен вызываться НЕ чаще 1 раза в 1.5с.
    Если stream выдал 10 чанков за 0.5с — должен быть 1 edit (после throttle).

    Plus финальный edit с feedback markup. Итого 1-2 edits всего, не 10."""
    import src.handlers.ai_chat as ai_chat_mod
    import src.analytics_engine as ae

    monkeypatch.setattr(ai_chat_mod, "get_students_for_parent",
                        lambda _: [{"id": 5, "fio": "X", "display_name": "X"}])
    monkeypatch.setattr(ai_chat_mod, "get_grade_history_for_student_all",
                        lambda *a, **kw: [])
    monkeypatch.setattr(ai_chat_mod, "get_user_lang", lambda _: 'ru')
    monkeypatch.setattr(ai_chat_mod, "get_recent_chat_history", lambda *a: [])
    saved = []
    monkeypatch.setattr(ai_chat_mod, "save_chat_message",
                        lambda *a, **kw: saved.append(a) or 42)
    monkeypatch.setattr(ai_chat_mod.bot, "send_chat_action",
                        lambda *a, **kw: None, raising=False)

    # send_message возвращает мокнутое сообщение с message_id
    class _SentMsg:
        message_id = 999
    monkeypatch.setattr(ai_chat_mod.bot, "send_message",
                        lambda *a, **kw: _SentMsg(), raising=False)

    edit_calls = []
    monkeypatch.setattr(ai_chat_mod.bot, "edit_message_text",
                        lambda *a, **kw: edit_calls.append(kw) or None,
                        raising=False)

    # Mock AI: имитирует 10 быстрых чанков
    def fake_ai(**kwargs):
        cb = kwargs.get('stream_callback')
        if cb:
            for i in range(10):
                cb(f"chunk{i} " * (i + 1))
        return "Финальный ответ"

    monkeypatch.setattr(ae, "answer_parent_question", fake_ai)

    import json
    state = {"state": "ai_chat_mode", "data": json.dumps({"student_id": 5})}
    from src.handlers.ai_chat import _ask_ai
    _ask_ai(user_id=123, question="?", lang='ru', state=state)

    # Throttle: streaming edits — 0 или 1 (всё за <1.5с). Плюс финальный edit.
    # Итого 1-2 вызова. Не 10+.
    assert 1 <= len(edit_calls) <= 2, (
        f"Expected 1-2 edits (throttle 1.5s + final), got {len(edit_calls)}"
    )
    # Финальный должен содержать «Финальный ответ» и markup
    final = edit_calls[-1]
    assert final.get('text') == "Финальный ответ"
    assert 'reply_markup' in final


def test_handler_streaming_falls_back_when_placeholder_send_fails(monkeypatch):
    """Если bot.send_message (placeholder) упал — stream_callback не передаётся,
    final отправляется через send_message (не edit)."""
    import src.handlers.ai_chat as ai_chat_mod
    import src.analytics_engine as ae

    monkeypatch.setattr(ai_chat_mod, "get_students_for_parent",
                        lambda _: [{"id": 5, "fio": "X", "display_name": "X"}])
    monkeypatch.setattr(ai_chat_mod, "get_grade_history_for_student_all",
                        lambda *a, **kw: [])
    monkeypatch.setattr(ai_chat_mod, "get_user_lang", lambda _: 'ru')
    monkeypatch.setattr(ai_chat_mod, "get_recent_chat_history", lambda *a: [])
    monkeypatch.setattr(ai_chat_mod, "save_chat_message",
                        lambda *a, **kw: 42)
    monkeypatch.setattr(ai_chat_mod.bot, "send_chat_action",
                        lambda *a, **kw: None, raising=False)

    # send_message: первый вызов (placeholder) raise; второй (fallback final) — ok
    send_calls = []

    def fake_send(*args, **kwargs):
        send_calls.append((args, kwargs))
        if len(send_calls) == 1:
            raise RuntimeError("placeholder send failed")
        class _M:
            message_id = 888
        return _M()

    monkeypatch.setattr(ai_chat_mod.bot, "send_message", fake_send, raising=False)
    monkeypatch.setattr(ai_chat_mod.bot, "edit_message_text",
                        lambda *a, **kw: None, raising=False)

    received_callback = {'was_none': None}

    def fake_ai(**kwargs):
        received_callback['was_none'] = kwargs.get('stream_callback') is None
        return "Ответ"

    monkeypatch.setattr(ae, "answer_parent_question", fake_ai)

    import json
    state = {"state": "ai_chat_mode", "data": json.dumps({"student_id": 5})}
    from src.handlers.ai_chat import _ask_ai
    _ask_ai(user_id=123, question="?", lang='ru', state=state)

    # stream_callback должен быть None (placeholder упал)
    assert received_callback['was_none'] is True
    # send_message вызывался 2 раза: placeholder (raise) и final fallback
    assert len(send_calls) == 2
