# Архитектура проекта GradeSentinel

> Актуально на: 2026-04-30 (после security/stability аудита).
> Полный контекст для разработчика — см. `CLAUDE.md` в корне.

## Концепция

GradeSentinel — Telegram-бот для автоматического мониторинга оценок из электронных дневников (Google Sheets). Уведомляет родителей о новых/изменённых оценках, шлёт ежедневные сводки, поддерживает AI-анализ успеваемости.

**В продакшене с реальными пользователями** — любые изменения требуют осторожности (миграции, regression tests).

---

## Стек

- **Backend:** Python 3.10 (Docker slim image)
- **Bot lib:** pyTelegramBotAPI (синхронный, polling)
- **БД:** SQLite + WAL + `PRAGMA foreign_keys=ON`
- **Sheets API:** Google Sheets v4 (Service Account)
- **Платежи:** Telegram Payments API (Click / Payme)
- **AI:** Anthropic Claude API
- **WebApp:** Flask (отдельный контейнер)
- **Хостинг:** Raspberry Pi 3B
- **Observability:** опциональный Sentry через `SENTRY_DSN`

---

## Структура исходников

```
src/
├── main.py                # Точка входа, /start, авторизация, user-panel,
│                          # rate limit (thread-safe + GC), heartbeat
├── bot_instance.py        # Singleton telebot
├── config.py              # ★ Все константы (POLLING_INTERVAL, RATE_LIMIT_*, ...)
├── error_reporter.py      # ★ report() / warn() — единая точка ошибок + Sentry hook
├── telegram_utils.py      # ★ send_with_retry — корректная обработка 429 RetryAfter
├── google_sheets.py       # Кэшированный Sheets-сервис (singleton)
├── monitor_engine.py      # Polling-цикл (parallel ThreadPoolExecutor),
│                          # snapshot-сравнение, изоляция упавших таблиц
├── data_cleaner.py        # sanitize_grade — нормализация оценок
├── analytics_engine.py    # Claude API анализ успеваемости
├── schedulers.py          # Daily jobs (вечерняя сводка, тихие часы, четверти,
│                          # подписки, weekly cleanup) + per-job locks +
│                          # маркеры в БД (переживают рестарт)
├── notification_helpers.py # Форматирование сообщений, тихие часы
├── history_importer.py    # Импорт исторических листов
├── i18n.py                # t(key, lang, **kwargs)
├── ui.py                  # send_menu_safe, send_content
├── utils.py               # clean_student_name, mask_phone (PII в логах)
├── locales/               # ru.json, uz.json, en.json (синхронны — есть тест)
│
├── database_manager.py    # Schema, миграции, CRUD (большой файл — постепенно
│                          # дробится в src/db/)
├── db/                    # ★ Тематические модули БД (re-export из database_manager)
│   ├── connection.py      #   get_db_connection, init_db
│   ├── auth.py            #   is_head_of_family, can_manage_family,
│   │                      #   is_member_of_family, is_student_under_active_subscription
│   └── maintenance.py     #   delete_family_cascade, archive_old_grades,
│                          #   cleanup_expired_invites, cleanup_old_notification_queue
│
└── handlers/
    ├── admin.py           # admin panel, /add_family, /list_families
    ├── family.py          # /manage_family, _check_family_access, /grades
    ├── communication.py   # Поддержка → admin group, broadcast (с retry)
    ├── analytics.py       # /ai_report
    ├── settings.py        # Смена языка
    ├── subscription.py    # Оплата (Click/Payme), промокоды, /grant_sub
    └── invite.py          # Инвайт-ссылки

webapp/
├── app.py                 # Flask: /webapp, /api/students, /api/grades,
│                          # /api/quarters, /health. Авторизация через
│                          # _authorize_student_access (проверяет HMAC + подписку)
└── templates/, static/

tests/                     # 60+ pytest-тестов, запускаются в CI
data/sentinel.db           # БД (Docker volume)
data/.heartbeat            # Файл для healthcheck (touch'ит main thread)
config/credentials.json    # Google Service Account (volume, НЕ в репо)
.env                       # Токены (НЕ в репо)
.claude/                   # Claude Code settings
```

★ — модули, добавленные/переработанные в апреле 2026.

---

## Модель данных (SQLite)

| Таблица | Что хранит | Особенности |
|---------|-----------|-------------|
| `parents` | fio, phone (unique), telegram_id (unique), role, lang, notify_mode | Роль = `admin` / `senior` |
| `students` | fio, spreadsheet_id, display_name | display_name кэшируется при добавлении |
| `families` | family_name, head_id (FK), subscription_end | head_id перенесён из parents в families |
| `family_links` | M2M family↔parent, family↔student | UNIQUE(family_id, parent_id, student_id) |
| `grade_history` | subject, raw_text, grade_value, cell_reference, date_added | UNIQUE(student_id, cell_reference) |
| `grade_history_archive` | То же + archived_at | Старые записи (>180 дней) перемещаются weekly job |
| `quarter_grades` | Четвертные оценки | UNIQUE(student_id, subject, quarter) |
| `notification_queue` | Очередь тихих часов (22:00–07:00 Asia/Tashkent) | Сообщения старше 48ч чистятся |
| `family_invites` | Одноразовые инвайт-ссылки | Атомарное `use_invite()` через WHERE is_used=0 |
| `payments` | charge IDs, amount, plan | Каскадная чистка через `delete_family_cascade` |
| `user_states` | pending_lang, pending_invite, broadcast и др. | Replaces in-memory state — переживает рестарт |
| `promo_codes` | code, discount, expires_at | `expires_days` параметризован (защита от SQL-injection) |
| `support_msg_map` | message_id ↔ user_id для admin reply | |
| `settings` | key-value (плюс `scheduler_last_*` маркеры) | |

`PRAGMA foreign_keys=ON` включён в `get_db_connection()`. Удаление семьи через `delete_family_cascade` — атомарно (BEGIN IMMEDIATE), чистит payments/invites/links/осиротевших students со всей их историей.

---

## Ключевые компоненты

### 1. Telegram Bot

- **Авторизация** через `request_contact=True` (по номеру телефона).
- **Авторизация в callback'ах** (после фикса 2026-04): каждый callback с `family_id` обязан вызвать `_check_family_access()` → `can_manage_family()`. Парсинг callback_data — через безопасный `_parse_int_args()`.
- **Многоязычность:** все user-facing строки через `t(key, lang)`. Тест `test_locales_sync.py` гарантирует синхронность ключей и плейсхолдеров.
- **Rate limit:** in-memory dict под `threading.Lock`, GC неактивных раз в 600с. 5 req / 10s.
- **User panel cache:** TTL 30s под локом. Инвалидируется через `_invalidate_panel_cache()` после изменений.

### 2. Monitor Engine

- **Polling** каждые 300с (config: `POLLING_INTERVAL`).
- **Параллельный fetch** через `ThreadPoolExecutor(8 workers)` — одна сломанная таблица не блокирует остальные.
- **Изоляция ошибок:** consecutive failures по ученику отслеживаются; после 5 подряд — алерт админу (с cooldown 24ч).
- **Snapshot-сравнение:** хеширование `(student_id, cell_reference)`, детект нового vs изменённого.
- **Quota:** Google Sheets 300 read/min — запас при 100 учениках × 1 read/5min.

### 3. Schedulers

Один loop с `time.sleep(180)`. Каждая задача:
- Под отдельным `threading.Lock` (защита от перекрытия).
- С маркером в `settings` таблице (`scheduler_last_<job>`) — переживает рестарт.
- Маркер кэшируется в памяти (lazy load).

| Job | Время | Что делает |
|-----|-------|-----------|
| `morning` | 07:00 | Flush очереди тихих часов, утренняя сводка |
| `subscription` | 10:00 | Предупреждения об истечении подписки (7d, 1d, 0d) |
| `quarter` | 12:00, 18:00 | Проверка четвертных оценок |
| `alive` | 15:00 | "Бот жив" для пользователей с 48h+ тишины |
| `evening` | 19:00 | Вечерняя сводка с трендами |
| `cleanup` | вс 03:00 | Archive grades >180d, cleanup invites/queue |

### 4. Subscription / Payments

- **Telegram Payments API** через провайдеров Click/Payme.
- **Авторизация:** `_check_user_can_pay_for_family` — пользователь должен быть admin или членом семьи (фикс 2026-04).
- **`invoice_payload`** server-controlled: `f"{family_id}:{plan_key}:{months}"` — не подделывается.
- **`successful_payment`** — сначала `record_payment` + `extend_subscription`, затем сообщение пользователю.
- **Промокоды** — `expires_days` принудительно через `int()` + `?`-плейсхолдер (защита от SQL-injection).
- **Ручной перевод** на карту — admin подтверждает в Telegram, затем `extend_subscription`.

### 5. WebApp (Mini App)

Отдельный Flask-контейнер.

- **Авторизация:** HMAC-проверка `initData` Telegram + проверка членства родителя в семье ученика + активная подписка (для не-админов).
- **Endpoints:** `/api/students`, `/api/grades/<id>`, `/api/quarters/<id>`, `/health`.
- **Графики:** Chart.js (frontend).
- **Healthcheck:** `urllib.request → http://127.0.0.1:8443/health`.

### 6. Error reporting

`src/error_reporter.py` — единая точка для всех catch-блоков:
```python
from src.error_reporter import report
try: ...
except Exception as e:
    report("monitor.fetch_sheet", e, student_id=42)
```
- Всегда логирует с `exc_info=True`.
- Если `SENTRY_DSN` задан в env — отправляет в Sentry с тегами/extra-контекстом.
- Без `SENTRY_DSN` — no-op обёртка над `logger.exception`.

---

## Развёртывание

### Docker

```yaml
services:
  bot:           # Dockerfile, healthcheck по data/.heartbeat (mtime <180s)
    restart: always
  webapp:        # webapp/Dockerfile, healthcheck по /health
    profiles: [webapp]   # опциональный
```

Оба контейнера запускаются под non-root `bot` user. Шарят volume `sentinel_data:/app/data`.

### Healthcheck (главный бот)

Main thread пишет `data/.heartbeat` каждые 30с (`HEARTBEAT_INTERVAL`). Docker раз в 60с проверяет mtime — если файл «протух» >180с, считает контейнер unhealthy и `restart: always` его перезапустит.

### CI

`.github/workflows/tests.yml` — pytest на каждый push/PR.
`.github/workflows/deploy.yml` — авто-деплой на Pi при push в `main`.

---

## Что важно знать перед изменениями

1. **Не трогать схему `families`/`parents`/`family_links`** без миграции.
2. **Не вызывать Google API** в hot-path обработчика. `/grades` читает из `grade_history`.
3. **Не отправлять >30 сообщений** в цикле без `send_with_retry` или sleep — Telegram банит.
4. **Все callback'и с family_id** ОБЯЗАНЫ вызвать `_check_family_access()`.
5. **Все DB-запросы** — параметризованные (`?`), никаких f-strings c user input.
6. **Все user-facing строки** — через `t()`. Хардкоженный русский = bug.
7. **Логи без полных PII** — телефоны через `mask_phone()`.

Подробности — `CLAUDE.md`.
