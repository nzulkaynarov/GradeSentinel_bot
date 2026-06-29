"""ETL: перенос данных GradeSentinel SQLite → PostgreSQL (cutover, 2026-06-29).

Читает исходный sqlite (ТОЛЬКО ЧТЕНИЕ — файл остаётся как откат), грузит строки
с ЯВНЫМИ id в целевую PG, затем делает setval последовательностей. Объём данных
крошечный (~3800 строк), поэтому простой executemany без батчинга достаточно.

Предусловия:
  • целевая БД уже прошла `alembic upgrade head` (схема есть);
  • целевая БД ПУСТАЯ (или запускать с --truncate).

Запуск:
  SQLITE_PATH=/var/lib/gradesentinel/sentinel.db \
  DATABASE_URL=postgresql://gradesentinel:***@10.0.0.2:5432/gradesentinel?sslmode=require \
  python scripts/migrate_sqlite_to_pg.py [--truncate]
"""
import os
import sqlite3
import sys

import psycopg

# Порядок загрузки — по зависимостям FK (родители/семьи раньше связей).
TABLES = [
    "parents", "families", "students", "family_links",
    "grade_history", "quarter_grades",
    "app_states", "user_states", "support_msg_map",
    "notification_queue", "group_notification_queue",
    "ai_chat_messages", "ai_chat_feedback", "proactive_alerts",
    "family_invites", "payments", "settings", "promo_codes",
    "family_groups", "grade_history_archive",
]

# Таблицы с колонкой id IDENTITY → нужен setval после вставки явных id.
# (family_links — без id; settings — PK key text; app_states/user_states/
#  support_msg_map — PK не IDENTITY, id Telegram'ные/явные.)
ID_SEQ_TABLES = [
    "parents", "families", "students", "grade_history", "quarter_grades",
    "notification_queue", "group_notification_queue", "ai_chat_messages",
    "ai_chat_feedback", "proactive_alerts", "family_invites", "payments",
    "promo_codes", "family_groups", "grade_history_archive",
]


def main() -> int:
    sqlite_path = os.environ["SQLITE_PATH"]
    dsn = os.environ["DATABASE_URL"]
    truncate = "--truncate" in sys.argv

    src = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    inserted = {}
    with psycopg.connect(dsn) as dst:
        with dst.cursor() as cur:
            if truncate:
                cur.execute(
                    "TRUNCATE TABLE "
                    + ", ".join(f'"{t}"' for t in TABLES)
                    + " RESTART IDENTITY CASCADE"
                )
            for table in TABLES:
                rows = src.execute(f'SELECT * FROM "{table}"').fetchall()
                inserted[table] = len(rows)
                if not rows:
                    continue
                cols = list(rows[0].keys())
                collist = ", ".join(f'"{c}"' for c in cols)
                placeholders = ", ".join(["%s"] * len(cols))
                cur.executemany(
                    f'INSERT INTO "{table}" ({collist}) VALUES ({placeholders})',
                    [tuple(row[c] for c in cols) for row in rows],
                )
                print(f"  loaded {table}: {len(rows)}")

            # setval: явные id не двигают IDENTITY-последовательность.
            for table in ID_SEQ_TABLES:
                cur.execute(
                    f"""SELECT setval(
                            pg_get_serial_sequence('{table}', 'id'),
                            GREATEST((SELECT COALESCE(MAX(id), 0) FROM "{table}"), 1),
                            (SELECT COUNT(*) > 0 FROM "{table}")
                        )"""
                )
        dst.commit()

    # Сверка COUNT'ов sqlite ↔ pg.
    ok = True
    with psycopg.connect(dsn) as dst, dst.cursor() as cur:
        print("--- verify ---")
        for table in TABLES:
            s = src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            p = cur.fetchone()[0]
            flag = "OK" if s == p else "MISMATCH"
            if s != p:
                ok = False
            print(f"  {table:28} sqlite={s:<6} pg={p:<6} {flag}")
    src.close()
    print("ETL", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
