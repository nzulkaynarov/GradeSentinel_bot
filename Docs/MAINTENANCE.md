# Обслуживание и деплой (Deployment & Maintenance)

Актуально на 2026-09. Первичная настройка VPS и полный справочник — [deploy/README.md](../deploy/README.md).
Оперативные правила и словарь тегов логов — skill `/gs-prod-ops` (`.claude/skills/gs-prod-ops/SKILL.md`).

## Инфраструктура
| Узел | Что | Где |
|---|---|---|
| app-VPS `176.101.56.141` (`ssh vps`) | `gradesentinel-bot`, `gradesentinel-webapp` (gunicorn 127.0.0.1:8443), Caddy (`grades.railtech.uz`, авто-TLS), heartbeat-таймер, Hugo-лендинг | код `/opt/gradesentinel/` (per-release venv), секреты `/etc/gradesentinel/bot.env`, `credentials.json` |
| DB-VPS `170.168.6.209` = `10.0.0.2` (`ssh vps-db`) | PostgreSQL 17 (`gradesentinel`), суточный `pg_dump`, off-site rclone | WireGuard, `sslmode=require` |

На app-VPS живёт **чужой проект `railtech-b2b`** и два GitHub-runner'а — не трогать. TZ серверов — Asia/Tashkent.

## Деплой
1. Merge в `main` → GitHub Actions `Tests` (job `pytest`, required check) → `Deploy to VPS` (`workflow_run`).
2. Деплой staged: rsync в `/opt/gradesentinel.new` → venv из `requirements.lock` → smoke-компиляция →
   атомарный switch → `systemctl restart` → smoke → **авто-откат** при провале.
3. Миграции применяет `init_db()` (`alembic upgrade head`) при старте сервисов — отдельного шага нет.
   Ручные действия до/после (удаление мусорных строк, проверки) описываются в секции **Runbook** PR.
4. Проверка: `make prod-status`, `make prod-schema`, `make prod-grep TAG='ERROR|Traceback'`.

Откат — revert-PR в `main` (`gh pr revert`). Никаких правок на сервере (rsync затрёт) и `push --force`.

## Ежедневная эксплуатация
```bash
make prod-status                 # юниты
make prod-logs N=300             # хвост логов бота
make prod-follow                 # tail -f
make prod-grep TAG='STALE_SHEET|SHEET STUCK|GOOGLE_QUOTA'
make prod-sql SQL="select id, display_name, academic_year from students"
```
Watchdog: `gradesentinel-heartbeat.timer` раз в минуту проверяет mtime `/var/lib/gradesentinel/.heartbeat`
и рестартит бот при протухании >180 с. Scheduler-джобы (Ташкент): 07:00 flush тихих часов · 10:00 подписки ·
12:00/18:00 четверти · 15:00 alive · 17:00 proactive AI · 19:00 вечерняя сводка · ср 11:00 летний режим ·
вс 03:00 cleanup · вс 18:00 digest · вс 19:00 AI weekly. Маркеры `scheduler_last_*` в `settings`.

## Сезонный чек-лист
- **1–2 сентября (новый учебный год):** школы выдают новые ссылки. Монитор сам ставит на паузу таблицы прошлого
  учебного года (`[STALE_SHEET]`) и напоминает семьям «🔗 Сменить ссылку»; проверить
  `make prod-sql SQL="select id, academic_year from students"` и что нэджи ушли. Подробно —
  `Docs/plans/2026-09-02-academic-year-rollover.md`.
- **Конец четверти:** лист «Четверти!» — проверить `[NEW QUARTER]` в логах.
- **Лето (01.06–25.08):** летний режим (AI-нэджи по средам); оценок нет — тишина в `[NEW GRADE]` нормальна.

## Бэкапы
- DB-VPS: суточный `pg_dump -Fc` (14-дн ротация) + off-site rclone (`deploy/offsite-backup.sh`).
- Ручной дамп: `ssh vps-db 'sudo -u postgres pg_dump -Fc gradesentinel > /tmp/gs.dump'`.
- Восстановление проверять на пустой БД (`pg_restore -d gradesentinel_test`), не на живой.

## Секреты и доступы
`/etc/gradesentinel/bot.env` (`0640 root:gradesentinel`) — `BOT_TOKEN`, `ADMIN_ID`, `ADMIN_GROUP_ID`, `DATABASE_URL`,
`ANTHROPIC_API_KEY`, `WEBAPP_URL`, провайдеры платежей. Не читать/не выводить в логи и чаты. Ротация — вручную,
затем `systemctl restart gradesentinel-bot gradesentinel-webapp`.

## Открытые эксплуатационные задачи
- SSH-хардненинг app-VPS (root + password auth ещё открыты) — вручную, с параллельной сессией.
- Регулярный test-restore off-site бэкапа.
- `quarter_grades` без `academic_year` — четверти нового года перезапишут прошлогодние (~ноябрь): Фаза 2 RFC rollover.
