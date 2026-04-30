"""Тесты архивирования старых оценок и чистки БД."""
import src.database_manager as dbm


def _seed_old_grade(student_id: int, days_ago: int):
    """Вставляет оценку с указанной давностью (через прямой UPDATE date_added)."""
    with dbm.get_db_connection() as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO grade_history (student_id, subject, grade_value, raw_text, cell_reference)
            VALUES (?, 'Math', 5.0, '5', ?)
        ''', (student_id, f'Сегодня!Math:day-{days_ago}'))
        c.execute('''
            UPDATE grade_history
            SET date_added = datetime('now', ?)
            WHERE cell_reference = ?
        ''', (f'-{days_ago} days', f'Сегодня!Math:day-{days_ago}'))


def test_archive_moves_old_grades(temp_db):
    s_id = dbm.add_student("Test", "sheet1")
    _seed_old_grade(s_id, days_ago=10)
    _seed_old_grade(s_id, days_ago=200)

    moved = dbm.archive_old_grades(days=180)
    assert moved == 1

    # В архиве — одна, в основной таблице — одна
    with dbm.get_db_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) as n FROM grade_history WHERE student_id = ?", (s_id,))
        assert c.fetchone()['n'] == 1
        c.execute("SELECT COUNT(*) as n FROM grade_history_archive WHERE student_id = ?", (s_id,))
        assert c.fetchone()['n'] == 1


def test_cleanup_invites_no_active(temp_db):
    """Чистка истёкших инвайтов не трогает активные."""
    head = dbm.add_parent("H", "998900000099", role='senior')
    dbm.update_parent_telegram_id("998900000099", 200099)
    fam = dbm.add_family("F")
    dbm.set_family_head(fam, head)

    code = dbm.create_invite(fam, head, expires_hours=48)
    cleaned = dbm.cleanup_expired_invites(days=30)
    # Свежий инвайт не должен быть удалён
    assert cleaned == 0
    assert dbm.get_invite(code) is not None
