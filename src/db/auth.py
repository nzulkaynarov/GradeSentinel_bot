"""Re-export функций авторизации из database_manager.

Новый код должен импортировать ИЗ ЭТОГО модуля. Со временем имплементации
переедут сюда, а database_manager станет тонким фасадом.
"""
from src.database_manager import (
    is_head_of_family,
    is_member_of_family,
    can_manage_family,
    get_families_for_student,
    is_student_under_active_subscription,
    get_parent_role,
    get_families_for_head,
    is_subscription_active,
)

__all__ = [
    "is_head_of_family",
    "is_member_of_family",
    "can_manage_family",
    "get_families_for_student",
    "is_student_under_active_subscription",
    "get_parent_role",
    "get_families_for_head",
    "is_subscription_active",
]
