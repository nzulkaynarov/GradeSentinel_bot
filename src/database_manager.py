import sqlite3
import os
import logging
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sentinel.db")

@contextmanager
def get_db_connection():
    """Контекстный менеджер для безопасного подключения к БД."""
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.execute('PRAGMA journal_mode=WAL;')
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.commit()
        conn.close()

def init_db():
    """Инициализация таблиц базы данных."""
    # Ensure data directory exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Миграция: проверяем наличие колонки role в parents
        cursor.execute("PRAGMA table_info(parents)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'phone' in columns and 'role' not in columns:
            # Проверяем, был ли старый is_admin
            is_admin_exists = 'is_admin' in columns
            cursor.execute("ALTER TABLE parents ADD COLUMN role TEXT DEFAULT 'senior'")
            if is_admin_exists:
                cursor.execute("UPDATE parents SET role = 'admin' WHERE is_admin = 1")
            logger.info("Database migration: role column added.")

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
        if 'head_id' not in columns_families:
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
    """Возвращает список всех студентов {id, fio, spreadsheet_id, display_name}, привязанных к telegram_id.
    Если указан family_id, фильтрует только по этой семье."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        if family_id:
            cursor.execute('''
                SELECT DISTINCT s.id, s.fio, s.spreadsheet_id, s.display_name
                FROM students s
                JOIN family_links fl ON s.id = fl.student_id
                JOIN parents p ON fl.parent_id = p.id
                WHERE p.telegram_id = ? AND fl.family_id = ?
            ''', (telegram_id, family_id))
        else:
            cursor.execute('''
                SELECT DISTINCT s.id, s.fio, s.spreadsheet_id, s.display_name
                FROM students s
                JOIN family_links fl ON s.id = fl.student_id
                JOIN parents p ON fl.parent_id = p.id
                WHERE p.telegram_id = ?
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
    """Удаляет ребенка из конкретной семьи."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM family_links WHERE family_id = ? AND student_id = ?', (family_id, student_id))
        
        # Если студент больше ни к кому не привязан, можно удалить его совсем
        cursor.execute('SELECT COUNT(*) as count FROM family_links WHERE student_id = ?', (student_id,))
        if cursor.fetchone()['count'] == 0:
            cursor.execute('DELETE FROM students WHERE id = ?', (student_id,))
            cursor.execute('DELETE FROM grade_history WHERE student_id = ?', (student_id,))

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
    """Возвращает все оценки студента за сегодня."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT subject, grade_value, raw_text, date_added
            FROM grade_history
            WHERE student_id = ? AND date(date_added) = date('now')
            ORDER BY date_added
        ''', (student_id,))
        return [dict(row) for row in cursor.fetchall()]


def has_today_grades_for_parent(telegram_id: int) -> bool:
    """Проверяет, есть ли сегодня хоть одна оценка у детей родителя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) as c FROM grade_history gh
            JOIN family_links fl ON gh.student_id = fl.student_id
            JOIN parents p ON fl.parent_id = p.id
            WHERE p.telegram_id = ? AND date(gh.date_added) = date('now')
        ''', (telegram_id,))
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
