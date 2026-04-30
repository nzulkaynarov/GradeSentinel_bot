"""Re-export get_db_connection / DB_PATH.

Удобная точка входа для нового кода: `from src.db.connection import get_db_connection`.
"""
from src.database_manager import get_db_connection, DB_PATH, init_db

__all__ = ["get_db_connection", "DB_PATH", "init_db"]
