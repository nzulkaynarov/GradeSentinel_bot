"""Security PR-C / S1: IDOR при применении промокода.

До фикса `callback_promo_apply` парсил family_id прямо из callback_data и
звал `_apply_promo_to_family` без проверки членства в семье → crafted callback
`sub_promo_apply_<чужой_family_id>_<CODE>` продлевал чужую подписку и жёг
max_uses промо. Фикс: гейт `_check_user_can_pay_for_family` в callback'е +
defense-in-depth (is_member_of_family / admin) в `_apply_promo_to_family`.

Тесты бьют по слою defense-in-depth (`_apply_promo_to_family` — денежный путь):
  - чужой family_id отклоняется: слот промо цел, подписка не тронута, платежа нет;
  - свой family_id по-прежнему работает (регрессия happy-path).
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if ":" not in os.environ.get("BOT_TOKEN", ""):
    os.environ["BOT_TOKEN"] = "12345:test-token-for-handler-import"
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")

import pytest

import src.database_manager as dbm
from src.db.connection import get_db_connection


def _make_family(name="Fam"):
    return dbm.add_family(name)


def _make_parent(phone, tg_id, fio="P", role="senior"):
    pid = dbm.add_parent(fio, phone, role=role)
    dbm.update_parent_telegram_id(phone, tg_id)
    return pid


def _payment_rows(family_id):
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT plan FROM payments WHERE family_id = %s ORDER BY id",
            (family_id,),
        )
        return [dict(r) for r in cur.fetchall()]


@pytest.fixture
def sub_mod(monkeypatch):
    """handlers.subscription с заглушенными внешними эффектами (bot/notify)."""
    from src.handlers import subscription as mod

    calls = {"send_content": []}
    monkeypatch.setattr(mod, "send_content",
                        lambda *a, **k: calls["send_content"].append((a, k)))
    monkeypatch.setattr(mod, "_notify_family_about_subscription",
                        lambda *a, **k: None)
    monkeypatch.setattr(mod, "bot", MagicMock())
    mod._test_calls = calls
    yield mod
    if hasattr(mod, "_test_calls"):
        del mod._test_calls


# ─────────────────────── S1: IDOR closed ───────────────────────

def test_promo_foreign_family_rejected(temp_db, sub_mod):
    """Атакующий (член своей семьи) НЕ может применить промо к чужой семье."""
    victim_fam = _make_family("Victim")
    attacker_fam = _make_family("Attacker")
    attacker_pid = _make_parent("998900020001", 800001)
    dbm.link_parent_to_family(attacker_fam, attacker_pid)

    dbm.create_promo_code("HACK1", "monthly", free_months=3, max_uses=1)
    promo = dbm.get_promo_code("HACK1")

    # Атакующий целится в victim_fam, где он НЕ состоит.
    sub_mod._apply_promo_to_family(800001, victim_fam, promo, "ru")

    # Подписка жертвы не тронута, платёж не создан.
    assert _payment_rows(victim_fam) == []
    assert dbm.get_family_subscription(victim_fam)["subscription_end"] is None
    # Слот промо не потрачен — код всё ещё пригоден.
    assert dbm.use_promo_code("HACK1") is True


def test_promo_own_family_still_works(temp_db, sub_mod):
    """Регрессия: член своей семьи применяет промо как раньше."""
    fam = _make_family("Mine")
    pid = _make_parent("998900020002", 800002)
    dbm.link_parent_to_family(fam, pid)

    dbm.create_promo_code("GIFT3", "monthly", free_months=2, max_uses=1)
    promo = dbm.get_promo_code("GIFT3")

    sub_mod._apply_promo_to_family(800002, fam, promo, "ru")

    rows = _payment_rows(fam)
    assert len(rows) == 1
    assert rows[0]["plan"] == "promo_GIFT3"
    assert dbm.get_family_subscription(fam)["subscription_end"] is not None


def test_promo_admin_can_apply_to_any_family(temp_db, sub_mod):
    """Админ обходит проверку членства (как в платёжных путях)."""
    fam = _make_family("SomeFam")
    _make_parent("998900020003", 800003, role="admin")

    dbm.create_promo_code("ADM1", "monthly", free_months=1, max_uses=1)
    promo = dbm.get_promo_code("ADM1")

    sub_mod._apply_promo_to_family(800003, fam, promo, "ru")

    assert len(_payment_rows(fam)) == 1
    assert dbm.get_family_subscription(fam)["subscription_end"] is not None


# ─────────────────────── S3: rate-limit ввода промокода ───────────────────────

def test_promo_input_rate_limited(temp_db, sub_mod, monkeypatch):
    """N+1-й ввод промокода за окно отклоняется без обработки кода."""
    calls = {"applied": 0}
    monkeypatch.setattr(sub_mod, "_apply_promo_to_family",
                        lambda *a, **k: calls.__setitem__("applied", calls["applied"] + 1))
    # Промо валидный, семья одна — без rate-limit код бы применился.
    fam = _make_family("RLFam")
    pid = _make_parent("998900020004", 800004)
    dbm.link_parent_to_family(fam, pid)
    dbm.create_promo_code("RL1", "monthly", free_months=1, max_uses=5)

    monkeypatch.setattr(sub_mod, "is_rate_limited", lambda uid: True)

    msg = SimpleNamespace(text="RL1", chat=SimpleNamespace(id=800004))
    sub_mod._process_promo_code(msg)

    assert calls["applied"] == 0
    # юзеру ушло «слишком часто», код не применён
    assert sub_mod._test_calls["send_content"]


def test_promo_input_not_rate_limited_processes(temp_db, sub_mod, monkeypatch):
    """При отсутствии троттлинга код обрабатывается нормально (регрессия)."""
    calls = {"applied": 0}
    monkeypatch.setattr(sub_mod, "_apply_promo_to_family",
                        lambda *a, **k: calls.__setitem__("applied", calls["applied"] + 1))
    fam = _make_family("OKFam")
    pid = _make_parent("998900020005", 800005)
    dbm.link_parent_to_family(fam, pid)
    dbm.create_promo_code("OK1", "monthly", free_months=1, max_uses=5)

    monkeypatch.setattr(sub_mod, "is_rate_limited", lambda uid: False)

    msg = SimpleNamespace(text="OK1", chat=SimpleNamespace(id=800005))
    sub_mod._process_promo_code(msg)

    assert calls["applied"] == 1
