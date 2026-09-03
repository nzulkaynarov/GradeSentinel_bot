# GradeSentinel — команды разработчика. `make help` — список.
# Прод-цели только READ-ONLY (логи/статус/SELECT). Деплой = merge в main.
.DEFAULT_GOAL := help
SHELL := /bin/bash

COMPOSE  := docker compose -f docker-compose.test.yml
TEST_ENV := -e BOT_TOKEN='12345:ci-test' -e ADMIN_GROUP_ID='0'
PYTEST   := pytest -q --tb=short -p no:cacheprovider
# ssh-алиасы из ~/.ssh/config: app-VPS (176.101.56.141) и DB-VPS (PostgreSQL 17)
VPS      ?= vps
DB_VPS   ?= vps-db
N        ?= 100
PY       := $(if $(wildcard venv/bin/python),venv/bin/python,python3)

help: ## Список целей
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n",$$1,$$2}'

# ── Локальная разработка ─────────────────────────────────────────────
venv: ## Создать venv (3.12) и поставить зависимости
	(python3.12 -m venv venv || python3 -m venv venv) && venv/bin/pip install -r requirements.txt

run-bot: ## Бот локально (polling); нужен .env
	$(PY) -m src.main

run-webapp: ## WebApp локально на 127.0.0.1:8443
	$(PY) webapp/app.py

check: ## Быстрая проверка без Docker: компиляция + JSON локалей
	$(PY) -m compileall -q src webapp migrations tests scripts
	$(PY) -c "import json,glob;[json.load(open(f)) for f in glob.glob('src/locales/*.json')+glob.glob('webapp/static/locales/*.json')];print('locales JSON ok')"

# ── Тесты: Docker + PostgreSQL 17 (единственный поддерживаемый способ) ──
test-build: ## Пересобрать тестовый образ (после смены requirements)
	$(COMPOSE) build tests

test: ## Полный сьют (ARGS="-x" и т.п.)
	$(COMPOSE) run --rm $(TEST_ENV) tests $(PYTEST) $(ARGS)

test-k: ## Подмножество: make test-k K=rollover
	@test -n "$(K)" || (echo "usage: make test-k K=<pytest -k expr>"; exit 1)
	$(COMPOSE) run --rm $(TEST_ENV) tests $(PYTEST) -k "$(K)"

test-file: ## Один файл: make test-file F=tests/test_x.py
	@test -n "$(F)" || (echo "usage: make test-file F=tests/test_x.py"; exit 1)
	$(COMPOSE) run --rm $(TEST_ENV) tests $(PYTEST) $(F)

test-reset: ## Сбросить тестовую БД (обязательно после новой миграции)
	$(COMPOSE) down -v

# ── Alembic ──────────────────────────────────────────────────────────
migration: ## Новая ревизия: make migration NAME=0005_slug MSG="описание"
	@test -n "$(NAME)" || (echo "usage: make migration NAME=0005_slug MSG=\"...\""; exit 1)
	$(PY) -m alembic revision --rev-id $(NAME) -m "$(MSG)"

migrate: ## alembic upgrade head локально (нужен DATABASE_URL)
	$(PY) -m alembic upgrade head

migrate-current: ## Текущая ревизия локальной БД
	$(PY) -m alembic current

# ── Прод: только чтение ──────────────────────────────────────────────
prod-status: ## Статус systemd-юнитов на app-VPS
	ssh $(VPS) 'systemctl is-active gradesentinel-bot gradesentinel-webapp; systemctl status gradesentinel-bot --no-pager | head -12'

prod-logs: ## Последние N строк логов бота (N=100)
	ssh $(VPS) 'journalctl -u gradesentinel-bot -n $(N) --no-pager'

prod-follow: ## Логи бота в реальном времени
	ssh $(VPS) 'journalctl -u gradesentinel-bot -f'

prod-grep: ## Тег в логах за сутки: make prod-grep TAG='STALE_SHEET|NEW GRADE'
	@test -n "$(TAG)" || (echo "usage: make prod-grep TAG=STALE_SHEET"; exit 1)
	ssh $(VPS) 'journalctl -u gradesentinel-bot --since "24 hours ago" --no-pager' | grep -E "$(TAG)"

prod-sql: ## Read-only SQL на DB-VPS: make prod-sql SQL="select count(*) from grade_history"
	@test -n "$(SQL)" || (echo "usage: make prod-sql SQL=\"select ...\""; exit 1)
	ssh $(DB_VPS) 'sudo -u postgres psql gradesentinel -At -c "$(SQL)"'

prod-schema: ## Версия схемы на проде (alembic_version)
	ssh $(DB_VPS) 'sudo -u postgres psql gradesentinel -At -c "select * from alembic_version"'

prod-psql: ## Интерактивный psql на DB-VPS
	ssh -t $(DB_VPS) 'sudo -u postgres psql gradesentinel'

# ── Git / CI ─────────────────────────────────────────────────────────
pr: ## Открыть PR в main из текущей ветки (тело — из шаблона в редакторе)
	gh pr create --base main --fill

ci: ## Последние прогоны GitHub Actions
	gh run list --limit 8

prs: ## Открытые PR
	gh pr list

.PHONY: help venv run-bot run-webapp check test-build test test-k test-file test-reset \
	migration migrate migrate-current prod-status prod-logs prod-follow prod-grep \
	prod-sql prod-schema prod-psql pr ci prs
