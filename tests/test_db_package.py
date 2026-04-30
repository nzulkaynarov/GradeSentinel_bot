"""Тесты что src.db.* — корректные re-export (не сломались импорты после рефакторинга)."""


def test_db_auth_imports_match_original():
    from src.db import auth
    from src import database_manager as dm
    assert auth.is_head_of_family is dm.is_head_of_family
    assert auth.is_member_of_family is dm.is_member_of_family
    assert auth.can_manage_family is dm.can_manage_family
    assert auth.is_student_under_active_subscription is dm.is_student_under_active_subscription


def test_db_maintenance_imports_match_original():
    from src.db import maintenance
    from src import database_manager as dm
    assert maintenance.archive_old_grades is dm.archive_old_grades
    assert maintenance.cleanup_expired_invites is dm.cleanup_expired_invites
    assert maintenance.delete_family_cascade is dm.delete_family_cascade


def test_db_connection_imports_match_original():
    from src.db import connection
    from src import database_manager as dm
    assert connection.get_db_connection is dm.get_db_connection
    assert connection.init_db is dm.init_db
    assert connection.DB_PATH == dm.DB_PATH
