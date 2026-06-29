"""Регрессия пост-миграции sqlite→PostgreSQL: потребители дат.

psycopg отдаёт DATE/TIMESTAMP-колонки как date/datetime ОБЪЕКТЫ (не строки).
Код, делавший value[:10] / .replace(' ','T') (привычка времён SQLite-TEXT),
после миграции падал. Эти тесты ловят такие места.
"""
from datetime import date, datetime

import src.database_manager as dbm
from src import ai_tools
from src.utils import to_date_str


def test_to_date_str_accepts_str_datetime_date_none():
    assert to_date_str("2026-12-31 00:00:00") == "2026-12-31"
    assert to_date_str("2026-12-31") == "2026-12-31"
    assert to_date_str(datetime(2026, 12, 31, 5, 30, 0)) == "2026-12-31"
    assert to_date_str(date(2026, 12, 31)) == "2026-12-31"
    assert to_date_str(None) == ""


def test_subscription_status_tool_active_sub_pg_datetime(temp_db):
    """get_family_subscription отдаёт subscription_end как datetime (PG) — тул
    подписки не должен падать и должен показать АКТИВНУЮ подписку.
    (До фикса: end_str[:10] / .replace на datetime → TypeError → tool error.)"""
    fam = dbm.add_family("FamSub")
    dbm.extend_subscription(fam, 3)  # subscription_end = now + 3 мес (PG timestamp)

    out = ai_tools._format_subscription_status(fam, "ru")
    assert "АКТИВНА" in out, out

    # dispatch_tool оборачивает в try/except → до фикса TypeError проглатывался
    # и возвращался лейбл ошибки; теперь должен вернуть активный статус.
    out2 = ai_tools.dispatch_tool("get_subscription_status", {}, fam, "ru")
    assert "АКТИВНА" in out2, out2
    assert "недоступен" not in out2, out2


def test_subscription_status_tool_no_sub(temp_db):
    fam = dbm.add_family("FamNoSub")
    out = ai_tools.dispatch_tool("get_subscription_status", {}, fam, "ru")
    assert "ни разу не оплачивалась" in out, out
