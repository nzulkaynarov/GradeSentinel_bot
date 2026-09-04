"""Мелкая правда дашборда и стёртые учителем оценки (аудит 2026-09-03).

  • счётчик «Все оценки за период» показывал 100, пока KPI на том же экране
    показывал 525 — список урезан сервером, а числа никто не сопоставлял;
  • оценка, которую учитель стёр, оставалась в истории навсегда и продолжала
    влиять на средний балл, тренды, PDF и контекст AI-чата: монитор умел
    только добавлять и менять.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm  # noqa: E402
import src.monitor_engine as me  # noqa: E402


def _today():
    return (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)).date().isoformat()


def _insert(student_id, subject, raw_text="4", day=None):
    day = day or _today()
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO grade_history (student_id, subject, grade_value, raw_text, "
            "cell_reference, grade_date) VALUES (%s, %s, 4, %s, %s, %s)",
            (student_id, subject, raw_text, f"X-{subject}-{day}", day),
        )


def _subjects(student_id, day=None):
    day = day or _today()
    with dbm.get_db_connection() as conn:
        rows = conn.cursor().execute(
            "SELECT subject FROM grade_history WHERE student_id = %s AND grade_date = %s",
            (student_id, day),
        ).fetchall()
    return {r["subject"] for r in rows}


# ─── Стёртые учителем оценки ─────────────────────────────────────────
def test_grade_removed_from_sheet_is_dropped(temp_db):
    """Учитель стёр ячейку — оценка уходит и из истории."""
    sid = dbm.add_student("Kid", "ss-rm")
    _insert(sid, "Алгебра")
    _insert(sid, "Химия")

    removed = me._drop_removed_grades(sid, "Kid", _today(), {"Алгебра"})

    assert removed == 1
    assert _subjects(sid) == {"Алгебра"}


def test_nothing_dropped_when_sheet_matches(temp_db):
    sid = dbm.add_student("Kid", "ss-rm2")
    _insert(sid, "Алгебра")

    assert me._drop_removed_grades(sid, "Kid", _today(), {"Алгебра"}) == 0
    assert _subjects(sid) == {"Алгебра"}


def test_other_days_are_never_touched(temp_db):
    """Чистим только сегодня: за прошлые дни в БД есть записи импортёра,
    которых в сегодняшней колонке листа заведомо нет."""
    sid = dbm.add_student("Kid", "ss-rm3")
    yesterday = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)
                 - timedelta(days=1)).date().isoformat()
    _insert(sid, "История", day=yesterday)
    _insert(sid, "Алгебра")

    me._drop_removed_grades(sid, "Kid", _today(), {"Алгебра"})

    assert _subjects(sid, yesterday) == {"История"}


def test_other_students_are_never_touched(temp_db):
    sid = dbm.add_student("Kid", "ss-rm4")
    other = dbm.add_student("Other", "ss-rm5")
    _insert(sid, "Алгебра")
    _insert(other, "Алгебра")

    me._drop_removed_grades(sid, "Kid", _today(), set())

    assert _subjects(other) == {"Алгебра"}


def test_db_failure_does_not_break_the_cycle(temp_db):
    """Ошибка проверки не должна ронять polling-цикл."""
    sid = dbm.add_student("Kid", "ss-rm6")
    with patch.object(me, "_grades_on_date", side_effect=RuntimeError("db down")):
        assert me._drop_removed_grades(sid, "Kid", _today(), {"Алгебра"}) == 0


def test_removal_runs_only_with_non_empty_column(temp_db):
    """Ключевая защита: цикл вызывает проверку только когда в колонке что-то
    есть. Пустой список привёл бы к стиранию всего дня при сбое чтения."""
    source = open(os.path.join(ROOT, "src", "monitor_engine.py"), encoding="utf-8").read()
    # Именно ВЫЗОВ внутри цикла (с отступом), а не определение функции выше.
    idx = source.index("\n        _drop_removed_grades(")
    preceding = source[:idx]

    # Выше по коду обязателен ранний выход на пустой колонке
    assert "if not today_grades_pairs:" in preceding
    assert preceding.rindex("if not today_grades_pairs:") < idx


# ─── Счётчик оценок ──────────────────────────────────────────────────
def test_dashboard_reports_total_and_limit():
    """Фронт должен знать оба числа, чтобы подписать «100 из 525»."""
    import webapp.app as wa

    source = open(os.path.join(ROOT, "webapp", "app.py"), encoding="utf-8").read()
    assert '"recent_total"' in source
    assert '"recent_limit"' in source
    assert wa._RECENT_LIMIT == 100


def test_frontend_shows_both_numbers_when_truncated():
    js = open(os.path.join(ROOT, "webapp", "static", "app.js"), encoding="utf-8").read()
    assert "recent_total" in js
    assert "${shown} / ${total}" in js


# ─── i18n: подписи больше не захардкожены ────────────────────────────
def test_placeholder_translations_are_applied():
    js = open(os.path.join(ROOT, "webapp", "static", "app.js"), encoding="utf-8").read()
    assert "data-i18n-placeholder" in js          # атрибут наконец обрабатывается


def test_quarter_labels_come_from_locales():
    js = open(os.path.join(ROOT, "webapp", "static", "app.js"), encoding="utf-8").read()
    assert 't("quarter_1")' in js
    assert '"qc-q-label">1ч<' not in js


def test_month_names_exist_in_all_locales():
    import json

    for lang in ("ru", "uz", "en"):
        d = json.load(open(os.path.join(ROOT, "webapp", "static", "locales", f"{lang}.json"),
                           encoding="utf-8"))
        assert all(f"month_{i}" in d for i in range(1, 13)), lang
