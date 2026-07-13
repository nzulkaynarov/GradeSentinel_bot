"""Tests for /api/chat endpoint — AI assistant с контекстом ученика.

Реальные API-вызовы к Anthropic мокаем — тесты проверяют:
- Validation (400 на пустой question, длинный question)
- Rate limit (429 после 5 запросов/минута)
- Контекст формирования (compact rendering)
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

from src.analytics_engine import _format_grades_context, _tashkent_today_str  # noqa: E402


# ─── Unit: формирование контекста для prompt'а ────────────────
def test_format_grades_context_empty():
    assert "пусто" in _format_grades_context([]).lower()


def test_format_grades_context_renders_compact_lines():
    grades = [
        {"subject": "Алгебра", "grade_value": 4.0, "raw_text": "4",
         "grade_date": "2026-05-15", "date_added": "2026-05-15 10:00:00"},
        {"subject": "Литература", "grade_value": 5.0, "raw_text": "5",
         "grade_date": "2026-05-14", "date_added": "2026-05-14 11:00:00"},
    ]
    out = _format_grades_context(grades)
    assert "Алгебра" in out
    assert "Литература" in out
    assert "2026-05-15" in out
    assert "2026-05-14" in out


def test_format_grades_context_truncates_long_list():
    grades = [{"subject": f"Subj{i}", "grade_value": 4.0, "raw_text": "4",
               "grade_date": "2026-05-15"} for i in range(200)]
    out = _format_grades_context(grades, max_count=10)
    # Только 10 строк
    assert out.count("Subj") == 10


def test_format_grades_context_handles_missing_grade_date():
    """Fallback на date_added если grade_date нет."""
    grades = [{"subject": "Алгебра", "grade_value": 4.0, "raw_text": "4",
               "date_added": "2026-05-15 14:30:00"}]
    out = _format_grades_context(grades)
    assert "2026-05-15" in out


def test_format_grades_context_caps_per_student_not_total():
    """B21: cap на РЕБЁНКА, не суммарный. У семьи с несколькими детьми
    оценки КАЖДОГО ребёнка должны попадать в контекст — старый grades[:N]
    молча дропал детей за общим лимитом."""
    grades = []
    for i in range(5):
        grades.append({"subject": f"A{i}", "raw_text": "5",
                       "grade_date": "2026-05-21", "student_name": "Заур"})
    for i in range(5):
        grades.append({"subject": f"B{i}", "raw_text": "4",
                       "grade_date": "2026-05-20", "student_name": "Лола"})
    out = _format_grades_context(grades, max_count=3)
    # Каждый ребёнок capped до 3 — оба присутствуют (6 строк), НЕ суммарно 3.
    # Старая логика (grades[:3]) дала бы 3 строки только Заура, Лола исчезла бы.
    assert out.count("[Заур]") == 3
    assert out.count("[Лола]") == 3


def test_tashkent_today_str_format():
    """Регрессия: _tashkent_today_str возвращает ISO дату YYYY-MM-DD по UTC+5.

    Это критично для AI prompt'а — без сегодняшней даты AI не понимает
    relative expressions «прошлый месяц», «недавно» и т.д.
    (см. инцидент когда AI на «Сравни с прошлым месяцем» отказался отвечать
    с фразой «нет информации о прошлом месяце»)."""
    import re
    today = _tashkent_today_str()
    assert re.match(r'^\d{4}-\d{2}-\d{2}$', today)


def test_user_message_includes_today_date(monkeypatch):
    """Регрессия: prompt начинается с «Сегодня: YYYY-MM-DD» чтобы AI мог
    вычислять relative dates."""
    from src.analytics_engine import answer_parent_question
    captured = {}

    class FakeMessage:
        def __init__(self):
            self.content = [type('obj', (), {'text': 'ok'})()]

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                captured['messages'] = kwargs.get('messages')
                return FakeMessage()

    monkeypatch.setattr("src.analytics_engine._get_client", lambda: FakeClient())

    answer_parent_question(
        student_id=1,
        student_name="Test",
        grades=[{"subject": "Алгебра", "grade_value": 5.0, "raw_text": "5",
                  "grade_date": "2026-05-21"}],
        question="Сравни с прошлым месяцем",
        lang='ru',
    )
    assert 'messages' in captured
    # B16: первое user-сообщение теперь content-блоки (стабильный контекст с
    # cache_control + волатильный вопрос), а не строка.
    first_user = _flatten_content(captured['messages'][0]['content'])
    assert "Сегодня:" in first_user
    assert _tashkent_today_str() in first_user


def test_today_date_in_first_system_block(monkeypatch):
    """Дата вынесена в ПЕРВЫЙ system-блок (высокая заметность) — при большом
    grade-контексте, заканчивающемся месяцы назад, Haiku иначе терял дату,
    зарытую наверху user-контекста, и галлюцинировал «сегодня» у конца данных.
    Большой system_prompt при этом остаётся отдельным кэшируемым блоком."""
    from src.analytics_engine import answer_parent_question, _tashkent_today_str
    captured = {}

    class FakeMessage:
        def __init__(self):
            self.content = [type('obj', (), {'text': 'ok'})()]
            self.stop_reason = 'end_turn'

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                captured['system'] = kwargs.get('system')
                return FakeMessage()

    monkeypatch.setattr("src.analytics_engine._get_client", lambda: FakeClient())

    answer_parent_question(
        student_id=1, student_name="Test",
        grades=[{"subject": "Алгебра", "grade_value": 5.0, "raw_text": "5",
                 "grade_date": "2026-05-21"}],
        question="какое сегодня число", lang='ru',
    )
    system = captured['system']
    assert isinstance(system, list) and len(system) >= 2
    # Первый блок — дата, высокая заметность, без cache_control (крошечный,
    # меняется ежедневно), содержит именно сегодняшнюю дату.
    assert _tashkent_today_str() in system[0]['text']
    assert 'cache_control' not in system[0]
    # Большой промпт остаётся отдельным кэшируемым блоком.
    assert system[1].get('cache_control', {}).get('type') == 'ephemeral'


def _flatten_content(content):
    """Склеивает text из content-блоков (или возвращает строку как есть)."""
    if isinstance(content, str):
        return content
    return "\n".join(
        b.get("text", "") for b in content if isinstance(b, dict)
    )


# ─── Integration: rate limit endpoint ─────────────────────────
def test_rate_limit_blocks_after_5_requests():
    from webapp.app import _check_chat_rate_limit, _chat_rate_limit
    _chat_rate_limit.clear()

    tg_id = 99999
    for i in range(5):
        assert _check_chat_rate_limit(tg_id), f"Request {i+1} should pass"
    assert not _check_chat_rate_limit(tg_id), "6th request should be blocked"


def test_rate_limit_isolated_per_user():
    from webapp.app import _check_chat_rate_limit, _chat_rate_limit
    _chat_rate_limit.clear()

    for i in range(5):
        assert _check_chat_rate_limit(10001)
    # Другой юзер не затронут
    assert _check_chat_rate_limit(10002)


def test_rate_limit_clears_after_window(monkeypatch):
    from webapp.app import _check_chat_rate_limit, _chat_rate_limit
    _chat_rate_limit.clear()

    import time as _time
    base = _time.time()

    fake_time = [base]
    monkeypatch.setattr("time.time", lambda: fake_time[0])

    for _ in range(5):
        _check_chat_rate_limit(20001)
    assert not _check_chat_rate_limit(20001)

    # Через 61 секунду — должно опять пройти
    fake_time[0] = base + 61
    assert _check_chat_rate_limit(20001)


# ─── B15: санитайз истории (orphan-user / leading-assistant) ──────
def _capturing_client(captured, text="ok", stop_reason="end_turn"):
    """FakeClient, захватывающий messages/system/max_tokens из вызова create."""
    class FakeMessage:
        def __init__(self):
            self.stop_reason = stop_reason
            self.content = [type('Blk', (), {'type': 'text', 'text': text})()]

    class FakeClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                captured['messages'] = kwargs.get('messages')
                captured['system'] = kwargs.get('system')
                captured['max_tokens'] = kwargs.get('max_tokens')
                return FakeMessage()
    return FakeClient()


def _roles(messages):
    return [m['role'] for m in messages]


def test_history_with_trailing_orphan_user_builds_valid_messages(monkeypatch):
    """B15: история заканчивается осиротевшим user (AI упал, assistant не
    сохранён). Следующий вопрос собирает ВАЛИДНЫЙ messages_array — первое=user,
    строгое чередование, без ДВУХ user подряд, без краха."""
    captured = {}
    monkeypatch.setattr("src.analytics_engine._get_client",
                        lambda: _capturing_client(captured))
    from src.analytics_engine import answer_parent_question

    prev = [
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"},
        {"role": "user", "content": "Q2 orphan"},  # AI упал → orphan
    ]
    result = answer_parent_question(
        student_id=1, student_name="T",
        grades=[{"subject": "X", "raw_text": "5", "grade_date": "2026-05-21"}],
        question="Q3", lang='ru', prev_messages=prev, family_id=10,
    )
    assert result == "ok"
    roles = _roles(captured['messages'])
    assert roles[0] == 'user', roles
    for a, b in zip(roles, roles[1:]):
        assert a != b, f"consecutive same-role: {roles}"


def test_history_starting_with_assistant_drops_leading(monkeypatch):
    """B15: окно истории начинается с assistant (обрезано посреди пары) →
    ведущие assistant отбрасываются, первое сообщение = user."""
    captured = {}
    monkeypatch.setattr("src.analytics_engine._get_client",
                        lambda: _capturing_client(captured))
    from src.analytics_engine import answer_parent_question

    prev = [
        {"role": "assistant", "content": "A0 leading"},
        {"role": "user", "content": "Q1"},
        {"role": "assistant", "content": "A1"},
    ]
    answer_parent_question(
        student_id=1, student_name="T",
        grades=[{"subject": "X", "raw_text": "5", "grade_date": "2026-05-21"}],
        question="Q2", lang='ru', prev_messages=prev, family_id=10,
    )
    msgs = captured['messages']
    roles = _roles(msgs)
    assert roles[0] == 'user', roles
    for a, b in zip(roles, roles[1:]):
        assert a != b, f"consecutive same-role: {roles}"
    # "A0 leading" не должно попасть в контекст (ведущий assistant отброшен)
    assert all(
        "A0 leading" not in _flatten_content(m['content']) for m in msgs
    )


# ─── B16: prompt caching markers ─────────────────────────────────
def test_prompt_caching_markers_present(monkeypatch):
    """B16: system идёт как список с cache_control; первый (стабильный) блок
    grade-контекста тоже помечен cache_control; max_tokens поднят до 1500."""
    captured = {}
    monkeypatch.setattr("src.analytics_engine._get_client",
                        lambda: _capturing_client(captured))
    from src.analytics_engine import answer_parent_question, _CHAT_MAX_TOKENS

    answer_parent_question(
        student_id=1, student_name="T",
        grades=[{"subject": "X", "raw_text": "5", "grade_date": "2026-05-21"}],
        question="?", lang='ru', family_id=10,
    )
    system = captured['system']
    assert isinstance(system, list)
    assert system[-1].get('cache_control') == {"type": "ephemeral"}

    first_content = captured['messages'][0]['content']
    assert isinstance(first_content, list)
    # Стабильный блок (контекст) кэшируется, волатильный (вопрос) — нет
    assert first_content[0].get('cache_control') == {"type": "ephemeral"}
    assert 'cache_control' not in first_content[1]

    assert captured['max_tokens'] == _CHAT_MAX_TOKENS == 1500


# ─── B20: обрезка по max_tokens ──────────────────────────────────
def test_max_tokens_truncation_appends_notice(monkeypatch):
    """B20: stop_reason='max_tokens' → к ответу добавляется пометка об обрезке,
    а не тихо сохраняется обрубок."""
    captured = {}
    monkeypatch.setattr(
        "src.analytics_engine._get_client",
        lambda: _capturing_client(captured, text="частичный ответ",
                                  stop_reason="max_tokens"))
    from src.analytics_engine import answer_parent_question, _CHAT_TRUNCATION_NOTICE

    result = answer_parent_question(
        student_id=1, student_name="T",
        grades=[{"subject": "X", "raw_text": "5", "grade_date": "2026-05-21"}],
        question="Подробно разбери всё", lang='ru', family_id=10,
    )
    assert result.startswith("частичный ответ")
    assert _CHAT_TRUNCATION_NOTICE['ru'].strip() in result


def test_normal_answer_has_no_truncation_notice(monkeypatch):
    """Регресс: обычный (end_turn) ответ НЕ получает пометку об обрезке."""
    captured = {}
    monkeypatch.setattr(
        "src.analytics_engine._get_client",
        lambda: _capturing_client(captured, text="полный ответ",
                                  stop_reason="end_turn"))
    from src.analytics_engine import answer_parent_question, _CHAT_TRUNCATION_NOTICE

    result = answer_parent_question(
        student_id=1, student_name="T",
        grades=[{"subject": "X", "raw_text": "5", "grade_date": "2026-05-21"}],
        question="?", lang='ru', family_id=10,
    )
    assert result == "полный ответ"
    assert _CHAT_TRUNCATION_NOTICE['ru'].strip() not in result
