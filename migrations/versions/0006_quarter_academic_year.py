"""quarter_grades.academic_year — четверти перестают перезаписывать прошлогодние

Фаза 2 RFC rollover (`Docs/plans/2026-09-02-academic-year-rollover.md`).

Ключ таблицы был `(student_id, subject, quarter)`, года в нём нет. Как только в
новой таблице школы появлялись первые четвертные, `upsert_quarter_grade`
перезаписывал строку прошлого года: карточка предмета превращалась в смесь двух
учебных лет (Q1 нового рядом с Q2–Q4 старого), а «прогноз годовой» считался по
этой смеси. Проявилось бы к первой четверти, то есть к ноябрю 2026.

Backfill: существующие строки относятся к учебному году, который записан в
`students.academic_year` (после миграции 0004 он есть у всех). Если у ученика
он всё же NULL — берём учебный год по дате: сентябрь–декабрь → year,
январь–август → year-1, по Ташкенту (UTC+5), как везде в проекте.

UNIQUE пересобирается на `(student_id, academic_year, subject, quarter)`.
Порядок важен: сначала колонка и backfill, потом NOT NULL, и только затем смена
ограничения — иначе UNIQUE построился бы по строкам с NULL.

env.py без target_metadata → миграция вручную (op.execute).

Revision ID: 0006_quarter_academic_year
Revises: 0005_archive_dedup
Create Date: 2026-09-03
"""
from alembic import op

revision = "0006_quarter_academic_year"
down_revision = "0005_archive_dedup"
branch_labels = None
depends_on = None

_OLD_UQ = "quarter_grades_student_id_subject_quarter_key"
_NEW_UQ = "uq_quarter_grades_year_subject_quarter"

# В константе — чтобы тест прогнал ровно этот SQL против живого PostgreSQL.
BACKFILL_SQL = """
UPDATE quarter_grades q
SET academic_year = COALESCE(
        s.academic_year,
        CASE
            WHEN EXTRACT(MONTH FROM ((now() AT TIME ZONE 'utc') + interval '5 hours')::date) >= 9
                THEN EXTRACT(YEAR FROM ((now() AT TIME ZONE 'utc') + interval '5 hours')::date)::int
            ELSE EXTRACT(YEAR FROM ((now() AT TIME ZONE 'utc') + interval '5 hours')::date)::int - 1
        END)
FROM students s
WHERE s.id = q.student_id AND q.academic_year IS NULL
"""


def upgrade():
    op.execute("ALTER TABLE quarter_grades ADD COLUMN academic_year integer")
    op.execute(BACKFILL_SQL)
    # Ученик мог быть удалён, а строки остаться (исторически FK не всегда каскадил).
    op.execute(
        """
        UPDATE quarter_grades
        SET academic_year = CASE
            WHEN EXTRACT(MONTH FROM ((now() AT TIME ZONE 'utc') + interval '5 hours')::date) >= 9
                THEN EXTRACT(YEAR FROM ((now() AT TIME ZONE 'utc') + interval '5 hours')::date)::int
            ELSE EXTRACT(YEAR FROM ((now() AT TIME ZONE 'utc') + interval '5 hours')::date)::int - 1
        END
        WHERE academic_year IS NULL
        """
    )
    op.execute("ALTER TABLE quarter_grades ALTER COLUMN academic_year SET NOT NULL")
    op.execute(f"ALTER TABLE quarter_grades DROP CONSTRAINT IF EXISTS {_OLD_UQ}")
    op.execute(
        f"ALTER TABLE quarter_grades ADD CONSTRAINT {_NEW_UQ} "
        f"UNIQUE (student_id, academic_year, subject, quarter)"
    )


def downgrade():
    # Обратно к ключу без года: строки разных лет схлопнулись бы, поэтому
    # оставляем только самый свежий год каждого (ученик, предмет, четверть).
    op.execute(
        """
        DELETE FROM quarter_grades q
        USING (
            SELECT student_id, subject, quarter, MAX(academic_year) AS keep_year
              FROM quarter_grades GROUP BY student_id, subject, quarter
        ) latest
        WHERE q.student_id = latest.student_id AND q.subject = latest.subject
          AND q.quarter = latest.quarter AND q.academic_year <> latest.keep_year
        """
    )
    op.execute(f"ALTER TABLE quarter_grades DROP CONSTRAINT IF EXISTS {_NEW_UQ}")
    op.execute(
        f"ALTER TABLE quarter_grades ADD CONSTRAINT {_OLD_UQ} "
        f"UNIQUE (student_id, subject, quarter)"
    )
    op.execute("ALTER TABLE quarter_grades DROP COLUMN academic_year")
