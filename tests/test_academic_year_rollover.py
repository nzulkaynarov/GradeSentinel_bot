"""Rollover учебного года (инцидент 2026-09-02).

Даты в шапке «Все оценки!» без года («2 сентября»). Раньше год вычислялся от
«сейчас»: в сентябре 2026 прошлогодняя таблица (2025/26) давала колонку
«2 сентября» = 2026-09-02 = сегодня → монитор разослал прошлогодние оценки как
новые. Школа каждый год выдаёт новую ссылку, старая остаётся привязанной.

Фикс: `students.academic_year` (год начала уч. года таблицы) →
  • парсер шапки восстанавливает год из academic_year, а не от текущей даты;
  • монитор/quarter-check/hourly-sync НЕ опрашивают таблицы прошлого уч. года;
  • семье — напоминание обновить ссылку (кнопка «Сменить ссылку»), админу — алерт;
  • importer выводит academic_year по содержимому листа при новой привязке;
  • defense-in-depth: при неизвестном году «сегодняшние» оценки, 1:1
    совпадающие с оценками ровно год назад, считаются эхом прошлого года.
"""
import os
import sys
from datetime import date, datetime
from unittest.mock import patch, MagicMock

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.history_importer import (  # noqa: E402
    _parse_russian_date,
    _parse_master_sheet_for_date,
    current_academic_year,
    infer_sheet_academic_year,
    is_sheet_stale,
    resolve_academic_year_from_sheet,
)
import src.database_manager as dbm  # noqa: E402
import src.monitor_engine as me  # noqa: E402


# ─── Чистые функции ──────────────────────────────────────────────────
def test_current_academic_year_boundaries():
    assert current_academic_year(date(2026, 9, 1)) == 2026
    assert current_academic_year(date(2026, 8, 31)) == 2025
    assert current_academic_year(date(2027, 1, 15)) == 2026
    assert current_academic_year(date(2027, 5, 25)) == 2026


def test_parse_with_explicit_academic_year_ignores_now():
    """Ключ инцидента: «2 сентября» в таблице 2025/26 = 2025-09-02, даже если
    сейчас сентябрь 2026."""
    now = datetime(2026, 9, 2, 10, 0)
    d = _parse_russian_date("2 сентября", now=now, academic_year=2025)
    assert d == datetime(2025, 9, 2)
    # Весна того же учебного года → следующий календарный год
    assert _parse_russian_date("3 мая", now=now, academic_year=2025) == datetime(2026, 5, 3)
    # Без academic_year — legacy fallback от now (поведение до фикса сохранено)
    assert _parse_russian_date("2 сентября", now=now).year == 2026


def test_master_sheet_old_year_does_not_match_today():
    """Регрессия инцидента: колонка «2 сентября» прошлогодней таблицы НЕ
    возвращает оценки за сегодня (2026-09-02)."""
    data = [
        ["Все оценки"],
        ["Оценки", "2 сентября", "3 сентября"],
        ["Физкультура", "5", ""],
        ["Математика", "", "4"],
    ]
    today = date(2026, 9, 2)
    assert _parse_master_sheet_for_date(data, today, academic_year=2025) == []
    # А для таблицы текущего года — находит
    assert _parse_master_sheet_for_date(data, today, academic_year=2026) == [("Физкультура", "5")]


@pytest.mark.parametrize("months,today,expected", [
    # Есть весенние оценки → лист охватывает весну → закончившийся/текущий уч. год
    ({9, 10, 12, 1, 3, 5}, date(2026, 9, 2), 2025),   # старый лист в сентябре
    ({9, 1, 5}, date(2027, 3, 1), 2026),               # текущий лист весной
    ({9, 5}, date(2026, 7, 10), 2025),                 # летом смотрим прошлый год
    # Только осенние оценки / пусто → зависит от даты привязки
    ({9, 10}, date(2026, 11, 1), 2026),                # новый лист осенью
    (set(), date(2026, 8, 20), 2026),                  # пустой лист в августе → предстоящий
    (set(), date(2026, 7, 1), 2025),                   # пустой лист в июле → прошлый/текущий
    ({9}, date(2026, 9, 5), 2026),                     # первые оценки нового года
])
def test_infer_sheet_academic_year(months, today, expected):
    assert infer_sheet_academic_year(months, today=today) == expected


def test_infer_ignores_prefilled_header_without_grades():
    """Шапка нового листа заполнена датами на весь год (включая май), но
    оценки только в сентябре → это НОВЫЙ лист, не прошлогодний."""
    data = [
        ["Все оценки"],
        ["Оценки", "2 сентября", "3 сентября", "14 мая", "15 мая"],
        ["Математика", "5", "", "", ""],
    ]
    assert resolve_academic_year_from_sheet(data, today=date(2026, 9, 3)) == 2026
    # Тот же лист, но с оценкой в мае → прошлогодний
    data[2][3] = "4"
    assert resolve_academic_year_from_sheet(data, today=date(2026, 9, 3)) == 2025


def test_is_sheet_stale():
    assert is_sheet_stale(2025, date(2026, 9, 2)) is True
    assert is_sheet_stale(2026, date(2026, 9, 2)) is False
    assert is_sheet_stale(2025, date(2026, 8, 31)) is False   # уч. год ещё не сменился
    assert is_sheet_stale(None, date(2026, 9, 2)) is False    # неизвестен → не stale


# ─── БД: academic_year на ученике ────────────────────────────────────
def test_relink_resets_academic_year(temp_db):
    sid = dbm.add_student("Kid", "OLD", display_name="8 Orion", academic_year=2025)
    assert dbm.get_student_academic_year(sid) == 2025
    assert dbm.update_student_spreadsheet(sid, "NEW", display_name="9 Orion") is True
    assert dbm.get_student_academic_year(sid) is None  # переопределится импортом
    assert dbm.set_student_academic_year(sid, 2026) is True
    assert dbm.get_student_academic_year(sid) == 2026
    assert dbm.get_active_spreadsheets()[0]["academic_year"] == 2026


def test_importer_infers_and_persists_academic_year(temp_db):
    """Новая привязка (academic_year NULL) → importer выводит год по листу,
    пишет в students и импортирует даты с этим годом."""
    from src.history_importer import import_history_for_student

    sid = dbm.add_student("Kid", "SHEET", display_name="9 Orion")
    master = [
        ["Все оценки"],
        ["Оценки", "2 сентября", "3 сентября"],
        ["Математика", "5", "4"],
    ]

    def fake_get_sheet_data(spreadsheet_id, range_name):
        return master if range_name.startswith("Все оценки") else None

    with patch("src.history_importer.get_sheet_data", side_effect=fake_get_sheet_data), \
         patch("src.history_importer._tashkent_today_date", return_value=date(2026, 9, 10)):
        result = import_history_for_student(sid, "SHEET")

    assert dbm.get_student_academic_year(sid) == 2026
    assert result["imported"] == 2
    with dbm.get_db_connection() as conn:
        rows = conn.cursor().execute(
            "SELECT grade_date::text AS d FROM grade_history WHERE student_id = %s ORDER BY d",
            (sid,),
        ).fetchall()
    assert [r["d"] for r in rows] == ["2026-09-02", "2026-09-03"]


# ─── Монитор: stale-таблица не опрашивается, семья получает нэдж ─────
@pytest.fixture(autouse=True)
def _reset_monitor_state():
    me._pending_grades.clear()
    me._student_failure_counts.clear()
    me._last_failure_alert.clear()
    me._stale_logged_on.clear()
    yield
    me._pending_grades.clear()
    me._stale_logged_on.clear()


@pytest.fixture
def family_with_old_sheet(temp_db):
    head_id = dbm.add_parent("Head", "998900000333", role='senior')
    dbm.update_parent_telegram_id("998900000333", 333333)
    fam_id = dbm.add_family("F-rollover")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    sid = dbm.add_student("Kid", "ss-old", display_name="8 Orion", academic_year=2025)
    dbm.link_student_to_family(fam_id, sid)
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "UPDATE families SET subscription_end = now() + interval '30 days' WHERE id = %s",
            (fam_id,),
        )
    return {"student_id": sid, "family_id": fam_id, "tg_id": 333333}


def _september_2026():
    """Патчит «сегодня» монитора на 2026-09-02 10:00 Ташкента (05:00 UTC)."""
    fixed_utc = datetime(2026, 9, 2, 5, 0)

    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.replace(tzinfo=tz) if tz else fixed_utc

    return patch("src.monitor_engine.datetime", _FakeDT)


def test_stale_sheet_not_polled_and_family_nudged(family_with_old_sheet):
    info = family_with_old_sheet
    sender = MagicMock()
    sender.send.return_value = True
    sender.send_to_admin.return_value = True

    with _september_2026(), \
         patch("src.monitor_engine.get_sheet_data") as sheets, \
         patch("src.monitor_engine.is_quiet_hours", return_value=False), \
         patch("src.notifications.get_sender", return_value=sender):
        me._check_for_new_grades_impl()
        # Повторный цикл: без повторного нэджа (маркер в settings)
        me._check_for_new_grades_impl()

    sheets.assert_not_called()                      # Sheets вообще не читали
    assert sender.send.call_count == 1              # ровно один нэдж семье
    args, kwargs = sender.send.call_args
    assert args[0] == info["tg_id"]
    assert "2026/27" in args[1]
    assert kwargs["kb"] is not None                 # глава семьи получает кнопку
    assert kwargs["kb"].keyboard[0][0].callback_data == f"relink_list_{info['family_id']}"
    assert sender.send_to_admin.call_count == 1     # алерт админу один раз
    assert dbm.get_setting(f"relink_nudge:{info['student_id']}") == "2026-09-02"
    # Никаких оценок не записано
    with dbm.get_db_connection() as conn:
        n = conn.cursor().execute(
            "SELECT COUNT(*) c FROM grade_history WHERE student_id = %s", (info["student_id"],)
        ).fetchone()["c"]
    assert n == 0


def test_stale_nudge_deferred_in_quiet_hours(family_with_old_sheet):
    """Ночью (тихие часы) нэдж не шлём и маркер не ставим — уйдёт днём с кнопкой."""
    info = family_with_old_sheet
    sender = MagicMock()
    with _september_2026(), \
         patch("src.monitor_engine.get_sheet_data") as sheets, \
         patch("src.monitor_engine.is_quiet_hours", return_value=True), \
         patch("src.notifications.get_sender", return_value=sender):
        me._check_for_new_grades_impl()
    sheets.assert_not_called()
    sender.send.assert_not_called()
    assert dbm.get_setting(f"relink_nudge:{info['student_id']}") is None


def test_current_year_sheet_is_polled(family_with_old_sheet):
    """После смены ссылки (academic_year = текущий) опрос возобновляется."""
    info = family_with_old_sheet
    dbm.set_student_academic_year(info["student_id"], 2026)
    with _september_2026(), \
         patch("src.monitor_engine.get_sheet_data", return_value=[["x"]]) as sheets, \
         patch("src.history_importer._parse_master_sheet_for_date", return_value=[]):
        me._check_for_new_grades_impl()
    assert sheets.call_count == 1


def test_echo_guard_skips_last_year_duplicate(family_with_old_sheet):
    """academic_year неизвестен, а «сегодняшние» оценки 1:1 = ровно год назад →
    считаем эхом прошлогодней таблицы, ничего не пишем."""
    info = family_with_old_sheet
    sid = info["student_id"]
    dbm.set_student_academic_year(sid, None)
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO grade_history (student_id, subject, raw_text, grade_value, "
            "cell_reference, grade_date) VALUES (%s, 'Физкультура', '5', 5, 'X1', '2025-09-02')",
            (sid,),
        )
    with _september_2026(), \
         patch("src.monitor_engine.get_sheet_data", return_value=[["x"]]), \
         patch("src.history_importer._parse_master_sheet_for_date",
               return_value=[("Физкультура", "5")]):
        me._check_for_new_grades_impl()
        me._check_for_new_grades_impl()  # второй цикл (pending → confirm) тоже не пишет

    assert me._pending_grades == {}
    with dbm.get_db_connection() as conn:
        n = conn.cursor().execute(
            "SELECT COUNT(*) c FROM grade_history WHERE student_id = %s AND grade_date = '2026-09-02'",
            (sid,),
        ).fetchone()["c"]
    assert n == 0
