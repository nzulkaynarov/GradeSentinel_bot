"""Security PR-C / S2: валидация Telegram WebApp initData.

Закрывает пробел инфра-аудита (HMAC вообще не был покрыт тестами) и проверяет
новый TTL-гейт свежести auth_date (replay-защита утёкшей ссылки на дашборд).

Эталонный initData подписывается тем же алгоритмом, что и Telegram-клиент:
  secret = HMAC_SHA256("WebAppData", BOT_TOKEN)
  hash   = HMAC_SHA256(secret, data_check_string)
где data_check_string — отсортированные "k=v" из URL-decoded значений, БЕЗ hash,
но ВКЛЮЧАЯ signature (Ed25519 third-party поле Telegram 7.x+).
"""
import os
import sys
import hmac
import hashlib
import json
import time
from urllib.parse import urlencode

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("DATABASE_PATH", "/tmp/gs_test_initdata.db")
os.environ.setdefault("BOT_TOKEN", "12345:test-token")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")

import pytest

import webapp.app as webapp


def _sign_init_data(bot_token, *, auth_date, user=None,
                    with_signature=True, tamper_hash=False, omit_hash=False):
    """Собирает валидный (или намеренно битый) initData-query-string.

    data_check_string считается из URL-decoded значений (как это делает
    parse_qs в validate_init_data), signature включается, hash исключается.
    """
    user = user or {"id": 777001, "first_name": "Test", "language_code": "ru"}
    fields = {
        "auth_date": str(int(auth_date)),
        "query_id": "AAHtest",
        "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False),
    }
    if with_signature:
        # реальное содержимое signature не проверяется криптографически ботом,
        # но ОБЯЗАНО участвовать в data_check_string — иначе hash не сойдётся.
        fields["signature"] = "ZmFrZV9lZDI1NTE5X3NpZw"

    data_check_string = "\n".join(sorted(f"{k}={v}" for k, v in fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not omit_hash:
        fields["hash"] = "0" * 64 if tamper_hash else computed
    return urlencode(fields)


@pytest.fixture
def token():
    return webapp.BOT_TOKEN


# ─────────────────────── happy path ───────────────────────

def test_valid_fresh_init_data(token):
    init_data = _sign_init_data(token, auth_date=time.time())
    user = webapp.validate_init_data(init_data)
    assert user["id"] == 777001
    assert user["language_code"] == "ru"


def test_signature_field_is_part_of_hash(token):
    """Регрессия HMAC-тонкости: signature ВКЛЮЧён в data_check_string.
    Если бы функция его исключала — валидный initData падал бы на 'Invalid hash'."""
    init_data = _sign_init_data(token, auth_date=time.time(), with_signature=True)
    assert webapp.validate_init_data(init_data)["id"] == 777001


# ─────────────────────── negative: hash ───────────────────────

def test_tampered_hash_rejected(token):
    init_data = _sign_init_data(token, auth_date=time.time(), tamper_hash=True)
    with pytest.raises(ValueError):
        webapp.validate_init_data(init_data)


def test_missing_hash_rejected(token):
    init_data = _sign_init_data(token, auth_date=time.time(), omit_hash=True)
    with pytest.raises(ValueError):
        webapp.validate_init_data(init_data)


def test_wrong_token_rejected():
    """Подпись чужим токеном не проходит под нашим BOT_TOKEN."""
    init_data = _sign_init_data("99999:attacker-token", auth_date=time.time())
    with pytest.raises(ValueError):
        webapp.validate_init_data(init_data)


# ─────────────────────── negative: auth_date TTL (S2) ───────────────────────

def test_expired_auth_date_rejected(token):
    """auth_date старше 24ч → подпись валидна, но initData протух → отказ."""
    stale = time.time() - webapp.INIT_DATA_MAX_AGE - 60
    init_data = _sign_init_data(token, auth_date=stale)
    with pytest.raises(ValueError):
        webapp.validate_init_data(init_data)


def test_recent_auth_date_within_ttl_ok(token):
    """auth_date в пределах суток — принимается (граница TTL)."""
    recent = time.time() - webapp.INIT_DATA_MAX_AGE + 3600  # 23ч назад
    init_data = _sign_init_data(token, auth_date=recent)
    assert webapp.validate_init_data(init_data)["id"] == 777001


def test_future_auth_date_beyond_skew_rejected(token):
    """auth_date заметно в будущем (за пределами clock-skew) → отказ."""
    future = time.time() + webapp.INIT_DATA_CLOCK_SKEW + 600
    init_data = _sign_init_data(token, auth_date=future)
    with pytest.raises(ValueError):
        webapp.validate_init_data(init_data)


def test_small_clock_skew_tolerated(token):
    """Небольшой дрейф часов (auth_date чуть в будущем) допускается."""
    slightly_future = time.time() + 60  # 1 мин вперёд, в пределах skew
    init_data = _sign_init_data(token, auth_date=slightly_future)
    assert webapp.validate_init_data(init_data)["id"] == 777001


def test_missing_auth_date_rejected(token):
    """initData без auth_date после hash-проверки → отказ (не бессрочный доступ)."""
    user = {"id": 777001, "first_name": "Test", "language_code": "ru"}
    fields = {
        "query_id": "AAHtest",
        "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False),
        "signature": "ZmFrZQ",
    }
    dcs = "\n".join(sorted(f"{k}={v}" for k, v in fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    with pytest.raises(ValueError):
        webapp.validate_init_data(urlencode(fields))
