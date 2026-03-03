import sqlite3
import os
from contextlib import contextmanager
from typing import Optional, List, Dict, Any

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
        
        # 1. Семьи
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS families (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            family_name TEXT NOT NULL,
            subscription_end TIMESTAMP
        )
        ''')

        # 2. Родители
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS parents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fio TEXT NOT NULL,
            phone TEXT UNIQUE NOT NULL,
            telegram_id INTEGER UNIQUE,
            is_admin BOOLEAN DEFAULT 0
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

        # 4. Связи "Семьи - Родители - Дети" (Многие-ко-многим)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS family_links (
            family_id INTEGER,
            parent_id INTEGER,
            student_id INTEGER,
            FOREIGN KEY(family_id) REFERENCES families(id),
            FOREIGN KEY(parent_id) REFERENCES parents(id),
            FOREIGN KEY(student_id) REFERENCES students(id)
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

def get_active_spreadsheets() -> List[Dict[str, Any]]:
    """Возвращает список всех словарей {student_id, spreadsheet_id, fio} для опроса таблиц."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('SELECT id as student_id, fio, spreadsheet_id FROM students WHERE spreadsheet_id IS NOT NULL AND spreadsheet_id != ""')
        return [dict(row) for row in cursor.fetchall()]

if __name__ == '__main__':
    init_db()
    print("Database initialized successfully at", DB_PATH)
