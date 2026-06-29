# Cutover runbook: GradeSentinel SQLite → PostgreSQL (2026-06-29)

Переключение боевого бота с локального SQLite (`/var/lib/gradesentinel/sentinel.db`)
на PostgreSQL 17 (БД `gradesentinel` на DB-VPS `10.0.0.2`, WireGuard).

**Простой:** ~5–15 мин (платящие пользователи) — делать в низкий трафик (ночь).
**Откат:** боевой `sentinel.db` только читается и бэкапится → возврат < 5 мин.

---

## Готово ДО окна (ветка `feat/postgres-migration`)
- Весь код портирован на psycopg v3; **440 тестов зелёные на Postgres 17**
  (`docker compose -f docker-compose.test.yml run --rm tests`).
- Схема — Alembic baseline (`migrations/0001_baseline.py`): BIGINT для Telegram-id,
  IDENTITY, timestamp наивный UTC.
- ETL `scripts/migrate_sqlite_to_pg.py` (dry-run пройден: COUNT'ы сходятся, sequence ок).
- `deploy.yml` пишет `DATABASE_URL` в `bot.env` из секрета `DATABASE_URL`.
- `scripts/cutover.sh` — одношаговый помощник для шагов на app-VPS.

---

## Шаги окна (выполнять по порядку)

### 1. Пароль роли + DSN (DB-VPS)
```bash
PW=$(openssl rand -hex 24)
ssh -i ~/.ssh/railtech_dbvps_ed25519 root@170.168.6.209 \
  "sudo -u postgres psql -v ON_ERROR_STOP=1 -c \"ALTER ROLE gradesentinel WITH LOGIN PASSWORD '$PW';\""
DSN="postgresql://gradesentinel:${PW}@10.0.0.2:5432/gradesentinel?sslmode=require"
echo "$DSN"   # понадобится в шаге 2
```

### 2. Секрет `DATABASE_URL` в GitHub (чтобы деплой прописал его в bot.env)
```bash
gh secret set DATABASE_URL --repo nzulkaynarov/GradeSentinel_bot --body "$DSN"
# либо вручную: Settings → Secrets and variables → Actions → New secret
```

### 3. Деплой кода (merge ветки в main → self-hosted runner)
```bash
gh pr create --base main --head feat/postgres-migration --fill   # или PR в UI
gh pr merge --merge   # push в main запускает Deploy to VPS:
#  rsync кода + pip install (psycopg/alembic) + bot.env c DATABASE_URL + restart
```
> После деплоя бот рестартует и `init_db()` создаст схему на пустой PG (данных ещё нет).
> Сразу идём к шагу 4 (ETL) — окно простоя это покрывает.

### 4. Перенос данных + рестарт (app-VPS) — одной командой
```bash
ssh root@176.101.56.141 'bash /opt/gradesentinel/scripts/cutover.sh'
# cutover.sh: stop → backup sqlite → alembic upgrade → ETL --truncate → start → smoke
# DATABASE_URL он берёт из /etc/gradesentinel/bot.env (прописан деплоем).
```

### 5. Ручной smoke (обязательно)
- Telegram: `/start`, открыть оценки ребёнка, статус подписки, инвайт.
- WebApp: `https://grades.railtech.uz` дашборд грузится; `curl -fsS http://127.0.0.1:8443/health`.
- Логи: `ssh root@176.101.56.141 'journalctl -u gradesentinel-bot -n 80 --no-pager'`
  — нет `current transaction is aborted`, нет трейсбеков psycopg.
- Сверка: ETL в шаге 4 печатает COUNT sqlite↔pg по каждой таблице (должно `OK`).

### 6. Бэкапы на DB-VPS
Добавить `gradesentinel` в суточный `pg_dump` (как `railtech`):
проверить/расширить `/usr/local/bin/railtech-db-backup.sh` на DB-VPS, чтобы дампил и эту БД.

---

## Откат (если smoke красный)
```bash
# Вариант A: вернуть прошлый main → CI передеплоит код на SQLite (bot.env без DATABASE_URL)
gh pr ... revert / git revert <merge> && git push   # runner редеплоит
# Вариант B (быстрее): на app-VPS убрать DATABASE_URL из bot.env и откатить код
#   — но проще revert main. Боевой sentinel.db НЕ менялся → данные целы.
ssh root@176.101.56.141 'systemctl restart gradesentinel-bot gradesentinel-webapp'
```
Бэкап sqlite от шага 4: `/var/lib/gradesentinel/sentinel.db.pre-pg-*`.

## После окна
- Наблюдать 24–72ч: доступность PG/WireGuard, латентность, отсутствие aborted-tx.
- Не удалять `sentinel.db` минимум неделю (архивировать после стабилизации).
- ⚠️ Бот теперь зависит от сети (PG по WireGuard). Поведение при недоступности БД —
  «явная деградация + ретрай» (psycopg_pool с реконнектом). Следить за алертами.
