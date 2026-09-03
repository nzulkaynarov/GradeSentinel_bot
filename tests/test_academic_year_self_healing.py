"""Самовосстановление учебного года (разбор прода 2026-09-03).

PR #116 ввёл `students.academic_year`, но принимал записанное значение на веру.
На проде backfill миграции подхватил ложную запись за «сегодня» и проставил
прошлогодней таблице academic_year = 2026 — рассылка прошлогодних оценок
продолжилась, и починить это можно было только руками.

Здесь проверяется, что запись больше не считается истиной в последней инстанции:
  • лист в руках → год сверяется с его содержимым и чинится в обе стороны;
  • пауза (stale) не окончательна — раз в сутки лист всё равно читается,
    иначе заниженный год выключал бы ученику мониторинг навсегда;
  • эхо-guard имеет НЕГАТИВНЫЕ тесты: настоящие оценки не должны им гаситься
    (без них любое ослабление условия молча теряло бы уведомления);
  • несостоявшееся чтение листа не путается с пустым листом.
"""
import os
import sys
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm  # noqa: E402
import src.monitor_engine as me  # noqa: E402


# ─── Листы: прошлогодний (есть весенние оценки) и новый (только сентябрь) ──
LAST_YEAR_SHEET = [
    ["Все оценки", "Заур"],
    ["Оценки", "2 сентября", "14 мая"],
    ["Физкультура", "5", ""],
    ["Алгебра", "", "4"],          # весенняя оценка — метка учебного года 2025/26
]

NEW_YEAR_SHEET = [
    ["Все оценки", "Заур"],
    ["Оценки", "2 сентября", "3 сентября"],
    ["Физкультура", "5", ""],
    ["Алгебра", "", "4"],
]


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
def family(temp_db):
    head_id = dbm.add_parent("Head", "998900000444", role='senior')
    dbm.update_parent_telegram_id("998900000444", 444444)
    fam_id = dbm.add_family("F-heal")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    sid = dbm.add_student("Kid", "ss-heal", display_name="8 Orion", academic_year=2025)
    dbm.link_student_to_family(fam_id, sid)
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "UPDATE families SET subscription_end = now() + interval '30 days' WHERE id = %s",
            (fam_id,),
        )
    return {"student_id": sid, "family_id": fam_id, "tg_id": 444444}


def _september_2026():
    """«Сегодня» монитора = 2026-09-02 10:00 Ташкента (05:00 UTC)."""
    fixed_utc = datetime(2026, 9, 2, 5, 0)

    class _FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.replace(tzinfo=tz) if tz else fixed_utc

    return patch("src.monitor_engine.datetime", _FakeDT)


def _run_cycle(sheet, sender=None, quiet=False):
    """Один цикл монитора с замоканным Sheets и Sender. Парсинг — настоящий."""
    sender = sender or MagicMock(**{"send.return_value": True,
                                    "send_to_admin.return_value": True})
    with _september_2026(), \
         patch("src.monitor_engine.get_sheet_data", return_value=sheet) as sheets, \
         patch("src.monitor_engine.is_quiet_hours", return_value=quiet), \
         patch("src.notifications.get_sender", return_value=sender), \
         patch("src.monitor_engine.send_notification", return_value=True) as notify, \
         patch("src.monitor_engine._send_to_groups_for_student"):
        me._check_for_new_grades_impl()
    return sheets, sender, notify


def _year_of(student_id):
    return dbm.get_student_academic_year(student_id)


def _grades_on(student_id, day):
    with dbm.get_db_connection() as conn:
        return conn.cursor().execute(
            "SELECT COUNT(*) c FROM grade_history WHERE student_id = %s AND grade_date = %s",
            (student_id, day),
        ).fetchone()["c"]


# ─── Сценарий прода: год завышен, лист прошлогодний ──────────────────
def test_corrupted_year_is_lowered_from_sheet_content(family):
    """Инцидент 2026-09-03: backfill проставил 2026 прошлогодней таблице.

    Лист содержит майские оценки — в учебном году 2026/27 их быть не может,
    значит лист прошлогодний. Год чинится, оценки не рассылаются."""
    sid = family["student_id"]
    dbm.set_student_academic_year(sid, 2026)

    _, sender, notify = _run_cycle(LAST_YEAR_SHEET)

    assert _year_of(sid) == 2025                     # запись исправлена по факту
    assert _grades_on(sid, "2026-09-02") == 0        # прошлогодние оценки не записаны
    notify.assert_not_called()                       # и не разосланы
    assert sender.send.call_count == 1               # семье ушёл нэдж «обновите ссылку»


def test_correction_survives_restart_via_db(family):
    """Исправленный год лежит в БД, а не в памяти: следующий цикл его видит."""
    sid = family["student_id"]
    dbm.set_student_academic_year(sid, 2026)
    _run_cycle(LAST_YEAR_SHEET)
    me._stale_logged_on.clear()  # имитируем рестарт: in-memory состояние пусто

    sheets, _, notify = _run_cycle(LAST_YEAR_SHEET)

    assert _year_of(sid) == 2025
    notify.assert_not_called()
    sheets.assert_not_called()  # суточная перепроверка уже израсходована → лист не читаем


# ─── Обратная сторона: заниженный год не должен замораживать навсегда ─
def test_stale_student_is_rechecked_once_a_day_and_unfrozen(family):
    """Год ошибочно занижен (лист привязан в августе, когда оценок ещё не было).

    Пауза не приговор: раз в сутки лист читается, содержимое показывает новый
    учебный год → пауза снимается, оценки за сегодня обрабатываются."""
    sid = family["student_id"]
    assert _year_of(sid) == 2025                     # stale относительно 2026/27

    sheets, sender, _ = _run_cycle(NEW_YEAR_SHEET)

    assert sheets.call_count == 1                    # суточная перепроверка прочитала лист
    assert _year_of(sid) == 2026                     # год повышен по содержимому
    sender.send.assert_not_called()                  # нэдж НЕ ушёл: таблица оказалась актуальной
    # Оценка за сегодня попала в двухфазное подтверждение, второй цикл её запишет
    assert ("Физкультура" in {k[1] for k in me._pending_grades})


def test_daily_recheck_is_claimed_once_per_day(family):
    """Второй цикл в тот же день лист не читает — квота Sheets не тратится,
    и повторный нэдж семье не уходит (интервал напоминаний — дни, не минуты)."""
    sid = family["student_id"]
    with patch("src.monitor_engine._reconcile_academic_year", return_value=2025):
        sheets1, sender1, _ = _run_cycle(LAST_YEAR_SHEET)
        sheets2, sender2, _ = _run_cycle(LAST_YEAR_SHEET)

    assert sheets1.call_count == 1
    assert sender1.send.call_count == 1        # нэдж ушёл один раз
    sheets2.assert_not_called()
    sender2.send.assert_not_called()           # и не повторился на следующем цикле
    assert dbm.get_setting(f"stale_recheck:{sid}") == "2026-09-02"
    assert dbm.get_setting(f"relink_nudge:{sid}") == "2026-09-02"


def test_actual_year_is_not_touched(family):
    """Год совпал с содержимым → в БД ничего не пишем и лист обрабатываем."""
    sid = family["student_id"]
    dbm.set_student_academic_year(sid, 2026)

    with patch("src.monitor_engine.set_student_academic_year") as setter:
        _run_cycle(NEW_YEAR_SHEET)

    setter.assert_not_called()
    assert _year_of(sid) == 2026


def test_empty_sheet_never_raises_the_year(family):
    """Лист без оценок (пустой, битый, не распознан) — не свидетельство.

    Иначе пустой ответ Sheets «омолаживал» бы год приостановленной таблицы,
    снимал паузу и возобновлял рассылку прошлогодних оценок."""
    sid = family["student_id"]
    empty_sheet = [["Все оценки"], ["Оценки", "2 сентября"], ["Физкультура", ""]]

    _run_cycle(empty_sheet)

    assert _year_of(sid) == 2025          # остаётся на паузе


def test_unparseable_sheet_keeps_the_year(family):
    """Сломанная структура листа не должна ронять цикл и менять год."""
    sid = family["student_id"]
    dbm.set_student_academic_year(sid, 2026)

    _run_cycle([["мусор"]])

    assert _year_of(sid) == 2026


def test_null_year_is_left_to_importer(family):
    """academic_year IS NULL — штатное «ещё не определён», монитор его не выставляет."""
    sid = family["student_id"]
    dbm.set_student_academic_year(sid, None)

    _run_cycle(NEW_YEAR_SHEET)

    assert _year_of(sid) is None


# ─── Эхо-guard: негативные кейсы (их не было — мутации проходили) ─────
def _seed_last_year(sid, pairs, day="2025-09-02"):
    with dbm.get_db_connection() as conn:
        cur = conn.cursor()
        for subject, raw in pairs:
            cur.execute(
                "INSERT INTO grade_history (student_id, subject, raw_text, grade_value, "
                "cell_reference, grade_date) VALUES (%s, %s, %s, NULL, %s, %s)",
                (sid, subject, raw, f"X-{subject}-{day}", day),
            )


def test_echo_guard_lets_different_grades_through(family):
    """Оценки НЕ совпадают с прошлогодними → это настоящие оценки, гасить нельзя."""
    sid = family["student_id"]
    dbm.set_student_academic_year(sid, None)
    _seed_last_year(sid, [("Физкультура", "5")])

    with _september_2026(), \
         patch("src.monitor_engine.get_sheet_data", return_value=[["x"]]), \
         patch("src.monitor_engine.is_quiet_hours", return_value=False), \
         patch("src.history_importer._parse_master_sheet_for_date",
               return_value=[("Алгебра", "4")]), \
         patch("src.monitor_engine.send_notification", return_value=True) as notify, \
         patch("src.monitor_engine._send_to_groups_for_student"):
        me._check_for_new_grades_impl()   # цикл 1 — pending
        me._check_for_new_grades_impl()   # цикл 2 — подтверждение и отправка

    assert _grades_on(sid, "2026-09-02") == 1
    assert notify.call_count == 1


def test_echo_guard_lets_partial_overlap_through(family):
    """Совпала лишь часть набора → день всё равно настоящий, гасить нельзя.

    Фиксирует, что правило именно «подмножество», а не «пересечение»: без этого
    теста ослабление условия до `today_set & previous` проходит весь сюит."""
    sid = family["student_id"]
    dbm.set_student_academic_year(sid, None)
    _seed_last_year(sid, [("Физкультура", "5")])

    with _september_2026(), \
         patch("src.monitor_engine.get_sheet_data", return_value=[["x"]]), \
         patch("src.monitor_engine.is_quiet_hours", return_value=False), \
         patch("src.history_importer._parse_master_sheet_for_date",
               return_value=[("Физкультура", "5"), ("Алгебра", "4")]), \
         patch("src.monitor_engine.send_notification", return_value=True) as notify, \
         patch("src.monitor_engine._send_to_groups_for_student"):
        me._check_for_new_grades_impl()
        me._check_for_new_grades_impl()

    assert _grades_on(sid, "2026-09-02") == 2
    assert notify.call_count == 1


# ─── Несостоявшееся чтение листа ≠ пустой лист ───────────────────────
def test_import_skipped_when_master_sheet_unavailable(temp_db):
    """get_sheet_data вернула None (403 / исчерпанные ретраи): год выводить не из
    чего, поэтому импорт не идёт вовсе — иначе даты распарсились бы fallback'ом
    «год от сегодня» и прошлогодние оценки легли бы с сегодняшними датами."""
    from src.history_importer import import_history_for_student

    sid = dbm.add_student("Kid", "ss-unavailable")
    with patch("src.history_importer.get_sheet_data", return_value=None) as fetch:
        result = import_history_for_student(sid, "ss-unavailable")

    assert result == {'imported': 0, 'skipped': 0, 'total': 0}
    assert _year_of(sid) is None          # ложный год не записан
    assert fetch.call_count == 1          # и лист «Неделя» тоже не читался вслепую
