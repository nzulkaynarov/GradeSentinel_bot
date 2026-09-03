---
name: gs-prod-ops
description: "Безопасная работа с продом GradeSentinel: хосты, что на VPS нельзя трогать, теги логов, read-only SQL, деплой/откат. Использовать при любом обращении к серверам, логам или прод-БД."
---

# /gs-prod-ops — прод без сюрпризов

## Хосты (ssh-алиасы из `~/.ssh/config`)
| Алиас | Что | Замечания |
|---|---|---|
| `vps` | app-VPS `176.101.56.141`, Ubuntu 24.04 | Бот + webapp + Caddy + **чужой проект `railtech-b2b`** (`/opt/railtech-b2b`, `/var/www/railtech-b2b`) и 2 GitHub-runner'а. Ничего кроме `gradesentinel-*` не трогать. |
| `vps-db` | DB-VPS `170.168.6.209` (= `10.0.0.2` по WireGuard) | PostgreSQL 17, `sudo -u postgres psql gradesentinel`. Там же суточные pg_dump + off-site rclone. |

Серверная TZ — **Asia/Tashkent (+05)**: `journalctl` и python-логи в местном времени; в БД naive-UTC
(`date_added`) и timestamptz (`notified_at`). «Сегодня по Ташкенту» в коде = naive UTC + 5h.

## Правила
- **Read-only по умолчанию.** `SELECT`, `journalctl`, `systemctl status/is-active`. Любой `DELETE/UPDATE`
  на проде — отдать владельцу готовой командой (auto-mode их и так блокирует).
- **Не править файлы на VPS** — следующий деплой (rsync) затрёт. Фикс только через PR.
- **Не рестартовать сервисы вручную** без явной просьбы: heartbeat-таймер сам рестартит бот при зависании.
- Секреты в `/etc/gradesentinel/bot.env` — не читать, не выводить.

## Быстрые команды (Makefile)
```
make prod-status                              # юниты
make prod-logs N=300                          # хвост логов
make prod-grep TAG='STALE_SHEET|NEW GRADE'    # теги за сутки
make prod-sql SQL="select id, academic_year from students"
make prod-schema                              # alembic_version на проде
```

## Теги логов (grep-словарь)
| Тег | Смысл |
|---|---|
| `[NEW GRADE]` / `[GRADE CHANGED]` / `[GRADE TRIMMED]` | монитор нашёл/изменил оценку |
| `[PENDING]` | двухфазное подтверждение, ждём следующий цикл |
| `[STALE_SHEET]` | таблица прошлого учебного года — опрос приостановлен, нэдж семье |
| `[STALE_ECHO]` | «сегодняшние» оценки 1:1 = год назад при неизвестном academic_year → пропуск |
| `[ACADEMIC_YEAR]` | importer вывел учебный год таблицы |
| `[DATE_PARSE_FAIL]` | шапка листа получена, но ни одна дата не распозналась |
| `[GOOGLE_QUOTA]` | 429 от Sheets API |
| `[SHEET STUCK]` | 5 подряд ошибок чтения таблицы ученика |
| `[NEW QUARTER]` / `[QUARTER CHANGED]` | четвертные |
| `ntype=<type> tg=<id> status=sent\|queued\|skipped` | Sender: судьба каждого уведомления |
| `Flushing group notification queue` | утренний слив тихих часов (07:00) |

## Полезный SQL (read-only)
```sql
select * from alembic_version;
select id, fio, display_name, academic_year from students;
select id, student_id, subject, raw_text, grade_date, cell_reference, notified_at
  from grade_history where grade_date >= current_date - 3 order by id desc;
select count(*) from notification_queue; select count(*) from group_notification_queue;
select key, value from settings where key like 'scheduler_last_%' or key like 'relink_nudge:%';
```

## Деплой и откат
- Push/merge в `main` → workflow `Tests` (job **`pytest`**, required check) → `Deploy to VPS` (`workflow_run`,
  staged: `/opt/gradesentinel.new` → smoke-компиляция → атомарный switch → restart → smoke → авто-откат).
- Миграции применяет `init_db()` (alembic upgrade head) при старте бота/webapp — отдельного шага нет.
- Откат: `gh pr revert` / revert-PR в main. Не `git push --force`, не правки на сервере.
- Проверка после деплоя: `make prod-status`, `make prod-schema`, `make prod-grep TAG='ERROR|Traceback'`.
