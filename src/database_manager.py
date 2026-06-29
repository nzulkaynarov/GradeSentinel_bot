"""Подключение к БД + инициализация схемы + re-export всего CRUD-слоя.

Миграция SQLite → PostgreSQL (2026-06-29):
  • соединение — src/db/pg.py (psycopg v3 + пул); см. также src/db/connection.py;
  • схема — Alembic (migrations/), а не in-code PRAGMA/ALTER миграции;
  • даты — timestamp (наивный UTC); арифметика «+5ч Ташкент» нормализует now()
    через `at time zone 'utc'`.

Файл остаётся фасадом: re-export функций из src/db/* для обратной совместимости
(существующие `from src.database_manager import add_grade` продолжают работать).
"""
import logging
import os
from typing import Any, Dict, List

from src.db.migrate import apply_migrations
from src.db.pg import (  # noqa: F401  (re-export для обратной совместимости)
    ForeignKeyViolation,
    IntegrityError,
    OperationalError,
    UniqueViolation,
    close_pool,
    get_db_connection,
)

logger = logging.getLogger(__name__)


def _ensure_admin() -> None:
    """Регистрирует супер-админа из ADMIN_ID (идемпотентно).

    Порт SQLite `INSERT OR IGNORE` → `INSERT ... ON CONFLICT DO NOTHING`.
    Телефон-заглушка `admin_<id>` удовлетворяет UNIQUE(phone) до первой
    реальной авторизации.
    """
    admin_id = os.environ.get("ADMIN_ID")
    if not admin_id:
        return
    try:
        admin_id_int = int(admin_id)
    except ValueError:
        logger.error("ADMIN_ID in environment is not a valid integer")
        return
    if admin_id_int <= 0:
        logger.warning(
            "ADMIN_ID=%s is not a valid Telegram user id (must be > 0). "
            "Skipping admin row insert to avoid ghost parent.",
            admin_id,
        )
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO parents (fio, phone, telegram_id, role) "
            "VALUES ('Super Admin', %s, %s, 'admin') ON CONFLICT DO NOTHING",
            (f"admin_{admin_id_int}", admin_id_int),
        )
        cursor.execute(
            "UPDATE parents SET role = 'admin' WHERE telegram_id = %s",
            (admin_id_int,),
        )


def _backfill_family_links() -> None:
    """Идемпотентный data-repair: семьям с head_id без строки в family_links —
    создать связь. Лечит исторические данные (главы, заведённые до того как
    process_head_choice стал звать link_parent_to_family). На чистой БД — no-op.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO family_links (family_id, parent_id)
            SELECT f.id, f.head_id FROM families f
            WHERE f.head_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM family_links fl
                  WHERE fl.family_id = f.id AND fl.parent_id = f.head_id
              )
            """
        )
        if cursor.rowcount and cursor.rowcount > 0:
            logger.info("Backfill: linked %d family heads to family_links.", cursor.rowcount)


def init_db() -> None:
    """Готовит БД к работе: применяет миграции Alembic (создаёт/обновляет схему),
    регистрирует супер-админа и чинит исторические orphan-связи семей.
    Вызывается на старте бота и в тест-харнесе."""
    apply_migrations()
    _ensure_admin()
    _backfill_family_links()
    logger.info("Database ready (PostgreSQL, schema via Alembic).")


# ─── CRUD re-export: оценки (write-path + четвертные + дашборд/scheduler) ──────
# Имплементация в src/db/grades.py. Re-export безопасен (нет обратных импортов).
from src.db.grades import (  # noqa: E402, F401
    add_grade,
    get_existing_grade,
    grade_exists_by_content,
    get_existing_grade_by_content,
    update_grade,
    update_grade_by_content,
    upsert_quarter_grade,
    get_quarter_grades,
    get_grade_history_for_student,
    get_grade_history_for_student_all,
    get_weakest_subject,
    get_today_grades_for_student,
    get_overnight_grades_for_student,
    get_yesterday_grades_for_student,
    has_today_grades_for_parent,
    has_recent_grades_for_parent,
    get_parents_for_student,
)

# Статистика и листинг — src/db/stats.py.
from src.db.stats import (  # noqa: E402, F401
    get_global_stats,
    get_user_stats,
    get_all_telegram_ids,
    get_user_info_by_tg_id,
    get_all_parents_with_children,
)

# Персистентное состояние (FSM, last_menu_id, support_msg_map) — src/db/state.py.
from src.db.state import (  # noqa: E402, F401
    get_last_menu_id,
    update_last_menu_id,
    set_user_state,
    get_user_state,
    clear_user_state,
    save_support_msg_map,
    get_support_user_id,
)

# Очередь уведомлений (тихие часы) — src/db/notifications.py.
from src.db.notifications import (  # noqa: E402, F401
    queue_notification,
    get_and_clear_queued_notifications,
    get_all_queued_telegram_ids,
    queue_group_notification,
    get_and_clear_queued_group_notifications,
    get_all_queued_group_targets,
)

# AI-чат (история + feedback) — src/db/ai_chat.py.
from src.db.ai_chat import (  # noqa: E402, F401
    save_chat_message,
    get_recent_chat_history,
    clear_chat_history,
    save_family_chat_message,
    get_recent_family_chat_history,
    clear_family_chat_history,
    save_feedback,
    get_feedback_for_message,
    get_message_owner,
    MAX_HISTORY_FOR_AI,
)

# Proactive-алерты (dedup) — src/db/alerts.py.
from src.db.alerts import (  # noqa: E402, F401
    save_alert,
    was_alerted_recently,
    get_last_alert_at,
    ALERT_COOLDOWN_HOURS,
)


# ====================
# Оценки за сегодня / ночь / вчера (для сводок и scheduler'а).
# Локальные определения переопределяют одноимённые re-export'ы из grades.py.
# Даты считаются по Ташкенту (UTC+5, без DST); now() нормализуется к наивному UTC.
# ====================
def get_today_grades_for_student(student_id: int) -> List[Dict[str, Any]]:
    """Все оценки студента за сегодня (по Ташкенту, UTC+5), по одной (свежей)
    на предмет. Дата — по grade_date, fallback date_added+5ч для legacy-строк."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT subject, grade_value, raw_text, date_added FROM (
                SELECT DISTINCT ON (subject)
                       subject, grade_value, raw_text, date_added
                FROM grade_history
                WHERE student_id = %s
                  AND COALESCE(grade_date, (date_added + interval '5 hours')::date)
                      = ((now() at time zone 'utc') + interval '5 hours')::date
                ORDER BY subject, date_added DESC
            ) t
            ORDER BY date_added
            """,
            (student_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_overnight_grades_for_student(student_id: int) -> List[Dict[str, Any]]:
    """Оценки, добавленные за ночь (22:00 вчера → сейчас по Ташкенту), по одной
    (свежей) на предмет. Нижняя граница = 17:00 UTC вчера."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # date_trunc('day', now_tashkent) - 2ч - 5ч = 22:00 вчера Ташкент = 17:00 вчера UTC.
        cursor.execute(
            """
            SELECT subject, grade_value, raw_text, cell_reference, date_added FROM (
                SELECT DISTINCT ON (subject)
                       subject, grade_value, raw_text, cell_reference, date_added
                FROM grade_history
                WHERE student_id = %s
                  AND date_added >= date_trunc('day', (now() at time zone 'utc') + interval '5 hours')
                                    - interval '2 hours' - interval '5 hours'
                  AND date_added <= (now() at time zone 'utc')
                ORDER BY subject, date_added DESC
            ) t
            ORDER BY date_added
            """,
            (student_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_yesterday_grades_for_student(student_id: int) -> List[Dict[str, Any]]:
    """Все оценки студента за вчера (по Ташкенту, UTC+5)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT subject, grade_value, raw_text
            FROM grade_history
            WHERE student_id = %s
              AND COALESCE(grade_date, (date_added + interval '5 hours')::date)
                  = (((now() at time zone 'utc') + interval '5 hours')::date - 1)
            ORDER BY date_added
            """,
            (student_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def has_today_grades_for_parent(telegram_id: int) -> bool:
    """Есть ли сегодня хоть одна оценка у детей родителя (по Ташкенту, UTC+5)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) AS c FROM grade_history gh
            JOIN family_links fl ON gh.student_id = fl.student_id
            JOIN parents p ON fl.parent_id = p.id
            WHERE p.telegram_id = %s
              AND COALESCE(gh.grade_date, (gh.date_added + interval '5 hours')::date)
                  = ((now() at time zone 'utc') + interval '5 hours')::date
            """,
            (telegram_id,),
        )
        return cursor.fetchone()["c"] > 0


# Инвайт-ссылки — src/db/invites.py.
from src.db.invites import (  # noqa: E402, F401
    create_invite,
    get_invite,
    use_invite,
)

# Семейные групповые чаты — src/db/groups.py.
from src.db.groups import (  # noqa: E402, F401
    link_group_to_family,
    get_family_for_group,
    get_groups_for_family,
    get_groups_for_student,
    unlink_group,
    update_group_thread,
)

# Обслуживание БД (архив, чистки, каскадное удаление) — src/db/maintenance.py.
from src.db.maintenance import (  # noqa: E402, F401
    archive_old_grades,
    cleanup_old_notification_queue,
    cleanup_expired_invites,
    delete_family_cascade,
)

# Настройки (key-value) — src/db/settings.py.
from src.db.settings import (  # noqa: E402, F401
    get_setting,
    set_setting,
    get_plans_from_db,
    save_plans_to_db,
)

# Промокоды — src/db/promo.py.
from src.db.promo import (  # noqa: E402, F401
    create_promo_code,
    get_promo_code,
    use_promo_code,
    list_promo_codes,
    delete_promo_code,
)

# ─── Families re-export ───────────────────────────────────────────────────────
# Сначала families — auth.py через обратный re-export берёт get_families_for_student
# / is_student_under_active_subscription отсюда.
from src.db.families import (  # noqa: E402, F401
    get_active_spreadsheets,
    get_active_spreadsheets_with_subscription,
    get_families_for_student,
    get_families_for_user,
    is_student_under_active_subscription,
    add_family,
    add_student,
    update_student_display_name,
    update_student_spreadsheet,
    set_family_head,
    link_parent_to_family,
    link_student_to_family,
    get_child_count,
    get_all_families,
    get_students_for_parent,
    has_children_for_grades,
    get_family_members,
    get_family_members_telegram_ids,
    get_family_students,
    delete_parent_from_family,
    delete_student_from_family,
)

# ─── Payments re-export ───────────────────────────────────────────────────────
from src.db.payments import (  # noqa: E402, F401
    get_family_subscription,
    extend_subscription,
    record_payment,
    is_subscription_active,
    has_any_active_subscription,
    cancel_subscription,
    get_families_expiring_in_days,
    get_families_expired_today,
)

# ─── Auth re-export (В САМОМ КОНЦЕ) ───────────────────────────────────────────
# auth.py делает обратный re-export get_families_for_student /
# is_student_under_active_subscription / is_subscription_active — они уже
# определены выше (families/payments), поэтому к этой строке доступны.
from src.db.auth import (  # noqa: E402, F401
    get_parent_by_phone,
    get_parent_by_telegram,
    get_parent_id_by_telegram,
    update_parent_telegram_id,
    update_parent_first_name,
    get_greeting_name,
    add_parent,
    get_parent_role,
    get_user_lang,
    set_user_lang,
    get_notify_mode,
    set_notify_mode,
    get_families_for_head,
    is_head_of_any_family,
    is_head_of_family,
    is_member_of_family,
    can_manage_family,
)


if __name__ == "__main__":
    init_db()
    print("Database initialized (PostgreSQL, schema via Alembic).")
