# GradeSentinel — План рефакторинга и развития продукта (2026-06-29)

Синтез из `Docs/audit-and-research-2026-06-29.md` (техлид-аудит + продуктовый аудит + ресерч мировой практики: ClassDojo/Remind/Seesaw/TalkingPoints/Bloomz/ParentSquare, OneRoster/Ed-Fi, multi-tenant SaaS). Приоритеты: **P0 = надёжность/безопасность сейчас**, **P1 = архитектура под рост + разворот источника данных**, **P2 = рост продукта**.

---

## 🎯 Стратегический стержень (читать первым)

И продуктовый, и архитектурный ресёрч сошлись в одном: **весь продукт держится на одном хрупком допущении — что родитель сам получит и выдаст боту доступ к Google-таблице, которую ведёт учитель.** Это:
- источник **обрыва активации** (медианный родитель не владеет таблицей — ею владеет учитель/школа; в УЗ дневник чаще `kundalik.com`, а не ad-hoc Google Sheets);
- причина **дрейфа B2C → B2B-школа** (CONTEXT уже намекает: `schools`, RBAC, teacher-view, «B2B killer feature»);
- **экзистенциальный технический риск** (скрейпинг таблиц — слабейшая ступень: нет схемы-контракта, квоты Sheets API с биллингом за перелимит в 2026, нет цепочки согласия данных).

Мировая практика: зрелый edtech **journal/SIS-driven, не teacher-manual** (Bloomz синкает из SIS; стандарт — OneRoster 1.2 / Ed-Fi), **teacher/school-first** для дистрибуции (ClassDojo-виральность), **digest-by-default + тиры срочности**, **zero-friction** (Remind: без аккаунта), **двуязычность** (TalkingPoints).

**➡️ Развилка для владельца (нужно решение перед P1/P2):**
- **B2B-школа** (к чему ведут все улики): единица онбординга = школа. Учитель/класс подключает источник ОДИН раз, родители входят по инвайту (инфра инвайтов уже есть — это самый чистый путь). Контракт и интеграция живут на уровне школы; биллинг может оставаться по-семейный внутри.
- **B2C-self-serve**: тогда ОБЯЗАТЕЛЬНО убрать зависимость от учителя — коннектор `kundalik.com` / Google Classroom, иначе self-serve будет стопориться на шаге «вставь ссылку».

**Хватит straddling** (ручной `/grant_sub` + ручное подтверждение карты — это для пилота, не для масштаба; freemium-утечка ниже — неверная экономика для B2B-контракта). Выровнять биллинг + онбординг + интеграцию под ОДНУ модель.

---

## P0 — Надёжность и безопасность (сделать первым; дёшево, снимает реальный риск)

| # | Задача | Почему | Где |
|---|---|---|---|
| 0.1 | **Включить off-site зашифрованный бэкап + ежемесячный restore-drill** | `deploy/offsite-backup.sh` сейчас `exit 0` пока не настроен → потеря DB-VPS = полная потеря РЕАЛЬНОГО PII (телефоны, имена детей, оценки). Бэкапы лежат на той же машине. Непроверенный бэкап = надежда, не бэкап. | `deploy/offsite-backup.sh` (rclone **crypt**), restore в scratch-БД + сверка COUNT |
| 0.2 | **Миграции — явный одиночный gated-шаг в деплое + pre-dump** | `init_db()→apply_migrations()` зовётся И ботом, И webapp при одновременном рестарте → конкурентный `alembic upgrade`; плохая миграция уезжает в прод без ревью/бэкапа | `deploy.yml`, убрать `apply_migrations` из `webapp/app.py:67`; `pg_dump` перед `alembic upgrade head` |
| 0.3 | **Деградация при недоступности БД** | новый сетевой хоп (WireGuard→DB-VPS) — блипа не было при SQLite; сейчас хендлер виснет ~20с и падает, без friendly-UX | `src/db/pg.py`: retry-once + дружелюбная ошибка + admin-alert (паттерн `_track_ai_outcome`); снизить interactive timeout |
| 0.4 | **Watchdog «последний успешный цикл монитора»** | heartbeat ловит смерть треда, но НЕ заклинивший цикл при живом процессе → тихая деградация уведомлений | писать timestamp последнего успешного цикла в БД, алерт при устаревании; `monitor_engine.py`/`schedulers.py` |
| 0.5 | **Тесты на платежи + webapp authz/IDOR + initData freshness** | деньги + авторизация (`_check_user_can_pay_for_family`) с НУЛЁМ тестов; `validate_init_data` не проверяет `auth_date` → captured initData реплеится вечно | `handlers/subscription.py`, `webapp/app.py:79,128` |
| 0.6 | мелочи: убрать stale `DATABASE_PATH` env из `gradesentinel-bot.service`; отключить старый sqlite `gradesentinel-backup.timer` (бэкапит замороженный файл); проверить `MemoryMax=400M` под большой фан-аут | гигиена/OOM-риск | systemd units |

## P1 — Архитектура под рост + разворот источника данных

| # | Задача | Почему | Где |
|---|---|---|---|
| 1.1 | **Вынести in-memory state в Postgres** (`_pending_grades`, rate-limit, panel-cache, failure-counts) | 🔴 **связывающее ограничение всего масштабирования**: сейчас 2 инстанса невозможны (двойные уведомления), рестарт теряет pending. Таблица `pending_grades` (content-key уже = DB identity) → переживает рестарт + разблокирует multi-instance | `monitor_engine.py:63`, `rate_limiter.py`, `main.py:23` |
| 1.2 | **Pluggable ingestion-adapter layer** (канонная модель ~OneRoster Gradebook/Rostering) | убирает экзистенциальный single-point-of-failure скрейпинга; «говорим на стандарте K-12» = sales-аргумент. Sheets → один адаптер; добавить (а) структурный GS-шаблон с залоченными заголовками+валидацией (низкофрикционный онбординг), (б) путь к `kundalik`/OneRoster-коннектору. **Virtual-webhook**: один tenant-aware poller с адаптивным расписанием → diff → внутренний event `grade.changed`; фан-аут подписывается | новый `src/ingestion/`; `monitor_engine` рефактор |
| 1.3 | **Сервисный слой `src/services/`** (payments/subscriptions/grades) | бизнес-логика и сайд-эффекты сейчас inline в Telegram-callback'ах (`record_payment` в `callback_admin_confirm_card`) → нетестируемо, не переиспользуемо из webapp. Хендлеры → тонкие (authz→validate→service→reply) | extract из `handlers/*` |
| 1.4 | **Разбить god-модули** | `subscription.py` 1342 (31 `@bot`-хендлер), `main.py` 1056 (auth/panel/onboarding/router — 4 концерна), `analytics_engine.py` 1018 (5 AI-фич+кэш), `webapp/app.py` 1326 | по швам из аудита |
| 1.5 | **Централизовать Tashkent-tz + убрать shadowing** | `interval '5 hours'` размазан по многим SQL-строкам (1 пропуск = оценки на день мимо); `database_manager` переопределяет одноимённые re-export-функции (тихий footgun) | один tz-helper/SQL-фрагмент; функции дат → жить только в `db/grades.py` |
| 1.6 | сузить высокочастотные `except Exception` до конкретных; deploy — manual-approval gate на `main` + (опц.) ephemeral cloud-runner вместо self-hosted на проде (SPOF + blast-radius secrets) | надёжность/безопасность | `deploy.yml`, hot-path хендлеры |

## P2 — Рост продукта (после развилки B2C/B2B)

| # | Задача | Почему | Где |
|---|---|---|---|
| 2.1 | **Починить обрыв активации** | teacher-side flow подключения таблицы (шаблон + 4-шаговая инструкция «добавь этот email редактором», deep-link для пересылки учителю); **инструментировать воронку** — ratio `add_child` success vs `child_no_access_error` = главная невидимая метрика; среднесрок — импортер `kundalik`/Classroom | `handlers/family.py` |
| 2.2 | **Монетизация** | включить **Click/Payme дефолтом** (рельсы уже есть), демоутить ручную карту; **реальный 14-дн trial** (`subscription_end = now+14d` на 1-м ребёнке — тогда уже построенные expiry-reminder'ы есть на что срабатывать); решить **freemium-утечку** (`subscription_end IS NULL` = мониторинг бесплатный навсегда — `db/families.py:51`): осознанно freemium ИЛИ trial-expiry | `handlers/subscription.py`, `db/families.py:51`, `db/payments.py` |
| 2.3 | **Уведомления attention-first** | 3 тира: **instant** (низкая оценка/пропуск/сообщение учителя) → **daily digest по умолчанию** (рутина) → **weekly trend**; per-child + per-event opt-out; **настраиваемые** тихие часы (сейчас хардкод 22–07); промоутить anomaly-алерты даже в summary-режиме. Снижает «тонут в пятёрках» → усталость → отток | `schedulers.py`, `notification_helpers.py`, `up_notifications` |
| 2.4 | **Если B2B:** школа = единица онбординга (учитель подключает класс 1 раз, родители по инвайту), teacher class-overview, **multi-tenancy** (bridge-модель: pooled+`tenant_id` везде, КАЖДОЕ authz-решение scoped к tenant — picker = подсказка, не источник истины), per-school конфиг/white-label, in-country data-localization | новый `schools`/RBAC слой |
| 2.5 | мелкие продуктовые: «обновлено школой: <время>» в дашборде (stale-таблица ≠ «бот сломан»); family-comparison как first-class вкладка; «pause over summer» (freeze подписки) | доверие/ретеншн | webapp, `schedulers` |

---

## Рекомендованный порядок
1. **P0 целиком** (1–2 недели; снимает риск потери данных + небезопасный деплой + дыры в тестах денег/authz). Не требует продуктовых решений.
2. **Развилка B2C/B2B** — решение владельца. От него зависит форма P1.2/P2.
3. **P1.1 (externalize state)** — самый важный архитектурный шаг, разблокирует всё остальное.
4. **P1.2 (ingestion adapter)** — параллельно с развилкой; даже в B2C нужен.
5. P1.3–1.5 (сервис-слой + god-split + tz) — снижают стоимость всех дальнейших фич.
6. **P2** — по выбранной модели.

## Что НЕ ломать (из железных правил web-rewrite RFC)
`src/main.py`/`bot_instance.py`/`monitor_engine.py` и `webapp/app.py` + Mini App — «untouchable» в контексте web-rewrite (рефактор P1.1/1.3/1.4 их затронет → делать осознанно, с тестами, не в рамках web-rewrite). Бот/Mini App работают — любой их рефактор только под зелёные тесты (444 на PG) + eyes-on.

## Открытые ветки на мердж (не относится к плану, но в очереди)
`feat/summer-mode` (этап 2), `fix/weekly-reports-summer-false-alarm` — готовы с тестами.
