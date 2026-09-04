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


# ─── Отчёт за учебный год и за все годы обучения ─────────────────────
def _seed_year(student_id, year, subjects_grades):
    """Оценки внутри учебного года: осенние в year, весенние в year+1."""
    for i, (subject, value) in enumerate(subjects_grades):
        day = f"{year}-10-{5 + i:02d}"
        with dbm.get_db_connection() as conn:
            conn.cursor().execute(
                "INSERT INTO grade_history (student_id, subject, grade_value, raw_text, "
                "cell_reference, grade_date) VALUES (%s, %s, %s, %s, %s, %s)",
                (student_id, subject, float(value), str(value),
                 f"X-{subject}-{day}", day),
            )


def test_grades_query_slices_by_academic_year(temp_db):
    sid = dbm.add_student("Заур", "sheet")
    _seed_year(sid, 2024, [("Алгебра", 4)])
    _seed_year(sid, 2025, [("Алгебра", 3), ("Химия", 5)])

    only_2025 = dbm.get_grades_for_academic_years(sid, [2025])
    everything = dbm.get_grades_for_academic_years(sid)

    assert len(only_2025) == 2
    assert {g["academic_year"] for g in only_2025} == {2025}
    assert {g["academic_year"] for g in everything} == {2024, 2025}


def test_spring_grades_stay_in_their_academic_year(temp_db):
    """Май 2026 принадлежит году, начавшемуся в сентябре 2025."""
    sid = dbm.add_student("Заур", "sheet")
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO grade_history (student_id, subject, grade_value, raw_text, "
            "cell_reference, grade_date) VALUES (%s, 'Химия', 4, '4', 'X', '2026-05-21')",
            (sid,),
        )

    assert [g["academic_year"] for g in dbm.get_grades_for_academic_years(sid, [2025])] == [2025]
    assert dbm.get_grades_for_academic_years(sid, [2026]) == []


def test_year_query_reads_archive(temp_db):
    """История выпускника почти вся лежит в архиве — без него отчёт пуст."""
    sid = dbm.add_student("Заур", "sheet")
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO grade_history_archive (student_id, subject, grade_value, raw_text, "
            "cell_reference, grade_date, date_added) "
            "VALUES (%s, 'Алгебра', 5, '5', 'X', '2023-11-02', '2023-11-02 12:00:00')",
            (sid,),
        )

    rows = dbm.get_grades_for_academic_years(sid, [2023])

    assert [r["subject"] for r in rows] == ["Алгебра"]


def test_pdf_all_years_summarises_each_class(temp_db):
    """Сценарий выпускника: сводка по годам с классом каждого года."""
    from webapp.app import _generate_dashboard_pdf

    head_id = dbm.add_parent("Head", "998900000901", role='senior')
    dbm.update_parent_telegram_id("998900000901", 901001)
    fam_id = dbm.add_family("F-grad")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    sid = dbm.add_student("Заур", "sheet-8", display_name="8 Orion")
    dbm.link_student_to_family(fam_id, sid)
    dbm.set_student_academic_year(sid, 2024)
    _seed_year(sid, 2024, [("Алгебра", 4), ("Химия", 4)])
    dbm.update_student_spreadsheet(sid, "sheet-9", display_name="9 Orion")
    dbm.set_student_academic_year(sid, 2025)
    _seed_year(sid, 2025, [("Алгебра", 3)])

    pdf_bytes, filename, _n, period_label, _l = _generate_dashboard_pdf(
        sid, 901001, days=30, academic_year='all')

    assert pdf_bytes[:4] == b'%PDF'
    assert 'all_years' in filename
    assert 'все годы' in period_label.lower()


def test_pdf_single_year_is_labelled_with_that_class(temp_db):
    """Отчёт за 8 класс подписан «8 Orion», хотя ребёнок уже в девятом."""
    from webapp.app import _generate_dashboard_pdf

    head_id = dbm.add_parent("Head", "998900000902", role='senior')
    dbm.update_parent_telegram_id("998900000902", 902001)
    fam_id = dbm.add_family("F-grad2")
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    sid = dbm.add_student("Заур", "sheet-8", display_name="8 Orion")
    dbm.link_student_to_family(fam_id, sid)
    dbm.set_student_academic_year(sid, 2024)
    _seed_year(sid, 2024, [("Алгебра", 4)])
    dbm.update_student_spreadsheet(sid, "sheet-9", display_name="9 Orion")
    dbm.set_student_academic_year(sid, 2025)

    _pdf, filename, _n, period_label, _l = _generate_dashboard_pdf(
        sid, 902001, days=30, academic_year='2024')

    assert '8 Orion' in period_label
    assert '2024' in filename
