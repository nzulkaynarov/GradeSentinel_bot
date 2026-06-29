"""Смена ссылки на таблицу у существующего ученика сохраняет историю.

Сентябрьский сценарий: школа выдаёт новую ссылку на оценки. grade_history и
quarter_grades привязаны к student_id (не к spreadsheet_id), поэтому
update_student_spreadsheet меняет ссылку in-place без потери истории — в
отличие от delete+re-add, который каскадно сносит оценки.
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.database_manager as dbm


def _seed_grade(student_id, subject, raw_text, grade_date, cell_reference="X1"):
    with dbm.get_db_connection() as conn:
        conn.cursor().execute(
            "INSERT INTO grade_history "
            "(student_id, subject, grade_value, raw_text, cell_reference, grade_date) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (student_id, subject, None, raw_text, cell_reference, grade_date),
        )


def _spreadsheet_of(student_id):
    with dbm.get_db_connection() as conn:
        row = conn.cursor().execute(
            "SELECT spreadsheet_id, display_name FROM students WHERE id = %s",
            (student_id,),
        ).fetchone()
        return (row["spreadsheet_id"], row["display_name"]) if row else (None, None)


def _grade_count(student_id):
    with dbm.get_db_connection() as conn:
        return conn.cursor().execute(
            "SELECT COUNT(*) c FROM grade_history WHERE student_id = %s",
            (student_id,),
        ).fetchone()["c"]


def test_relink_preserves_history(temp_db):
    sid = dbm.add_student("Заур 8 Orion", "OLD_SHEET_ID", display_name="8 Orion")
    _seed_grade(sid, "Математика", "5", "2025-09-10")
    _seed_grade(sid, "История", "4", "2025-12-01")
    assert _grade_count(sid) == 2

    ok = dbm.update_student_spreadsheet(sid, "NEW_SHEET_ID", display_name="9 Orion")
    assert ok is True

    ss, dn = _spreadsheet_of(sid)
    assert ss == "NEW_SHEET_ID"          # ссылка сменилась
    assert dn == "9 Orion"               # класс обновился
    assert _grade_count(sid) == 2        # вся история на месте (keyed by student_id)


def test_relink_without_display_name_keeps_old(temp_db):
    sid = dbm.add_student("Kid", "OLD", display_name="7 Nova")
    ok = dbm.update_student_spreadsheet(sid, "NEW")  # без display_name
    assert ok is True
    ss, dn = _spreadsheet_of(sid)
    assert ss == "NEW"
    assert dn == "7 Nova"                # display_name не затёрт


def test_relink_unknown_student_returns_false(temp_db):
    assert dbm.update_student_spreadsheet(999999, "NEW") is False
