# GradeSentinel
**v2.5** | PostgreSQL 17 | Telegram Payments

**GradeSentinel** — система мониторинга школьной успеваемости. Бот отслеживает изменения в Google Таблицах (электронных дневниках) и мгновенно уведомляет родителей в Telegram о новых оценках.

---

## Основные возможности

### Мульти-ролевая семейная система
* **Гибкие связи:** Один родитель может состоять в нескольких семьях. У одной семьи — до 5 детей.
* **Иерархия доступа:**
  * **Супер-Администратор** — полный контроль, создание семей, рассылки, `/grant_sub`
  * **Глава семьи (Head)** — управление семьёй, добавление детей и родственников, инвайт-ссылки
  * **Родственник (Senior/Parent)** — получение уведомлений, просмотр оценок
* **Инвайт-ссылки:** Глава семьи генерирует одноразовую ссылку (`t.me/bot?start=inv_CODE`) — родственник переходит и автоматически привязывается к семье.

### Мониторинг и оповещения
* **Snapshot Engine** — бот делает "снимки" дневников, находит дельту (новая оценка, изменение), уведомляет.
* **Кэширование** — `/grades` показывает оценки из локальной БД (`grade_history`), не тратя квоту Google API.
* **Обнаружение изменений** — если учитель исправил оценку, бот уведомит: "Было 3 → Стало 4".
* **Интервал опроса** — каждые 5 минут, Exponential Backoff при ошибках.
* **Вечерняя сводка** — ежедневный дайджест оценок в 19:00.
* **Тихие часы** — уведомления 22:00–07:00 копятся и доставляются утром.

### AI-аналитика (Claude API)
* **AI-анализ по запросу** — развёрнутый отчёт за 14 дней: сильные/слабые предметы, рекомендации.
* **Еженедельный AI-отчёт** — автоматическая рассылка по воскресеньям в 19:00.
* Доступ к AI-анализу требует активной подписки.

### Подписка и оплата
* **Telegram Payments API** с провайдерами **Click / Payme** (Узбекистан).
* **3 тарифа:** 1 месяц (29 900), 3 месяца (79 900), 12 месяцев (249 900 UZS).
* Подписка привязана к семье. Без подписки — мониторинг и AI отключаются.
* Админ может выдать подписку вручную: `/grant_sub <family_id> <months>`.

### Мультиязычность
* Поддержка 3 языков: Русский, O'zbek, English.
* Выбор языка при первом `/start` и через кнопку "Язык" в меню.

### Обратная связь
* **Поддержка:** Кнопка `💬 Поддержка` — сообщение пересылается в закрытую группу администраторов. Ответ через Reply доставляется пользователю.
* **Рассылка:** Супер-Админ отправляет новости всем через `📢 Рассылка`.

---

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Авторизация / главное меню |
| `/help` | Контекстная справка (адаптируется под роль) |
| `/grades` | Оценки за сегодня |
| `/status` | Статистика (пользовательская / глобальная для админа) |
| `/ai_report` | AI-анализ успеваемости |
| `/add_family` | Создать семью (админ) |
| `/list_families` | Список семей (админ) |
| `/grant_sub` | Выдать подписку (админ) |

---

## Быстрый старт (Deploy)

### Требования
* Docker и Docker Compose
* `credentials.json` (Google Service Account)
* Токен Telegram бота от `@BotFather`
* (Опционально) `PAYMENT_PROVIDER_TOKEN` для платежей
* (Опционально) `ANTHROPIC_API_KEY` для AI-аналитики

### Установка
```bash
cp .env.example .env
# Отредактируйте .env:
#   BOT_TOKEN, ADMIN_ID, ADMIN_GROUP_ID — обязательные
#   PAYMENT_PROVIDER_TOKEN — для Click/Payme
#   ANTHROPIC_API_KEY — для AI-анализа
#   WEBAPP_URL — для Mini App дашборда
```

Положите `credentials.json` в `config/`, затем:
```bash
docker-compose up -d --build
```

### Переменные окружения (.env)

| Переменная | Обязательная | Описание |
|-----------|:---:|----------|
| `BOT_TOKEN` | Да | Токен бота от @BotFather |
| `ADMIN_ID` | Да | Telegram ID администратора |
| `ADMIN_GROUP_ID` | Да | ID группы для обратной связи |
| `PAYMENT_PROVIDER_TOKEN` | Нет | Токен Click/Payme из @BotFather → Payments |
| `ANTHROPIC_API_KEY` | Нет | Ключ Claude API для AI-аналитики |
| `WEBAPP_URL` | Нет | URL для Mini App дашборда (HTTPS) |
| `WEBAPP_PORT` | Нет | Порт WebApp сервера (по умолчанию 8443) |

---

## Архитектура

### Структура проекта
```
src/
├── main.py                  # Точка входа, /start, /help, роутинг меню
├── bot_instance.py          # Singleton бота
├── database_manager.py      # Фасад БД (re-export из src/db/*, init_db → Alembic)
├── db/                      # Домены БД: auth, families, grades, payments, promo, groups, invites, …
├── config.py                # Все константы (из ENV с дефолтами)
├── google_sheets.py         # Google Sheets API (кэшированный сервис)
├── monitor_engine.py        # Polling-цикл «Все оценки!», stale-таблицы, outbox уведомлений
├── history_importer.py      # Импорт истории/четвертей, учебный год таблицы
├── data_cleaner.py          # Очистка "грязных" оценок
├── analytics_engine.py      # Claude AI анализ (+ пакет src/ai/: промпты, клиент, кэш)
├── ai_tools.py              # tool-use для AI-чата
├── schedulers.py            # Daily/weekly jobs (сводки, тихие часы, четверти, подписки, летний режим)
├── notifications/           # Единый Sender: тихие часы, notify_mode, retry, типы уведомлений
├── ui.py, i18n.py, utils.py # Меню, мультиязычность, утилиты
├── locales/                 # ru.json, uz.json, en.json (синхронны)
└── handlers/
    ├── admin.py             # /status, /add_family, /list_families
    ├── family.py            # Управление семьёй, /grades, смена ссылки на таблицу
    ├── panel.py             # User panel (меню родителя)
    ├── navigation.py        # Reply-keyboard, role-toggle admin↔parent
    ├── ai_chat.py           # AI-чат (conversation-first UX)
    ├── group.py             # Бот в семейных группах
    ├── state_flows.py       # Многошаговые flow через user_states
    ├── communication.py     # Поддержка, рассылка
    ├── analytics.py         # /ai_report, еженедельные отчёты
    ├── settings.py          # Смена языка
    ├── subscription/        # Пакет: подписка, платежи, промокоды, /grant_sub
    └── invite.py            # Инвайт-ссылки для семей

migrations/                  # Alembic (0001_baseline … 0004_student_academic_year)
webapp/                      # Mini App дашборд (Flask + Chart.js)
deploy/                      # systemd-юниты, Caddyfile, install.sh, бэкапы
Makefile                     # make help — тесты, миграции, read-only прод
```

### База данных (PostgreSQL 17)

> PostgreSQL 17 на отдельном DB-VPS (`10.0.0.2`, WireGuard, `sslmode=require`); драйвер `psycopg` v3 + пул, схема — Alembic (`migrations/`). Миграция с SQLite — 2026-06-29.

| Таблица | Назначение |
|---------|-----------|
| `parents` | Пользователи: fio, phone, telegram_id, role, lang |
| `students` | Ученики: fio, spreadsheet_id, display_name, academic_year |
| `families` | Семьи: family_name, head_id, subscription_end |
| `family_links` | M2M связи: family↔parent, family↔student |
| `grade_history` | История оценок: subject, raw_text, grade_value, cell_reference |
| `quarter_grades` | Четвертные оценки (с привязкой к учебному году) |
| `student_years` | Класс и ссылка на таблицу по учебным годам |
| `notification_queue` | Очередь тихих часов |
| `family_invites` | Инвайт-ссылки: invite_code, expires_at, is_used |
| `payments` | История платежей: amount, currency, plan, charge IDs |
| `user_states` | Временные состояния (выбор языка, инвайт) |

8 индексов на часто используемые столбцы (grade_history, family_links, parents, и др.).

*(Подробная архитектура: `Docs/Project_overview.md`)*

---

## Разработка

`make help` — все команды. Ключевые: `make test` (Docker + PostgreSQL 17 — единственный честный прогон),
`make check` (компиляция + JSON локалей без Docker), `make prod-status` / `make prod-grep TAG=…` (read-only прод).
Подробно: [Docs/DEVELOPMENT.md](Docs/DEVELOPMENT.md), эксплуатация — [Docs/MAINTENANCE.md](Docs/MAINTENANCE.md).

## Разработка с Claude Code

Проект использует [Claude Code](https://claude.com/claude-code) как основной AI-помощник для разработки.

- `CLAUDE.md` в корне — контекст проекта (стек, архитектура, конвенции, опасные места). Загружается автоматически в каждой сессии.
- `Docs/plans/*-SESSION-HANDOFF.md` — переносимый контекст между сессиями и машинами (память Claude машинно-локальна). Новая сессия начинается с чтения последнего.
- `.claude/settings.json` — разрешения (read-only прод разрешён, write на прод/деструктивные git — только с подтверждением) и hook `post-edit-check.sh` (компилирует каждый отредактированный `.py`, валидирует JSON локалей).
- `.claude/settings.local.json` — локальные оверрайды (в `.gitignore`).
- `.claude/skills/` — проектные skills (вызов `/имя`):

| Skill | Когда |
|---|---|
| `/gs-session-start` | первым делом в новой сессии: handoff, git, PR, здоровье прода |
| `/gs-prod-ops` | любое обращение к VPS/логам/прод-БД (хосты, что не трогать, теги логов) |
| `/gs-incident` | бот «сделал не то» в проде: таймлайн → улики → корень → фикс → док |
| `/gs-migration` | изменение схемы БД (Alembic, backfill, тесты) |
| `/gs-i18n` | любой текст пользователю (3 локали, плейсхолдеры, HTML) |
| `/gs-pr` | ветка/коммит/PR, branch protection, кто мержит |
| `/gs-subagent-brief` | блок контекста для промптов субагентов (мультиагентная работа) |

Встроенные: `/code-review <PR>` — ревью PR, `/security-review` — security-проверка ветки.
