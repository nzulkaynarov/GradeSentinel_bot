# CLAUDE.md

Контекст проекта для Claude Code. Этот файл автоматически загружается в каждую сессию.

---

## Что это

**GradeSentinel** — Telegram-бот мониторинга школьной успеваемости (Узбекистан).
- Отслеживает оценки в Google Таблицах (электронные дневники), уведомляет родителей.
- Multi-role family system (admin / head / senior parent), инвайт-ссылки.
- Telegram Payments (Click / Payme), 3 тарифа подписки.
- AI-аналитика через Claude API (Anthropic).
- 3 языка: ru / uz / en.
- Mini App дашборд (Flask + Chart.js).

**В продакшене.** Реальные пользователи. Любые изменения требуют осторожности.

---

## Стек

- Python 3.10 (slim)
- pyTelegramBotAPI (sync polling, не aiogram)
- SQLite + WAL (файл `data/sentinel.db`)
- Google Sheets API v4 (Service Account, `config/credentials.json`)
- Telegram Payments API
- Anthropic SDK (`anthropic`)
- Flask (для WebApp)
- Docker + docker-compose
- Хостинг: Raspberry Pi 3B

---

## Структура

```
src/
├── main.py              # Точка входа: /start, /help, авторизация, user panel,
│                        #   rate limit (thread-safe + GC), heartbeat thread
├── bot_instance.py      # Singleton telebot
├── database_manager.py  # ВСЁ SQL: схема, миграции, CRUD, авторизация (1500+ строк — нужно дробить)
├── google_sheets.py     # Кэшированный сервис, get_sheet_data, get_spreadsheet_title
├── monitor_engine.py    # Polling-цикл (каждые 300с), детект новых/изменённых оценок,
│                        #   ThreadPoolExecutor(8) для параллельного fetch'а
├── data_cleaner.py      # sanitize_grade — нормализация оценок
├── analytics_engine.py  # Claude API анализ
├── schedulers.py        # Daily jobs: вечерняя сводка (19:00), тихие часы (07:00),
│                        #   четверти (12/18:00), подписка (10:00), bot_alive (15:00),
│                        #   weekly cleanup (вс 03:00). Маркеры в БД (переживают рестарт)
├── notification_helpers.py  # Форматирование сообщений, тихие часы, эмоции
├── telegram_utils.py    # send_with_retry — корректная обработка 429 RetryAfter
├── history_importer.py  # Импорт листов "Все оценки" / "Четверти" при добавлении ученика
├── i18n.py              # t(key, lang, **kwargs)
├── ui.py                # send_menu_safe, send_content
├── utils.py             # clean_student_name, mask_phone (PII в логах)
├── locales/             # ru.json, uz.json, en.json (синхронны — проверяется тестом)
└── handlers/
    ├── admin.py         # admin panel, /add_family, /list_families, /status
    ├── family.py        # /manage_family, _check_family_access, /grades
    ├── communication.py # Поддержка → admin group, broadcast (с retry)
    ├── analytics.py     # /ai_report
    ├── settings.py      # Смена языка
    ├── subscription.py  # /subscription, оплата, /grant_sub, промокоды (1222 строки)
    └── invite.py        # Инвайт-ссылки

webapp/
├── app.py               # Flask: /webapp, /api/students, /api/grades, /api/quarters,
│                        #   /health (для healthcheck). Авторизация через _authorize_student_access
└── templates/, static/

tests/                   # pytest, запускается в CI (.github/workflows/tests.yml)
data/sentinel.db         # БД (volume в Docker)
data/.heartbeat          # Файл для Docker healthcheck (touch'ит main thread каждые 30с)
config/credentials.json  # Google Service Account (volume, НЕ в репо)
.env                     # Токены (НЕ в репо)
.claude/                 # Claude Code settings (settings.local.json в .gitignore)
```

---

## Ключевые таблицы БД

| Таблица | Что хранит |
|---------|-----------|
| `parents` | fio, phone (unique), telegram_id (unique), role (admin/senior), lang, notify_mode |
| `students` | fio, spreadsheet_id, display_name |
| `families` | family_name, head_id (FK→parents), subscription_end |
| `family_links` | M2M family↔parent, family↔student |
| `grade_history` | Кэш оценок: subject, raw_text, grade_value, cell_reference, date_added |
| `grade_history_archive` | Архив старых оценок (>180 дней). Чистит weekly job |
| `quarter_grades` | Четвертные оценки |
| `notification_queue` | Очередь тихих часов (22:00-07:00 Ташкент) |
| `family_invites` | Одноразовые инвайт-ссылки (48h expiry) |
| `payments` | Charge IDs, amount, plan |
| `user_states` | Временные состояния (pending_lang, pending_invite, broadcast и т.д.) |
| `promo_codes` | Промокоды |
| `support_msg_map` | message_id ↔ user_id для admin reply |
| `settings` | key-value (плюс хранит plans JSON и `scheduler_last_*` маркеры) |

`PRAGMA foreign_keys=ON` включён в `get_db_connection()`. Удаление семьи через
`delete_family_cascade` — атомарно чистит payments/invites/links/осиротевших students.

---

## Конвенции и подводные камни

### Архитектурные

1. **`get_db_connection()` — context manager в `database_manager.py:11`**.
   Коммитит на успешном выходе, делает rollback при exception. `PRAGMA foreign_keys=ON`.

2. **`pyTelegramBotAPI` синхронный**, polling-режим. Один main thread + scheduler thread (демон). Никаких корутин.

3. **`register_next_step_handler` — in-memory**. При рестарте бота пользователи теряют состояние посредине многошаговых flow. Долгосрочно — переезд на `user_states` в БД.

4. **`bot.polling(none_stop=True)`** в main.py:end. Webhook не используется. Рядом запускается `_heartbeat_loop` — раз в 30с touch'ит `data/.heartbeat`, Docker healthcheck смотрит mtime.

5. **Rate limit — in-memory dict** под локом (`_rate_limit_store` + `_rate_limit_lock` в main.py). 5 req / 10 sec. GC неактивных пользователей раз в 600с. Сбрасывается при рестарте.

6. **`_panel_cache`** под локом (main.py). TTL 30s. После любых изменений семьи/подписки вызывать `_invalidate_panel_cache(chat_id)`.

7. **Schedulers** — один loop с `time.sleep(180)` и проверкой `now.hour == X and now.minute < 6`. Перекрытия защищены per-job локами + маркерами в БД (`scheduler_last_*` в settings, переживают рестарт). Маркеры кэшируются в памяти, БД-read только один раз после старта.

### Безопасность

8. **Все DB-запросы используют параметризацию `?`**. `create_promo_code` принудительно конвертирует expires_days через `int()` — SQL-injection безопасен.

9. **WebApp** валидирует initData HMAC + проверяет принадлежность ученика родителю + активную подписку через `_authorize_student_access` (webapp/app.py). Админ обходит проверку подписки.

10. **Все callback handlers с `family_id`** обязаны вызывать `_check_family_access(call, family_id)` из handlers/family.py — он использует `can_manage_family()` (admin OR head_of_family). `_parse_int_args(call.data, prefix, count)` — безопасный парсер callback_data.

11. **Авторизационные хелперы в database_manager.py:**
    - `is_user_admin(user_id)` = `get_parent_role(user_id) == 'admin'`
    - `is_head_of_family(tg, fid)` — глава именно этой семьи
    - `is_member_of_family(tg, fid)` — состоит в семье
    - `can_manage_family(tg, fid)` — admin OR head (для деструктивных действий)

12. **Логи не содержат полные телефоны** — используем `mask_phone()` из utils.py.

### Telegram Payments

13. **`invoice_payload` сервер-контролируемый** — пользователь не может его подменить, формат `f"{family_id}:{plan_key}:{months}"`. Доверять можно.

14. `pre_checkout_query` ВСЕГДА должен отвечать в течение 10 сек. Не делать тяжёлых запросов в `handle_pre_checkout`.

15. **`successful_payment` не должен зависеть от ответа пользователю** — деньги уже списаны. Сначала `record_payment`, `extend_subscription`, потом сообщение.

### Уведомления

16. **Тихие часы 22:00–07:00 Ташкент** (UTC+5). `is_quiet_hours()` в notification_helpers.py. Сообщения копятся в `notification_queue`, утренний flush через scheduler.

17. **Broadcast** в отдельном `threading.Thread` (communication.py). Использует `send_with_retry` из telegram_utils — корректно обрабатывает 429 RetryAfter и 5xx. Базовая пауза 0.04с между сообщениями (~25/sec).

18. **Notification format** — всегда HTML (`parse_mode='HTML'`). НЕ Markdown — экранирование разное.

### i18n

19. Все user-facing строки через `t("key", lang, **kwargs)`. Захардкоженный русский в коде = bug.

20. Локали `ru.json` / `uz.json` / `en.json` синхронны — проверяется `tests/test_locales_sync.py` (ключи + плейсхолдеры). Если добавляешь ключ — добавляй во все три и проверь что плейсхолдеры одинаковые.

21. Заголовки для админ-группы (support cards) — всегда на русском (admin удобство).

### Google Sheets

22. **Сервис кэширован** (singleton в google_sheets.py:19). Не пересоздавать.

23. Quotas: 300 read/min/user. Polling 100 учеников × 1 read = 20/min — запас есть. Параллельный fetch через `ThreadPoolExecutor(_FETCH_WORKERS=8)` в monitor_engine.

24. **Один сломанный sheet больше НЕ блокирует цикл** — fetch параллельный, exception ловится. Счётчик `_student_failure_counts` отслеживает consecutive failures, после 5 подряд — алерт админу (cooldown 24ч).

25. **429 от Sheets API логируется с тегом `[GOOGLE_QUOTA]`** — для отдельного грепа в логах при подозрении на превышение квот.

26. **Лист "Сегодня" — A1:B50**, "Четверти" — A1:G50.

### Мониторинг и полнота данных

27. **`_polling_lock`** в monitor_engine — если предыдущий цикл не завершился за 300с, новый не стартует (skip).

28. **`archive_old_grades(days=180)`** — атомарен через `BEGIN IMMEDIATE` + select-by-id. Не теряет записи при параллельных INSERT'ах. Запускается раз в неделю (вс 03:00).

29. **Heartbeat-файл** `data/.heartbeat` — пишется main thread'ом каждые 30с. Healthcheck в Docker проверяет mtime (>180с = рестарт). Не дёргает Bot API.

---

## Как запускать

```bash
# Локально (Mac, develop)
cp .env.example .env  # заполнить токены
docker-compose up -d --build
docker-compose logs -f

# Тесты (pytest)
pytest tests/ -v

# Прод (Raspberry Pi, ветка main, авто-деплой через GitHub Actions)
git push origin main
```

### Переменные окружения (.env)

| Var | Обязательно | Описание |
|-----|:-:|----------|
| BOT_TOKEN | ✅ | От @BotFather |
| ADMIN_ID | ✅ | Личный TG ID супер-админа |
| ADMIN_GROUP_ID | ✅ | Группа для поддержки (с минусом, -100…) |
| CLICK_PROVIDER_TOKEN, PAYME_PROVIDER_TOKEN | — | Для платежей |
| ANTHROPIC_API_KEY | — | Для AI-аналитики |
| WEBAPP_URL | — | HTTPS для Mini App |

---

## Style guide

- **Python 3.10**, никаких новых features из 3.11+ (бот собирается на slim:3.10).
- **Type hints желательны** в новых функциях. Старые — постепенно.
- **Логгер** — `logging.getLogger(__name__)`, никаких `print`.
- **Не показывать сырые exception в UI** — `f"Ошибка: {e}"` это bug. Логи — да, пользователю — обобщённое сообщение через `t("...")`.
- **Не использовать `except: pass`** — минимум `logger.debug(f"...: {e}")`.
- **Импорты внутри функций** допустимы только для разрыва циклических зависимостей (handlers ↔ database_manager). Везде где можно — на верх файла.
- **Захардкоженные числа** (цены, интервалы, лимиты) выносить в константы или `settings`.

---

## Чего НЕ делать

- **Не амендить published commits** на main (auto-deploy на Pi).
- **Не пушить .env, credentials.json, data/*.db, *.xlsx** (в .gitignore, но проверяй).
- **Не использовать `--no-verify`** на коммитах.
- **Не менять схему `families` / `parents` / `family_links`** без миграции (есть пользователи в проде).
- **Не вызывать Google API в hot path обработчика** (только в monitor_engine и при добавлении ребёнка). `/grades` читает из `grade_history`.
- **Не вызывать `bot.send_message` синхронно в цикле >30 пользователей** без `send_with_retry` — Telegram забанит за flood.
- **Не парсить callback_data вручную через split** — используй `_parse_int_args(call.data, prefix, count)`. И обязательно `_check_family_access(call, fid)` перед action'ом с `family_id`.

---

## Открытые задачи / технический долг

- **`python:3.10-slim` имеет 2 high CVE** (предупреждение IDE). Оценить апгрейд до 3.12 — ломает «никаких 3.11+ features» инвариант, нужно общее решение.
- **`database_manager.py` 1500+ строк** — нужно дробить по доменам (auth, payments, families, grades).
- **`handlers/subscription.py` 1222 строки** — то же.
- **`register_next_step_handler` теряется при рестарте** — миграция многошаговых flow на `user_states` в БД.
- **`_rate_limit_store` / `_panel_cache` теряются при рестарте** — допустимо, но если пойдём в multi-instance, нужен Redis.

---

## Полезные ссылки

- pyTelegramBotAPI: https://github.com/eternnoir/pyTelegramBotAPI
- Telegram Payments: https://core.telegram.org/bots/payments
- Google Sheets API quota: https://developers.google.com/sheets/api/limits
- Anthropic SDK: https://github.com/anthropics/anthropic-sdk-python
