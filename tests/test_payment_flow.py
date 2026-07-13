"""Payment flow: атомарность и устойчивость (PR-B).

Покрывает:
  B1 — handle_successful_payment: happy-path, paid_by=NULL (payer без parents),
       malformed payload, идемпотентность по charge_id (дубль не двоит подписку).
  B6 — промо: применение начисляет ровно раз; исчерпанный max_uses → use_promo_code
       False и НЕ начисляет.
  B7 — extend_subscription: первая подписка из NULL при двух вызовах аддитивна.
  Миграция 0002 — применяется (conftest делает alembic upgrade head): payments
  пишутся с NULL paid_by и с UNIQUE(charge_id) без падения схемы.

handlers/* импортируют src.bot_instance, который требует валидный BOT_TOKEN.
"""
import os
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# bot_instance валидирует ":" в BOT_TOKEN — force-set до импорта handlers.
if ":" not in os.environ.get("BOT_TOKEN", ""):
    os.environ["BOT_TOKEN"] = "12345:test-token-for-handler-import"
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")

import pytest

import src.database_manager as dbm
from src.db.connection import get_db_connection


# ─────────────────────────── helpers ───────────────────────────

def _now_naive_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_family(name="PayFam"):
    return dbm.add_family(name)


def _make_parent(phone, tg_id, fio="Payer", role="senior"):
    pid = dbm.add_parent(fio, phone, role=role)
    dbm.update_parent_telegram_id(phone, tg_id)
    return pid


def _payment_rows(family_id):
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT paid_by, telegram_payment_charge_id, amount, plan "
            "FROM payments WHERE family_id = %s ORDER BY id",
            (family_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def _make_payment_message(user_id, payload, charge_id="charge_1",
                          currency="UZS", amount=2990000):
    payment = SimpleNamespace(
        invoice_payload=payload,
        total_amount=amount,
        currency=currency,
        telegram_payment_charge_id=charge_id,
        provider_payment_charge_id="prov_1",
    )
    return SimpleNamespace(
        successful_payment=payment,
        chat=SimpleNamespace(id=user_id),
    )


@pytest.fixture
def sub_mod(monkeypatch):
    """Импортирует handlers.subscription и глушит внешние эффекты (bot/notify)."""
    from src.handlers import subscription as mod

    calls = {"send_content": [], "admin_alert": [], "notify_family": [], "refund": []}

    monkeypatch.setattr(mod, "send_content",
                        lambda *a, **k: calls["send_content"].append((a, k)))
    monkeypatch.setattr(mod, "_alert_admin_payment",
                        lambda text: calls["admin_alert"].append(text))
    monkeypatch.setattr(mod, "_notify_family_about_subscription",
                        lambda *a, **k: calls["notify_family"].append((a, k)))

    fake_bot = MagicMock()
    monkeypatch.setattr(mod, "bot", fake_bot)
    mod._test_calls = calls  # удобный доступ из теста
    yield mod
    if hasattr(mod, "_test_calls"):
        del mod._test_calls


# ─────────────────────────── B1: successful_payment ───────────────────────────

def test_successful_payment_happy_path(temp_db, sub_mod):
    fam = _make_family()
    parent_id = _make_parent("998900010001", 700001)

    msg = _make_payment_message(700001, f"{fam}:monthly:1", charge_id="ch_happy")
    sub_mod.handle_successful_payment(msg)

    rows = _payment_rows(fam)
    assert len(rows) == 1
    assert rows[0]["paid_by"] == parent_id
    assert rows[0]["telegram_payment_charge_id"] == "ch_happy"

    sub = dbm.get_family_subscription(fam)
    assert sub["subscription_end"] is not None
    assert sub["subscription_end"] > _now_naive_utc()
    # юзер получил подтверждение, денежный алерт админу не звался
    assert sub_mod._test_calls["send_content"]
    assert sub_mod._test_calls["admin_alert"] == []


def test_successful_payment_parent_none_does_not_lose_money(temp_db, sub_mod):
    """Плательщик без строки parents: платёж всё равно записан (paid_by=NULL),
    подписка активирована, админ уведомлён — деньги не теряются молча."""
    fam = _make_family()
    # НЕ создаём parents-строку для 700002
    msg = _make_payment_message(700002, f"{fam}:monthly:1", charge_id="ch_noparent")
    sub_mod.handle_successful_payment(msg)

    rows = _payment_rows(fam)
    assert len(rows) == 1
    assert rows[0]["paid_by"] is None  # nullable после миграции 0002
    assert rows[0]["telegram_payment_charge_id"] == "ch_noparent"

    sub = dbm.get_family_subscription(fam)
    assert sub["subscription_end"] is not None
    assert sub["subscription_end"] > _now_naive_utc()
    # админ предупреждён о незарегистрированном плательщике
    assert sub_mod._test_calls["admin_alert"]


def test_successful_payment_malformed_payload_no_crash(temp_db, sub_mod):
    fam = _make_family()

    # 2 части вместо 3
    msg = _make_payment_message(700003, f"{fam}:monthly", charge_id="ch_bad1")
    sub_mod.handle_successful_payment(msg)  # не должно бросить

    # нечисловой family_id
    msg2 = _make_payment_message(700003, "abc:monthly:1", charge_id="ch_bad2")
    sub_mod.handle_successful_payment(msg2)

    # нечисловые months
    msg3 = _make_payment_message(700003, f"{fam}:monthly:xx", charge_id="ch_bad3")
    sub_mod.handle_successful_payment(msg3)

    assert _payment_rows(fam) == []           # ничего не записано
    sub = dbm.get_family_subscription(fam)
    assert sub["subscription_end"] is None    # подписка не тронута
    assert len(sub_mod._test_calls["admin_alert"]) == 3  # каждый сбой заалерчен


def test_successful_payment_duplicate_charge_not_doubled(temp_db, sub_mod):
    """Повторная доставка того же successful_payment (тот же charge_id) не двоит
    подписку и не создаёт второй платёж."""
    fam = _make_family()
    _make_parent("998900010004", 700004)

    payload = f"{fam}:monthly:1"
    sub_mod.handle_successful_payment(
        _make_payment_message(700004, payload, charge_id="ch_dup"))
    first_end = dbm.get_family_subscription(fam)["subscription_end"]

    # тот же charge_id → идемпотентный no-op
    sub_mod.handle_successful_payment(
        _make_payment_message(700004, payload, charge_id="ch_dup"))
    second_end = dbm.get_family_subscription(fam)["subscription_end"]

    assert len(_payment_rows(fam)) == 1          # только один платёж
    assert first_end == second_end               # подписка не продлилась второй раз


def test_successful_payment_stars_refund_on_bad_payload(temp_db, sub_mod):
    """XTR (Stars) + malformed payload → пытаемся вернуть звёзды."""
    msg = _make_payment_message(700005, "garbage", charge_id="ch_star",
                                currency="XTR", amount=100)
    sub_mod.handle_successful_payment(msg)
    sub_mod.bot.refund_star_payment.assert_called_once_with(700005, "ch_star")


# ─────────────────────────── B6: promo ───────────────────────────

def test_promo_applied_exactly_once(temp_db, sub_mod):
    fam = _make_family()
    _make_parent("998900010006", 700006)
    dbm.create_promo_code("GIFT2", "monthly", free_months=2, max_uses=1)
    promo = dbm.get_promo_code("GIFT2")

    sub_mod._apply_promo_to_family(700006, fam, promo, "ru")

    rows = _payment_rows(fam)
    assert len(rows) == 1
    assert rows[0]["plan"] == "promo_GIFT2"
    first_end = dbm.get_family_subscription(fam)["subscription_end"]
    assert first_end is not None

    # Слот исчерпан: повторный use_promo_code → False, НЕ начисляет
    assert dbm.use_promo_code("GIFT2") is False

    # Повторное применение того же промо (симуляция гонки): guard внутри
    # _apply_promo_to_family не даёт начислить второй раз.
    sub_mod._apply_promo_to_family(700006, fam, promo, "ru")
    assert len(_payment_rows(fam)) == 1  # второго платежа нет
    assert dbm.get_family_subscription(fam)["subscription_end"] == first_end


def test_use_promo_code_exhausted_returns_false(temp_db):
    dbm.create_promo_code("ONCE", "monthly", free_months=1, max_uses=1)
    assert dbm.use_promo_code("ONCE") is True
    assert dbm.use_promo_code("ONCE") is False


# ─────────────────────────── B7: extend_subscription additive ───────────────────────────

def test_extend_subscription_additive_from_null(temp_db):
    """Две первые оплаты (из NULL) должны дать 2 месяца, не 1 (не перезапись)."""
    fam = _make_family()

    dbm.extend_subscription(fam, 1)
    dbm.extend_subscription(fam, 1)

    end = dbm.get_family_subscription(fam)["subscription_end"]
    delta_days = (end - _now_naive_utc()).days
    # 2 календарных месяца ≈ 59–62 дня; заведомо > 1 месяца (>35 дней).
    assert delta_days > 35, f"ожидали ~2 месяца, получили {delta_days} дней"
    assert delta_days < 65


def test_extend_subscription_additive_when_active(temp_db):
    """Продление активной подписки прибавляет к её концу, не от now."""
    fam = _make_family()
    dbm.extend_subscription(fam, 3)       # активна ~3 месяца
    dbm.extend_subscription(fam, 3)       # +3 → ~6 месяцев

    end = dbm.get_family_subscription(fam)["subscription_end"]
    delta_days = (end - _now_naive_utc()).days
    assert delta_days > 150, f"ожидали ~6 месяцев, получили {delta_days} дней"


def test_extend_subscription_missing_family_noop(temp_db):
    """Несуществующая семья — silent no-op (контракт сохранён)."""
    dbm.extend_subscription(999999, 1)  # не должно бросить
    assert dbm.get_family_subscription(999999) is None
