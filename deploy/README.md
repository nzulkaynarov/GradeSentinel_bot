# Развёртывание GradeSentinel на чистом Ubuntu 24.04 VPS

Этот каталог содержит всё нужное для bare-metal деплоя. Никакого Docker.

## Что внутри

| Файл | Что делает |
|---|---|
| `install.sh` | Идемпотентный one-shot bootstrap VPS (запускать от root **один раз**) |
| `gradesentinel-bot.service` | systemd-юнит Telegram-бота |
| `gradesentinel-webapp.service` | systemd-юнит Flask + gunicorn (2 worker × 4 threads, слушает `127.0.0.1:8443`) |
| `gradesentinel-heartbeat.service` + `.timer` | Watchdog: рестартит бот, если `/var/lib/gradesentinel/.heartbeat` старше 180с |
| `Caddyfile` | Reverse proxy `grades.railtech.uz` → `127.0.0.1:8443` с авто-Let's Encrypt |
| `deploy-sudoers` | Узкий passwordless sudo для юзера `deploy` (используется GH runner'ом) |

## Архитектура на VPS

```
Internet → Caddy :443 (TLS auto) → 127.0.0.1:8443 → Flask WebApp
                                                       └─ читает /var/lib/gradesentinel/sentinel.db

Telegram polling ← Bot process (systemd) ─→ /var/lib/gradesentinel/sentinel.db
                            │
                            └→ Google Sheets API (с /etc/gradesentinel/credentials.json)
```

Файлы:
- `/opt/gradesentinel/` — код приложения (sync через rsync на каждом деплое)
- `/opt/gradesentinel/venv/` — Python venv (обновляется на деплое если изменился `requirements.txt`)
- `/var/lib/gradesentinel/sentinel.db` — БД SQLite (+ `.db-wal`, `.db-shm`)
- `/var/lib/gradesentinel/.heartbeat` — файл watchdog'а
- `/etc/gradesentinel/bot.env` — все секреты (BOT_TOKEN и т.д.), `0640 root:gradesentinel`
- `/etc/gradesentinel/credentials.json` — Google service account, `0640 root:gradesentinel`

Юзеры:
- `gradesentinel` — system, без shell, владеет процессами бота/webapp
- `deploy` — обычный, владеет GH Actions runner'ом, имеет узкий sudo

---

## Первоначальный setup (~30 минут)

### 1. Подключаешься к VPS как root

```bash
ssh root@176.101.56.141
```

### 2. Создаёшь юзера `deploy`, кладёшь свой ssh-ключ

```bash
adduser deploy                      # задай пароль
usermod -aG sudo deploy
mkdir -p /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
```

С локальной машины:
```bash
ssh-copy-id deploy@176.101.56.141
```

Проверь что `ssh deploy@176.101.56.141` работает по ключу.

### 3. Хардненинг SSH (отключение root и пароля)

```bash
sudo tee /etc/ssh/sshd_config.d/99-hardening.conf > /dev/null <<EOF
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
KbdInteractiveAuthentication no
MaxAuthTries 3
ClientAliveInterval 300
ClientAliveCountMax 2
EOF

sudo sshd -t                        # проверка синтаксиса — критично
sudo systemctl reload ssh
```

⚠️ Перед `reload ssh` — открой второй ssh-терминал в параллели. Если что-то сломал — вернёшься через старую сессию.

### 4. Прописываешь DNS (у регистратора `railtech.uz`)

```
A    grades    176.101.56.141    TTL 300
```

Подожди распространения (1-15 минут). Проверь:
```bash
dig +short grades.railtech.uz       # должно вернуть 176.101.56.141
```

⚠️ Если DNS не успел распространиться к моменту первого `caddy reload` — Caddy не выдаст сертификат, увидишь ошибку про ACME challenge.

### 5. Клонируешь репо на VPS и запускаешь `install.sh`

```bash
sudo apt update && sudo apt install -y git
sudo mkdir -p /tmp/gradesentinel-setup && sudo chown deploy:deploy /tmp/gradesentinel-setup
cd /tmp/gradesentinel-setup
git clone https://github.com/<your-user>/<your-repo>.git .
sudo bash deploy/install.sh
```

Скрипт сам всё сделает: пакеты, юзеров, директории, swap, UFW, fail2ban, Caddy, systemd-юниты, sudoers. **На повторный запуск безопасен.**

### 6. Регистрируешь GitHub Actions runner

GitHub → Settings → Actions → Runners → **New self-hosted runner → Linux x64**.

Скопируй команды оттуда (там одноразовый токен) и запусти от юзера `deploy`:

```bash
sudo -iu deploy
mkdir -p ~/actions-runner && cd ~/actions-runner
# (команды curl/tar/config.sh из GitHub UI)
```

При вопросах config.sh:
- Runner name: `vps-prod` (или любое)
- Labels: оставь дефолт (`self-hosted,Linux,X64`)

Поставь как сервис:
```bash
sudo ./svc.sh install deploy
sudo ./svc.sh start
```

В GitHub проверь — runner появился со статусом `Idle` (зелёный).

### 7. Обновляешь GitHub Secrets

В Settings → Secrets and variables → Actions:
- ✅ `BOT_TOKEN`, `ADMIN_ID`, `ADMIN_GROUP_ID`, `ANTHROPIC_API_KEY`, `GOOGLE_SHEETS_CREDENTIALS` — оставь как было
- 🔄 `WEBAPP_URL` — поменяй на `https://grades.railtech.uz`
- ❌ `WEBAPP_PORT` — **удали** (теперь захардкожен в systemd-юните)
- (опционально) `CLICK_PROVIDER_TOKEN`, `PAYME_PROVIDER_TOKEN`, `SENTRY_DSN`

### 8. Мержишь PR `feature/bare-metal-migration` в `main`

Push в `main` → workflow `.github/workflows/deploy.yml` запустится автоматом.

### 9. Проверяешь что всё работает

```bash
sudo systemctl status gradesentinel-bot gradesentinel-webapp caddy
sudo journalctl -u gradesentinel-bot -f --since "5 min ago"

# Из любой точки в интернете:
curl https://grades.railtech.uz/health
# → {"status":"ok"}
```

В Telegram:
- `/start` → бот отвечает
- Ты как админ (по `ADMIN_ID`) автоматически авторизован
- Создай первую семью, добавь ученика, проверь WebApp кнопку

---

## Эксплуатация

### Логи

```bash
sudo journalctl -u gradesentinel-bot -f       # бот
sudo journalctl -u gradesentinel-webapp -f    # webapp
sudo journalctl -u caddy -f                    # reverse proxy
sudo tail -f /var/log/caddy/grades.log         # access log JSON
```

### Бэкап БД (PostgreSQL на DB-VPS)

После миграции 2026-06-29 БД — PostgreSQL 17 на **отдельном DB-VPS**
(`170.168.6.209` / внутр. `10.0.0.2` через WireGuard). Бэкапы делаются **там**,
а не на app-VPS (на app-VPS БД больше нет — старые SQLite-юниты удалены).

**Локальный суточный дамп** (уже настроен на DB-VPS):
`gradesentinel-db-backup.sh` + `gradesentinel-db-backup.cron` — `pg_dump -Fc`
(custom-формат, pg_restore-able) → `/var/backups/railtech-db/gradesentinel_<TS>.dump`,
cron 02:35, ротация **14 дней**, файлы `root:600`.

```bash
# Разовый ручной дамп с локалки (через SSH на DB-VPS):
ssh -i ~/.ssh/railtech_dbvps_ed25519 root@170.168.6.209 \
    'sudo -u postgres pg_dump -Fc --no-owner --no-privileges gradesentinel' > ~/backups/gs-$(date +%F).dump
```

### Off-site бэкап (rclone → облако) — тоже на DB-VPS

Локальные дампы лежат на том же DB-VPS, где живёт БД — при его гибели теряются
вместе с базой. `offsite-backup.sh` (устанавливается на DB-VPS как
`/usr/local/bin/gradesentinel-offsite-backup.sh`, cron 03:05 — через 30 мин после
локального дампа) зеркалит `/var/backups/railtech-db/gradesentinel_*.dump` в облако
через `rclone`. **Скрипт мягко скипает, пока не сконфигурирован** — алертов не шлёт.

> ⚠️ **Privacy:** в дампе — PII реальных родителей/детей (телефоны, имена, оценки).
> Используй rclone **crypt** remote (client-side шифрование) поверх B2/S3 —
> провайдер будет хранить только зашифрованные блобы.

**Провижининг (один раз, на DB-VPS под root):**

```bash
# 0. Скопировать скрипт + cron на DB-VPS (репо там не выкатывается автоматом —
#    возьми файлы из deploy/ этого репо, напр. scp с локалки):
install -m 0755 deploy/offsite-backup.sh /usr/local/bin/gradesentinel-offsite-backup.sh
install -m 0644 deploy/gradesentinel-offsite-backup.cron /etc/cron.d/gradesentinel-offsite-backup
mkdir -p /etc/gradesentinel && chmod 0700 /etc/gradesentinel

# 1. Установить rclone
curl https://rclone.org/install.sh | sudo bash

# 2. Завести бакет у провайдера (рекоменд. Backblaze B2 — дёшево; или S3).
#    Создать application key с доступом только на этот бакет.

# 3. Настроить rclone: сначала backend (b2/s3), затем crypt поверх него.
sudo rclone --config /etc/gradesentinel/rclone.conf config
#   - remote 'b2raw'   : type=b2,  account+key, bucket
#   - remote 'secret'  : type=crypt, remote=b2raw:gradesentinel-db, задать пароли
#   (crypt шифрует и имена файлов, и содержимое)

# 4. Указать целевой remote (скрипт бежит как root — права root:600 достаточно)
echo 'RCLONE_REMOTE=secret:' | sudo tee /etc/gradesentinel/offsite-backup.env
sudo chmod 0600 /etc/gradesentinel/offsite-backup.env /etc/gradesentinel/rclone.conf

# 5. Прогнать вручную + проверить
sudo /usr/local/bin/gradesentinel-offsite-backup.sh
sudo rclone --config /etc/gradesentinel/rclone.conf ls secret:
```

Off-site хранит то же 14-дневное окно, что и локально (`rclone sync` зеркалит).
Нужна более длинная история — включи versioning/lifecycle на стороне бакета.
Скрипт защищён от «пустого sync»: если локальный `pg_dump` сломался и дампов нет —
sync **не** запускается (иначе снёс бы off-site копию).

> ℹ️ Если на облачном remote остались старые SQLite-файлы `sentinel-*.db.gz` от
> прежней (до-PG) схемы — удали их вручную: `rclone --config … delete secret: --include "sentinel-*.db.gz"`.

**Восстановление (pg_restore):**

```bash
# 1. Достать нужный дамп из off-site (или взять локальный /var/backups/railtech-db/…):
rclone --config /etc/gradesentinel/rclone.conf copy secret:gradesentinel_<TS>.dump .

# 2. Восстановить на чистую/целевую БД (custom-формат → pg_restore, НЕ psql):
sudo -u postgres pg_restore --clean --if-exists --no-owner --no-privileges \
    -d gradesentinel gradesentinel_<TS>.dump
```

> ⚠️ **Обязательный периодический ручной тест (test-restore):** раз в квартал
> прогоняй восстановление в **отдельную пустую** БД и сверяй целостность —
> бэкап без проверенного restore не считается рабочим:
> ```bash
> sudo -u postgres createdb gs_restore_test
> sudo -u postgres pg_restore --no-owner --no-privileges -d gs_restore_test gradesentinel_<TS>.dump
> sudo -u postgres psql -d gs_restore_test -c '\dt'   # таблицы на месте?
> sudo -u postgres psql -d gs_restore_test -c 'SELECT count(*) FROM parents; SELECT count(*) FROM grade_history;'
> sudo -u postgres dropdb gs_restore_test
> ```

### Откат к прошлой версии

```bash
# На VPS:
cd /opt/gradesentinel
sudo -u gradesentinel git -C /home/deploy/actions-runner/_work/<repo>/<repo> log --oneline -10
# найти предыдущий хороший SHA → revert через GitHub:
gh pr revert <last-pr-number>
# или прямо в _work:
sudo -u deploy git -C /home/deploy/actions-runner/_work/<repo>/<repo> checkout <good-sha>
sudo systemctl restart gradesentinel-bot gradesentinel-webapp
```

(Лучшая практика — `gh pr revert` через PR. Не правь код напрямую на VPS — следующий деплой затрёт.)

### Обновление зависимостей

Меняешь `requirements.txt` → push → деплой сам обновит venv.

### Перезапуск вручную

```bash
sudo systemctl restart gradesentinel-bot
sudo systemctl restart gradesentinel-webapp
sudo systemctl reload caddy
```

---

## Чеклист «всё ок»

- [ ] `ssh root@vps` запрещён, `ssh deploy@vps` по ключу работает
- [ ] `sudo ufw status` — открыт SSH, 80, 443
- [ ] `sudo fail2ban-client status sshd` — активен
- [ ] `dig grades.railtech.uz` возвращает IP VPS
- [ ] `curl https://grades.railtech.uz/health` → 200 OK + JSON
- [ ] Runner в GitHub зелёный
- [ ] `systemctl is-active gradesentinel-bot gradesentinel-webapp caddy` → все `active`
- [ ] `sudo systemctl list-timers | grep heartbeat` — таймер активен
- [ ] В Telegram бот отвечает на `/start`
- [ ] Открытие WebApp в Telegram грузит дашборд

---

## Troubleshooting

**Caddy не выдаёт сертификат:**
```bash
sudo journalctl -u caddy -n 50
# часто: DNS ещё не распространился, или 80 закрыт в UFW, или провайдер блокирует :80
dig grades.railtech.uz
sudo ufw status | grep 80
```

**Бот не стартует, юнит постоянно рестартится:**
```bash
sudo journalctl -u gradesentinel-bot -n 100
# проверь:
sudo -u gradesentinel cat /etc/gradesentinel/bot.env  # есть ли BOT_TOKEN?
sudo -u gradesentinel /opt/gradesentinel/venv/bin/python -c "import telebot; print(telebot.__version__)"  # venv ок?
ls -la /var/lib/gradesentinel/  # есть ли права на запись?
```

**WebApp 502 Bad Gateway:**
```bash
sudo systemctl status gradesentinel-webapp
sudo ss -tlnp | grep 8443  # должен слушать 127.0.0.1:8443 (от gunicorn)
sudo journalctl -u gradesentinel-webapp -n 50
# Если gunicorn не стартует — проверь venv:
sudo -u gradesentinel /opt/gradesentinel/venv/bin/gunicorn --version
# Если "command not found" — pip не установил gunicorn, перезапусти deploy.yml
```

**Дашборд возвращает 401 «Invalid hash» в Telegram WebApp:**
```bash
sudo journalctl -u gradesentinel-webapp -f | grep "auth failed"
# Известные причины (исторически):
#   1. BOT_TOKEN в /etc/gradesentinel/bot.env != токен бота → проверь
#      curl https://api.telegram.org/bot<TOKEN>/getMe — должен вернуть нужного бота
#   2. validate_init_data использовал URL-encoded values вместо decoded — починено
#   3. signature поле НЕ должно исключаться из data_check_string — починено
# Если 401 после правильного BOT_TOKEN — поймай initData из логов Caddy
# и вычисли HMAC вручную (см. webapp/app.py:validate_init_data).
```

**Дашборд медленно грузится:**
```bash
# Проверка времени ответа /api/dashboard:
time curl -sH "X-Telegram-Init-Data: <real-init-data>" https://grades.railtech.uz/api/dashboard/1
# Должно быть <300ms. Если больше:
#   - Сколько grade_history записей? sqlite3 /var/lib/gradesentinel/sentinel.db "SELECT COUNT(*) FROM grade_history"
#   - gunicorn workers заняты? sudo systemctl status gradesentinel-webapp (CPU%)
#   - Не упёрся ли MemoryMax=200M? Если да — увеличить в unit-файле
```

**Heartbeat-watchdog слишком агрессивный (бот реально работает но рестартится):**
```bash
# Проверь интервал записи в src/config.py — HEARTBEAT_INTERVAL=30
# Если CPU перегружен и main thread не успевает за 180с — увеличь WatchdogSec
# или причина в polling блокировке (см. monitor_engine).
sudo systemctl status gradesentinel-heartbeat.timer
sudo systemctl list-timers gradesentinel-heartbeat.timer
```

**Хочу временно отключить watchdog для отладки:**
```bash
sudo systemctl stop gradesentinel-heartbeat.timer
# отладишь, потом:
sudo systemctl start gradesentinel-heartbeat.timer
```
