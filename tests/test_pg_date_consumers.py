"""Регрессия пост-миграции sqlite→PostgreSQL: потребители дат.

psycopg отдаёт DATE/TIMESTAMP-колонки как date/datetime ОБЪЕКТЫ (не строки).
Код, делавший value[:10] / .replace(' ','T') (привычка времён SQLite-TEXT),
после миграции падал. Эти тесты ловят такие места.
"""
import os
import types as _types
from datetime import date, datetime
from unittest.mock import patch

# handlers/* импортируют src.bot_instance, который требует валидный BOT_TOKEN
# (с двоеточием) при импорте. Тестовые заглушки — до импорта хендлеров.
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("ADMIN_ID", "0")
os.environ.setdefault("ADMIN_GROUP_ID", "0")

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


def test_cmd_subscription_active_sub_pg_datetime(temp_db):
    """cmd_subscription (handlers/subscription.py) при активной подписке рендерит
    дату окончания. subscription_end приходит из PG как datetime → до фикда
    sub_end[:10] падал TypeError на datetime-объекте."""
    from src.handlers import subscription as sub_mod

    parent = dbm.add_parent("Родитель", "998900010001", role="senior")
    dbm.update_parent_telegram_id("998900010001", 200001)
    fam = dbm.add_family("СемьяАктив")
    dbm.set_family_head(fam, parent)
    dbm.link_parent_to_family(fam, parent)
    dbm.extend_subscription(fam, 3)  # PG timestamp, активна

    message = _types.SimpleNamespace(chat=_types.SimpleNamespace(id=200001))

    with patch.object(sub_mod, "bot") as mock_bot:
        sub_mod.cmd_subscription(message)  # не должно бросить TypeError

    assert mock_bot.send_message.called, "cmd_subscription не отправил сообщение"
    sent_text = mock_bot.send_message.call_args[0][1]
    # Дата окончания = сегодня + 3 мес; проверяем что YYYY-MM-DD отрендерился
    expected_prefix = str(date.today().year)
    assert expected_prefix in sent_text, sent_text


def test_family_manage_menu_active_sub_pg_datetime(temp_db):
    """_send_family_manage_menu (handlers/family.py) для админа при активной
    подписке строит label через to_date_str — не падает на datetime."""
    from src.handlers import family as fam_mod

    admin = dbm.add_parent("Админ", "998900010002", role="admin")
    dbm.update_parent_telegram_id("998900010002", 200002)
    fam = dbm.add_family("СемьяМеню")
    dbm.set_family_head(fam, admin)
    dbm.link_parent_to_family(fam, admin)
    dbm.extend_subscription(fam, 1)

    with patch.object(fam_mod, "send_menu_safe") as mock_send:
        fam_mod._send_family_manage_menu(200002, fam)  # админ, активная подписка

    assert mock_send.called, "меню семьи не отправлено"


def test_pdf_export_grade_date_none_datetime_added(temp_db):
    """pdf_export: строка без grade_date, но с datetime в date_added, вперемешку
    со строкой где grade_date=date-объект. sort-key и ячейка обязаны быть
    строками YYYY-MM-DD, иначе sorted() сравнит date с str → TypeError."""
    from webapp.pdf_export import build_dashboard_pdf

    summary = {'current_avg': 4.0, 'delta': 0.0, 'status': 'stable',
               'problem_subjects': [], 'top_subjects': []}
    recent = [
        # grade_date None → fallback на datetime date_added
        {'subject': 'Математика', 'raw_text': '5',
         'grade_date': None, 'date_added': datetime(2026, 5, 20, 8, 30, 0)},
        # grade_date как date-объект (PG DATE)
        {'subject': 'Физика', 'raw_text': '4',
         'grade_date': date(2026, 5, 21), 'date_added': datetime(2026, 5, 21, 9, 0, 0)},
    ]

    pdf = build_dashboard_pdf('Ученик', summary, [], recent, 'неделя', 'ru')
    assert pdf.startswith(b'%PDF-')
    assert len(pdf) > 1000
