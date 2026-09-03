---
name: gs-subagent-brief
description: "Обязательный контекст-блок для промпта любого кодящего субагента в GradeSentinel (PG %s, sync-бот, i18n×3, Docker-тесты, деплой=merge). Вставлять в промпт перед запуском Agent/Workflow."
---

# /gs-subagent-brief — что вставить в промпт субагента

Скопировать блок ниже в начало промпта каждого кодящего субагента (без него агенты пишут SQLite-измы,
`?`-плейсхолдеры, async и ломают i18n):

```
Контекст GradeSentinel (прод, реальные пользователи):
1. PostgreSQL 17 / psycopg v3: плейсхолдеры %s (НЕ ?), INSERT ... RETURNING id (не lastrowid),
   get_db_connection() — context manager, сам коммитит/откатывает; Row — Mapping (row['c'] и row[0]);
   PG отдаёт date/datetime/bool объектами — to_date_str() для строк; «сегодня по Ташкенту» = naive UTC + 5h.
2. Бот СИНХРОННЫЙ (pyTelegramBotAPI polling) — никаких async/await/aiogram. Handlers регистрируются в
   порядке state_flows → navigation → ai_chat → остальные. Новый scheduler-job — запись в _job_locks.
3. i18n: любой текст пользователю через t(); новый ключ — во ВСЕ ТРИ src/locales/{ru,uz,en}.json с
   одинаковыми плейсхолдерами (sync-тесты). WebApp — свои локали в webapp/static/locales/.
4. Callback с family_id → _check_family_access + _parse_int_args. Уведомления — HTML. Рассылки — send_with_retry.
5. Учебный год — явное поле students.academic_year; даты шапки листов без года: парсеру передавать
   academic_year=; в тестах с датами — фиксированные даты, не «сегодня − N».
6. Тесты: `make test` (Docker + PG). Прогнать перед завершением, сообщить число passed.
7. НЕ пушить/мержить в main (авто-деплой на прод). Работать в своей ветке от main, не трогать .env/credentials.
8. Отчёт: что изменил (файлы), какие тесты добавил, что НЕ сделал и почему.
```

Декомпозиция по доменам (границы почти не пересекаются): ядро/мониторинг (`monitor_engine`,
`history_importer`, `schedulers`, `notifications/`) · подписки (`handlers/subscription/`, `db/payments|promo`)
· AI (`analytics_engine`, `src/ai/`, `ai_tools`, `handlers/ai_chat`) · семья/инвайты (`handlers/family|invite`,
`db/families|invites`) · webapp (`webapp/`) · web-rewrite (`api/`, `web/`, `landing/`) · deploy (`deploy/`).

Ревью-задачи: находки о «багах» сверять с CLAUDE.md (SQLite-реликты в старых доках — не баги кода) и
проверять adversarial-агентом перед докладом владельцу.
