"""Tests for AI conversation history (PR_D R6).

Multi-turn чат: user задаёт вопрос → AI отвечает, оба сохраняются. На
следующем turn'е prev_messages передаётся в Anthropic API, что даёт
follow-up без потери контекста.
"""
import src.database_manager as dbm


# ─── Unit: save / load / clear ────────────────────────────────────────
def test_empty_history_returns_empty_list(temp_db):
    assert dbm.get_recent_chat_history(999, 1) == []


def test_save_and_retrieve_in_order(temp_db):
    """Сохранили 3 сообщения — получили в хронологическом порядке."""
    dbm.save_chat_message(123, 5, 'user', "Привет, как дела у ребёнка?")
    dbm.save_chat_message(123, 5, 'assistant', "В целом хорошо, есть пятёрки.")
    dbm.save_chat_message(123, 5, 'user', "А по математике?")

    history = dbm.get_recent_chat_history(123, 5)
    assert len(history) == 3
    assert history[0]['role'] == 'user'
    assert history[0]['content'] == "Привет, как дела у ребёнка?"
    assert history[1]['role'] == 'assistant'
    assert history[2]['content'] == "А по математике?"


def test_history_isolated_by_student(temp_db):
    """Разные ученики — разные ветки беседы."""
    dbm.save_chat_message(123, 5, 'user', "Про Заура")
    dbm.save_chat_message(123, 7, 'user', "Про Умарбека")

    h5 = dbm.get_recent_chat_history(123, 5)
    h7 = dbm.get_recent_chat_history(123, 7)
    assert len(h5) == 1
    assert len(h7) == 1
    assert h5[0]['content'] == "Про Заура"
    assert h7[0]['content'] == "Про Умарбека"


def test_history_isolated_by_parent(temp_db):
    """Разные родители — изолированные истории даже про одного ребёнка."""
    dbm.save_chat_message(100, 5, 'user', "Мама спрашивает")
    dbm.save_chat_message(200, 5, 'user', "Папа спрашивает")

    assert dbm.get_recent_chat_history(100, 5)[0]['content'] == "Мама спрашивает"
    assert dbm.get_recent_chat_history(200, 5)[0]['content'] == "Папа спрашивает"


def test_history_limit_respected(temp_db):
    """Получаем последние N сообщений когда их больше limit."""
    for i in range(30):
        dbm.save_chat_message(123, 5, 'user', f"msg{i}")

    history = dbm.get_recent_chat_history(123, 5, limit=5)
    assert len(history) == 5
    # Последние 5 (msg25-msg29) в хронологическом порядке
    assert [m['content'] for m in history] == ["msg25", "msg26", "msg27", "msg28", "msg29"]


def test_clear_chat_history(temp_db):
    dbm.save_chat_message(123, 5, 'user', "a")
    dbm.save_chat_message(123, 5, 'assistant', "b")
    assert len(dbm.get_recent_chat_history(123, 5)) == 2

    dbm.clear_chat_history(123, 5)
    assert dbm.get_recent_chat_history(123, 5) == []


def test_clear_does_not_affect_other_students(temp_db):
    dbm.save_chat_message(123, 5, 'user', "Заур")
    dbm.save_chat_message(123, 7, 'user', "Умарбек")

    dbm.clear_chat_history(123, 5)
    assert dbm.get_recent_chat_history(123, 5) == []
    assert dbm.get_recent_chat_history(123, 7) == [{'role': 'user', 'content': 'Умарбек',
                                                     'created_at': dbm.get_recent_chat_history(123, 7)[0]['created_at']}]


def test_invalid_role_raises(temp_db):
    """Sanity: только 'user' и 'assistant' allowed."""
    import pytest
    with pytest.raises(ValueError):
        dbm.save_chat_message(123, 5, 'system', "test")
