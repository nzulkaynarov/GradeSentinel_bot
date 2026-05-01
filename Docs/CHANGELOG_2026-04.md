# Audit & Refactor — 2026-04-30

> Большой security/stability/maintainability аудит. Закрыто 26 пунктов.
> Полная архитектура — `Docs/ARCHITECTURE.md`. Контекст для разработки — `CLAUDE.md`.

---

## 🔴 Критичные уязвимости (закрыты)

| # | Что было | Как починили |
|---|----------|--------------|
| 1 | Обход авторизации в 11 callback-хендлерах: любой пользователь мог удалить чужую семью / детей / создать invite на чужую семью через crafted callback_data | Helper `_check_family_access` (family.py) + `can_manage_family` (db/auth.py) + безопасный `_parse_int_args`. Применено в `del_par`, `del_stud`, `confirm_delete_family`, `gen_invite`, `add_child`, `add_member`, `list_edit`, `open_manage`, `back_manage`, `admin_manage`, `delete_family` |
| 2 | SQL-injection в `create_promo_code` — `expires_days` интерполировался в SQL через f-string | Принудительный `int()` + параметризованный `?` плейсхолдер |
| 3 | `get_db_connection` коммитил даже при exception → риск порчи данных | Rollback-on-exception, `PRAGMA foreign_keys=ON`, явный commit только при успехе |
| 4 | FK constraints не работали (off by default), удаление семьи оставляло сирот в `payments`/`invites`/`quarter_grades` | `delete_family_cascade()` в `src/db/maintenance.py` — атомарно (BEGIN IMMEDIATE) чистит все связанные таблицы и осиротевших students. `PRAGMA foreign_keys=ON` в connection |
| 5 | Покупка подписки на чужую семью через crafted `sub_fam_*` callback | `_check_user_can_pay_for_family` — проверка членства user_id в family. Применено в `sub_fam`, `sub_pay`, `sub_card`, `sub_card_done` |

---

## 🟠 Стабильность и узкие места

| # | Что было | Как починили |
|---|----------|--------------|
| 6 | `_panel_cache` и `_rate_limit_store` без локов → race conditions; rate limit memory-leak (без TTL) | `threading.Lock` для обоих + GC неактивных пользователей раз в 600с. Rate limiter вынесен в `src/rate_limiter.py` (без зависимости от bot_instance — работает в CI) |
| 7 | Sequential polling: одна сломанная Sheet блокировала весь цикл на 14+ секунд | `ThreadPoolExecutor(workers=8)` для параллельного fetch'а. Failure-counter per-student с алертом после 5 подряд (cooldown 24ч) |
| 8 | Broadcast не уважал Telegram RetryAfter → терял большую часть рассылки на flood control | `src/telegram_utils.py` → `send_with_retry` с парсингом `retry_after`, exp backoff на 5xx, no-retry на 403 |
| 9 | Scheduler `last_*_date` в памяти → перекрытие задач + потери при рестарте | Per-job `threading.Lock` + маркеры в `settings` таблице. In-memory cache маркеров (lazy load из БД) |
| 10 | `_broadcast_pending` в памяти → админ теряет работу при рестарте | Сохраняется в `user_states` (`confirming_broadcast`), fallback при рестарте |
| 11 | WebApp возвращал оценки независимо от подписки → bypass монетизации | `_authorize_student_access` → `is_student_under_active_subscription`. 402 Payment Required для не-админов с истёкшей подпиской |
| 12 | Race condition в `use_invite` — двое могли одновременно использовать одну ссылку | `use_invite` (атомарный `WHERE is_used=0`) вызывается ВПЕРЕДИ `link_parent_to_family` |

---

## 🟡 Среднее

| # | Что было | Как починили |
|---|----------|--------------|
| 13 | `grade_history` рос неограниченно | `grade_history_archive` таблица + weekly cleanup job (вс 03:00). `archive_old_grades` транзакционный (BEGIN IMMEDIATE, batched delete по id). + `cleanup_expired_invites`, `cleanup_old_notification_queue` |
| 14 | Hardcoded русский в fallback'ах (Новый ученик, Неизвестно, Неизвестен) + полные телефоны в логах | Ключи `default_student_name`/`unknown_family`/`unknown_phone` во всех 3 локалях. `mask_phone()` в `src/utils.py` (показывает только последние 4 цифры) |
| 15 | 0 автотестов | 63 pytest теста: data_cleaner, authorization, cascade_delete, promo_codes (incl. SQL-injection regression), telegram_utils retry/RetryAfter, archive, rate_limit, scheduler markers, db package, locales sync, mask_phone, config |
| 16 | requirements без версий, Docker без healthcheck, root user | Все версии запиннены. `HEALTHCHECK` через heartbeat файл `data/.heartbeat` (без curl, без HTTP). Non-root `bot` user в обоих Dockerfile |
| 17 | Реальный детский дневник, GEMINI.md, устаревший AUDIT_REPORT.md в репо | `git rm`. `*.xlsx` и `*.db` в `.gitignore` |

---

## 🟢 Расширение

| # | Что |
|---|-----|
| 18 | **Heartbeat thread** в `main.py` — `data/.heartbeat` обновляется раз в 30с, Docker проверяет mtime для healthcheck (не зависит от внешней сети, без curl) |
| 19 | **WebApp Dockerfile** без `apt-get curl` — healthcheck через `python3 -c "urllib.request..."` |
| 20 | **Пакет `src/db/`** (`auth.py`, `maintenance.py`, `connection.py`) как re-export над database_manager. Точка миграции для будущего дробления — новый код пишет в `src/db/` |
| 21 | **`src/config.py`** — все константы централизованы (POLLING_INTERVAL, RATE_LIMIT_*, QUIET_HOURS_*, FETCH_WORKERS, GRADE_ARCHIVE_DAYS, MAX_CHILDREN_PER_FAMILY, INVITE_EXPIRES_HOURS, BROADCAST_*, HEARTBEAT_INTERVAL, SENTRY_DSN). `_env_int()` helper с fallback на default при невалидных env |
| 22 | **`src/error_reporter.py`** — `report(scope, exc, **ctx)` + `warn()`. Опциональный Sentry hook через `SENTRY_DSN`. Интегрирован в scheduler, monitor, broadcast |
| 23 | **`tests/test_locales_sync.py`** — гарантирует что ru/uz/en имеют одинаковый набор ключей и плейсхолдеров |
| 24 | **Type hints** в helper-функциях нового кода (`_check_family_access`, `_check_user_can_pay_for_family`, `send_with_retry`, etc.) |
| 25 | **Тесты на новый код** — `test_cascade_delete`, `test_config`, `test_db_package`, `test_scheduler_markers`, `test_utils_mask_phone`. Итого 63 passing |
| 26 | **`Docs/ARCHITECTURE.md`** переписан под текущую структуру |

---

## Перенастройка под Claude Code

| Файл | Что |
|------|-----|
| `CLAUDE.md` | Создан — контекст проекта в каждой сессии |
| `.claude/settings.json` | Allowlist (ls/grep/git status/pytest/docker logs), ask-list (push/commit/build), deny-list (.env, credentials.json, *.db) |
| `.claude/settings.local.json` | Локальные оверрайды |
| `.gitignore` | `.claude/settings.local.json`, `*.xlsx`, `*.db`, `*.sqlite3` |
| `.github/workflows/tests.yml` | CI pytest на push/PR |
| Удалено | `GEMINI.md`, `AUDIT_REPORT.md`, `Дневник…xlsx` |
| README.md | Секция «Разработка с Claude Code» |

---

## Ключевые новые модули

- `src/config.py` — централизованная конфигурация
- `src/rate_limiter.py` — per-user rate limiter (thread-safe, без зависимости от bot_instance)
- `src/telegram_utils.py` — `send_with_retry` с обработкой 429/5xx
- `src/error_reporter.py` — единая точка ошибок + Sentry-ready
- `src/db/connection.py` — re-export `get_db_connection`, `init_db`
- `src/db/auth.py` — re-export авторизационных функций
- `src/db/maintenance.py` — re-export cascade/cleanup/archive

## Ключевые изменения существующих модулей

- `src/main.py` — heartbeat, импорт rate limiter, panel cache thread-safe
- `src/database_manager.py` — rollback-on-exception, foreign_keys, `delete_family_cascade`, `is_head_of_family`/`is_member_of_family`/`can_manage_family`/`is_student_under_active_subscription`, `archive_old_grades`/`cleanup_*` с config defaults, защита init_db от пустой БД
- `src/monitor_engine.py` — параллельный polling, `_polling_lock`, failure tracking
- `src/schedulers.py` — per-job locks, БД-маркеры, weekly cleanup job
- `src/notification_helpers.py` — quiet hours из config
- `src/handlers/admin.py` — admin checks, `delete_family_cascade`
- `src/handlers/family.py` — `_check_family_access`, безопасный `_parse_int_args`, MAX_CHILDREN_PER_FAMILY из config
- `src/handlers/subscription.py` — `_check_user_can_pay_for_family`
- `src/handlers/communication.py` — broadcast retry, `_save_broadcast_pending` в БД
- `src/handlers/invite.py` — атомарный use_invite перед link, INVITE_EXPIRES_HOURS из config
- `webapp/app.py` — `_authorize_student_access` с проверкой подписки

---

## Метрики

- **Файлов изменено:** ~25
- **Новых файлов:** 13 (config, rate_limiter, telegram_utils, error_reporter, src/db/×3, tests/×9)
- **Тестов:** 0 → 63 (passing)
- **CI:** добавлен GitHub Actions
- **Покрытие критичных путей:** sanitize_grade, авторизация, cascade delete, promo (SQL-injection regression), retry, archive, scheduler, mask_phone

---

## Что НЕ сделано (на будущее)

- Дробление `database_manager.py` (1500 строк) и `subscription.py` (1300 строк) на меньшие модули. Паттерн установлен через `src/db/`, но миграция тел функций пока только для новых функций.
- Полные type hints во всех 50+ handler-функциях (только helpers).
- Webhook вместо polling.
- Миграция на PostgreSQL (актуально при >100 семей).
- Геймификация для учеников.

---

## Перед деплоем — обязательно

```bash
# 1. Smoke-test сборки
docker compose build

# 2. Тесты
python3 -m pytest tests/   # 63 passed

# 3. Миграция на копии прод-БД (важно из-за PRAGMA foreign_keys=ON)
cp /path/to/prod/sentinel.db /tmp/test.db
python3 -c "from src.database_manager import init_db; init_db()"
sqlite3 /tmp/test.db "PRAGMA foreign_key_check;"  # должно быть пусто
```

## Рекомендуемая структура коммитов

1. **Setup Claude Code** — `CLAUDE.md`, `.claude/`, `.gitignore`, удаление `GEMINI.md`/`AUDIT_REPORT.md`/`.xlsx`, обновление README
2. **Security fixes** — авторизация в callback'ах, SQL-injection promo, payment family check, invite race
3. **DB integrity** — `PRAGMA foreign_keys`, rollback-on-exception, `delete_family_cascade`
4. **Stability** — параллельный polling, RetryAfter, scheduler persistence, broadcast в БД, panel cache lock
5. **Refactor** — `src/db/`, `src/config.py`, `src/rate_limiter.py`, `src/telegram_utils.py`, `src/error_reporter.py`
6. **Tests + CI** — `tests/`, `pytest.ini`, `.github/workflows/tests.yml`
7. **Docs** — `Docs/ARCHITECTURE.md`, `Docs/CHANGELOG_2026-04.md`, README
8. **Docker** — pinned requirements, healthcheck, non-root user, webapp dockerfile

Каждый коммит обратимый по отдельности.
