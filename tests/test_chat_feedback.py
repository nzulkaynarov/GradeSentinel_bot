"""Tests for AI chat feedback — 👍/👎 (PR_H3).

Покрываем:
- save_chat_message теперь возвращает id (backward-compat при ignore)
- save_feedback вставляет + UPSERT'ит при повторе
- get_feedback_for_message возвращает текущее состояние
- get_message_owner для авторизации
- POST /api/chat/feedback: валидация, авторизация, успех, UPSERT
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


# ─── DB layer ─────────────────────────────────────────────────

def test_save_chat_message_returns_id(temp_db):
    """save_chat_message теперь возвращает row id (нужен для feedback)."""
    msg_id = dbm.save_chat_message(123, 5, 'assistant', 'Ответ')
    assert isinstance(msg_id, int)
    assert msg_id > 0


def test_save_chat_message_returns_unique_ids(temp_db):
    id1 = dbm.save_chat_message(123, 5, 'user', 'Q1')
    id2 = dbm.save_chat_message(123, 5, 'assistant', 'A1')
    id3 = dbm.save_chat_message(123, 5, 'user', 'Q2')
    assert len({id1, id2, id3}) == 3


def test_save_feedback_records_positive(temp_db):
    msg_id = dbm.save_chat_message(123, 5, 'assistant', 'A')
    dbm.save_feedback(msg_id, 123, 1)
    fb = dbm.get_feedback_for_message(msg_id)
    assert fb is not None
    assert fb['rating'] == 1
    assert fb['comment'] is None


def test_save_feedback_records_negative(temp_db):
    msg_id = dbm.save_chat_message(123, 5, 'assistant', 'A')
    dbm.save_feedback(msg_id, 123, -1)
    assert dbm.get_feedback_for_message(msg_id)['rating'] == -1


def test_save_feedback_upserts_on_toggle(temp_db):
    """👍 → 👎 должен заменять rating, не создавать дубль."""
    msg_id = dbm.save_chat_message(123, 5, 'assistant', 'A')
    dbm.save_feedback(msg_id, 123, 1)
    dbm.save_feedback(msg_id, 123, -1)
    fb = dbm.get_feedback_for_message(msg_id)
    assert fb['rating'] == -1

    # Sanity: только одна строка в таблице
    with dbm.get_db_connection() as conn:
        count = conn.cursor().execute(
            'SELECT COUNT(*) as c FROM ai_chat_feedback WHERE message_id = %s',
            (msg_id,)).fetchone()['c']
    assert count == 1


def test_save_feedback_with_comment(temp_db):
    msg_id = dbm.save_chat_message(123, 5, 'assistant', 'A')
    dbm.save_feedback(msg_id, 123, -1, comment="Не точно")
    fb = dbm.get_feedback_for_message(msg_id)
    assert fb['comment'] == "Не точно"


@pytest.mark.parametrize("bad_rating", [0, 2, -2, 99, None])
def test_save_feedback_rejects_invalid_rating(temp_db, bad_rating):
    msg_id = dbm.save_chat_message(123, 5, 'assistant', 'A')
    with pytest.raises(ValueError):
        dbm.save_feedback(msg_id, 123, bad_rating)


def test_get_feedback_returns_none_for_unrated(temp_db):
    msg_id = dbm.save_chat_message(123, 5, 'assistant', 'A')
    assert dbm.get_feedback_for_message(msg_id) is None


def test_get_message_owner(temp_db):
    msg_id = dbm.save_chat_message(99999, 5, 'assistant', 'A')
    assert dbm.get_message_owner(msg_id) == 99999


def test_get_message_owner_returns_none_for_missing(temp_db):
    assert dbm.get_message_owner(99999999) is None


def test_clear_chat_history_cascades_feedback(temp_db):
    """ON DELETE CASCADE: при clear_chat_history feedback тоже чистится
    (FK constraint в схеме). Иначе orphan feedback'и остаются висеть."""
    msg_id = dbm.save_chat_message(123, 5, 'assistant', 'A')
    dbm.save_feedback(msg_id, 123, 1)

    # Sanity
    assert dbm.get_feedback_for_message(msg_id) is not None

    dbm.clear_chat_history(123, 5)
    # ai_chat_messages.id ушёл → CASCADE удалил feedback
    assert dbm.get_feedback_for_message(msg_id) is None


# ─── HTTP endpoint ────────────────────────────────────────────

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def seeded_msg(temp_db):
    """Создаёт user+seed для теста endpoint: ученик с подпиской + assistant msg."""
    head_id = dbm.add_parent("Mom", "998900000222", role='senior')
    dbm.update_parent_telegram_id("998900000222", 222222)
    fam_id = dbm.add_family("F-fb")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    student_id = dbm.add_student("Kid", "ss-fb")
    dbm.link_student_to_family(fam_id, student_id)

    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)).strftime('%Y-%m-%d')
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "UPDATE families SET subscription_end = %s WHERE id = %s",
            (future, fam_id),
        )

    msg_id = dbm.save_chat_message(222222, student_id, 'assistant', 'AI ответ')
    return {"tg_id": 222222, "student_id": student_id, "msg_id": msg_id}


def test_feedback_endpoint_saves_positive(client, seeded_msg):
    info = seeded_msg
    with patch("webapp.app._get_authenticated_user",
                return_value={"telegram_id": info["tg_id"]}):
        resp = client.post("/api/chat/feedback", json={
            "message_id": info["msg_id"],
            "rating": 1,
        })
    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert dbm.get_feedback_for_message(info["msg_id"])['rating'] == 1


def test_feedback_endpoint_saves_negative(client, seeded_msg):
    info = seeded_msg
    with patch("webapp.app._get_authenticated_user",
                return_value={"telegram_id": info["tg_id"]}):
        resp = client.post("/api/chat/feedback", json={
            "message_id": info["msg_id"],
            "rating": -1,
        })
    assert resp.status_code == 200
    assert dbm.get_feedback_for_message(info["msg_id"])['rating'] == -1


def test_feedback_endpoint_rejects_invalid_rating(client, seeded_msg):
    info = seeded_msg
    with patch("webapp.app._get_authenticated_user",
                return_value={"telegram_id": info["tg_id"]}):
        for bad in [0, 2, -2, 99, "yes"]:
            resp = client.post("/api/chat/feedback", json={
                "message_id": info["msg_id"],
                "rating": bad,
            })
            assert resp.status_code == 400, f"rating={bad!r} should 400"


def test_feedback_endpoint_rejects_missing_message_id(client, seeded_msg):
    info = seeded_msg
    with patch("webapp.app._get_authenticated_user",
                return_value={"telegram_id": info["tg_id"]}):
        resp = client.post("/api/chat/feedback", json={"rating": 1})
    assert resp.status_code == 400


def test_feedback_endpoint_404_for_missing_message(client, seeded_msg):
    info = seeded_msg
    with patch("webapp.app._get_authenticated_user",
                return_value={"telegram_id": info["tg_id"]}):
        resp = client.post("/api/chat/feedback", json={
            "message_id": 99999999,
            "rating": 1,
        })
    assert resp.status_code == 404


def test_feedback_endpoint_404_for_other_user_message(client, seeded_msg):
    """Чужой msg_id → 404 (не 403 чтобы не утечка ownership info)."""
    info = seeded_msg
    OTHER_TG = 333333
    with patch("webapp.app._get_authenticated_user",
                return_value={"telegram_id": OTHER_TG}):
        resp = client.post("/api/chat/feedback", json={
            "message_id": info["msg_id"],
            "rating": 1,
        })
    assert resp.status_code == 404
    # Sanity: feedback НЕ записан
    assert dbm.get_feedback_for_message(info["msg_id"]) is None


def test_feedback_endpoint_upserts_on_change(client, seeded_msg):
    info = seeded_msg
    with patch("webapp.app._get_authenticated_user",
                return_value={"telegram_id": info["tg_id"]}):
        client.post("/api/chat/feedback", json={"message_id": info["msg_id"], "rating": 1})
        resp = client.post("/api/chat/feedback", json={"message_id": info["msg_id"], "rating": -1})
    assert resp.status_code == 200
    assert dbm.get_feedback_for_message(info["msg_id"])['rating'] == -1


def test_feedback_endpoint_rejects_comment_too_long(client, seeded_msg):
    info = seeded_msg
    with patch("webapp.app._get_authenticated_user",
                return_value={"telegram_id": info["tg_id"]}):
        resp = client.post("/api/chat/feedback", json={
            "message_id": info["msg_id"],
            "rating": 1,
            "comment": "x" * 501,
        })
    assert resp.status_code == 400


# ─── /api/chat returns message_id ──────────────────────────────

def test_chat_response_includes_message_id(client, seeded_msg, monkeypatch):
    """После PR_H3 /api/chat в success-ответе содержит message_id (для UI feedback)."""
    info = seeded_msg
    monkeypatch.setattr("webapp.app._check_chat_rate_limit", lambda _: True)

    from datetime import datetime, timedelta, timezone
    # Mock AI
    monkeypatch.setattr("src.analytics_engine.answer_parent_question",
                        lambda **kw: "Мок-ответ")

    with patch("webapp.app._authorize_student_access", return_value=info["tg_id"]):
        resp = client.post("/api/chat", json={
            "student_id": info["student_id"],
            "question": "Тест",
        })

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["answer"] == "Мок-ответ"
    assert "message_id" in body
    assert isinstance(body["message_id"], int)
    assert body["message_id"] > 0
