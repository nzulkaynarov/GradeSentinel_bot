"""Тесты delete_family_cascade — fix 4 (PRAGMA foreign_keys + ON DELETE CASCADE)."""
import src.database_manager as dbm
from src.db.maintenance import delete_family_cascade


def _seed_family(name="F", phone_prefix="9989000001"):
    head_id = dbm.add_parent("Head", phone_prefix + "1", role='senior')
    fam_id = dbm.add_family(name)
    dbm.set_family_head(fam_id, head_id)
    dbm.link_parent_to_family(fam_id, head_id)
    student_id = dbm.add_student("Kid", "sheet-id-1")
    dbm.link_student_to_family(fam_id, student_id)
    return fam_id, head_id, student_id


def test_delete_family_cleans_payments(temp_db):
    fam_id, head_id, _ = _seed_family()
    dbm.record_payment(
        family_id=fam_id, paid_by_parent_id=head_id, amount=29900,
        currency='UZS', plan='monthly', months=1
    )
    delete_family_cascade(fam_id)
    with dbm.get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as n FROM payments WHERE family_id = ?", (fam_id,))
        assert c.fetchone()['n'] == 0


def test_delete_family_cleans_invites(temp_db):
    fam_id, head_id, _ = _seed_family()
    dbm.create_invite(fam_id, head_id, expires_hours=48)
    delete_family_cascade(fam_id)
    with dbm.get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as n FROM family_invites WHERE family_id = ?", (fam_id,))
        assert c.fetchone()['n'] == 0


def test_delete_family_cleans_orphan_student_data(temp_db):
    fam_id, _, student_id = _seed_family()
    # Добавим оценку и четверть
    dbm.add_grade(student_id, "Math", 5.0, "5", "Сегодня!Math:2026-04-30")
    dbm.upsert_quarter_grade(student_id, "Math", 1, 5.0, "5")

    delete_family_cascade(fam_id)

    with dbm.get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as n FROM students WHERE id = ?", (student_id,))
        assert c.fetchone()['n'] == 0
        c.execute("SELECT COUNT(*) as n FROM grade_history WHERE student_id = ?", (student_id,))
        assert c.fetchone()['n'] == 0
        c.execute("SELECT COUNT(*) as n FROM quarter_grades WHERE student_id = ?", (student_id,))
        assert c.fetchone()['n'] == 0


def test_delete_family_returns_false_for_unknown(temp_db):
    assert delete_family_cascade(99999) is False


def test_delete_family_keeps_shared_student(temp_db):
    """Если студент привязан к двум семьям — удаление одной не должно удалить студента."""
    fam1, _, student_id = _seed_family("F1", phone_prefix="9989000010")
    fam2 = dbm.add_family("F2")
    dbm.link_student_to_family(fam2, student_id)

    delete_family_cascade(fam1)

    with dbm.get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as n FROM students WHERE id = ?", (student_id,))
        assert c.fetchone()['n'] == 1  # студент жив, привязан к F2
