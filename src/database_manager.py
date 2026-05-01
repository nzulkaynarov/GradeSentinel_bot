import sqlite3
import os
import logging
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sentinel.db")

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
            role TEXT DEFAULT 'senior'
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
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS grade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            grade_value REAL,
            raw_text TEXT NOT NULL,
            cell_reference TEXT NOT NULL,
            date_added TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(student_id) REFERENCES students(id),
            UNIQUE(student_id, cell_reference)
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

        # 11. Миграция: колонка lang для мультиязычности
        if _table_exists(cursor, 'parents'):
            cursor.execute("PRAGMA table_info(parents)")
            columns = [col[1] for col in cursor.fetchall()]
            if 'lang' not in columns:
                cursor.execute("ALTER TABLE parents ADD COLUMN lang TEXT DEFAULT 'ru'")
                logger.info("Database migration: lang column added to parents.")

        # 12. Индексы для производительности
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_grade_history_student_date ON grade_history(student_id, date_added)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_grade_history_student_cell ON grade_history(student_id, cell_reference)')
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
            date_added TIMESTAMP NOT NULL,
            archived_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_grade_archive_student_date ON grade_history_archive(student_id, date_added)')
                
def add_grade(student_id: int, subject: str, grade_value: Optional[float], raw_text: str, cell_reference: str) -> bool:
    """
    Добавляет новую оценку в БД, если такой еще нет.
    Возвращает True, если оценка новая (успешно добавлена),
    и False, если дубликат (такая cell_reference уже есть для этого студента).
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO grade_history (student_id, subject, grade_value, raw_text, cell_reference)
                VALUES (?, ?, ?, ?, ?)
            ''', (student_id, subject, grade_value, raw_text, cell_reference))
            return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            # Сработал UNIQUE(student_id, cell_reference)
            return False
    return False


def get_existing_grade(student_id: int, cell_reference: str) -> Optional[Dict[str, Any]]:
    """Возвращает существующую оценку по cell_reference, или None если не найдена."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT grade_value, raw_text, subject
            FROM grade_history
            WHERE student_id = ? AND cell_reference = ?
        ''', (student_id, cell_reference))
        row = cursor.fetchone()
        return dict(row) if row else None


def update_grade(student_id: int, cell_reference: str, grade_value: Optional[float], raw_text: str) -> bool:
    """Обновляет значение оценки по cell_reference. Возвращает True если обновлено."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE grade_history
            SET grade_value = ?, raw_text = ?, date_added = CURRENT_TIMESTAMP
            WHERE student_id = ? AND cell_reference = ?
        ''', (grade_value, raw_text, student_id, cell_reference))
        return cursor.rowcount > 0

def upsert_quarter_grade(student_id: int, subject: str, quarter: int,
                         grade_value: Optional[float], raw_text: str) -> bool:
    """Вставляет или обновляет четвертную оценку. Возвращает True если значение изменилось."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT grade_value, raw_text FROM quarter_grades
            WHERE student_id = ? AND subject = ? AND quarter = ?
        ''', (student_id, subject, quarter))
        existing = cursor.fetchone()

        if existing and existing['raw_text'] == raw_text:
            return False  # Не изменилось

        cursor.execute('''
            INSERT INTO quarter_grades (student_id, subject, quarter, grade_value, raw_text)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(student_id, subject, quarter)
            DO UPDATE SET grade_value = excluded.grade_value, raw_text = excluded.raw_text,
                          updated_at = CURRENT_TIMESTAMP
        ''', (student_id, subject, quarter, grade_value, raw_text))
        return True


def get_quarter_grades(student_id: int) -> List[Dict[str, Any]]:
    """Возвращает все четвертные оценки студента."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT subject, quarter, grade_value, raw_text
            FROM quarter_grades
            WHERE student_id = ?
            ORDER BY subject, quarter
        ''', (student_id,))
        return [dict(row) for row in cursor.fetchall()]


def get_grade_history_for_student(student_id: int, days: int = 14) -> List[Dict[str, Any]]:
    """Возвращает историю оценок студента за последние N дней."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT subject, grade_value, raw_text, date_added
            FROM grade_history
            WHERE student_id = ? AND date_added >= datetime('now', ?)
            ORDER BY date_added
        ''', (student_id, f'-{days} days'))
        return [dict(row) for row in cursor.fetchall()]

def get_grade_history_for_student_all(student_id: int, days: int = 30) -> List[Dict[str, Any]]:
    """Возвращает полную историю оценок студента за N дней (для WebApp API)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT subject, grade_value, raw_text, cell_reference, date_added
            FROM grade_history
            WHERE student_id = ? AND date_added >= datetime('now', ?)
            ORDER BY date_added DESC
        ''', (student_id, f'-{days} days'))
        return [dict(row) for row in cursor.fetchall()]

def get_parents_for_student(student_id: int) -> List[int]:
    """Возвращает список telegram_id всех родителей, привязанных к данному студенту через семью."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT p.telegram_id 
            FROM parents p
            JOIN family_links fl ON p.id = fl.parent_id
            WHERE fl.student_id = ? AND p.telegram_id IS NOT NULL
        ''', (student_id,))
        return [row['telegram_id'] for row in cursor.fetchall()]
    return []

def get_active_spreadsheets() -> List[Dict[str, Any]]:
    """Возвращает список всех словарей {student_id, spreadsheet_id, fio, display_name} для опроса таблиц."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id as student_id, fio, spreadsheet_id, display_name FROM students WHERE spreadsheet_id IS NOT NULL AND spreadsheet_id != ""')
        return [dict(row) for row in cursor.fetchall()]
    return []

def get_active_spreadsheets_with_subscription() -> List[Dict[str, Any]]:
    """Возвращает студентов только из семей с активной подпиской (или без ограничений если подписка не настроена)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT s.id as student_id, s.fio, s.spreadsheet_id, s.display_name
            FROM students s
            JOIN family_links fl ON s.id = fl.student_id
            JOIN families f ON fl.family_id = f.id
            WHERE s.spreadsheet_id IS NOT NULL AND s.spreadsheet_id != ''
              AND (f.subscription_end IS NULL OR f.subscription_end > datetime('now'))
        ''')
        return [dict(row) for row in cursor.fetchall()]
    return []

def get_parent_by_phone(phone: str) -> Optional[Dict[str, Any]]:
    """Находит родителя по номеру телефона."""
    # Нормализуем номер (удаляем +)
    phone = phone.replace("+", "")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM parents WHERE phone = ? OR phone = ?', (phone, "+" + phone))
        row = cursor.fetchone()
        return dict(row) if row else None

def update_parent_telegram_id(phone: str, telegram_id: int):
    """Привязывает telegram_id к родителю по номеру телефона."""
    phone = phone.replace("+", "")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE parents SET telegram_id = ? WHERE phone = ? OR phone = ?', (telegram_id, phone, "+" + phone))

def get_parent_role(telegram_id: int) -> Optional[str]:
    """Возвращает роль пользователя по его telegram_id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT role FROM parents WHERE telegram_id = ?', (telegram_id,))
        row = cursor.fetchone()
        return row['role'] if row else None
    return None

def get_user_lang(telegram_id: int) -> str:
    """Возвращает язык пользователя (ru/uz/en). По умолчанию 'ru'."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT lang FROM parents WHERE telegram_id = ?', (telegram_id,))
        row = cursor.fetchone()
        return row['lang'] if row and row['lang'] else 'ru'

def set_user_lang(telegram_id: int, lang: str):
    """Устанавливает язык пользователя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE parents SET lang = ? WHERE telegram_id = ?', (lang, telegram_id))


def get_notify_mode(telegram_id: int) -> str:
    """Возвращает режим уведомлений: 'instant' (по умолчанию) или 'summary_only'."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT notify_mode FROM parents WHERE telegram_id = ?', (telegram_id,))
        row = cursor.fetchone()
        return row['notify_mode'] if row and row['notify_mode'] else 'instant'


def set_notify_mode(telegram_id: int, mode: str):
    """Устанавливает режим уведомлений ('instant' или 'summary_only')."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE parents SET notify_mode = ? WHERE telegram_id = ?', (mode, telegram_id))

def get_families_for_head(head_telegram_id: int) -> List[Dict[str, Any]]:
    """Возвращает список семей, в которых данный пользователь является главой."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT f.id, f.family_name 
            FROM families f
            JOIN parents p ON f.head_id = p.id
            WHERE p.telegram_id = ?
        ''', (head_telegram_id,))
        return [dict(row) for row in cursor.fetchall()]
    return []

def is_head_of_any_family(telegram_id: int) -> bool:
    """Проверяет, является ли пользователь главой хотя бы одной семьи."""
    families = get_families_for_head(telegram_id)
    return len(families) > 0


def is_head_of_family(telegram_id: int, family_id: int) -> bool:
    """Проверяет, является ли пользователь главой конкретной семьи."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 1 FROM families f
            JOIN parents p ON f.head_id = p.id
            WHERE p.telegram_id = ? AND f.id = ?
        ''', (telegram_id, family_id))
        return cursor.fetchone() is not None


def is_member_of_family(telegram_id: int, family_id: int) -> bool:
    """Проверяет, является ли пользователь членом семьи (через family_links)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT 1 FROM family_links fl
            JOIN parents p ON fl.parent_id = p.id
            WHERE p.telegram_id = ? AND fl.family_id = ?
            LIMIT 1
        ''', (telegram_id, family_id))
        return cursor.fetchone() is not None


def can_manage_family(telegram_id: int, family_id: int) -> bool:
    """Может ли пользователь управлять семьёй (admin или head этой семьи)."""
    if get_parent_role(telegram_id) == 'admin':
        return True
    return is_head_of_family(telegram_id, family_id)


def get_families_for_student(student_id: int) -> List[Dict[str, Any]]:
    """Возвращает все семьи, к которым привязан ученик."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT f.id, f.family_name, f.subscription_end
            FROM families f
            JOIN family_links fl ON fl.family_id = f.id
            WHERE fl.student_id = ?
        ''', (student_id,))
        return [dict(row) for row in cursor.fetchall()]


def is_student_under_active_subscription(student_id: int) -> bool:
    """True, если хотя бы одна семья ученика имеет активную подписку."""
    families = get_families_for_student(student_id)
    return any(is_subscription_active(f['id']) for f in families)

def set_family_head(family_id: int, parent_id: int):
    """Делает родителя главой семьи."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE families SET head_id = ? WHERE id = ?', (parent_id, family_id))

def add_family(name: str) -> Optional[int]:
    """Создает новую семью и возвращает её ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO families (family_name) VALUES (?)', (name,))
        return cursor.lastrowid

def add_parent(fio: str, phone: str, role: str = 'senior') -> Optional[int]:
    """Создает нового родителя и возвращает его ID."""
    phone = phone.replace("+", "")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT OR IGNORE INTO parents (fio, phone, role) VALUES (?, ?, ?)', (fio, phone, role))
        if cursor.rowcount == 0:
            cursor.execute('SELECT id FROM parents WHERE phone = ?', (phone,))
            return cursor.fetchone()['id']
        return cursor.lastrowid

def add_student(fio: str, spreadsheet_id: str, display_name: str = None) -> Optional[int]:
    """Создает нового студента и возвращает его ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO students (fio, spreadsheet_id, display_name) VALUES (?, ?, ?)', (fio, spreadsheet_id, display_name))
        return cursor.lastrowid

def update_student_display_name(student_id: int, display_name: str):
    """Обновляет кэшированное display_name студента."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('UPDATE students SET display_name = ? WHERE id = ?', (display_name, student_id))

def link_parent_to_family(family_id: int, parent_id: int):
    """Привязывает родителя к семье."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Привязываем ко всем студентам этой семьи
        cursor.execute('SELECT DISTINCT student_id FROM family_links WHERE family_id = ? AND student_id IS NOT NULL', (family_id,))
        students = cursor.fetchall()
        if students:
            for s in students:
                cursor.execute('INSERT OR IGNORE INTO family_links (family_id, parent_id, student_id) VALUES (?, ?, ?)', 
                               (family_id, parent_id, s['student_id']))
        else:
            # Если студентов еще нет, просто создаем запись без студента
            cursor.execute('INSERT OR IGNORE INTO family_links (family_id, parent_id) VALUES (?, ?)', 
                           (family_id, parent_id))

def get_child_count(family_id: int) -> int:
    """Возвращает количество детей в семье."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(DISTINCT student_id) as count FROM family_links WHERE family_id = ? AND student_id IS NOT NULL', (family_id,))
        return cursor.fetchone()['count']
    return 0

def link_student_to_family(family_id: int, student_id: int):
    """Привязывает студента ко всей семье (всем родителям в этой семье)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Получаем всех родителей этой семьи
        cursor.execute('SELECT DISTINCT parent_id FROM family_links WHERE family_id = ?', (family_id,))
        parents = cursor.fetchall()
        if parents:
            for p in parents:
                cursor.execute('INSERT OR IGNORE INTO family_links (family_id, parent_id, student_id) VALUES (?, ?, ?)', 
                               (family_id, p['parent_id'], student_id))
        else:
            # Если родителей еще нет (странный случай), просто создаем запись без родителя
            cursor.execute('INSERT OR IGNORE INTO family_links (family_id, student_id) VALUES (?, ?)', 
                           (family_id, student_id))

def get_all_families() -> List[Dict[str, Any]]:
    """Возвращает список всех семей с информацией о главе и количестве детей."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT f.id, f.family_name, p.fio as head_fio,
                   (SELECT COUNT(DISTINCT student_id) FROM family_links fl2 WHERE fl2.family_id = f.id AND fl2.student_id IS NOT NULL) as child_count
            FROM families f
            LEFT JOIN parents p ON f.head_id = p.id
            GROUP BY f.id
        ''')
        return [dict(row) for row in cursor.fetchall()]
    return []

def get_students_for_parent(telegram_id: int, family_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Возвращает список всех студентов {id, fio, spreadsheet_id, display_name},
    видимых данному пользователю.

    Источников два — UNION:
    1) Прямая связь через family_links (parent ↔ student).
    2) Глава семьи (families.head_id) — даже если он не залинкован
       явно через family_links, студенты его семьи всё равно его. Иначе
       при создании семьи через `cmd_add_family` (где назначается head_id,
       но не всегда добавляется family_links для главы) глава не видел
       детей своей семьи. Это был реальный bug.

    Админ — отдельная история: ему доступны все студенты только в админ-панели,
    через `/grades` админ видит детей только тех семей где он head/parent.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if family_id:
            cursor.execute('''
                SELECT DISTINCT s.id, s.fio, s.spreadsheet_id, s.display_name
                FROM students s
                JOIN family_links fl ON s.id = fl.student_id
                JOIN parents p ON p.telegram_id = ?
                LEFT JOIN families f ON f.id = fl.family_id AND f.head_id = p.id
                WHERE fl.family_id = ?
                  AND (fl.parent_id = p.id OR f.head_id = p.id)
            ''', (telegram_id, family_id))
        else:
            cursor.execute('''
                SELECT DISTINCT s.id, s.fio, s.spreadsheet_id, s.display_name
                FROM students s
                JOIN family_links fl ON s.id = fl.student_id
                JOIN parents p ON p.telegram_id = ?
                LEFT JOIN families f ON f.id = fl.family_id AND f.head_id = p.id
                WHERE fl.parent_id = p.id OR f.head_id = p.id
            ''', (telegram_id,))
        return [dict(row) for row in cursor.fetchall()]
    return []
        
def has_children_for_grades(telegram_id: int) -> bool:
    """Проверяет, привязан ли пользователь хотя бы к одному ребенку."""
    return len(get_students_for_parent(telegram_id)) > 0

def get_family_members(family_id: int) -> List[Dict[str, Any]]:
    """Возвращает список всех взрослых членов семьи."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT p.id, p.fio, p.role, (p.id = f.head_id) as is_head
            FROM parents p
            JOIN family_links fl ON p.id = fl.parent_id
            JOIN families f ON f.id = fl.family_id
            WHERE fl.family_id = ?
        ''', (family_id,))
        return [dict(row) for row in cursor.fetchall()]
    return []


def get_family_members_telegram_ids(family_id: int) -> List[int]:
    """Возвращает telegram_id всех членов семьи (для уведомлений)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT p.telegram_id
            FROM parents p
            JOIN family_links fl ON p.id = fl.parent_id
            WHERE fl.family_id = ? AND p.telegram_id IS NOT NULL
        ''', (family_id,))
        return [row['telegram_id'] for row in cursor.fetchall()]
    return []

def get_family_students(family_id: int) -> List[Dict[str, Any]]:
    """Возвращает список всех детей в семье."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT s.id, s.fio, s.spreadsheet_id
            FROM students s
            JOIN family_links fl ON s.id = fl.student_id
            WHERE fl.family_id = ?
        ''', (family_id,))
        return [dict(row) for row in cursor.fetchall()]
    return []

def delete_parent_from_family(family_id: int, parent_id: int) -> bool:
    """Удаляет родителя из семьи. Главу семьи удалить нельзя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Check if parent is the head of this family
        cursor.execute('SELECT head_id FROM families WHERE id = ?', (family_id,))
        row = cursor.fetchone()
        if row and row['head_id'] == parent_id:
            return False
            
        cursor.execute('DELETE FROM family_links WHERE family_id = ? AND parent_id = ?', (family_id, parent_id))
        return True
    return False

def delete_student_from_family(family_id: int, student_id: int):
    """Удаляет ребенка из конкретной семьи. Если студент больше ни к кому не
    привязан — каскадно удаляет его и все связанные данные."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM family_links WHERE family_id = ? AND student_id = ?', (family_id, student_id))

        # Если студент больше ни к кому не привязан, удаляем его совсем со всеми данными
        cursor.execute('SELECT COUNT(*) as count FROM family_links WHERE student_id = ?', (student_id,))
        if cursor.fetchone()['count'] == 0:
            cursor.execute('DELETE FROM grade_history WHERE student_id = ?', (student_id,))
            cursor.execute('DELETE FROM quarter_grades WHERE student_id = ?', (student_id,))
            cursor.execute('DELETE FROM students WHERE id = ?', (student_id,))


def delete_family_cascade(family_id: int) -> bool:
    """Удаляет семью со всеми связанными данными в одной транзакции.

    Чистит: payments, family_invites, family_links, осиротевших students
    (вместе с их grade_history и quarter_grades), и саму запись families.

    Возвращает True если семья удалена, False если её не существовало.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute('SELECT id FROM families WHERE id = ?', (family_id,))
        if not cursor.fetchone():
            return False

        # Сначала находим студентов, которые останутся осиротевшими после удаления связей
        cursor.execute('''
            SELECT DISTINCT student_id FROM family_links
            WHERE family_id = ? AND student_id IS NOT NULL
        ''', (family_id,))
        student_ids = [row['student_id'] for row in cursor.fetchall()]

        # Удаляем связи
        cursor.execute('DELETE FROM family_links WHERE family_id = ?', (family_id,))

        # Студенты, у которых не осталось других семей — удаляем со всеми данными
        for s_id in student_ids:
            cursor.execute('SELECT COUNT(*) as cnt FROM family_links WHERE student_id = ?', (s_id,))
            if cursor.fetchone()['cnt'] == 0:
                cursor.execute('DELETE FROM grade_history WHERE student_id = ?', (s_id,))
                cursor.execute('DELETE FROM quarter_grades WHERE student_id = ?', (s_id,))
                cursor.execute('DELETE FROM students WHERE id = ?', (s_id,))

        # Связанные с семьёй данные
        cursor.execute('DELETE FROM payments WHERE family_id = ?', (family_id,))
        cursor.execute('DELETE FROM family_invites WHERE family_id = ?', (family_id,))
        cursor.execute('DELETE FROM family_groups WHERE family_id = ?', (family_id,))

        # И сама семья
        cursor.execute('DELETE FROM families WHERE id = ?', (family_id,))

        logger.info(f"Family {family_id} cascade-deleted (orphaned students: {len(student_ids)})")
        return True

def get_global_stats() -> Dict[str, Any]:
    """Возвращает глобальную статистику системы для администраторов."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        stats = {}
        stats['families'] = cursor.execute('SELECT COUNT(*) as c FROM families').fetchone()['c']
        stats['parents'] = cursor.execute('SELECT COUNT(*) as c FROM parents').fetchone()['c']
        stats['students'] = cursor.execute('SELECT COUNT(*) as c FROM students').fetchone()['c']
        stats['history_records'] = cursor.execute('SELECT COUNT(*) as c FROM grade_history').fetchone()['c']
        return stats
    return {}

def get_user_stats(telegram_id: int) -> Dict[str, Any]:
    """Возвращает персонализированную статистику для конкретного пользователя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        stats = {}
        
        # Получаем ID родителя
        cursor.execute('SELECT id FROM parents WHERE telegram_id = ?', (telegram_id,))
        parent_row = cursor.fetchone()
        if not parent_row:
            return {'families': 0, 'students': 0, 'history_records': 0}
            
        parent_id = parent_row['id']
        
        # Количество семей
        stats['families'] = cursor.execute('SELECT COUNT(DISTINCT family_id) as c FROM family_links WHERE parent_id = ?', (parent_id,)).fetchone()['c']
        
        # Количество доступных детей
        stats['students'] = cursor.execute('''
            SELECT COUNT(DISTINCT student_id) as c 
            FROM family_links 
            WHERE parent_id = ? AND student_id IS NOT NULL
        ''', (parent_id,)).fetchone()['c']
        
        # Количество записей оценок для этих детей
        stats['history_records'] = cursor.execute('''
            SELECT COUNT(*) as c
            FROM grade_history
            WHERE student_id IN (SELECT DISTINCT student_id FROM family_links WHERE parent_id = ?)
        ''', (parent_id,)).fetchone()['c']

        return stats

def get_all_telegram_ids() -> List[int]:
    """Возвращает список telegram_id всех зарегистрированных пользователей."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT telegram_id FROM parents WHERE telegram_id IS NOT NULL")
        return [row['telegram_id'] for row in cursor.fetchall()]
    return []

def get_user_info_by_tg_id(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает основную информацию о пользователе и его семьях по telegram_id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, fio, phone, role FROM parents WHERE telegram_id = ?", (telegram_id,))
        user = cursor.fetchone()
        if not user:
            return None
            
        # Get family names
        cursor.execute('''
            SELECT DISTINCT f.family_name 
            FROM families f 
            JOIN family_links fl ON f.id = fl.family_id 
            WHERE fl.parent_id = ?
        ''', (user['id'],))
        families = [row['family_name'] for row in cursor.fetchall()]
        
        return {
            'id': user['id'],
            'fio': user['fio'],
            'phone': user['phone'],
            'role': user['role'],
            'families': families
        }
        
if __name__ == '__main__':
    init_db()
    print("Database initialized successfully at", DB_PATH)

def get_last_menu_id(user_id: int) -> Optional[int]:
    """Возвращает ID последнего сообщения меню для пользователя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT last_menu_msg_id FROM app_states WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        return row['last_menu_msg_id'] if row else None

def update_last_menu_id(user_id: int, msg_id: Optional[int]):
    """Обновляет ID последнего сообщения меню для пользователя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO app_states (user_id, last_menu_msg_id)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET last_menu_msg_id = excluded.last_menu_msg_id
        ''', (user_id, msg_id))

# ====================
# Персистентные user_states
# ====================
def set_user_state(user_id: int, state: str, data: str = None):
    """Сохраняет состояние пользователя в БД."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO user_states (user_id, state, data, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id) DO UPDATE SET state = excluded.state, data = excluded.data, updated_at = CURRENT_TIMESTAMP
        ''', (user_id, state, data))

def get_user_state(user_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает состояние пользователя из БД."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT state, data FROM user_states WHERE user_id = ?', (user_id,))
        row = cursor.fetchone()
        if row:
            return {'state': row['state'], 'data': row['data']}
        return None

def clear_user_state(user_id: int):
    """Удаляет состояние пользователя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM user_states WHERE user_id = ?', (user_id,))

# ====================
# Персистентный маппинг сообщений поддержки
# ====================
def save_support_msg_map(admin_msg_id: int, user_id: int):
    """Сохраняет связь между сообщением в админ-группе и пользователем."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO support_msg_map (admin_msg_id, user_id) VALUES (?, ?)
        ''', (admin_msg_id, user_id))

def get_support_user_id(admin_msg_id: int) -> Optional[int]:
    """Находит user_id по ID сообщения в админ-группе."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM support_msg_map WHERE admin_msg_id = ?', (admin_msg_id,))
        row = cursor.fetchone()
        return row['user_id'] if row else None


# ====================
# Очередь уведомлений (тихие часы)
# ====================
def queue_notification(telegram_id: int, message: str):
    """Сохраняет уведомление в очередь для отложенной отправки."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO notification_queue (telegram_id, message) VALUES (?, ?)',
                       (telegram_id, message))


def get_and_clear_queued_notifications(telegram_id: int) -> List[str]:
    """Извлекает и удаляет все отложенные уведомления для пользователя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT message FROM notification_queue WHERE telegram_id = ? ORDER BY created_at', (telegram_id,))
        messages = [row['message'] for row in cursor.fetchall()]
        cursor.execute('DELETE FROM notification_queue WHERE telegram_id = ?', (telegram_id,))
        return messages


def get_all_queued_telegram_ids() -> List[int]:
    """Возвращает список уникальных telegram_id с отложенными уведомлениями."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT DISTINCT telegram_id FROM notification_queue')
        return [row['telegram_id'] for row in cursor.fetchall()]


# ====================
# Оценки за сегодня (для вечерней сводки)
# ====================
def get_today_grades_for_student(student_id: int) -> List[Dict[str, Any]]:
    """Возвращает все оценки студента за сегодня (по Ташкенту, UTC+5).
    Дедупликация по предмету: если оценка попала из 'Все оценки' и 'Сегодня',
    берём самую свежую запись для каждого предмета."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT subject, grade_value, raw_text, MAX(date_added) as date_added
            FROM grade_history
            WHERE student_id = ? AND date(date_added, '+5 hours') = date('now', '+5 hours')
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
    """Возвращает все оценки студента за вчера (по Ташкенту, UTC+5)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT subject, grade_value, raw_text
            FROM grade_history
            WHERE student_id = ? AND date(date_added, '+5 hours') = date('now', '+5 hours', '-1 day')
            ORDER BY date_added
        ''', (student_id,))
        return [dict(row) for row in cursor.fetchall()]


def has_today_grades_for_parent(telegram_id: int) -> bool:
    """Проверяет, есть ли сегодня хоть одна оценка у детей родителя (по Ташкенту, UTC+5)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) as c FROM grade_history gh
            JOIN family_links fl ON gh.student_id = fl.student_id
            JOIN parents p ON fl.parent_id = p.id
            WHERE p.telegram_id = ? AND date(gh.date_added, '+5 hours') = date('now', '+5 hours')
        ''', (telegram_id,))
        return cursor.fetchone()['c'] > 0


def has_recent_grades_for_parent(telegram_id: int, hours: int = 48) -> bool:
    """Проверяет, есть ли оценки у детей родителя за последние N часов."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) as c FROM grade_history gh
            JOIN family_links fl ON gh.student_id = fl.student_id
            JOIN parents p ON fl.parent_id = p.id
            WHERE p.telegram_id = ? AND gh.date_added >= datetime('now', ?)
        ''', (telegram_id, f'-{hours} hours'))
        return cursor.fetchone()['c'] > 0


# ====================
# Инвайт-ссылки
# ====================
def create_invite(family_id: int, created_by_parent_id: int, expires_hours: int = 48) -> str:
    """Создаёт инвайт-ссылку для семьи. Возвращает invite_code."""
    import secrets
    code = secrets.token_urlsafe(12)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO family_invites (family_id, invite_code, created_by, expires_at)
            VALUES (?, ?, ?, datetime('now', ?))
        ''', (family_id, code, created_by_parent_id, f'+{expires_hours} hours'))
    return code


def get_invite(invite_code: str) -> Optional[Dict[str, Any]]:
    """Возвращает данные инвайта, если он валиден (не использован, не истёк)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT fi.*, f.family_name
            FROM family_invites fi
            JOIN families f ON fi.family_id = f.id
            WHERE fi.invite_code = ? AND fi.is_used = 0
              AND fi.expires_at > datetime('now')
        ''', (invite_code,))
        row = cursor.fetchone()
        return dict(row) if row else None


def use_invite(invite_code: str, used_by_parent_id: int) -> bool:
    """Помечает инвайт как использованный."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE family_invites SET is_used = 1, used_by = ?
            WHERE invite_code = ? AND is_used = 0
        ''', (used_by_parent_id, invite_code))
        return cursor.rowcount > 0


def get_parent_id_by_telegram(telegram_id: int) -> Optional[int]:
    """Возвращает internal parent ID по telegram_id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM parents WHERE telegram_id = ?', (telegram_id,))
        row = cursor.fetchone()
        return row['id'] if row else None


def get_parent_by_telegram(telegram_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает полную запись родителя по telegram_id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM parents WHERE telegram_id = ?', (telegram_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


# ====================
# Подписки
# ====================
def get_family_subscription(family_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает информацию о подписке семьи."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT subscription_end FROM families WHERE id = ?', (family_id,))
        row = cursor.fetchone()
        if not row:
            return None
        return {'subscription_end': row['subscription_end']}


def extend_subscription(family_id: int, months: int = 1):
    """Продлевает подписку семьи на N месяцев от текущей даты или от конца текущей подписки."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT subscription_end FROM families WHERE id = ?', (family_id,))
        row = cursor.fetchone()
        if not row:
            return

        current_end = row['subscription_end']
        if current_end:
            # Продлеваем от конца текущей подписки (если ещё активна)
            cursor.execute('''
                UPDATE families SET subscription_end =
                    CASE
                        WHEN subscription_end > datetime('now')
                        THEN datetime(subscription_end, ?)
                        ELSE datetime('now', ?)
                    END
                WHERE id = ?
            ''', (f'+{months} months', f'+{months} months', family_id))
        else:
            cursor.execute(
                "UPDATE families SET subscription_end = datetime('now', ?) WHERE id = ?",
                (f'+{months} months', family_id))


def record_payment(family_id: int, paid_by_parent_id: int, amount: int,
                   currency: str, plan: str, months: int,
                   telegram_charge_id: str = None, provider_charge_id: str = None):
    """Записывает платёж в историю."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO payments (family_id, paid_by, amount, currency, plan, months,
                                  telegram_payment_charge_id, provider_payment_charge_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (family_id, paid_by_parent_id, amount, currency, plan, months,
              telegram_charge_id, provider_charge_id))


def is_subscription_active(family_id: int) -> bool:
    """Проверяет, активна ли подписка семьи."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT subscription_end FROM families WHERE id = ?
        ''', (family_id,))
        row = cursor.fetchone()
        if not row or not row['subscription_end']:
            return False
        # Сравниваем с текущим временем
        cursor.execute(
            "SELECT ? > datetime('now') as active",
            (row['subscription_end'],))
        return cursor.fetchone()['active'] == 1


def get_families_for_user(telegram_id: int) -> List[Dict[str, Any]]:
    """Возвращает все семьи, к которым привязан пользователь."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT f.id, f.family_name, f.subscription_end, f.head_id
            FROM families f
            JOIN family_links fl ON f.id = fl.family_id
            JOIN parents p ON fl.parent_id = p.id
            WHERE p.telegram_id = ?
        ''', (telegram_id,))
        return [dict(row) for row in cursor.fetchall()]


def has_any_active_subscription(telegram_id: int) -> bool:
    """Проверяет, есть ли у пользователя хотя бы одна семья с активной подпиской."""
    families = get_families_for_user(telegram_id)
    return any(is_subscription_active(f['id']) for f in families)


# ====================
# Настройки (key-value)
# ====================
# ====================
# Семейные групповые чаты (бот в чате семьи)
# ====================
def link_group_to_family(family_id: int, chat_id: int, chat_title: str,
                         added_by_parent_id: int,
                         message_thread_id: Optional[int] = None) -> bool:
    """Привязывает Telegram-группу к семье. Возвращает True если привязка создана,
    False если этот chat_id уже привязан (к этой или другой семье).

    message_thread_id — для супергрупп с темами. Если задан, все уведомления
    будут падать именно в эту тему (через Telegram Bot API param `message_thread_id`)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO family_groups (family_id, chat_id, chat_title, message_thread_id, added_by)
                VALUES (?, ?, ?, ?, ?)
            ''', (family_id, chat_id, chat_title, message_thread_id, added_by_parent_id))
            return True
        except sqlite3.IntegrityError:
            return False


def get_family_for_group(chat_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает {'family_id', 'family_name', 'message_thread_id'} для chat_id или None."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT fg.family_id, fg.message_thread_id, f.family_name
            FROM family_groups fg
            JOIN families f ON f.id = fg.family_id
            WHERE fg.chat_id = ?
        ''', (chat_id,))
        row = cursor.fetchone()
        return dict(row) if row else None


def get_groups_for_family(family_id: int) -> List[Dict[str, Any]]:
    """Возвращает список dict с chat_id и message_thread_id для семьи."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT chat_id, message_thread_id FROM family_groups WHERE family_id = ?',
            (family_id,)
        )
        return [dict(row) for row in cursor.fetchall()]


def get_groups_for_student(student_id: int) -> List[Dict[str, Any]]:
    """Возвращает список dict {chat_id, message_thread_id} для всех групп
    привязанных к семьям этого ученика (с дедупликацией по chat_id)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT fg.chat_id, fg.message_thread_id
            FROM family_groups fg
            JOIN family_links fl ON fl.family_id = fg.family_id
            WHERE fl.student_id = ?
        ''', (student_id,))
        return [dict(row) for row in cursor.fetchall()]


def unlink_group(chat_id: int) -> bool:
    """Удаляет привязку группы. True если удалили, False если её и не было."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM family_groups WHERE chat_id = ?', (chat_id,))
        return cursor.rowcount > 0


def update_group_thread(chat_id: int, message_thread_id: Optional[int]) -> bool:
    """Меняет тему привязанной группы (для супергрупп с темами).
    Передать None чтобы сбросить (писать в General)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE family_groups SET message_thread_id = ? WHERE chat_id = ?',
            (message_thread_id, chat_id),
        )
        return cursor.rowcount > 0


def archive_old_grades(days: Optional[int] = None) -> int:
    """Переносит оценки старше N дней из grade_history в grade_history_archive.

    days по умолчанию берётся из config.GRADE_ARCHIVE_DAYS.

    Атомарно по отношению к параллельным INSERT'ам: переносим только конкретные
    id, отобранные SELECT'ом, а не запрос по `date_added < cutoff` в каждом
    statement'е (иначе DELETE мог бы захватить запись, которая не попала в
    INSERT — или удалить ту, что прилетела между запросами).
    Возвращает число перенесённых записей.
    """
    if days is None:
        from src.config import GRADE_ARCHIVE_DAYS
        days = GRADE_ARCHIVE_DAYS
    with get_db_connection() as conn:
        cursor = conn.cursor()
        # BEGIN IMMEDIATE — запрашиваем write-lock сразу, чтобы между SELECT и
        # DELETE никто не вставил новые строки в обозреваемый диапазон
        cursor.execute('BEGIN IMMEDIATE')
        cutoff = f'-{int(days)} days'
        cursor.execute(
            'SELECT id FROM grade_history WHERE date_added < datetime("now", ?)',
            (cutoff,),
        )
        ids = [row['id'] for row in cursor.fetchall()]
        if not ids:
            return 0

        # Переносим именно эти id (порциями по 500, чтобы не упереться в лимит
        # параметров SQLite — обычно 999, но safer)
        moved = 0
        chunk_size = 500
        for i in range(0, len(ids), chunk_size):
            chunk = ids[i:i + chunk_size]
            placeholders = ','.join('?' * len(chunk))
            cursor.execute(
                f'''INSERT INTO grade_history_archive
                    (student_id, subject, grade_value, raw_text, cell_reference, date_added)
                    SELECT student_id, subject, grade_value, raw_text, cell_reference, date_added
                    FROM grade_history
                    WHERE id IN ({placeholders})''',
                chunk,
            )
            moved += cursor.rowcount
            cursor.execute(
                f'DELETE FROM grade_history WHERE id IN ({placeholders})',
                chunk,
            )

        logger.info(f"Archived {moved} grades older than {days} days")
        return moved


def cleanup_old_notification_queue(hours: Optional[int] = None) -> int:
    """Удаляет нерасфлушенные сообщения старше N часов (страховка от утечек).
    hours по умолчанию из config.NOTIFICATION_QUEUE_TTL_HOURS."""
    if hours is None:
        from src.config import NOTIFICATION_QUEUE_TTL_HOURS
        hours = NOTIFICATION_QUEUE_TTL_HOURS
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM notification_queue
            WHERE created_at < datetime('now', ?)
        ''', (f'-{int(hours)} hours',))
        if cursor.rowcount > 0:
            logger.info(f"Cleaned {cursor.rowcount} stale notifications older than {hours}h")
        return cursor.rowcount


def cleanup_expired_invites(days: Optional[int] = None) -> int:
    """Удаляет инвайты, истекшие более N дней назад.
    days по умолчанию из config.EXPIRED_INVITE_TTL_DAYS."""
    if days is None:
        from src.config import EXPIRED_INVITE_TTL_DAYS
        days = EXPIRED_INVITE_TTL_DAYS
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM family_invites
            WHERE expires_at < datetime('now', ?)
        ''', (f'-{int(days)} days',))
        if cursor.rowcount > 0:
            logger.info(f"Cleaned {cursor.rowcount} expired invites")
        return cursor.rowcount


def get_setting(key: str, default: str = None) -> Optional[str]:
    """Возвращает значение настройки по ключу."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
        row = cursor.fetchone()
        return row['value'] if row else default


def set_setting(key: str, value: str):
    """Устанавливает настройку."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        ''', (key, value))


def get_plans_from_db() -> Optional[Dict[str, Any]]:
    """Возвращает тарифы из БД или None если не заданы."""
    import json
    raw = get_setting('plans')
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
    return None


def save_plans_to_db(plans: Dict[str, Any]):
    """Сохраняет тарифы в БД."""
    import json
    set_setting('plans', json.dumps(plans, ensure_ascii=False))


# ====================
# Промокоды
# ====================
def create_promo_code(code: str, plan: str, discount_percent: int = 0,
                      free_months: int = 0, max_uses: int = 1,
                      expires_days: Optional[int] = None) -> bool:
    """Создаёт промокод. Возвращает True если создан."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            if expires_days is not None:
                modifier = f'+{int(expires_days)} days'
                cursor.execute('''
                    INSERT INTO promo_codes
                        (code, plan, discount_percent, free_months, max_uses, expires_at)
                    VALUES (?, ?, ?, ?, ?, datetime('now', ?))
                ''', (code.upper(), plan, discount_percent, free_months, max_uses, modifier))
            else:
                cursor.execute('''
                    INSERT INTO promo_codes
                        (code, plan, discount_percent, free_months, max_uses, expires_at)
                    VALUES (?, ?, ?, ?, ?, NULL)
                ''', (code.upper(), plan, discount_percent, free_months, max_uses))
            return True
        except Exception as e:
            logger.error(f"Failed to create promo code: {e}")
            return False


def get_promo_code(code: str) -> Optional[Dict[str, Any]]:
    """Возвращает промокод если он валиден (не исчерпан, не истёк)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM promo_codes
            WHERE code = ? AND used_count < max_uses
              AND (expires_at IS NULL OR expires_at > datetime('now'))
        ''', (code.upper(),))
        row = cursor.fetchone()
        return dict(row) if row else None


def use_promo_code(code: str) -> bool:
    """Увеличивает счётчик использований промокода."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE promo_codes SET used_count = used_count + 1
            WHERE code = ? AND used_count < max_uses
        ''', (code.upper(),))
        return cursor.rowcount > 0


def list_promo_codes() -> List[Dict[str, Any]]:
    """Возвращает все промокоды."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM promo_codes ORDER BY created_at DESC')
        return [dict(row) for row in cursor.fetchall()]


def delete_promo_code(code: str) -> bool:
    """Удаляет промокод."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM promo_codes WHERE code = ?', (code.upper(),))
        return cursor.rowcount > 0


# ====================
# Отмена подписки
# ====================
def cancel_subscription(family_id: int) -> bool:
    """Аннулирует подписку семьи (устанавливает subscription_end = now)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE families SET subscription_end = datetime('now') WHERE id = ?",
            (family_id,))
        return cursor.rowcount > 0


def get_families_expiring_in_days(days: int) -> List[Dict[str, Any]]:
    """Возвращает семьи, чья подписка истекает ровно через N дней (±12ч)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT f.id as family_id, f.family_name, f.subscription_end
            FROM families f
            WHERE f.subscription_end IS NOT NULL
              AND f.subscription_end > datetime('now')
              AND f.subscription_end <= datetime('now', ?)
        ''', (f'+{days + 1} days',))
        results = []
        from datetime import datetime, timedelta
        target_date = (datetime.utcnow() + timedelta(days=days)).date()
        for row in cursor.fetchall():
            end_str = row['subscription_end']
            try:
                end_date = datetime.fromisoformat(end_str).date()
            except (ValueError, TypeError):
                continue
            if end_date == target_date:
                results.append(dict(row))
        return results


def get_families_expired_today() -> List[Dict[str, Any]]:
    """Возвращает семьи, чья подписка истекла сегодня (по Ташкенту, UTC+5)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT f.id as family_id, f.family_name, f.subscription_end
            FROM families f
            WHERE f.subscription_end IS NOT NULL
              AND date(f.subscription_end, '+5 hours') = date('now', '+5 hours')
              AND f.subscription_end <= datetime('now')
        ''')
        return [dict(row) for row in cursor.fetchall()]


def get_all_parents_with_children() -> List[Dict[str, Any]]:
    """Возвращает всех родителей с их детьми (для массовых рассылок)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT DISTINCT p.telegram_id, s.id as student_id,
                   COALESCE(s.display_name, s.fio) as display_name
            FROM parents p
            JOIN family_links fl ON p.id = fl.parent_id
            JOIN students s ON fl.student_id = s.id
            WHERE p.telegram_id IS NOT NULL AND s.spreadsheet_id IS NOT NULL
        ''')
        return [dict(row) for row in cursor.fetchall()]
