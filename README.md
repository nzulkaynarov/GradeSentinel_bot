# GradeSentinel
**v2.5** | Docker-based | Telegram Payments

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
├── database_manager.py      # SQL: таблицы, миграции, индексы, CRUD
├── google_sheets.py         # Google Sheets API
├── monitor_engine.py        # Polling-цикл, snapshot-сравнение
├── data_cleaner.py          # Очистка "грязных" оценок
├── analytics_engine.py      # Claude AI анализ
├── schedulers.py            # Вечерняя сводка, тихие часы, bot_alive
├── ui.py                    # Меню, send_menu_safe, send_content
├── i18n.py                  # Мультиязычность
├── utils.py                 # Утилиты
├── locales/                 # ru.json, uz.json, en.json
└── handlers/
    ├── admin.py             # /status, /add_family, /list_families
    ├── family.py            # Управление семьёй, /grades, /manage_family
    ├── communication.py     # Поддержка, рассылка
    ├── analytics.py         # /ai_report, еженедельные отчёты
    ├── settings.py          # Смена языка
    ├── subscription.py      # Подписка, платежи, /grant_sub
    └── invite.py            # Инвайт-ссылки для семей
```

### База данных (SQLite)

| Таблица | Назначение |
|---------|-----------|
| `parents` | Пользователи: fio, phone, telegram_id, role, lang |
| `students` | Ученики: fio, spreadsheet_id, display_name |
| `families` | Семьи: family_name, head_id, subscription_end |
| `family_links` | M2M связи: family↔parent, family↔student |
| `grade_history` | История оценок: subject, raw_text, grade_value, cell_reference |
| `quarter_grades` | Четвертные оценки |
| `notification_queue` | Очередь тихих часов |
| `family_invites` | Инвайт-ссылки: invite_code, expires_at, is_used |
| `payments` | История платежей: amount, currency, plan, charge IDs |
| `user_states` | Временные состояния (выбор языка, инвайт) |

8 индексов на часто используемые столбцы (grade_history, family_links, parents, и др.).

*(Подробная архитектура: `Docs/Project_overview.md`)*

---

## Разработка с Claude Code

Проект использует [Claude Code](https://claude.com/claude-code) как основной AI-помощник для разработки.

- `CLAUDE.md` в корне — контекст проекта (стек, архитектура, конвенции, опасные места). Загружается автоматически в каждой сессии.
- `.claude/settings.json` — общие разрешения и переменные окружения для команд.
- `.claude/settings.local.json` — локальные оверрайды (в `.gitignore`).

Полезные slash-команды:
- `/init` — пересобрать `CLAUDE.md` после крупного рефакторинга.
- `/security-review` — security-проверка текущей ветки перед PR.
- `/review` — review pull request.
