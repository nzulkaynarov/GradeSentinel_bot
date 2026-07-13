# Web Rewrite — текущее состояние и action items

**Назначение этого документа:** живой статус миграции для всех агентов и людей, работающих удалённо. Обновляется в конце каждой фазы. Архитектурные решения и обоснования — в [`web-rewrite-rfc.md`](web-rewrite-rfc.md).

**Последнее обновление:** 2026-05-14
**Текущее состояние:** Phase 0+1 в проде, Phase 2 NEXT (ждёт стартового сигнала)

---

## TL;DR

- ✅ `https://grades.railtech.uz/` — лендинг + `/docs/` + `/uz/` + `/en/` живут как Hugo статика
- ✅ Mini App (`/webapp`, `/api/dashboard/*`, `/api/quarters/*`, `/api/students`, `/api/grades`, `/static/*`, `/health`) — продолжает работать на Flask `127.0.0.1:8443`, не задет
- ⏸ `/api/*` (всё кроме legacy) — зарезервировано под FastAPI (Phase 2)
- ⏸ `/cabinet/*` — родительский портал (Phase 3, Next.js)
- ⏸ `/admin/*` — админка (Phase 5, тот же Next.js)
- ❌ Никаких поддоменов `app.*`/`admin.*`/`api.*` — single domain зафиксирован

## Прод-инфраструктура

| Что | Где | State |
|---|---|---|
| VPS | `176.101.56.141` (vps00249.eskiz.uz, Ubuntu 24.04) | Active |
| Bot | systemd `gradesentinel-bot.service` | Active, polling |
| WebApp Flask | systemd `gradesentinel-webapp.service`, gunicorn 2×4 на `127.0.0.1:8443` | Active |
| Hugo landing | `/var/www/gradesentinel-landing/` (www-data:www-data 0755), 30 файлов, 292KB | Live |
| Caddy | v2.11.2, `/etc/caddy/Caddyfile` (split handle блоки, см. RFC §4) | Active |
| Self-hosted runner | `actions.runner.nzulkaynarov-GradeSentinel_bot.vps-prod.service` | Active |
| Heartbeat watchdog | `gradesentinel-heartbeat.timer` (раз в минуту) | Active |
| Backup БД | `gradesentinel-backup.timer` (ежедневно 03:30) | Active |
| FastAPI | — | **БУДЕТ Phase 2**, порт `127.0.0.1:8444` |
| Next.js | — | **БУДЕТ Phase 3**, порт `127.0.0.1:3000` |

## DNS

`grades.railtech.uz` A-запись → `176.101.56.141`. **Других поддоменов не будет.** Если кто-то предложит `app.grades.railtech.uz` или подобное — отказать, single-domain решение.

## Что DONE (Phase 0 + Phase 1)

### Phase 0 — PR #37 merged 2026-05-14

Фундамент:

- `Docs/web-rewrite-rfc.md` — этот RFC.
- `frontend/` — 4 HTML-мокапа от пользователя (design source, read-only).
- `landing/` — Hugo skeleton (hugo.toml с 3 языками, layouts, partials, i18n YAML).
- `web/` — Next.js 15 skeleton (package.json, tsconfig, app/, Tailwind v4 globals.css).
- `api/` — FastAPI skeleton (pyproject.toml, main.py с CORS, `/health`, pytest).
- Дизайн-токены извлечены в `landing/assets/css/tokens-v2.css` и `web/app/globals.css` (Tailwind v4 `@theme` блоки с обеими темами).

### Phase 1 — PR #38 merged + fix #39 merged 2026-05-14

Лендинг в проде:

- Полный порт `frontend/index.html` → `landing/layouts/index.html` (hero, flow, bento, day-in-life, pricing, FAQ, big CTA) + полный CSS `landing/assets/css/main.css` (~600 строк) + JS `main.js` (reveal-on-scroll, FAQ accordion, docs TOC scroll-spy).
- Полный порт `frontend/instructions.html` → `landing/content/docs/_index.md` (10 разделов: с чего начать, роли, создание семьи, добавление ребёнка, инвайт, уведомления, AI, подписка, команды, troubleshoot). Layout `landing/layouts/docs/list.html` (TOC + content + rail).
- i18n: `landing/i18n/{ru,uz,en}.yaml` (~80 ключей/lang). Ru — полный, uz/en — главная переведена, docs — заглушки со ссылкой на ru.
- CI: `.github/workflows/landing-deploy.yml` (peaceiris/actions-hugo 0.140.2, build, rsync, curl smoke).
- Caddyfile: split handle блоки (см. RFC §4).
- `deploy/deploy-sudoers` расширен: `install -d`, `rsync`, `chown -R` для `/var/www/gradesentinel-landing`. **Установлен вручную на прод** (sudoers не auto-rsync).
- `deploy/gradesentinel-{bot,webapp}.service` — исправлен `Documentation=` URL (`anthropics/` → `nzulkaynarov/`).

Smoke check всех путей после деплоя — все 200 OK, Mini App не задет.

## Что NEXT (Phase 2)

**Цель:** FastAPI с Phone OTP auth, миграция БД, admin brute-force защита.

**Что строим:**

- `api/api/routes/auth.py` — phone request-otp / verify / set-password / login / refresh / logout / forgot
- `api/api/routes/me.py` — GET текущий родитель, его семьи, дети, подписка
- `api/api/routes/admin.py` — login админа с rate-limit + lockout + audit
- `api/api/deps.py` — `current_parent`, `current_admin`, JWT decode
- `api/api/services/otp_via_bot.py` — `from src.bot_instance import bot; bot.send_message(telegram_id, code)`
- `api/api/services/jwt.py` — issue/verify access + refresh tokens
- `api/api/services/migrations.py` — idempotent ALTER TABLE + CREATE TABLE на старте
- `deploy/gradesentinel-api.service` — systemd unit на `127.0.0.1:8444`
- `deploy/Caddyfile` — добавить `@flask_legacy_api` carve-out + `handle /api/* { reverse_proxy 127.0.0.1:8444 }`
- `deploy/deploy-sudoers` — `install`/`restart`/`enable` для нового unit'а
- `.github/workflows/deploy.yml` — расширить под установку и рестарт api.service

**Endpoints (см. RFC §5 для деталей):**

- `POST /api/auth/phone/request-otp` — `{phone}` → проверяет parents.phone+telegram_id, шлёт OTP в бот
- `POST /api/auth/phone/verify` — `{phone, code}` → set_password_token (TTL 15 мин) или JWT
- `POST /api/auth/set-password` — `{token, password}` → JWT + refresh
- `POST /api/auth/login` — `{phone, password}` → JWT + refresh
- `POST /api/auth/refresh` — refresh cookie → новый JWT
- `POST /api/auth/logout` — invalidate refresh
- `POST /api/auth/forgot` — alias request-otp (purpose=reset)
- `GET /api/me` — текущий родитель
- `POST /api/admin/login` — bcrypt-check, rate-limit, lockout, audit

**БД-миграция (см. RFC §6):** аддитивно — `parents.password_hash/password_set_at/last_login_at` + таблицы `auth_otp`, `auth_refresh_tokens`, `auth_admin_log`.

### Action items для пользователя перед Phase 2

- [ ] Сгенерировать секреты на VPS и добавить в `/etc/gradesentinel/bot.env`:

  ```bash
  JWT_SECRET=$(openssl rand -base64 64 | tr -d '\n')
  ADMIN_LOGIN=admin   # или другой
  ADMIN_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=' | head -c 20)
  ADMIN_PASSWORD_HASH=$(python -c "import bcrypt; print(bcrypt.hashpw(b'$ADMIN_PASSWORD', bcrypt.gensalt(rounds=12)).decode())")
  # ADMIN_PASSWORD сохранить в password manager — больше не покажется
  ```

  Также в GH Actions Secrets для деплоя: `JWT_SECRET`, `ADMIN_LOGIN`, `ADMIN_PASSWORD_HASH`.

- [ ] Сделать backup БД до миграции: `ssh root@176.101.56.141 'sudo -u gradesentinel sqlite3 /var/lib/gradesentinel/sentinel.db ".backup /tmp/backup-pre-phase2-$(date +%Y%m%d-%H%M).db"'`
- [ ] Approve PR с новым systemd unit `gradesentinel-api.service` и Caddyfile carve-out
- [ ] Ручная установка обновлённого `deploy-sudoers` на прод после merge (sudoers не auto-rsync)
- [ ] Тестовый родитель в БД — у владельца уже есть (Зулькайнаров, 2 ребёнка)

## Что pending (Phase 3+)

### Phase 3 — Next.js портал родителей (`/cabinet/*`)

- `web/app/cabinet/...` (Next.js 15 App Router) с route group `(parent)`
- Login + dashboard (по мотивам `frontend/dashboard.html`)
- Tremor/Recharts для чартов (тренды, sparklines по предметам)
- next-intl ru/uz/en, next-themes light/dark
- API calls к `/api/*` (auth, me, family, students, history)
- `deploy/gradesentinel-web.service` — `next start` на `127.0.0.1:3000`
- Caddyfile: `handle /cabinet* { reverse_proxy 127.0.0.1:3000 }`

### Phase 3.5 — AI Digest

- `POST /api/digest/<student_id>` — Claude API, кэш 6h в БД (новая таблица `ai_digest_cache`)
- В Next.js кабинете — кнопка «Сгенерировать разбор» + блок с результатом

### Phase 5 — Next.js админка (`/admin/*`)

- `web/app/admin/...` под route group `(admin)`
- 9 табов из `frontend/admin.html` (обзор, статистика, логи, семьи, пользователи, платежи, промо, рассылка, настройки)
- Auth — login/bcrypt через `/api/admin/login`
- Та же Next.js, тот же `gradesentinel-web.service`, дополнительный `handle /admin*` в Caddy
- Решить: оставить Telegram admin-команды (`/list_families`, `/broadcast`) или сансет

### Phase 6 — операционка

- Backup БД в MinIO S3 (пользовательский). Нужны `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_ENDPOINT`, `MINIO_BUCKET` в GH Secrets
- Prometheus exporter или Plausible analytics (TBD)
- SSH хардненинг (только key-auth, без root login)

## Известные ограничения и follow-ups

- Hugo создаёт пустые `/ru/`, `/categories/`, `/tags/` директории — косметика, можно фиксить добавлением `disableKinds = ["taxonomy", "term", "RSS"]` в `landing/hugo.toml`
- Docs uz/en — заглушки. Полный перевод — отдельный PR
- Self-hosted GH runner на самом VPS — единая точка отказа для деплоя. Резервный вариант — GitHub-hosted runner с SSH ключом

## Constraints (нерушимые правила)

> **ОБНОВЛЕНО 2026-07-13:** пункты 1-2 (заморозка `src/main.py`/`monitor_engine.py`/`webapp/app.py`)
> **СНЯТЫ.** Эти правила ставились в мае как защита во время web-rewrite Phase 0/1, когда у кода не было
> тестов и CI-гейта. С тех пор: 521 тест, branch protection (`pytest`), атомарный деплой с авто-откатом,
> `main.py` и `monitor_engine.py` уже безопасно рефакторились (PR-M3, PR-F1). `webapp/app.py` прошёл аудит
> 2026-07-13 и подлежит модуляризации (см. `Docs/plans/2026-07-13-tech-debt-and-modularization-tz.md`
> и RFC/аудит webapp). Правки этих файлов — как везде: через PR + зелёный CI + ревью owner. Осторожность
> сохраняется (прод), но абсолютного запрета нет.

1. ~~`src/main.py`, `src/bot_instance.py`, `src/monitor_engine.py` — НЕ ТРОГАЕМ~~ — снято (см. выше), правки через PR+CI.
2. ~~`webapp/app.py` + Mini App — НЕ ТРОГАЕМ~~ — снято (см. выше); модуляризация запланирована после аудита.
3. Существующие таблицы БД — только `ALTER TABLE ADD COLUMN`, никаких `DROP`/`RENAME`
4. Все правки на проде — через PR + CI rsync. Руками только: `mkdir`/`chown` новых путей, чтение логов, backup БД, установка нового sudoers
5. Перед миграциями БД — обязательный backup
6. Не амендим уже published commits на main
7. Никаких новых поддоменов — single domain зафиксирован
8. Никаких `--no-verify`, `--force-push на main`, `rm -rf` без чёткой цели

## Полезные команды для следующих агентов

```bash
# SSH на прод
ssh root@176.101.56.141

# Backup БД (перед миграциями)
ssh root@176.101.56.141 'sudo -u gradesentinel sqlite3 /var/lib/gradesentinel/sentinel.db ".backup /tmp/backup-$(date +%Y%m%d-%H%M).db"'

# Логи бота
ssh root@176.101.56.141 'journalctl -u gradesentinel-bot -n 100 --no-pager'

# Логи webapp
ssh root@176.101.56.141 'journalctl -u gradesentinel-webapp -n 100 --no-pager'

# Логи прошедшего GH Actions job
ssh root@176.101.56.141 'ls -lt /home/deploy/actions-runner/_diag/Worker_*.log | head -3'

# Установка нового sudoers после merge (если deploy-sudoers изменился)
ssh root@176.101.56.141 'visudo -cf /opt/gradesentinel/deploy/deploy-sudoers && install -m 0440 -o root -g root /opt/gradesentinel/deploy/deploy-sudoers /etc/sudoers.d/deploy-runner && visudo -cf /etc/sudoers.d/deploy-runner'

# Smoke check всех endpoints
for url in / /docs/ /uz/ /uz/docs/ /en/ /en/docs/ /webapp /health; do
  printf "%-15s -> " "$url"
  curl -fsS -o /dev/null -w "%{http_code} %{size_download}B  %{time_total}s\n" "https://grades.railtech.uz$url"
done

# Re-trigger landing-deploy через GH UI
# https://github.com/nzulkaynarov/GradeSentinel_bot/actions/workflows/landing-deploy.yml → Run workflow
```
