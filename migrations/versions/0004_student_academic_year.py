"""students.academic_year — явный учебный год таблицы (rollover учебного года)

Инцидент 2026-09-02: в шапке «Все оценки!» даты без года («2 сентября»). Год
вычислялся от «сейчас» → в сентябре 2026 прошлогодняя таблица (2025/26) давала
колонку «2 сентября» = 2026-09-02 = сегодня → монитор разослал прошлогодние
оценки как новые. Школа каждый год выдаёт НОВУЮ ссылку; старая остаётся
привязанной к ученику, пока родитель её не сменит.

Решение (RFC `Docs/plans/2026-09-02-academic-year-rollover.md`):
  • `students.academic_year` — год НАЧАЛА учебного года, к которому относится
    привязанная таблица (2025 = 2025/26). Парсер шапки берёт год отсюда, а не
    от текущей даты → «2 сентября» в таблице 2025/26 всегда = 2025-09-02.
  • NULL = «ещё не определён» (новая привязка): importer выводит год по
    содержимому листа (`infer_sheet_academic_year`) и записывает.
  • Монитор НЕ опрашивает таблицы с academic_year < текущего учебного года
    (stale) и просит родителя обновить ссылку.

Backfill: год = учебный год последней оценки ученика (правило: сентябрь–декабрь
→ year, январь–август → year-1). Максимум берётся по ОБЕИМ таблицам оценок —
grade_history и grade_history_archive: weekly job переносит записи старше 180
дней в архив, поэтому у ученика, чьи оценки давно не обновлялись, свежих строк
в grade_history может не быть вовсе. Без учёта архива такой ученик получил бы
«сегодняшний» учебный год, его прошлогодняя таблица считалась бы актуальной, и
инцидент повторился бы ровно на нём. GREATEST в PostgreSQL игнорирует NULL, так
что достаточно одной непустой таблицы.

У ученика вообще без оценок — текущий учебный год на момент миграции.

ВНИМАНИЕ (post-mortem 2026-09-03): на проде эта ревизия применилась 2026-09-02 в
исходном виде — без учёта архива и с неудалённой ложной записью монитора за
«сегодня». Backfill подхватил её через max(grade_date) и проставил прошлогодней
таблице academic_year текущего года. Alembic не переприменяет уже накатанную
ревизию, поэтому прод чинится не миграцией, а кодом: монитор сверяет записанный
год с содержимым листа (_reconcile_academic_year) и исправляет запись сам.
Правки здесь важны для новых установок и как документация правильной логики.

env.py без target_metadata → миграция вручную (op.execute).

Revision ID: 0004_student_academic_year
Revises: 0003_grade_notified_outbox
Create Date: 2026-09-02
"""
from alembic import op

revision = "0004_student_academic_year"
down_revision = "0003_grade_notified_outbox"
branch_labels = None
depends_on = None


# Backfill вынесен в константу, чтобы тест мог прогнать РОВНО этот SQL против
# PostgreSQL 17 с подставленными данными (tests/test_migration_0004_backfill.py).
# Схему тестовая БД получает одним `alembic upgrade head` на пустых таблицах,
# поэтому иначе backfill остался бы непроверенным.
#
# Учебный год по дате: month >= 9 → year, иначе year - 1.
# Ташкентское «сегодня» для fallback — naive UTC + 5h (конвенция проекта).
BACKFILL_SQL = """
UPDATE students s
SET academic_year = CASE
    WHEN EXTRACT(MONTH FROM x.d) >= 9 THEN EXTRACT(YEAR FROM x.d)::int
    ELSE EXTRACT(YEAR FROM x.d)::int - 1
END
FROM (
    SELECT s2.id,
           COALESCE(
               GREATEST(
                   (SELECT MAX(gh.grade_date) FROM grade_history gh
                     WHERE gh.student_id = s2.id),
                   (SELECT MAX(ga.grade_date) FROM grade_history_archive ga
                     WHERE ga.student_id = s2.id)
               ),
               ((now() AT TIME ZONE 'utc') + interval '5 hours')::date
           ) AS d
    FROM students s2
) x
WHERE x.id = s.id AND s.academic_year IS NULL
"""


def upgrade():
    op.execute("ALTER TABLE students ADD COLUMN academic_year integer")
    op.execute(BACKFILL_SQL)


def downgrade():
    op.execute("ALTER TABLE students DROP COLUMN academic_year")
