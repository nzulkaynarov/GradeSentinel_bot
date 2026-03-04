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
    conn = sqlite3.connect(DB_PATH)
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
            subscription_end TIMESTAMP
        )
        ''')

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
            spreadsheet_id TEXT NOT NULL
        )
        ''')

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
        
        # 6. Автоматическая регистрация администратора из .env
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
    """Возвращает список всех словарей {student_id, spreadsheet_id, fio} для опроса таблиц."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id as student_id, fio, spreadsheet_id FROM students WHERE spreadsheet_id IS NOT NULL AND spreadsheet_id != ""')
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

def get_family_by_head(head_telegram_id: int) -> Optional[int]:
    """Возвращает ID семьи, в которой данный пользователь является главой (или он админ и в семье)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT fl.family_id 
            FROM family_links fl
            JOIN parents p ON fl.parent_id = p.id
            WHERE p.telegram_id = ? AND (p.role = 'head' OR p.role = 'admin')
            LIMIT 1
        ''', (head_telegram_id,))
        row = cursor.fetchone()
        return row['family_id'] if row else None

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

def add_student(fio: str, spreadsheet_id: str) -> Optional[int]:
    """Создает нового студента и возвращает его ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('INSERT INTO students (fio, spreadsheet_id) VALUES (?, ?)', (fio, spreadsheet_id))
        return cursor.lastrowid

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
        return [dict(row) for row in cursor.fetchall()]
    return []

def get_students_for_parent(telegram_id: int) -> List[Dict[str, Any]]:
    """Возвращает список всех студентов {student_id, fio, spreadsheet_id}, привязанных к telegram_id."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        return [dict(row) for row in cursor.fetchall()]
    return []

if __name__ == '__main__':
    init_db()
    print("Database initialized successfully at", DB_PATH)
def get_family_members(family_id: int) -> List[Dict[str, Any]]:
    """Возвращает список всех взрослых членов семьи."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
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

def delete_parent_from_family(family_id: int, parent_id: int) -> bool:
    """Удаляет родителя из семьи. Главу семьи удалить нельзя."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT role FROM parents WHERE id = ?', (parent_id,))
        row = cursor.fetchone()
        if row and row['role'] == 'head':
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
