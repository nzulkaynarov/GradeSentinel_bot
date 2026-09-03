# Разработка (Development Guide)

Актуально на 2026-09. Стек: Python 3.12, pyTelegramBotAPI (sync polling), PostgreSQL 17 (psycopg v3),
Alembic, Flask+gunicorn (Mini App), Google Sheets API, Anthropic SDK. Прод — bare-metal VPS, деплой
push→`main` (см. [MAINTENANCE.md](MAINTENANCE.md), [deploy/README.md](../deploy/README.md)).

## Требования
- Python 3.12 (локально допустим 3.10+; тесты в любом случае идут в Docker на 3.12)
- Docker (для тестов — контейнер `postgres:17`)
- `gh` CLI (PR, статус CI), `make`
- Секреты: `.env` (из `.env.example`) и `config/credentials.json` (Google Service Account) — **не в git**

## Быстрый старт
```bash
git clone <repo> && cd GradeSentinel_bot
make venv                      # venv + requirements.txt
cp .env.example .env           # заполнить BOT_TOKEN, ADMIN_ID, ADMIN_GROUP_ID, DATABASE_URL, …
mkdir -p data config           # credentials.json → config/
make run-bot                   # бот в polling-режиме
make run-webapp                # Mini App на 127.0.0.1:8443 (в другом терминале)
make help                      # все цели
```

Локальная БД — PostgreSQL по `DATABASE_URL` (например, тот же `docker compose -f docker-compose.test.yml up db`
с `postgresql://gs:test@localhost:5432/gstest`). Схема создаётся автоматически: `init_db()` → `alembic upgrade head`.

## Тесты — только через Docker
```bash
make test                      # полный сьют против PostgreSQL 17 (~5 c после сборки образа)
make test-k K=rollover         # подмножество по -k
make test-file F=tests/test_x.py
make test-reset                # сбросить тестовую БД (обязательно после новой миграции)
make test-build                # пересобрать образ после смены requirements
make check                     # без Docker: компиляция + валидность JSON локалей
```
Локальный `pytest` без `DATABASE_URL` **молча пропускает все БД-тесты** — не считается прогоном.
CI (`.github/workflows/tests.yml`, job `pytest`) — required check для `main`.

### Правила для тестов
- Даты фиксированные (`date(2026, 5, 21)`), не «сегодня − N»: сезонные тесты протухают в сентябре/январе.
- Даты шапки листов без года → передавать `academic_year=` в парсер (CLAUDE.md §26e).
- БД-тесты используют фикстуру `temp_db` (TRUNCATE до/после); in-memory состояние монитора сбрасывать
  (`_pending_grades`, `_student_failure_counts`, `_stale_logged_on`).
- Sheets и Telegram — только моки (`patch('src.monitor_engine.get_sheet_data')`, `MagicMock` для Sender).

## Схема БД
Только через Alembic: `make migration NAME=000N_slug MSG="…"`, ручной `op.execute`, docstring
«Проблема → Решение → Инварианты». Подробно — skill `/gs-migration` (`.claude/skills/gs-migration/SKILL.md`).

## Git-процесс
- Ветка от `main`: `fix/`, `feat/`, `refactor/`, `docs/`, `chore/`. Не базировать PR на другой feature-ветке.
- `make test` → `git push -u origin <branch>` → `make pr` (или `gh pr create --base main`).
- **Merge в `main` = деплой на прод** (workflow `Tests` → `Deploy to VPS`). Мержит владелец.
- Не амендить опубликованные коммиты, не `--no-verify`, не `push --force`.

## Конвенции кода
См. CLAUDE.md («Конвенции и подводные камни», «Style guide») — это источник истины. Ключевое:
- SQL: `%s`-плейсхолдеры, `RETURNING id`, `get_db_connection()` как context manager, Row — Mapping.
- Бот синхронный. Порядок регистрации handlers важен (`state_flows → navigation → ai_chat → rest`).
- Тексты пользователю только через `t()`, три локали синхронны (skill `/gs-i18n`).
- Логгер `logging.getLogger(__name__)`, теги в квадратных скобках для grep (`[STALE_SHEET]`, `[PENDING]`).
- Константы — в `src/config.py` (читаются из ENV с дефолтами).

## Работа с Claude Code
Проектные skills в `.claude/skills/` (вызов `/gs-…`): `gs-session-start`, `gs-prod-ops`, `gs-incident`,
`gs-migration`, `gs-i18n`, `gs-pr`, `gs-subagent-brief`. Hook `.claude/hooks/post-edit-check.sh`
компилирует каждый отредактированный `.py` и валидирует JSON локалей сразу после правки.
Контекст между сессиями/машинами — `Docs/plans/*-SESSION-HANDOFF.md` (память Claude машинно-локальна).
