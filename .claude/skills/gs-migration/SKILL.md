---
name: gs-migration
description: "Как добавить Alembic-миграцию в GradeSentinel (PostgreSQL 17, ручной op.execute, backfill, тесты через Docker). Использовать при любом изменении схемы БД."
---

# /gs-migration — изменение схемы

Схема — только через Alembic (`migrations/versions/`), `env.py` без `target_metadata` → всё руками через
`op.execute`. Прод применяет миграции автоматически: `init_db()` → `alembic upgrade head` при старте
сервисов после деплоя. Тесты поднимают схему тем же путём (`conftest.py`).

## Шаги
1. Ревизия: `make migration NAME=000N_slug MSG="что и зачем"` (или скопировать предыдущий файл).
   Обязательно: `revision = "000N_slug"`, `down_revision = "<предыдущая>"`, рабочий `downgrade()`.
2. Docstring в стиле репо: **Проблема → Решение → Инварианты/backfill → env.py note**. Это основная
   документация схемы (см. `0003_grade_notified_outbox.py`, `0004_student_academic_year.py`).
3. `upgrade()`:
   - Новые колонки — nullable или с безопасным DEFAULT; подумать, что означает DEFAULT для СТАРЫХ строк
     (пример: `notified_at DEFAULT now()` = «уже доставлено», иначе первый импорт стал бы спамом).
   - Backfill — одним `UPDATE ... FROM (...)`, детерминированно; «сегодня по Ташкенту» =
     `((now() AT TIME ZONE 'utc') + interval '5 hours')::date`.
   - Индексы — частичные там, где выборка почти всегда пустая (`WHERE notified_at IS NULL`).
   - Не менять `families` / `parents` / `family_links` без явного согласования (живые пользователи).
4. Код: SELECT'ы, которые должны видеть новую колонку, — обновить (`src/db/*.py`), плейсхолдеры `%s`,
   `RETURNING id`, Row — Mapping.
5. Тесты: `make test-reset && make test` (том Postgres персистит — без reset старая схема).
   Добавить тест на backfill/инвариант, если он нетривиален.
6. В PR — секция **Runbook**: что сделать на проде ДО/ПОСЛЕ merge (ручные DELETE, проверка
   `make prod-schema`, ожидаемые значения).

## Грабли
- Ложные/мусорные строки, от которых зависит backfill, удалять ДО merge (миграция 0004: `max(grade_date)`).
- `EXTRACT(...)` возвращает numeric — кастовать `::int`.
- Даты из PG приходят объектами `date/datetime`, не строками — `to_date_str()` для форматирования.
