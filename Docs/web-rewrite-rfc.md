# RFC: Web-портал, админка и публичный сайт

**Статус:** Phase 0+1 DONE и в проде, Phase 2 NEXT
**Автор:** @nzulkaynarov + Claude
**Создан:** 2026-05-14
**Обновлён:** 2026-05-14 (single-domain топология, см. §4)
**Связанные документы:**

- `Docs/web-rewrite-status.md` — текущее состояние, что в проде, action items
- `Docs/rfc-grades-source-of-truth.md` — другой RFC, не пересекается

---

## 1. Контекст

У бота сейчас:
- **Telegram Mini App** на `grades.railtech.uz/webapp` (Flask + HTML/JS, HMAC initData auth).
- **Никакого публичного сайта** — пользователи приходят только через инвайт-ссылку в Telegram.
- **Админ-функции в Telegram** через слэш-команды (`/add_family`, `/list_families`, broadcast).

Подготовлены 4 HTML-мокапа в `frontend/`:
- `index.html` — лендинг (paper editorial style, theme-v2)
- `instructions.html` — публичная документация (corporate teal, theme.css)
- `dashboard.html` — родительский веб-портал (paper editorial, theme-v2, custom SVG-чарты)
- `admin.html` — админ-панель (corporate teal, theme.css, React via Babel CDN)

## 2. Цели

1. Дать **публичный лендинг** для маркетинга и SEO (Узбекистан, рынок школьников).
2. Дать **полноценный родительский веб-портал** — Mini App тесен для подробной аналитики и истории.
3. Дать **веб-админку** в дополнение к Telegram-командам — broadcast с превью, разбор инцидентов, аналитика.
4. **Не задеть бот и Mini App** — они в проде, работают, не трогаем.

## 3. Стек (зафиксирован)

| Слой | Технология | Почему |
|---|---|---|
| Лендинг + публичная документация | **Hugo** | Статика, нативный i18n, мгновенный TTFB, ноль рантайма. |
| Веб-портал + админка | **Next.js 15 + TypeScript + Tailwind v4 + shadcn/ui + Tremor/Recharts + Lucide + next-themes + next-intl + react-hook-form + zod** | Один build, route groups `(portal)` и `(admin)`, шареные компоненты. |
| API | **FastAPI** (отдельный systemd unit) | OpenAPI → автогенерация TS-типов, Pydantic-валидация, async для AI Digest. |
| Auth (родитель) | Phone + OTP через бота → пароль (bcrypt) → JWT в httpOnly cookie | Нет SMS-провайдера (используем `parents.telegram_id`). Естественный gate: родитель уже есть в БД. |
| Auth (админ) | `ADMIN_LOGIN` + bcrypt(`ADMIN_PASSWORD`) из env | Один пользователь, не нужен IP-allowlist но **обязательно** rate-limit + lockout. |

## 4. Топология деплоя

**Single domain** — `grades.railtech.uz` обслуживает всё. Никаких поддоменов `app.*`/`admin.*`/`api.*` (зафиксировано 2026-05-14 после Phase 1).

| Путь | Назначение | Бэкенд |
|---|---|---|
| `/` | Hugo лендинг (paper editorial) | Hugo статика (`/var/www/gradesentinel-landing/`) |
| `/docs/`, `/uz/`, `/en/` | Документация и i18n-версии | Hugo статика |
| `/webapp*`, `/static/*`, `/health` | Telegram Mini App (HMAC initData) | Flask `127.0.0.1:8443` |
| `/api/dashboard/*`, `/api/quarters/*`, `/api/students`, `/api/grades` | Legacy Mini App API (НЕ трогаем) | Flask `127.0.0.1:8443` |
| `/api/*` (всё остальное под /api/) | Новые endpoints — auth, me, admin, family, subscription, digest | FastAPI `127.0.0.1:8444` (Phase 2+) |
| `/cabinet/*` | Веб-портал родителей | Next.js `127.0.0.1:3000` (Phase 3) |
| `/admin/*` | Админ-панель | Тот же Next.js `127.0.0.1:3000` (Phase 5), route group |

### Caddyfile carve-out для `/api/*`

Mini App и FastAPI делят `/api/*` namespace. Caddy ищет совпадение по path-matchers в порядке появления `handle` блоков (first match wins) — legacy endpoints перехватываются явным списком, всё остальное под `/api/*` идёт в FastAPI:

```caddy
# 1. Specific legacy Mini App API endpoints (проверяются первыми)
@flask_legacy_api path /api/dashboard/* /api/quarters/* /api/students /api/grades
handle @flask_legacy_api { reverse_proxy 127.0.0.1:8443 }

# 2. Всё остальное под /api/* — FastAPI
handle /api/* { reverse_proxy 127.0.0.1:8444 }

# 3. Next.js — один процесс, два пути через route groups
handle /cabinet* { reverse_proxy 127.0.0.1:3000 }
handle /admin* { reverse_proxy 127.0.0.1:3000 }

# 4. Прочие Mini App пути
handle /webapp* /static/* /health { reverse_proxy 127.0.0.1:8443 }

# 5. Catchall — Hugo статика
handle { root * /var/www/gradesentinel-landing; file_server }
```

**Правило именования FastAPI endpoints:** ВСЕ новые endpoints должны попадать под `/api/*` но НЕ начинаться с `dashboard/`, `quarters/`, `students`, `grades` (занято Mini App'ом). Допустимые префиксы: `/api/auth/*`, `/api/me`, `/api/admin/*`, `/api/family/*`, `/api/subscription/*`, `/api/digest/*`.

**Next.js — один процесс, два пути:** не `basePath` (он принимает одно значение), а через роуты — `app/cabinet/...` и `app/admin/...` (или route groups для шареных layout'ов: `app/(parent)/cabinet/...`, `app/(admin)/admin/...`). Один systemd unit `gradesentinel-web.service` на `127.0.0.1:3000`.

## 5. Аутентификация

### Родитель (веб-портал)

```
[Login page]
   ↓ phone (+998 XX XXX XX XX)
[API: POST /api/auth/phone/request-otp]
   → ищет parents.phone, проверяет parents.telegram_id
   → если нет telegram_id: ошибка «Запустите бот в Telegram»
   → если есть: генерирует 6-значный OTP, hash в auth_otp, шлёт через bot.send_message
   ↓ user вводит OTP
[API: POST /api/auth/phone/verify]
   → возвращает one-time set_password_token (TTL 15 мин, если password_hash NULL)
   → или JWT (если уже есть пароль) — это reset-flow без подтверждения
   ↓ user задаёт/меняет пароль (≥8 chars, bcrypt)
[API: POST /api/auth/set-password]
   → пишет parents.password_hash, выдаёт JWT
```

Дальше — обычный `phone + password → JWT`. Reset — тот же OTP-flow.

**Critical edge case:** если у родителя нет `telegram_id` (он добавлен через инвайт но не запускал `/start`), показываем «Сначала запустите бот в Telegram, чтобы получить код». Этот случай естественно совпадает с моделью бота.

### Админ

`ADMIN_LOGIN` (например `admin`) + bcrypt(`ADMIN_PASSWORD`) в `/etc/gradesentinel/bot.env`. Пароль ≥16 символов, генерируется через `openssl rand -base64 24`.

**Brute-force защита (обязательна):**
- Rate-limit на `POST /api/admin/login`: 5 попыток / 15 мин на IP.
- Account lockout: 10 неудач подряд → блок на 1 час.
- Audit log: все попытки в `auth_admin_log` (ip, ua, success, ts).

## 6. Миграция БД (Phase 2)

```sql
-- Расширение parents для пароля
ALTER TABLE parents ADD COLUMN password_hash TEXT;
ALTER TABLE parents ADD COLUMN password_set_at TIMESTAMP;
ALTER TABLE parents ADD COLUMN last_login_at TIMESTAMP;

-- OTP-коды
CREATE TABLE auth_otp (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone TEXT NOT NULL,
  code_hash TEXT NOT NULL,
  purpose TEXT NOT NULL,         -- 'set_password' | 'reset_password'
  attempts INTEGER DEFAULT 0,
  expires_at TIMESTAMP NOT NULL,
  used_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_auth_otp_phone ON auth_otp(phone);

-- Refresh-токены (для long-lived sessions)
CREATE TABLE auth_refresh_tokens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  parent_id INTEGER NOT NULL REFERENCES parents(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  user_agent TEXT,
  ip TEXT,
  expires_at TIMESTAMP NOT NULL,
  revoked_at TIMESTAMP,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Audit log админ-логинов
CREATE TABLE auth_admin_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ip TEXT NOT NULL,
  user_agent TEXT,
  success INTEGER NOT NULL,      -- 0 | 1
  reason TEXT,                    -- 'bad_password' | 'rate_limited' | 'locked' | 'ok'
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_auth_admin_log_ip_ts ON auth_admin_log(ip, created_at);
```

**Не трогаем** `families` / `family_links` / `students` / `grade_history` / `quarter_grades` / `subscriptions` / `payments` / `promo_codes` — обещано в CLAUDE.md правило #4.

## 7. Фазы

| # | Фаза | Статус | Длительность | Риск |
|---|---|---|---|---|
| **0** | RFC + дизайн-токены + 3 пустых скаффолда + CI lint | **DONE** 2026-05-14 (PR #37) | 1-2 дня | 🟢 |
| **1** | Hugo лендинг + `/docs` на проде (`grades.railtech.uz/`) | **DONE** 2026-05-14 (PR #38 + fix #39) | 3-5 дней | 🟢 |
| **2** | FastAPI + Phone OTP + миграция БД + admin brute-force защита | **NEXT** | 6-8 дней | 🟡 |
| **3** | Next.js портал родителей (`/cabinet/*`, без AI digest) | pending | 7-10 дней | 🟡 |
| **3.5** | AI Digest endpoint + кэш 6h | pending | 2-3 дня | 🟢 |
| **4** | Next.js админка (`/admin/*`) поверх той же базы | pending | 10-14 дней | 🟡 |
| **5** | Бэкапы БД (MinIO S3), мониторинг, SSH-харднинг | pending | 2-3 дня | 🟢 |

**Итого ~6-8 недель календарно.** Подробный статус — см. [`Docs/web-rewrite-status.md`](web-rewrite-status.md).

## 8. Гарантии

Что **не трогаем** в течение всех фаз 0-5:
- `src/main.py`, `src/bot_instance.py`, `src/monitor_engine.py` — бот.
- `webapp/app.py` + `webapp/templates/` + `webapp/static/` — Mini App продолжает работать.
- Существующие таблицы БД, поля, индексы (кроме добавления новых).
- Деплой systemd-юнитов `gradesentinel-bot.service` и `gradesentinel-webapp.service`.

Что **добавляем**:

- Новые директории: `landing/`, `web/`, `api/`, `Docs/web-rewrite-*.md`.
- Новые systemd-юниты: `gradesentinel-api.service` (Phase 2), `gradesentinel-web.service` (Phase 3).
- Caddyfile-правила для **path-based routing на одном домене** (никаких новых поддоменов — см. §4).
- БД-миграции только аддитивные (новые таблицы, новые столбцы).
- Расширения `deploy/deploy-sudoers` для каждого нового systemd unit'а + установка новых путей. **Sudoers не auto-rsync** — после каждого PR с изменением `deploy-sudoers` нужна ручная установка на проде: `install -m 0440 -o root -g root /opt/gradesentinel/deploy/deploy-sudoers /etc/sudoers.d/deploy-runner`.

## 9. Дизайн-система

**Две темы, не унифицируем:**
- `theme-v2.css` (paper editorial, Bricolage Grotesque) → используется в Hugo (лендинг + docs) и в Next.js portal (`(portal)`).
- `theme.css` (corporate teal, Manrope) → используется в Next.js admin (`(admin)`).

Токены экстрактнуты в Phase 0:
- `landing/assets/css/tokens-v2.css` (для Hugo)
- `web/app/globals.css` с `@theme` блоками (Tailwind v4 CSS-first config)

## 10. Структура репо

```
GradeSentinel_bot/
├── src/                    # бот, не трогаем
├── webapp/                 # текущий Flask Mini App, не трогаем
├── frontend/               # 🆕 HTML-мокапы (design source, читаем-только после Phase 0)
├── api/                    # 🆕 FastAPI (Phase 2)
├── landing/                # 🆕 Hugo (Phase 1)
├── web/                    # 🆕 Next.js (Phase 3+4)
├── deploy/                 # systemd, Caddyfile — пополняется по мере фаз
├── docs/                   # 🆕 RFC и проектные документы
└── tests/                  # +api тесты по мере появления endpoints
```

## 11. CI/CD

Phase 0 добавляет проверки, **но не деплоит** новые проекты:
- `landing/`: `hugo --minify` в CI (smoke build).
- `web/`: `npm run build` + `npm run lint` (без деплоя).
- `api/`: `pytest` (когда появятся тесты) + `ruff check` (новый шаг).

Деплой каждой части включается в свою фазу:
- Phase 1: rsync `landing/public/` → `/var/www/gradesentinel-landing/`.
- Phase 2: systemd-юнит `gradesentinel-api.service`.
- Phase 3+4: systemd-юнит `gradesentinel-web.service` (Next.js standalone build).
