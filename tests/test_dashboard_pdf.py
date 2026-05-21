"""Smoke-test PDF экспорта дашборда (Dashboard refresh).

Покрываем:
- pdf_export.build_dashboard_pdf возвращает валидные PDF bytes
- кириллица не крашит (DejaVu может не быть локально — fallback не падает)
- endpoint /api/dashboard/<id>/pdf отдаёт application/pdf
- auth: чужой student → 403
- Content-Disposition: attachment с filename
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


# ─── pdf_export unit ─────────────────────────────────────────

def test_build_dashboard_pdf_returns_bytes():
    from webapp.pdf_export import build_dashboard_pdf
    summary = {
        'current_avg': 4.3, 'delta': 0.2, 'status': 'improving',
        'problem_subjects': [{'name': 'Математика', 'avg': 3.0, 'count': 4}],
        'top_subjects': [{'name': 'Литература', 'avg': 5.0, 'count': 5}],
        'total_grades': 12,
    }
    by_subject = [{'name': 'Математика', 'avg': 3.0, 'count': 4}]
    recent = [{'subject': 'Математика', 'raw_text': '3', 'grade_date': '2026-05-21'}]

    pdf = build_dashboard_pdf('Заур (8 Orion)', summary, by_subject, recent, 'неделя', 'ru')
    assert isinstance(pdf, bytes)
    assert len(pdf) > 1000  # реальный PDF >1KB
    # PDF magic bytes
    assert pdf.startswith(b'%PDF-')


def test_build_dashboard_pdf_handles_empty_data():
    """Минимальные данные — не падаем."""
    from webapp.pdf_export import build_dashboard_pdf
    summary = {'current_avg': None, 'delta': None, 'status': 'stable',
               'problem_subjects': [], 'top_subjects': []}
    pdf = build_dashboard_pdf('Empty', summary, [], [], 'week', 'en')
    assert pdf.startswith(b'%PDF-')


@pytest.mark.parametrize("lang", ['ru', 'uz', 'en'])
def test_build_dashboard_pdf_all_languages(lang):
    """Каждый язык генерится без падений."""
    from webapp.pdf_export import build_dashboard_pdf
    summary = {'current_avg': 4.0, 'delta': 0.0, 'status': 'stable',
               'problem_subjects': [], 'top_subjects': []}
    pdf = build_dashboard_pdf('Test', summary, [], [], 'X', lang)
    assert len(pdf) > 1000


# ─── HTTP endpoint integration ───────────────────────────────

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def seeded_student(temp_db):
    """Семья с подпиской + ученик + несколько оценок."""
    head_id = dbm.add_parent("Mom", "998900000444", role='senior')
    dbm.update_parent_telegram_id("998900000444", 444444)
    fam_id = dbm.add_family("F-pdf")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    student_id = dbm.add_student("Kid PDF", "ss-pdf")
    dbm.link_student_to_family(fam_id, student_id)

    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)).strftime('%Y-%m-%d')
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "UPDATE families SET subscription_end = ? WHERE id = ?", (future, fam_id),
        )

    today = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date()
    for i in range(5):
        d = (today - timedelta(days=i)).isoformat()
        dbm.add_grade(student_id, "Алгебра", 4.0 + (i % 2), str(4 + (i % 2)),
                       f"r{i}", grade_date=d)

    return {"student_id": student_id, "tg_id": 444444}


def test_pdf_endpoint_returns_application_pdf(client, seeded_student):
    info = seeded_student
    with patch("webapp.app._authorize_student_access", return_value=info["tg_id"]):
        resp = client.get(f"/api/dashboard/{info['student_id']}/pdf?days=30")

    assert resp.status_code == 200
    assert resp.mimetype == 'application/pdf'
    assert resp.data.startswith(b'%PDF-')
    cd = resp.headers.get('Content-Disposition', '')
    assert 'attachment' in cd
    assert '.pdf' in cd


def test_pdf_endpoint_filename_contains_student_name(client, seeded_student):
    info = seeded_student
    with patch("webapp.app._authorize_student_access", return_value=info["tg_id"]):
        resp = client.get(f"/api/dashboard/{info['student_id']}/pdf")

    cd = resp.headers.get('Content-Disposition', '')
    # Latin-safe transform убрал кириллицу → "Kid_PDF" сохранилось
    assert 'Kid' in cd or 'PDF' in cd


def test_pdf_endpoint_filename_ascii_only_in_header(client, temp_db):
    """RFC 7230: HTTP headers должны быть ASCII. gunicorn отвергает
    Content-Disposition с кириллицей с 400. Hotfix: ascii-fallback
    в filename= + URL-encoded UTF-8 в filename*= (RFC 6266)."""
    # Создаём ученика с кириллическим именем
    import src.database_manager as dbm
    head_id = dbm.add_parent("Mom", "998900000555", role='senior')
    dbm.update_parent_telegram_id("998900000555", 555555)
    fam_id = dbm.add_family("F-cyr")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    sid = dbm.add_student("Зулькайнаров Заур (8 Orion)", "ss-cyr")
    dbm.link_student_to_family(fam_id, sid)
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)).strftime('%Y-%m-%d')
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "UPDATE families SET subscription_end = ? WHERE id = ?", (future, fam_id),
        )

    with patch("webapp.app._authorize_student_access", return_value=555555):
        resp = client.get(f"/api/dashboard/{sid}/pdf")

    assert resp.status_code == 200
    cd = resp.headers.get('Content-Disposition', '')
    # Header сам должен быть ASCII-only (никакой кириллицы)
    assert cd.encode('ascii', errors='strict')  # raises UnicodeEncodeError если non-ascii
    # Должны быть оба filename и filename*
    assert 'filename=' in cd
    assert "filename*=UTF-8''" in cd


def test_pdf_send_endpoint_calls_bot_send_document(client, seeded_student, monkeypatch):
    """POST /pdf/send → backend генерит PDF и шлёт через bot.send_document.
    Tests что endpoint вызывает send_document с правильными args."""
    import io
    info = seeded_student
    sent = []

    class FakeBot:
        def send_document(self, chat_id, doc, caption=None, visible_file_name=None):
            sent.append({
                'chat_id': chat_id,
                'doc_size': len(doc.read()) if hasattr(doc, 'read') else 0,
                'caption': caption,
                'visible_file_name': visible_file_name,
            })

    monkeypatch.setattr("webapp.app._get_webapp_bot", lambda: FakeBot())

    with patch("webapp.app._authorize_student_access", return_value=info["tg_id"]):
        resp = client.post(f"/api/dashboard/{info['student_id']}/pdf/send?days=7")

    assert resp.status_code == 200
    assert resp.get_json() == {"ok": True}
    assert len(sent) == 1
    assert sent[0]['chat_id'] == info['tg_id']
    assert sent[0]['doc_size'] > 1000  # реальный PDF >1KB
    assert sent[0]['caption']
    assert sent[0]['visible_file_name'].endswith('.pdf')


def test_pdf_send_endpoint_503_when_bot_unavailable(client, seeded_student, monkeypatch):
    """Если BOT_TOKEN не задан / _webapp_bot init failed → 503."""
    info = seeded_student
    monkeypatch.setattr("webapp.app._get_webapp_bot", lambda: None)

    with patch("webapp.app._authorize_student_access", return_value=info["tg_id"]):
        resp = client.post(f"/api/dashboard/{info['student_id']}/pdf/send")

    assert resp.status_code == 503
    assert resp.get_json()['error'] == 'bot_unavailable'


def test_pdf_send_endpoint_500_when_bot_fails(client, seeded_student, monkeypatch):
    """bot.send_document raise → 500 с error: send_failed."""
    info = seeded_student

    class FakeBot:
        def send_document(self, *a, **kw):
            raise RuntimeError("Telegram unreachable")

    monkeypatch.setattr("webapp.app._get_webapp_bot", lambda: FakeBot())

    with patch("webapp.app._authorize_student_access", return_value=info["tg_id"]):
        resp = client.post(f"/api/dashboard/{info['student_id']}/pdf/send")

    assert resp.status_code == 500
    assert resp.get_json()['error'] == 'send_failed'


def test_pdf_endpoint_404_for_other_student(client, seeded_student, temp_db):
    """Foreign student → 403 (auth_student_access защищает)."""
    info = seeded_student
    OTHER_TG = 555555
    # Эмулируем authorize которое возвращает other_tg но student остался seeded
    # — endpoint проверяет get_students_for_parent(other_tg) → student not in list → abort(403)
    with patch("webapp.app._authorize_student_access", return_value=OTHER_TG):
        resp = client.get(f"/api/dashboard/{info['student_id']}/pdf")
    assert resp.status_code == 403


# ─── locales sync проверка (наши новые ключи в 3 языках) ──────

def test_new_locale_keys_in_all_languages():
    """Новые ключи для action-bar должны быть в ru, uz, en."""
    import json
    new_keys = ['action_share', 'action_export_pdf', 'action_export_loading',
                'action_export_error', 'dashboard_ai_hint']
    for lang in ['ru', 'uz', 'en']:
        with open(f'webapp/static/locales/{lang}.json') as f:
            data = json.load(f)
        for key in new_keys:
            assert key in data, f"[{lang}] missing key: {key}"
            assert data[key].strip(), f"[{lang}] empty value for {key}"
