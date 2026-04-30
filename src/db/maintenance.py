"""Re-export функций обслуживания БД (архивирование, чистки, каскадные удаления).

Используется schedulers (weekly cleanup) и admin handlers.
"""
from src.database_manager import (
    archive_old_grades,
    cleanup_old_notification_queue,
    cleanup_expired_invites,
    delete_family_cascade,
)

__all__ = [
    "archive_old_grades",
    "cleanup_old_notification_queue",
    "cleanup_expired_invites",
    "delete_family_cascade",
]
