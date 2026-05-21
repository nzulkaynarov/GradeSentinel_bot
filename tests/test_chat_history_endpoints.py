"""Тесты для /api/chat/history и /api/chat/clear endpoints (PR_H2).

Endpoint'ы существовали с PR_D R6, но не были покрыты integration-тестами.
PR_H2 добавляет UI рендер истории в webapp dashboard — добавляем тесты
чтобы случайное изменение auth/contract'а endpoint'ов не сломало UI silently.
"""
import os
import sys
from unittest.mock import patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")

import src.database_manager as dbm
from webapp.app import app


@pytest.fixture
def seeded_chat(temp_db):
    """Семья с подпиской + ученик + 4 сообщения в чате (2 turn'а)."""
    head_id = dbm.add_parent("Mom", "998900000111", role='senior')
    dbm.update_parent_telegram_id("998900000111", 111111)
    fam_id = dbm.add_family("F-chat")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    student_id = dbm.add_student("Kid", "ss-chat")
    dbm.link_student_to_family(fam_id, student_id)

    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)).strftime('%Y-%m-%d')
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "UPDATE families SET subscription_end = ? WHERE id = ?",
            (future, fam_id),
        )

    # Сохраняем 2 turn'а
    dbm.save_chat_message(111111, student_id, 'user', 'Как дела у ребёнка?')
    dbm.save_chat_message(111111, student_id, 'assistant', 'В целом хорошо.')
    dbm.save_chat_message(111111, student_id, 'user', 'А по математике?')
    dbm.save_chat_message(111111, student_id, 'assistant', 'Есть тройка.')

    return {"student_id": student_id, "tg_id": 111111, "family_id": fam_id}


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_history_endpoint_returns_chronological_messages(client, seeded_chat):
    """GET /api/chat/history/<id> → JSON {messages: [...]} chronologically."""
    info = seeded_chat
    with patch("webapp.app._authorize_student_access", return_value=info["tg_id"]):
        resp = client.get(f"/api/chat/history/{info['student_id']}")

    assert resp.status_code == 200
    body = resp.get_json()
    assert "messages" in body
    msgs = body["messages"]
    assert len(msgs) == 4
    # Chronological — oldest first
    assert msgs[0]['role'] == 'user'
    assert msgs[0]['content'] == 'Как дела у ребёнка?'
    assert msgs[1]['role'] == 'assistant'
    assert msgs[3]['content'] == 'Есть тройка.'


def test_history_endpoint_empty_for_new_student(client, seeded_chat):
    """Если в чате не было сообщений — возвращаем messages: []."""
    info = seeded_chat
    # Очищаем
    dbm.clear_chat_history(info['tg_id'], info['student_id'])
    with patch("webapp.app._authorize_student_access", return_value=info["tg_id"]):
        resp = client.get(f"/api/chat/history/{info['student_id']}")

    assert resp.status_code == 200
    assert resp.get_json() == {"messages": []}


def test_history_endpoint_isolated_per_telegram_id(client, seeded_chat):
    """Другой telegram_id (с тем же student_id) НЕ видит чужую историю.

    auth-уровень проверки делается в _authorize_student_access — но как
    safety net проверяем что get_recent_chat_history тоже фильтрует по tg_id."""
    info = seeded_chat
    # Эмулируем что другой родитель (другой tg_id) запросил тот же student
    OTHER_TG = 222222
    with patch("webapp.app._authorize_student_access", return_value=OTHER_TG):
        resp = client.get(f"/api/chat/history/{info['student_id']}")

    assert resp.status_code == 200
    body = resp.get_json()
    # Этот tg_id ничего не писал — должен видеть пусто
    assert body == {"messages": []}


def test_clear_endpoint_deletes_history(client, seeded_chat):
    """POST /api/chat/clear/<id> → удаляет все сообщения для (tg_id, student_id)."""
    info = seeded_chat
    # Sanity: до очистки 4 сообщения
    assert len(dbm.get_recent_chat_history(info['tg_id'], info['student_id'])) == 4

    with patch("webapp.app._authorize_student_access", return_value=info["tg_id"]):
        resp = client.post(f"/api/chat/clear/{info['student_id']}")

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    # История пуста
    assert dbm.get_recent_chat_history(info['tg_id'], info['student_id']) == []


def test_clear_endpoint_only_affects_caller(client, seeded_chat):
    """Clear от чужого tg_id НЕ удаляет нашу историю (auth scope)."""
    info = seeded_chat
    OTHER_TG = 222222
    with patch("webapp.app._authorize_student_access", return_value=OTHER_TG):
        resp = client.post(f"/api/chat/clear/{info['student_id']}")

    assert resp.status_code == 200
    # История owner'а нетронута
    assert len(dbm.get_recent_chat_history(info['tg_id'], info['student_id'])) == 4
