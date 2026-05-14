# RFC: Web-портал, админка и публичный сайт

**Статус:** Approved (Phase 0 в работе)
**Автор:** @nzulkaynarov + Claude
**Дата:** 2026-05-14
**Связанные документы:**
- `docs/grade-date-refactor-rfc.md` (другой текущий RFC, не пересекается)
- Память: `project_web_portal_2026_05.md`

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

| Хост | Что | Технология |
|---|---|---|
| `grades.railtech.uz/` | Лендинг + `/docs` | Hugo (статика → Caddy) |
| `grades.railtech.uz/webapp/*` | **Telegram Mini App** (НЕ трогаем) | Flask (текущий) |
| `app.grades.railtech.uz` | Веб-портал родителей | Next.js, route group `(portal)` |
| `admin.grades.railtech.uz` | Админ-панель | Тот же Next.js, route group `(admin)` |
| `api.grades.railtech.uz` или `/api/*` | REST API портала+админки | FastAPI |

Решение по `api.*` vs `/api/*` принимаем в Phase 2.

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

| # | Фаза | Длительность | Риск |
|---|---|---|---|
| **0** | RFC + дизайн-токены + 3 пустых скаффолда + CI lint | 1-2 дня | 🟢 |
| **1** | Hugo лендинг + `/docs` на проде (`grades.railtech.uz/`) | 3-5 дней | 🟢 |
| **2** | FastAPI + Phone OTP + миграция БД + admin brute-force защита | 6-8 дней | 🟡 |
| **3** | Next.js портал родителей (без AI digest) | 7-10 дней | 🟡 |
| **3.5** | AI Digest endpoint + кэш 6h | 2-3 дня | 🟢 |
| **4** | Next.js админка поверх той же базы | 10-14 дней | 🟡 |
| **5** | Бэкапы БД, мониторинг, SSH-харднинг | 2-3 дня | 🟢 |

**Итого ~6-8 недель календарно.**

## 8. Гарантии

Что **не трогаем** в течение всех фаз 0-5:
- `src/main.py`, `src/bot_instance.py`, `src/monitor_engine.py` — бот.
- `webapp/app.py` + `webapp/templates/` + `webapp/static/` — Mini App продолжает работать.
- Существующие таблицы БД, поля, индексы (кроме добавления новых).
- Деплой systemd-юнитов `gradesentinel-bot.service` и `gradesentinel-webapp.service`.

Что **добавляем**:
- Новые директории: `landing/`, `web/`, `api/`, `docs/web-rewrite-*.md`.
- Новые systemd-юниты (Phase 2+): `gradesentinel-api.service`.
- Caddyfile-правила для новых поддоменов.
- БД-миграции только аддитивные (новые таблицы, новые столбцы).

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
