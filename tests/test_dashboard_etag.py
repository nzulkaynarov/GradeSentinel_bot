"""ETag для /api/dashboard — 304 Not Modified при unchanged watermark.

Проверяем:
- первый запрос возвращает 200 + ETag
- повторный с правильным If-None-Match → 304 без тела
- после INSERT новой оценки ETag меняется → клиент получит 200
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
os.environ.setdefault("DATABASE_PATH", "/tmp/gs_test_etag.db")

import src.database_manager as dbm
from webapp.app import app


@pytest.fixture
def seeded_student(temp_db):
    """Создаёт активную семью с одним учеником и одной оценкой."""
    head_id = dbm.add_parent("Head", "998900000333", role='senior')
    dbm.update_parent_telegram_id("998900000333", 333333)
    fam_id = dbm.add_family("F-etag")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    student_id = dbm.add_student("Kid", "ss-etag")
    dbm.link_student_to_family(fam_id, student_id)
    # Активная подписка
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)).strftime('%Y-%m-%d')
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "UPDATE families SET subscription_end = ? WHERE id = ?",
            (future, fam_id),
        )
    # Одна оценка
    today = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date().isoformat()
    dbm.add_grade(student_id, "Алгебра", 5.0, "5",
                  f"Сегодня!Алгебра:{today}", grade_date=today)
    return {"student_id": student_id, "tg_id": 333333}


@pytest.fixture
def client():
    """Flask test client."""
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_first_request_returns_etag(client, seeded_student):
    """Первый запрос → 200 + заголовок ETag."""
    info = seeded_student
    with patch("webapp.app._authorize_student_access", return_value=info["tg_id"]):
        resp = client.get(f"/api/dashboard/{info['student_id']}?days=7")
    assert resp.status_code == 200
    assert "ETag" in resp.headers
    assert resp.headers["ETag"].startswith('"')
    assert resp.headers["ETag"].endswith('"')
    # JSON ответ — содержит summary
    body = resp.get_json()
    assert "summary" in body


def test_matching_if_none_match_returns_304(client, seeded_student):
    """Повторный запрос с актуальным If-None-Match → 304, пустое тело."""
    info = seeded_student
    with patch("webapp.app._authorize_student_access", return_value=info["tg_id"]):
        first = client.get(f"/api/dashboard/{info['student_id']}?days=7")
        etag = first.headers["ETag"]

        second = client.get(
            f"/api/dashboard/{info['student_id']}?days=7",
            headers={"If-None-Match": etag},
        )
    assert second.status_code == 304
    assert second.data == b""
    # ETag всё ещё в ответе (клиент может его обновить)
    assert second.headers.get("ETag") == etag


def test_etag_changes_after_new_grade(client, seeded_student):
    """После INSERT новой оценки watermark меняется → ETag должен поменяться."""
    info = seeded_student
    with patch("webapp.app._authorize_student_access", return_value=info["tg_id"]):
        first = client.get(f"/api/dashboard/{info['student_id']}?days=7")
        etag_v1 = first.headers["ETag"]

    # Новая оценка
    from datetime import datetime, timedelta, timezone
    today = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date().isoformat()
    dbm.add_grade(info["student_id"], "Физика", 4.0, "4",
                  f"Сегодня!Физика:{today}", grade_date=today)

    with patch("webapp.app._authorize_student_access", return_value=info["tg_id"]):
        second = client.get(f"/api/dashboard/{info['student_id']}?days=7")
        etag_v2 = second.headers["ETag"]

    assert etag_v1 != etag_v2, "ETag должен меняться после INSERT новой оценки"
    assert second.status_code == 200


def test_different_days_different_etag(client, seeded_student):
    """Один и тот же student с разными ?days получает разные ETag."""
    info = seeded_student
    with patch("webapp.app._authorize_student_access", return_value=info["tg_id"]):
        r7 = client.get(f"/api/dashboard/{info['student_id']}?days=7")
        r30 = client.get(f"/api/dashboard/{info['student_id']}?days=30")
    assert r7.headers["ETag"] != r30.headers["ETag"]


def test_stale_if_none_match_returns_200(client, seeded_student):
    """Если клиент шлёт устаревший ETag — получает 200 (не 304)."""
    info = seeded_student
    with patch("webapp.app._authorize_student_access", return_value=info["tg_id"]):
        resp = client.get(
            f"/api/dashboard/{info['student_id']}?days=7",
            headers={"If-None-Match": '"deadbeefdeadbeef"'},
        )
    assert resp.status_code == 200
    assert "ETag" in resp.headers
