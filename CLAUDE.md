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

- Python 3.12 (на проде, в CI пока 3.10 — но 3.12-совместим)
- pyTelegramBotAPI (sync polling, не aiogram)
- SQLite + WAL (`/var/lib/gradesentinel/sentinel.db` на проде, `data/sentinel.db` локально)
- Google Sheets API v4 (Service Account, `/etc/gradesentinel/credentials.json` на проде)
- Telegram Payments API
- Anthropic SDK (`anthropic`)
- Flask + **gunicorn** (2 worker × 4 thread, gthread) для WebApp, слушает `127.0.0.1:8443`, наружу через Caddy
- Chart.js v4.4.0 bundled локально (`webapp/static/vendor/`) — без CDN
- **Bare-metal deploy:** systemd units + venv (никакого Docker)
- **Reverse proxy:** Caddy (auto Let's Encrypt) — `grades.railtech.uz`
- Хостинг: VPS Ubuntu 24.04 (4 GB RAM, 2 vCPU, 80 GB NVMe)

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
├── app.py               # Flask + gunicorn-friendly. Endpoints:
│                        #   /webapp                    — HTML дашборд
│                        #   /api/dashboard/init        — bootstrap (студенты + lang + first_name)
│                        #   /api/dashboard/<id>?days=N — главный: summary, trend_by_day, by_subject, recent
│                        #   /api/quarters/<id>         — четверти (lazy)
│                        #   /api/students, /api/grades — legacy, оставлены для обратной совместимости
│                        #   /health                    — для Caddy/мониторинга
│                        #   Pure functions: compute_summary, compute_trend_by_day, compute_by_subject
│                        #   (unit-tested в tests/test_webapp_dashboard.py)
│                        #   Авторизация: HMAC-SHA256(BOT_TOKEN) валидирует Telegram initData,
│                        #   `signature` поле включается в data_check_string, значения — URL-decoded
├── templates/dashboard.html   # data-i18n атрибуты, skeleton loading, hero/cards layout
└── static/
    ├── app.js          # i18n runtime + single-call flow + last_seen для подсветки нового
    ├── style.css       # Tg theme variables, light/dark, skeleton shimmer, smooth animations
    ├── vendor/chart.umd.min.js  # Chart.js 4.4.0 bundled (нет CDN)
    └── locales/{ru,uz,en}.json  # 46 ключей синхронных (тест проверяет)

deploy/                  # bare-metal деплой (см. deploy/README.md)
├── install.sh                       # one-shot bootstrap VPS
├── gradesentinel-bot.service        # systemd-юнит бота
├── gradesentinel-webapp.service     # systemd-юнит webapp
├── gradesentinel-heartbeat.{service,timer}  # watchdog рестартит бот при зависании
├── Caddyfile                        # reverse proxy grades.railtech.uz → :8443
└── deploy-sudoers                   # узкий passwordless sudo для GH runner юзера

tests/                   # pytest, запускается в CI (.github/workflows/tests.yml)
data/sentinel.db         # БД локально для разработки (gitignored)
data/.heartbeat          # Heartbeat файл (timer-watchdog проверяет mtime)
config/credentials.json  # Google Service Account ЛОКАЛЬНО (НЕ в репо). На проде — /etc/gradesentinel/
.env                     # Токены ЛОКАЛЬНО (НЕ в репо). На проде — /etc/gradesentinel/bot.env
.claude/                 # Claude Code settings (settings.local.json в .gitignore)
```

**На проде (bare-metal VPS):**
- `/opt/gradesentinel/` — код (sync на каждом деплое через rsync)
- `/opt/gradesentinel/venv/` — Python venv
- `/var/lib/gradesentinel/` — `sentinel.db`, `.heartbeat` (read/write для service user)
- `/etc/gradesentinel/bot.env` — секреты (`0640 root:gradesentinel`)
- `/etc/gradesentinel/credentials.json` — Google service account
- `/etc/systemd/system/gradesentinel-*.{service,timer}` — юниты
- `/etc/caddy/Caddyfile` — reverse proxy конфиг

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

4. **`bot.polling(none_stop=True)`** в main.py:end. Webhook не используется. Рядом запускается `_heartbeat_loop` — раз в 30с touch'ит heartbeat-файл (`data/.heartbeat` локально, `/var/lib/gradesentinel/.heartbeat` на проде через ENV `HEARTBEAT_PATH`). На проде systemd-таймер `gradesentinel-heartbeat.timer` раз в минуту проверяет mtime файла и `systemctl restart gradesentinel-bot` если протух >180с.

5. **Rate limit — in-memory dict** под локом (`_rate_limit_store` + `_rate_limit_lock` в main.py). 5 req / 10 sec. GC неактивных пользователей раз в 600с. Сбрасывается при рестарте.

6. **`_panel_cache`** под локом (main.py). TTL 30s. После любых изменений семьи/подписки вызывать `_invalidate_panel_cache(chat_id)`.

7. **Schedulers** — один loop с `time.sleep(180)` и проверкой `now.hour == X and now.minute < 6`. Перекрытия защищены per-job локами + маркерами в БД (`scheduler_last_*` в settings, переживают рестарт). Маркеры кэшируются в памяти, БД-read только один раз после старта.

### Безопасность

8. **Все DB-запросы используют параметризацию `?`**. `create_promo_code` принудительно конвертирует expires_days через `int()` — SQL-injection безопасен.

9. **WebApp** валидирует initData HMAC через `validate_init_data` + проверяет принадлежность ученика родителю + активную подписку через `_authorize_student_access` (webapp/app.py). Админ обходит проверку подписки.

   **Тонкости HMAC** (важно для дашборда):
   - data_check_string использует **URL-decoded** значения (через `parse_qs`), не raw URL-encoded
   - Поле `signature` (Ed25519 third-party валидация Telegram WebApp 7.x+) **включается** в data_check_string
   - Только `hash` исключается из compute
   - При нарушении любого из этих правил — все запросы получают 401 «Invalid hash»

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

26. **Лист "Все оценки" — A1:ZZ50 (~265 колонок)**, "Четверти" — A1:G50. Monitor читает «Все оценки!» и извлекает колонку для сегодняшней даты через `_parse_master_sheet_for_date` (history_importer.py). Лист «Сегодня!» больше НЕ используется monitor'ом после этапа 4 RFC MONOSOURCE_GRADES (21.05.2026).

26a. **`cell_reference` — это metadata, не identity-ключ.** После этапа 1C RFC UNIQUE constraint на `(student, subject, grade_date, raw_text)`. Monitor и history_importer используют разные форматы `cell_reference` («Все оценки!{date}:{subject}» vs «Все оценки!{col}{row}»). Identity-операции в monitor'е идут через content-based функции: `get_existing_grade_by_content(student, subject, date)`, `update_grade_by_content(student, subject, date, ...)`, `_pending_grades` по ключу `(student, subject, grade_date)`. До PR #43 monitor использовал cell_reference как identity → race condition с importer'ом (инцидент 21.05.2026).

26b. **Групповые уведомления уважают тихие часы и кладутся в очередь** — `_send_to_groups_for_student` в `is_quiet_hours()` пишет в `group_notification_queue` (отдельная таблица от `notification_queue`). Утренний flush в 07:00 (`_flush_quiet_hours_queue` в schedulers.py) сливает накопленное вместе с личной morning-сводкой. inline_markup НЕ сохраняется — после ночи callback'ы могут устареть.

### Мониторинг и полнота данных

27. **`_polling_lock`** в monitor_engine — если предыдущий цикл не завершился за 300с, новый не стартует (skip).

28. **`archive_old_grades(days=180)`** — атомарен через `BEGIN IMMEDIATE` + select-by-id. Не теряет записи при параллельных INSERT'ах. Запускается раз в неделю (вс 03:00).

29. **Heartbeat-файл** `.heartbeat` — пишется main thread'ом каждые 30с. На проде `gradesentinel-heartbeat.timer` (systemd timer) раз в минуту проверяет mtime; если >180с — `systemctl restart gradesentinel-bot`. Не дёргает Bot API.

### WebApp дашборд

30. **Один endpoint `/api/dashboard/<id>?days=N`** отдаёт всё что нужно дашборду (summary, trend_by_day, by_subject, recent_grades, user info) — никаких 3 sequential calls.

31. **Pure-функции агрегации** в `webapp/app.py`: `compute_summary`, `compute_trend_by_day`, `compute_by_subject`. Они не трогают БД, легко тестируются. Все в `tests/test_webapp_dashboard.py` (19 тестов покрывают edge-cases).

32. **i18n дашборда** — отдельные локали `webapp/static/locales/{ru,uz,en}.json` (46 ключей, синхронны через `tests/test_webapp_locales_sync.py`). НЕ дублируют `src/locales/` — у бота и webapp разные UI-ключи.

33. **Lang определяется backend'ом**: `parents.lang` (БД) → дефолт `ru`. Возвращается в `/api/dashboard/init.user.lang`. Фронт подгружает `/static/locales/<lang>.json` после bootstrap.

34. **Chart.js — bundled локально** (`webapp/static/vendor/chart.umd.min.js`, ~200KB). Никаких CDN. CSP в Caddyfile разрешает только `'self'` для script-src.

35. **WebApp host = `127.0.0.1`** (default из ENV `WEBAPP_HOST`). Наружу выпускает Caddy через 443. Не менять на `0.0.0.0` — обходит TLS и логирование.

36. **gunicorn** в проде, не Werkzeug dev server. ExecStart в `gradesentinel-webapp.service`: `gunicorn --workers 2 --threads 4 --worker-class gthread webapp.app:app`. `init_db()` вызывается на module-level в `webapp/app.py` — gunicorn-friendly.

---

## Как запускать

```bash
# Локально (Mac, develop) — venv, без Docker
python3.12 -m venv venv             # или 3.10/3.11 — совместим
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                # заполнить токены
mkdir -p data config
# положить credentials.json в config/
python -m src.main                  # бот в polling режиме
# в другом терминале:
python webapp/app.py                # WebApp на 127.0.0.1:8443

# Тесты (pytest)
BOT_TOKEN=test-token ADMIN_ID=0 ADMIN_GROUP_ID=0 pytest tests/ -v

# Прод (VPS Ubuntu 24.04, ветка main, авто-деплой через GitHub Actions)
git push origin main
# → workflow .github/workflows/deploy.yml: rsync кода → pip install → systemctl restart
# первая настройка VPS — см. deploy/README.md
```

### Bare-metal на проде

Бот и webapp — два systemd-юнита, читают секреты из `/etc/gradesentinel/bot.env`,
БД хранят в `/var/lib/gradesentinel/`. Reverse proxy — Caddy с авто-TLS на `grades.railtech.uz`.
Watchdog: systemd timer раз в минуту проверяет `mtime` heartbeat-файла и рестартит бот при зависании.

```bash
# На VPS — основные команды:
sudo systemctl status gradesentinel-bot gradesentinel-webapp
sudo journalctl -u gradesentinel-bot -f
sudo -u gradesentinel sqlite3 /var/lib/gradesentinel/sentinel.db ".backup /tmp/backup.db"
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

- **Python 3.12** на проде, **3.10+** совместимо в коде (CI пока на 3.10).
- **Type hints желательны** в новых функциях. Старые — постепенно.
- **Логгер** — `logging.getLogger(__name__)`, никаких `print`.
- **Не показывать сырые exception в UI** — `f"Ошибка: {e}"` это bug. Логи — да, пользователю — обобщённое сообщение через `t("...")`.
- **Не использовать `except: pass`** — минимум `logger.debug(f"...: {e}")`.
- **Импорты внутри функций** допустимы только для разрыва циклических зависимостей (handlers ↔ database_manager). Везде где можно — на верх файла.
- **Захардкоженные числа** (цены, интервалы, лимиты) выносить в константы или `settings`.

---

## Чего НЕ делать

- **Не амендить published commits** на main (auto-деплой на VPS).
- **Не пушить .env, credentials.json, data/*.db, *.xlsx** (в .gitignore, но проверяй).
- **Не использовать `--no-verify`** на коммитах.
- **Не менять схему `families` / `parents` / `family_links`** без миграции (есть пользователи в проде).
- **Не вызывать Google API в hot path обработчика** (только в monitor_engine и при добавлении ребёнка). `/grades` читает из `grade_history`.
- **Не вызывать `bot.send_message` синхронно в цикле >30 пользователей** без `send_with_retry` — Telegram забанит за flood.
- **Не парсить callback_data вручную через split** — используй `_parse_int_args(call.data, prefix, count)`. И обязательно `_check_family_access(call, fid)` перед action'ом с `family_id`.
- **Не править файлы прямо на VPS** — следующий деплой через rsync затрёт. Все правки — через PR.
- **Не менять `WEBAPP_HOST` на `0.0.0.0`** — webapp должен слушать только loopback, наружу выпускает Caddy. Иначе обходится TLS и логирование.

---

## Открытые задачи / технический долг

**Архитектурные:**
- **`handlers/subscription.py` 1318 строк** — отложено сознательно. Платёжные сервисы (CLICK/PAYME) не подключены (владелец выдаёт подписки вручную через `/grant_sub`). Split безопаснее делать когда платежи активны и линии разделения яснее. State-machine flow уже мигрирован на user_states.

**Этапы RFC grade_date — текущий статус:**
- ~~Этап 4 (`MONOSOURCE_GRADES`)~~ — ✅ closed 21.05.2026 PR #47, см. ниже.
- **Этап 5** — удалить `_pending_grades` (двухфазное подтверждение). Только после недели стабильной работы этапа 4 — раньше 28.05.2026 не трогать. «Все оценки!» более стабильный лист (учитель редактирует через свою систему, не вручную), pending-механика возможно избыточна. Решать на основе production-наблюдений: если не было [PENDING] событий за неделю → можно удалять.

**Эксплуатация прод-системы:**
- **SSH-хардненинг VPS** — сейчас root login и password auth открыты (для recovery). После недели стабильной работы — отключить root + только key-auth. ВРУЧНУЮ, не автоматически, чтобы не залочиться.
- **Бэкапы за пределы VPS** — сейчас snapshot в `/var/backups/gradesentinel/` (ротация 7 дней). При гибели VPS — потеряем. Хочется S3/Backblaze sync ежедневно.
- **`_rate_limit_store` / `_panel_cache`** в памяти — допустимо для single-instance. При переходе в multi-instance нужен Redis (далеко в будущем).

**Косметика / следующие фичи:**
- **`/api/dashboard` ETag** — для повторных открытий дашборда отдавать 304 при неизменённых данных. Сейчас всегда 200.

**Закрыто (история):**
- ✅ **Этап 4 RFC MONOSOURCE_GRADES** (PR #47). 21.05.2026 in prod. Monitor переключён с «Сегодня!A1:B50» на «Все оценки!A1:ZZ50» → парсит колонку сегодняшней даты через `_parse_master_sheet_for_date`. Закрывает архитектурный source двух writer'ов с разными форматами cell_reference. Hourly `history_importer` оставлен как backup для листов «Неделя!» и «Четверти!». `_shadow_compare_with_master` удалён.
- ✅ Очередь для групповых уведомлений (PR #44). 21.05.2026 in prod. Таблица `group_notification_queue` + flush в 07:00 параллельно с личной morning-сводкой. До этого в тихие часы группы молча дропались (defensive после PR #42).
- ✅ Content-based identity в monitor (PR #43). 21.05.2026 in prod. Заменили `get_existing_grade(cell_ref)` на `get_existing_grade_by_content(student, subject, date)`. `_pending_grades` ключ теперь content-based. Закрыл root cause инцидента 21.05.
- ✅ Cross-domain cell_reference дедуп + тихие часы для групп (PR #42). 21.05.2026 in prod. Инцидент: 14 ночных уведомлений в семейный чат из-за race condition. Defensive fix: `grade_exists_by_content()` content-key fallback + `is_quiet_hours()` гейт.
- ✅ telegram_first_name в `parents` + использование в приветствиях/webapp. 19.05.2026 in prod (PR между #41 и #42).
- ✅ Этап 1A–1C RFC (grade_date NOT NULL + UNIQUE по содержимому). 14.05.2026 in prod.
- ✅ Multi-grade «2/5» в sanitize_cell + двухфазное pending подтверждение. 13.05.2026 in prod.
- ✅ CI Python 3.10 → 3.12. 14.05.2026.
- ✅ Бэкап БД systemd timer'ом (ежедневно 03:30 TST). 14.05.2026.
- ✅ Network log noise (retry на debug, WARN только на 2+). 14.05.2026.
- ✅ systemd `StartLimitIntervalSec` warning при каждом старте. 14.05.2026.
- ✅ `database_manager.py` split: 1789 → 655 строк (–63%), 12 модулей в `src/db/`. 14.05.2026.
- ✅ `register_next_step_handler` → `user_states` (все 11 callsite'ов через state_flows.py). 14.05.2026.
- ✅ `datetime.utcnow()` → timezone-aware (Python 3.12 deprecation, 16 callsite'ов). 14.05.2026.
- ✅ AI-инсайт в дашборде (`compute_dashboard_insight` в analytics_engine + кэш в webapp).
- ✅ WebApp кнопка прямо в user panel (когда `has_kids`, не только после `/grades`).

---

## Полезные ссылки

- pyTelegramBotAPI: https://github.com/eternnoir/pyTelegramBotAPI
- Telegram Payments: https://core.telegram.org/bots/payments
- Google Sheets API quota: https://developers.google.com/sheets/api/limits
- Anthropic SDK: https://github.com/anthropics/anthropic-sdk-python
