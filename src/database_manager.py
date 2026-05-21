import sqlite3
import os
import logging
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get(
    "DATABASE_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sentinel.db"),
)

@contextmanager
def get_db_connection():
    """Контекстный менеджер для безопасного подключения к БД.

    Коммитит только при успешном выходе из блока. При исключении делает rollback,
    чтобы избежать частично применённых транзакций.
    """
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.execute('PRAGMA foreign_keys=ON;')
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception as rb_err:
            logger.error(f"Rollback failed: {rb_err}")
        raise
    finally:
        conn.close()

def _table_exists(cursor, table: str) -> bool:
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cursor.fetchone() is not None


def _stage_1c_already_applied(cursor) -> bool:
    """True если grade_history.grade_date уже NOT NULL — миграция 1C сделана."""
    cursor.execute("PRAGMA table_info(grade_history)")
    for row in cursor.fetchall():
        # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
        if row[1] == 'grade_date':
            return bool(row[3])
    return False


def init_db():
    """Инициализация таблиц базы данных."""
    # Ensure data directory exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Миграция: проверяем наличие колонки role в parents (только для существующих БД)
        if _table_exists(cursor, 'parents'):
            cursor.execute("PRAGMA table_info(parents)")
            columns = [column[1] for column in cursor.fetchall()]
            if 'phone' in columns and 'role' not in columns:
                # Проверяем, был ли старый is_admin
                is_admin_exists = 'is_admin' in columns
                cursor.execute("ALTER TABLE parents ADD COLUMN role TEXT DEFAULT 'senior'")
                if is_admin_exists:
                    cursor.execute("UPDATE parents SET role = 'admin' WHERE is_admin = 1")
                logger.info("Database migration: role column added.")

            # Миграция: notify_mode
            cursor.execute("PRAGMA table_info(parents)")
            columns_fresh = [column[1] for column in cursor.fetchall()]
            if 'notify_mode' not in columns_fresh:
                cursor.execute("ALTER TABLE parents ADD COLUMN notify_mode TEXT DEFAULT 'instant'")
                logger.info("Database migration: notify_mode column added.")

            # Миграция: telegram_first_name (для приветствий "Здравствуйте, Super!").
            # Обновляется на каждом /start — Telegram-имя могло поменяться.
            cursor.execute("PRAGMA table_info(parents)")
            columns_fresh = [column[1] for column in cursor.fetchall()]
            if 'telegram_first_name' not in columns_fresh:
                cursor.execute("ALTER TABLE parents ADD COLUMN telegram_first_name TEXT")
                logger.info("Database migration: telegram_first_name column added.")

        # 1. Семьи
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS families (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            family_name TEXT NOT NULL,
            subscription_end TIMESTAMP,
            head_id INTEGER,
            FOREIGN KEY(head_id) REFERENCES parents(id)
        )
        ''')

        # Миграция 2: Добавляем head_id в families если его нет
        cursor.execute("PRAGMA table_info(families)")
        columns_families = [column[1] for column in cursor.fetchall()]
        # Существуют ли таблицы parents и family_links — для миграции данных
        parents_exists = _table_exists(cursor, 'parents')
        links_exists = _table_exists(cursor, 'family_links')
        if 'head_id' not in columns_families and parents_exists and links_exists:
            cursor.execute("ALTER TABLE families ADD COLUMN head_id INTEGER")
            logger.info("Database migration: head_id column added to families.")
            
            # Переносим всех текущих глав из parents в families
            cursor.execute('''
                SELECT p.id as parent_id, fl.family_id 
                FROM parents p
                JOIN family_links fl ON p.id = fl.parent_id
                WHERE p.role = 'head'
            ''')
            heads = cursor.fetchall()
            for h in heads:
                cursor.execute("UPDATE families SET head_id = ? WHERE id = ?", (h['parent_id'], h['family_id']))
                
            # Очищаем глобальные роли (остается admin и senior)
            cursor.execute("UPDATE parents SET role = 'senior' WHERE role = 'head'")
            logger.info("Database migration: moved 'head' roles to families.head_id")

        # 2. Родители (пользователи)
        # role: 'admin' (супер-админ), 'head' (глава семьи), 'senior' (член семьи)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS parents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fio TEXT NOT NULL,
            phone TEXT UNIQUE NOT NULL,
            telegram_id INTEGER UNIQUE,
            role TEXT DEFAULT 'senior',
            telegram_first_name TEXT
        )
        ''')

        # 3. Дети (Студенты)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fio TEXT NOT NULL,
            spreadsheet_id TEXT NOT NULL,
            display_name TEXT
        )
        ''')

        # Миграция: добавляем display_name если нет
        cursor.execute("PRAGMA table_info(students)")
        columns_students = [column[1] for column in cursor.fetchall()]
        if 'display_name' not in columns_students:
            cursor.execute("ALTER TABLE students ADD COLUMN display_name TEXT")
            logger.info("Database migration: display_name column added to students.")

        # 4. Связи "Семьи - Родители - Дети"
        # У одного студента может быть несколько родителей (членов семьи)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS family_links (
            family_id INTEGER,
            parent_id INTEGER,
            student_id INTEGER,
            FOREIGN KEY(family_id) REFERENCES families(id),
            FOREIGN KEY(parent_id) REFERENCES parents(id),
            FOREIGN KEY(student_id) REFERENCES students(id),
            UNIQUE(family_id, parent_id, student_id)
        )
        ''')

        # 5. История оценок (для мониторинга)
        # grade_date — фактическая дата оценки (NOT NULL после этапа 1C RFC).
        # UNIQUE по содержимому (student, subject, grade_date, raw_text) —
        # cell_reference остался как debug-info, но больше не определяет уникальность.
        # Для legacy-БД миграция в блоке 11b ниже (recreate-table).
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS grade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            grade_value REAL,
            raw_text TEXT NOT NULL,
            cell_reference TEXT NOT NULL,
            grade_date DATE NOT NULL,
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(student_id) REFERENCES students(id),
            UNIQUE(student_id, subject, grade_date, raw_text)
        )
        ''')
        
        # 6. Четвертные оценки
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS quarter_grades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            quarter INTEGER NOT NULL,
            grade_value REAL,
            raw_text TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(student_id) REFERENCES students(id),
            UNIQUE(student_id, subject, quarter)
        )
        ''')

        # 7. Автоматическая регистрация администратора из .env
        admin_id = os.environ.get("ADMIN_ID")
        if admin_id:
            try:
                admin_id_int = int(admin_id)
                # Поскольку мы не знаем номер телефона админа заранее при первой авторизации по ID,
                # создадим заглушку для телефона из ID, чтобы удовлетворить ограничение UNIQUE.
                # Пользователь потом сможет авторизоваться с любого телефона, либо бот обновит запись.
                # Но лучше: используем telegram_id сразу как приоритетный ключ.
                cursor.execute('''
                INSERT OR IGNORE INTO parents (fio, phone, telegram_id, role) 
                VALUES ('Super Admin', ?, ?, 'admin')
                ''', (f"admin_{admin_id_int}", admin_id_int))
                
                # Если админ уже был по телефону, но без telegram_id, или роль сбилась:
                cursor.execute('UPDATE parents SET role = "admin" WHERE telegram_id = ?', (admin_id_int,))
            except ValueError:
                logger.error("ADMIN_ID in environment is not a valid integer")

        # 7. Состояния приложения (для очистки меню)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS app_states (
            user_id INTEGER PRIMARY KEY,
            last_menu_msg_id INTEGER
        )
        ''')

        # 8. Состояния пользователей (для multi-step flows, персистентно)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_states (
            user_id INTEGER PRIMARY KEY,
            state TEXT NOT NULL,
            data TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # 9. Маппинг сообщений поддержки (admin_group msg_id -> user_id)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS support_msg_map (
            admin_msg_id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL
        )
        ''')

        # 10. Очередь уведомлений для тихих часов
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS notification_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # 10a. Очередь групповых уведомлений (тихие часы для семейных чатов).
        # Inline-markup НЕ сохраняем — после ночи кнопки могут устареть.
        # Ключ flush'а: (chat_id, message_thread_id) — в одной супергруппе
        # может быть несколько тем (по теме на семью).
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS group_notification_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            message_thread_id INTEGER,
            message TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # 10b. AI conversation history (PR_D R6). Сообщения родитель↔AI для
        # multi-turn чата с памятью контекста. Ключ — (telegram_id, student_id):
        # каждый ребёнок имеет отдельную ветку разговора. role: 'user'|'assistant'.
        # Limit логикой держим N последних, чтобы не палить токены.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS ai_chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_ai_chat_tg_student '
            'ON ai_chat_messages(telegram_id, student_id, created_at)'
        )

        # 10c. Proactive AI alerts (PR_H5). Dedup log — чтобы не отправлять
        # один и тот же тип alert'а по одному ребёнку чаще раза в 48 часов.
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS proactive_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            alert_type TEXT NOT NULL,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
        )
        ''')
        cursor.execute(
            'CREATE INDEX IF NOT EXISTS idx_proactive_alerts_lookup '
            'ON proactive_alerts(student_id, alert_type, sent_at DESC)'
        )

        # 11. Миграция: колонка lang для мультиязычности
        if _table_exists(cursor, 'parents'):
            cursor.execute("PRAGMA table_info(parents)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'lang' not in columns:
                cursor.execute("ALTER TABLE parents ADD COLUMN lang TEXT DEFAULT 'ru'")
                logger.info("Database migration: lang column added to parents.")

        # 11a. Миграция: nullable колонка grade_date в grade_history и архиве.
        # Этап 1A RFC (Docs/rfc-grades-source-of-truth.md): отделяем фактическую
        # дату оценки от технического `date_added`. На этой стадии — просто
        # ALTER ADD COLUMN; backfill отдельным скриптом scripts/backfill_grade_date.py.
        for tbl in ('grade_history', 'grade_history_archive'):
            if _table_exists(cursor, tbl):
                cursor.execute(f"PRAGMA table_info({tbl})")
                cols = [c[1] for c in cursor.fetchall()]
                if 'grade_date' not in cols:
                    cursor.execute(f"ALTER TABLE {tbl} ADD COLUMN grade_date DATE")
                    logger.info(f"Database migration: grade_date column added to {tbl}.")

        # 11b. Этап 1C RFC: grade_date NOT NULL + UNIQUE(student, subject,
        # grade_date, raw_text) вместо UNIQUE(student, cell_reference).
        # SQLite не умеет ALTER COLUMN SET NOT NULL или DROP UNIQUE — нужен
        # recreate-table. Идемпотентно: если новая схема уже применена — skip.
        # Безопасность: если есть строки с grade_date IS NULL — отказываемся
        # мигрировать и логируем WARNING. Запустить scripts/backfill_grade_date.py
        # --apply, потом следующий рестарт бота добъёт миграцию.
        if _table_exists(cursor, 'grade_history') and not _stage_1c_already_applied(cursor):
            cursor.execute("SELECT COUNT(*) FROM grade_history WHERE grade_date IS NULL")
            null_count = cursor.fetchone()[0]
            if null_count > 0:
                logger.warning(
                    f"Skip stage 1C migration: {null_count} grade_history rows have "
                    f"grade_date IS NULL. Run scripts/backfill_grade_date.py --apply first."
                )
            else:
                cursor.execute("SELECT COUNT(*) FROM grade_history")
                old_count = cursor.fetchone()[0]
                cursor.executescript('''
                    CREATE TABLE grade_history_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        student_id INTEGER NOT NULL,
                        subject TEXT NOT NULL,
                        grade_value REAL,
                        raw_text TEXT NOT NULL,
                        cell_reference TEXT NOT NULL,
                        grade_date DATE NOT NULL,
                        date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(student_id) REFERENCES students(id),
                        UNIQUE(student_id, subject, grade_date, raw_text)
                    );
                ''')
                # ORDER BY id: при коллизии (одинаковое содержимое из разных
                # листов — Сегодня! и Все оценки!) выигрывает строка с меньшим
                # id, то есть самая ранняя. Обычно это запись от monitor'а из
                # «Сегодня» (writer #1), потерянная же сторона — дубль импорта.
                cursor.execute('''
                    INSERT OR IGNORE INTO grade_history_new
                      (id, student_id, subject, grade_value, raw_text,
                       cell_reference, grade_date, date_added)
                    SELECT id, student_id, subject, grade_value, raw_text,
                           cell_reference, grade_date, date_added
                    FROM grade_history
                    ORDER BY id
                ''')
                cursor.execute("SELECT COUNT(*) FROM grade_history_new")
                new_count = cursor.fetchone()[0]
                cursor.executescript('''
                    DROP TABLE grade_history;
                    ALTER TABLE grade_history_new RENAME TO grade_history;
                ''')
                logger.info(
                    f"Database migration 1C: grade_history recreated. "
                    f"NOT NULL grade_date + UNIQUE(student,subject,grade_date,raw_text). "
                    f"Rows: {old_count} -> {new_count} (dropped {old_count - new_count} content-dupes)."
                )

        # 12. Индексы для производительности
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_grade_history_student_date ON grade_history(student_id, date_added)')
        # idx_grade_history_student_cell — НЕ UNIQUE после этапа 1C, но всё ещё
        # используется как покрывающий для get_existing_grade / update_grade
        # (`WHERE student_id = ? AND cell_reference = ?`). Не дропать.
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_grade_history_student_cell ON grade_history(student_id, cell_reference)')
        # Покрывающий индекс под чтения по фактической дате оценки (этап 1C).
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_grade_history_student_grade_date ON grade_history(student_id, grade_date)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_family_links_parent ON family_links(parent_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_family_links_student ON family_links(student_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_family_links_family ON family_links(family_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_parents_telegram ON parents(telegram_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_notification_queue_tg ON notification_queue(telegram_id)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_quarter_grades_student ON quarter_grades(student_id)')

        # 13. Таблица инвайт-ссылок для семей
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS family_invites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            family_id INTEGER NOT NULL,
            invite_code TEXT UNIQUE NOT NULL,
            created_by INTEGER NOT NULL,
            used_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            is_used INTEGER DEFAULT 0,
            FOREIGN KEY(family_id) REFERENCES families(id),
            FOREIGN KEY(created_by) REFERENCES parents(id)
        )
        ''')

        # 14. Таблица платежей и подписок
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            family_id INTEGER NOT NULL,
            telegram_payment_charge_id TEXT,
            provider_payment_charge_id TEXT,
            amount INTEGER NOT NULL,
            currency TEXT NOT NULL DEFAULT 'UZS',
            plan TEXT NOT NULL DEFAULT 'basic',
            months INTEGER NOT NULL DEFAULT 1,
            paid_by INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(family_id) REFERENCES families(id),
            FOREIGN KEY(paid_by) REFERENCES parents(id)
        )
        ''')

        # 15. Настройки бота (key-value, для тарифов и прочего)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        ''')

        # 16. Промокоды
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS promo_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            plan TEXT NOT NULL,
            discount_percent INTEGER NOT NULL DEFAULT 0,
            free_months INTEGER NOT NULL DEFAULT 0,
            max_uses INTEGER NOT NULL DEFAULT 1,
            used_count INTEGER NOT NULL DEFAULT 0,
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')

        # Однократная backfill-миграция: для всех families с head_id, у которых
        # нет записи в family_links — создать её. Это лечит исторические данные,
        # созданные до того как process_head_choice стал делать link_parent_to_family.
        # Безопасно при повторном запуске благодаря NOT EXISTS.
        # Дёшево: один SQL без цикла на стороне Python.
        cursor.execute('''
            INSERT INTO family_links (family_id, parent_id)
            SELECT f.id, f.head_id
            FROM families f
            WHERE f.head_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM family_links fl
                  WHERE fl.family_id = f.id AND fl.parent_id = f.head_id
              )
        ''')
        if cursor.rowcount > 0:
            logger.info(f"Backfill migration: linked {cursor.rowcount} family heads to family_links.")

        # 17. Семейные групповые чаты — куда дублируются уведомления об оценках.
        # Один chat_id может быть привязан только к одной семье (UNIQUE),
        # одна семья может иметь несколько групп (например, чат с обоими бабушками+дедушками
        # и отдельный мини-чат родителей).
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS family_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            family_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL UNIQUE,
            chat_title TEXT,
            message_thread_id INTEGER,
            added_by INTEGER NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(family_id) REFERENCES families(id),
            FOREIGN KEY(added_by) REFERENCES parents(id)
        )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_family_groups_family ON family_groups(family_id)')

        # Миграция: добавить колонку message_thread_id если БД создана старой версией
        cursor.execute("PRAGMA table_info(family_groups)")
        fg_cols = [c[1] for c in cursor.fetchall()]
        if 'message_thread_id' not in fg_cols:
            cursor.execute("ALTER TABLE family_groups ADD COLUMN message_thread_id INTEGER")
            logger.info("Database migration: message_thread_id column added to family_groups.")

        # 18. Архив старых оценок (для контроля размера БД)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS grade_history_archive (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            grade_value REAL,
            raw_text TEXT NOT NULL,
            cell_reference TEXT NOT NULL,
            grade_date DATE,
            date_added TIMESTAMP NOT NULL,
            archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_grade_archive_student_date ON grade_history_archive(student_id, date_added)')
                
# Оценки (write-path + четвертные + получение для дашборда / scheduler'а) —
# в src/db/grades.py. Re-export здесь же, безопасно (нет обратных импортов).
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
    get_today_grades_for_student,
    get_overnight_grades_for_student,
    get_yesterday_grades_for_student,
    has_today_grades_for_parent,
    has_recent_grades_for_parent,
    get_parents_for_student,
)

# Families CRUD + active_spreadsheets + scope per-user — в src/db/families.py.
# Re-export ниже в файле после auth (auth.py делает обратный re-export
# get_families_for_student из этого модуля).


# delete_family_cascade — в src/db/maintenance.py (re-export ниже)

# Статистика и листинг — в src/db/stats.py.
from src.db.stats import (  # noqa: E402, F401
    get_global_stats,
    get_user_stats,
    get_all_telegram_ids,
    get_user_info_by_tg_id,
    get_all_parents_with_children,
)


if __name__ == '__main__':
    init_db()
    print("Database initialized successfully at", DB_PATH)

# Персистентное состояние (FSM, last_menu_id, support_msg_map) — в src/db/state.py.
from src.db.state import (  # noqa: E402, F401
    get_last_menu_id,
    update_last_menu_id,
    set_user_state,
    get_user_state,
    clear_user_state,
    save_support_msg_map,
    get_support_user_id,
)


# Очередь уведомлений (тихие часы) — в src/db/notifications.py.
from src.db.notifications import (  # noqa: E402, F401
    queue_notification,
    get_and_clear_queued_notifications,
    get_all_queued_telegram_ids,
    queue_group_notification,
    get_and_clear_queued_group_notifications,
    get_all_queued_group_targets,
)


from src.db.ai_chat import (  # noqa: E402, F401
    save_chat_message,
    get_recent_chat_history,
    clear_chat_history,
    MAX_HISTORY_FOR_AI,
)


from src.db.alerts import (  # noqa: E402, F401
    save_alert,
    was_alerted_recently,
    get_last_alert_at,
    ALERT_COOLDOWN_HOURS,
)


# ====================
# Оценки за сегодня (для вечерней сводки)
# ====================
def get_today_grades_for_student(student_id: int) -> List[Dict[str, Any]]:
    """Возвращает все оценки студента за сегодня (по Ташкенту, UTC+5).
    Дедупликация по предмету: если оценка попала из 'Все оценки' и 'Сегодня',
    берём самую свежую запись для каждого предмета. Дата сегодня — по grade_date,
    fallback на date(date_added, '+5 hours') для legacy-записей."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT subject, grade_value, raw_text, MAX(date_added) as date_added
            FROM grade_history
            WHERE student_id = ?
              AND COALESCE(grade_date, date(date_added, '+5 hours'))
                  = date('now', '+5 hours')
            GROUP BY subject
            ORDER BY date_added
        ''', (student_id,))
        return [dict(row) for row in cursor.fetchall()]


def get_overnight_grades_for_student(student_id: int) -> List[Dict[str, Any]]:
    """Возвращает оценки студента, добавленные за ночь (с 22:00 до 07:00 по Ташкенту).
    Дедупликация по предмету: берём последнюю запись."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Берём оценки добавленные с 22:00 вчера по Ташкенту (17:00 UTC) до сейчас
        # datetime('now','+5 hours','start of day','-2 hours','-5 hours')
        #   = полночь Ташкента → -2ч = 22:00 вчера Ташкент → -5ч = 17:00 вчера UTC
        cursor.execute('''
            SELECT subject, grade_value, raw_text, cell_reference,
                   MAX(date_added) as date_added
            FROM grade_history
            WHERE student_id = ?
              AND date_added >= datetime('now', '+5 hours', 'start of day', '-2 hours', '-5 hours')
              AND date_added <= datetime('now')
            GROUP BY subject
            ORDER BY date_added
        ''', (student_id,))
        return [dict(row) for row in cursor.fetchall()]


def get_yesterday_grades_for_student(student_id: int) -> List[Dict[str, Any]]:
    """Возвращает все оценки студента за вчера (по Ташкенту, UTC+5).
    По grade_date с fallback на date(date_added, '+5 hours') для legacy-записей."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT subject, grade_value, raw_text
            FROM grade_history
            WHERE student_id = ?
              AND COALESCE(grade_date, date(date_added, '+5 hours'))
                  = date('now', '+5 hours', '-1 day')
            ORDER BY date_added
        ''', (student_id,))
        return [dict(row) for row in cursor.fetchall()]


def has_today_grades_for_parent(telegram_id: int) -> bool:
    """Проверяет, есть ли сегодня хоть одна оценка у детей родителя (по Ташкенту, UTC+5).
    Сравнение по grade_date с fallback на date(date_added, '+5 hours')."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) as c FROM grade_history gh
            JOIN family_links fl ON gh.student_id = fl.student_id
            JOIN parents p ON fl.parent_id = p.id
            WHERE p.telegram_id = ?
              AND COALESCE(gh.grade_date, date(gh.date_added, '+5 hours'))
                  = date('now', '+5 hours')
        ''', (telegram_id,))
        return cursor.fetchone()['c'] > 0


# Очередь уведомлений (тихие часы) — в src/db/notifications.py.
from src.db.notifications import (  # noqa: E402, F401
    queue_notification,
    get_and_clear_queued_notifications,
    get_all_queued_telegram_ids,
    queue_group_notification,
    get_and_clear_queued_group_notifications,
    get_all_queued_group_targets,
)


# Инвайт-ссылки — имплементация в src/db/invites.py.
from src.db.invites import (  # noqa: E402, F401
    create_invite,
    get_invite,
    use_invite,
)


# get_parent_id_by_telegram / get_parent_by_telegram — в src/db/auth.py.
# Подписки и платежи — в src/db/payments.py.
# get_families_for_user — в src/db/families.py.
# Re-export блоки внизу файла (после init_db).


# has_any_active_subscription — в src/db/payments.py (re-export ниже в файле)


# ====================
# Настройки (key-value)
# Семейные групповые чаты — имплементация в src/db/groups.py.
from src.db.groups import (  # noqa: E402, F401
    link_group_to_family,
    get_family_for_group,
    get_groups_for_family,
    get_groups_for_student,
    unlink_group,
    update_group_thread,
)


# Обслуживание БД (архивирование, чистки, каскад) — в src/db/maintenance.py.
from src.db.maintenance import (  # noqa: E402, F401
    archive_old_grades,
    cleanup_old_notification_queue,
    cleanup_expired_invites,
    delete_family_cascade,
)


# Settings k-v — имплементация переехала в src/db/settings.py.
# Re-export для backward compat: `from src.database_manager import get_setting`.
from src.db.settings import (  # noqa: E402, F401
    get_setting,
    set_setting,
    get_plans_from_db,
    save_plans_to_db,
)


# ====================
# Промокоды — имплементация переехала в src/db/promo.py.
# Тут оставлен re-export для backward compat: существующие импорты
# `from src.database_manager import create_promo_code` продолжают работать.
# Новый код должен импортировать из src.db.promo напрямую.
# ====================
from src.db.promo import (  # noqa: E402
    create_promo_code,
    get_promo_code,
    use_promo_code,
    list_promo_codes,
    delete_promo_code,
)


# cancel_subscription / get_families_expiring_in_days / get_families_expired_today —
# в src/db/payments.py (re-export ниже).


# get_all_parents_with_children — теперь в src/db/stats.py (re-export сверху)


# ─── Families re-export ──────────────────────────────────────────────
# Семьи + ученики + scope per-user. Имплементация в src/db/families.py.
# Сначала families — потому что auth.py через обратный re-export берёт
# get_families_for_student / is_student_under_active_subscription отсюда.
from src.db.families import (  # noqa: E402, F401
    get_active_spreadsheets,
    get_active_spreadsheets_with_subscription,
    get_families_for_student,
    get_families_for_user,
    is_student_under_active_subscription,
    add_family,
    add_student,
    update_student_display_name,
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

# ─── Payments re-export ──────────────────────────────────────────────
# После families: has_any_active_subscription(payments) → get_families_for_user
# (families). При вызове families уже загружен.
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

# ─── Auth re-export ──────────────────────────────────────────────────
# Помещён В САМЫЙ КОНЕЦ файла, потому что auth.py делает обратный re-export
# `get_families_for_student` / `is_student_under_active_subscription` /
# `is_subscription_active` — эти функции теперь re-export'ены выше через
# families/payments, и к моменту выполнения этой строки уже доступны как
# имена в database_manager. См. [feedback-codebase-gotchas] пункт 18.
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
