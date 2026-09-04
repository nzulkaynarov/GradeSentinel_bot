"""Оценки за прошлый класс можно посмотреть, и они подписаны своим классом.

Оценки никуда не деваются (история привязана к student_id), но посмотреть их
«как за 8 класс» было нельзя: класс и ссылка на таблицу живут одним полем в
`students` и перезаписываются при смене ссылки. Ученик переходит в 9 класс — и
прошлогодние оценки подписываются «9 Orion».

`student_years` хранит по снимку на учебный год: какой был класс и какая
таблица. Это же оставляет прошлогоднюю ссылку видимой как архив.
"""
import importlib.util
import os
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm  # noqa: E402


def _load_migration():
    path = os.path.join(ROOT, "migrations", "versions", "0007_student_year_history.py")
    spec = importlib.util.spec_from_file_location("migration_0007", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MIGRATION = _load_migration()


def _years(student_id):
    return [(y["academic_year"], y["display_name"], y["spreadsheet_id"])
            for y in dbm.get_student_years(student_id)]


# ─── Снимок класса и таблицы по годам ────────────────────────────────
def test_year_is_snapshotted_when_known(temp_db):
    sid = dbm.add_student("Заур", "sheet-8", display_name="8 Orion")
    dbm.set_student_academic_year(sid, 2025)

    assert _years(sid) == [(2025, "8 Orion", "sheet-8")]


def test_relink_keeps_previous_class_and_link(temp_db):
    """Главный сценарий: переход из 8 в 9 класс.

    Прошлый год должен остаться подписан «8 Orion» и помнить старую таблицу,
    иначе оценки восьмого класса уедут под именем девятого."""
    sid = dbm.add_student("Заур", "sheet-8", display_name="8 Orion")
    dbm.set_student_academic_year(sid, 2025)

    dbm.update_student_spreadsheet(sid, "sheet-9", display_name="9 Orion")
    dbm.set_student_academic_year(sid, 2026)     # год выведен по новой таблице

    assert _years(sid) == [
        (2026, "9 Orion", "sheet-9"),
        (2025, "8 Orion", "sheet-8"),
    ]


def test_snapshot_updates_within_the_same_year(temp_db):
    """Учитель переименовал таблицу — снимок текущего года обновляется,
    а не плодит вторую строку."""
    sid = dbm.add_student("Заур", "sheet-8", display_name="8 Orion")
    dbm.set_student_academic_year(sid, 2025)
    dbm.update_student_display_name(sid, "8 Orion (2 группа)")
    dbm.set_student_academic_year(sid, 2025)

    assert _years(sid) == [(2025, "8 Orion (2 группа)", "sheet-8")]


def test_reset_to_null_keeps_history(temp_db):
    """Смена ссылки обнуляет год до импорта — история от этого не страдает."""
    sid = dbm.add_student("Заур", "sheet-8", display_name="8 Orion")
    dbm.set_student_academic_year(sid, 2025)
    dbm.set_student_academic_year(sid, None)

    assert _years(sid) == [(2025, "8 Orion", "sheet-8")]


def test_backfill_snapshots_existing_students(temp_db):
    """Миграция переносит текущее состояние учеников в историю."""
    sid = dbm.add_student("Заур", "sheet-8", display_name="8 Orion", academic_year=2025)
    with dbm.get_db_connection() as conn:
        conn.cursor().execute("DELETE FROM student_years WHERE student_id = %s", (sid,))
    assert _years(sid) == []

    with dbm.get_db_connection() as conn:
        conn.cursor().execute(MIGRATION.BACKFILL_SQL)

    assert _years(sid) == [(2025, "8 Orion", "sheet-8")]


def test_migration_revision_chain():
    assert MIGRATION.revision == "0007_student_year_history"
    assert MIGRATION.down_revision == "0006_quarter_academic_year"


# ─── Список доступных лет для селектора ──────────────────────────────
def _seed_grade(student_id, day, subject="Алгебра"):
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO grade_history (student_id, subject, grade_value, raw_text, "
            "cell_reference, grade_date) VALUES (%s, %s, 4, '4', %s, %s)",
            (student_id, subject, f"X-{subject}-{day}", day),
        )


def test_available_years_carry_class_of_that_year(temp_db):
    from webapp.app import _available_years

    sid = dbm.add_student("Заур", "sheet-8", display_name="8 Orion")
    dbm.set_student_academic_year(sid, 2025)
    _seed_grade(sid, "2025-10-14")
    dbm.update_student_spreadsheet(sid, "sheet-9", display_name="9 Orion")
    dbm.set_student_academic_year(sid, 2026)
    _seed_grade(sid, "2026-09-04")

    years = _available_years(sid)

    assert [(y["academic_year"], y["display_name"], y["label"]) for y in years] == [
        (2026, "9 Orion", "2026/27"),
        (2025, "8 Orion", "2025/26"),
    ]


def test_available_years_include_archived_grades(temp_db):
    """Старые оценки уезжают в архив — год всё равно доступен для выбора."""
    from webapp.app import _available_years

    sid = dbm.add_student("Заур", "sheet-9", display_name="9 Orion")
    dbm.set_student_academic_year(sid, 2026)
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO grade_history_archive (student_id, subject, grade_value, raw_text, "
            "cell_reference, grade_date, date_added) "
            "VALUES (%s, 'Химия', 4, '4', 'X', '2024-11-05', '2024-11-05 12:00:00')",
            (sid,),
        )

    assert [y["academic_year"] for y in _available_years(sid)] == [2026, 2024]


def test_spring_grades_belong_to_the_year_that_started_in_september(temp_db):
    """Май 2026 — это учебный год 2025/26, а не 2026/27."""
    from webapp.app import _available_years

    sid = dbm.add_student("Заур", "sheet-8")
    _seed_grade(sid, "2026-05-21")

    assert [y["academic_year"] for y in _available_years(sid)] == [2025]


def test_year_without_grades_still_listed_from_snapshot(temp_db):
    """Год, в котором оценок ещё нет, остаётся в списке — из снимка привязки."""
    from webapp.app import _available_years

    sid = dbm.add_student("Заур", "sheet-9", display_name="9 Orion")
    dbm.set_student_academic_year(sid, 2026)

    assert [y["academic_year"] for y in _available_years(sid)] == [2026]
