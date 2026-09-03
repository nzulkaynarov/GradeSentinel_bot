"""grade_history_archive: чистка дублей + UNIQUE, чтобы они не возвращались

Инцидент 2026-09-03 (аудит дашборда). Цикл, работавший с мая:

  1. `archive_old_grades` отбирал записи по `date_added < now - 180 дней`.
  2. `history_importer` пишет в `date_added` НЕ «когда бот узнал», а саму дату
     оценки (12:00 того дня) — импортированная история рождалась уже «старой»
     и уезжала в архив на ближайшей воскресной чистке.
  3. Дедуп импортёра смотрел только в `grade_history`. Заархивированной оценки
     там нет → «новая» → импортируется заново.
  4. Через неделю чистка уносит её опять — и так каждое воскресенье.

К моменту разбора архив содержал 7141 строку против 720 уникальных: каждая
оценка продублирована до 16 раз, по разу за каждый прогон с 2026-05-09.
Побочный эффект был виден родителю: набор данных в дашборде менялся в
зависимости от того, когда посмотреть (после чистки часть истории исчезала,
затем импортёр её постепенно возвращал).

Код починен отдельно (`archive_old_grades` отбирает по `grade_date`, дедуп
импортёра читает обе таблицы). Эта миграция убирает уже накопленное и ставит
барьер, чтобы дубль не мог появиться снова даже при ошибке в коде.

Дедуп: оставляем строку с минимальным id в каждой группе
(student_id, subject, COALESCE(grade_date, date_added::date), raw_text).
COALESCE — ради 242 legacy-записей с NULL grade_date (до этапа 1A RFC).

UNIQUE — частичный, только для строк с непустым grade_date: в SQL два NULL не
равны, поэтому на legacy-записи индекс всё равно не подействовал бы, а
частичный не мешает им остаться. `archive_old_grades` вставляет с
ON CONFLICT DO NOTHING.

env.py без target_metadata → миграция вручную (op.execute).

Revision ID: 0005_archive_dedup
Revises: 0004_student_academic_year
Create Date: 2026-09-03
"""
from alembic import op

revision = "0005_archive_dedup"
down_revision = "0004_student_academic_year"
branch_labels = None
depends_on = None

_IDX_NAME = "uq_grade_archive_content"

# Вынесено в константу — тест прогоняет РОВНО этот SQL против живого PostgreSQL
# с посеянными дублями (tests/test_migration_0005_archive_dedup.py).
DEDUP_SQL = """
DELETE FROM grade_history_archive a
USING (
    SELECT MIN(id) AS keep_id,
           student_id, subject, raw_text,
           COALESCE(grade_date, (date_added::timestamp + interval '5 hours')::date) AS d
      FROM grade_history_archive
     GROUP BY student_id, subject, raw_text,
              COALESCE(grade_date, (date_added::timestamp + interval '5 hours')::date)
    HAVING COUNT(*) > 1
) dup
WHERE a.student_id = dup.student_id
  AND a.subject = dup.subject
  AND a.raw_text = dup.raw_text
  AND COALESCE(a.grade_date, (a.date_added::timestamp + interval '5 hours')::date) = dup.d
  AND a.id <> dup.keep_id
"""


def upgrade():
    op.execute(DEDUP_SQL)
    op.execute(
        f"CREATE UNIQUE INDEX {_IDX_NAME} ON grade_history_archive "
        f"(student_id, subject, grade_date, raw_text) WHERE grade_date IS NOT NULL"
    )


def downgrade():
    # Удалённые дубли не восстанавливаем — они были мусором.
    op.execute(f"DROP INDEX IF EXISTS {_IDX_NAME}")
