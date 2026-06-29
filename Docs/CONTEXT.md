# CONTEXT — snapshot для новой Claude-сессии

Цель: возобновить работу с проектом на другом устройстве без потери контекста.

При старте сессии: `Claude, прочитай CLAUDE.md и Docs/CONTEXT.md`.

**Последнее обновление:** 2026-06-29 (миграция на PostgreSQL + аудит/ресерч/план).

> **СОСТОЯНИЕ 2026-06-29 (continue here):**
> - ✅ **Миграция SQLite → PostgreSQL 17 завершена** (PR #91–#94 в `main`): psycopg v3 + пул, схема Alembic, DSN в `DATABASE_URL`. Бот живёт на PG (DB-VPS `10.0.0.2`, WireGuard). Тесты — Docker `postgres:17`, **444 зелёных**. Откат: `sentinel.db.pre-pg-*` (держать ≥недели). Деталь: `Docs/cutover-runbook-2026-06-29.md`.
> - ✅ Пофикшены пост-миграционные date-object баги (панель/AI-тул/analytics) — `src/utils.to_date_str`.
> - ✅ AI Tier 1/2 — уже в проде (этот файл синхронизирован): tool_use, стриминг, память диалога, 👍/👎.
> - 📋 **ГЛАВНОЕ для продолжения:** аудит (техлид+продукт) + ресерч мировой практики → `Docs/audit-and-research-2026-06-29.md`; **приоритизированный план рефакторинга+продукта → `Docs/refactor-and-product-plan-2026-06-29.md`** (P0 надёжность → P1 архитектура+источник данных → P2 рост; стержень — развилка B2C/B2B + уход от скрейпинга Google Sheets).
> - ⏳ ОТ ВЛАДЕЛЬЦА: решение B2C vs B2B; влить открытые ветки `feat/summer-mode` + `fix/weekly-reports-summer-false-alarm`; установить gradesentinel-бэкап на DB-VPS (`deploy/gradesentinel-db-backup.{sh,cron}`).

---

## Что это и где

**GradeSentinel** — Telegram-бот мониторинга школьных оценок (Узбекистан) +
Mini App дашборд + admin/landing/portal стек. В продакшене, реальные пользователи.

- **Прод:** Ubuntu 24.04 VPS, bare-metal (без Docker), `grades.railtech.uz` через Caddy.
  IP/детали — в [deploy/README.md](../deploy/README.md), [Docs/web-rewrite-status.md](web-rewrite-status.md).
- **БД:** PostgreSQL 17 на DB-VPS `10.0.0.2` (WireGuard, `sslmode=require`), psycopg v3 + пул, схема Alembic, DSN в `DATABASE_URL`. Миграция с SQLite 2026-06-29 (`sentinel.db` — откат). Тесты: Docker `postgres:17`.
- **Деплой:** GitHub Actions self-hosted runner → rsync `/opt/gradesentinel/` → `systemctl restart`. Каждый push в `main` = auto-deploy.
- **Бэкап:** PostgreSQL дампится на DB-VPS суточным `pg_dump` (`deploy/gradesentinel-db-backup.sh`, рядом с railtech, 14-дн ротация). Старый sqlite-таймер `gradesentinel-backup.timer` после миграции бэкапит замороженный `sentinel.db` — можно отключить.

Полная архитектура и конвенции — в [CLAUDE.md](../CLAUDE.md).

---

## Архитектура src/db/ (после refactor 14.05.2026)

`database_manager.py` (655 строк, было 1789) — фасад с `init_db()` и re-export'ами.
Реальная имплементация по доменам:

| Модуль | Содержит |
|---|---|
| `src/db/connection.py` | `get_db_connection`, `DB_PATH`, `init_db` |
| `src/db/auth.py` | lookup родителей, профиль, авторизационные предикаты (`can_manage_family`) |
| `src/db/families.py` | семьи, ученики, связи, `get_active_spreadsheets_with_subscription` |
| `src/db/grades.py` | `add_grade`, history, today/yesterday/overnight, quarter_grades |
| `src/db/groups.py` | family_groups (бот в чате семьи) |
| `src/db/invites.py` | family-invite ссылки |
| `src/db/payments.py` | subscriptions, expire-tracking, record_payment |
| `src/db/promo.py` | промокоды CRUD |
| `src/db/maintenance.py` | archive_old_grades, cleanup, delete_family_cascade |
| `src/db/notifications.py` | очередь тихих часов |
| `src/db/settings.py` | k-v store + plans JSON |
| `src/db/state.py` | user_states (FSM), last_menu_id, support_msg_map |
| `src/db/stats.py` | admin/user stats, broadcast helpers |

**Backward compat:** `from src.database_manager import X` продолжает работать для всех вынесенных функций.

---

## Подводные камни (must-read перед коммитом)

1. **Главное: `pyTelegramBotAPI` синхронный, `polling` режим.** Один main thread + scheduler thread (демон). Никаких `async/await`.

2. **`init_db()` запускается ДВАЖДЫ при auto-deploy:** сначала gunicorn-webapp (~160ms раньше), потом bot. Idempotent. Логи миграций (1A, 1C и т.п.) ищи в `journalctl -u gradesentinel-webapp`, не bot.

3. **Cycle protection в database_manager.py:** re-export модулей идёт в порядке `families → payments → auth` потому что auth.py делает обратный re-export `get_families_for_student` / `is_subscription_active`. Меняй порядок осторожно — protected ImportError'ом.

4. **`grade_history.grade_date NOT NULL` + UNIQUE по содержимому** (после этапа 1C RFC, в проде с 14.05.2026 14:45 TST). Старый UNIQUE по `cell_reference` снят. Дубликат ловится в `add_grade` → `IntegrityError` → False.

5. **Ячейка может содержать MULTI-grade `2/5`** (Узбекистан-специфично). `sanitize_cell(raw)` → `list[(value, text)]`. `sanitize_grade(raw)` только для четвертных (single-grade).

6. **Monitor двухфазно подтверждает оценки** (~5 мин задержка): первая поява → pending, вторая та же → notify. Защита от «оценок-призраков» (опечатки учителя). Хранится in-memory `_pending_grades` в `monitor_engine.py`, при рестарте теряется (восстановится 5-10 мин).

7. **`history_importer` НЕ пишет записи с сегодняшней TST датой.** Зона ответственности monitor'а. Защита от race с двухфазным подтверждением.

8. **`register_next_step_handler` БОЛЬШЕ НЕ используется.** Все multi-step flow на `user_states` таблице, диспетчер в `src/handlers/state_flows.py`. Импортируется ПЕРВЫМ в main.py (порядок регистрации message_handler'ов критичен).

9. **Все callback handlers с `family_id` ОБЯЗАНЫ вызывать `_check_family_access`** перед action'ом. `can_manage_family()` = единственный source of truth для admin OR head.

10. **WebApp `/api/dashboard` имеет ETag**: watermark = `MAX(date_added)+COUNT(*)+6h-bucket`. Клиент шлёт If-None-Match, 304 без тела. Cache-Control: private.

11. **Тихие часы 22:00–07:00 Tashkent** (UTC+5). `is_quiet_hours()` в notification_helpers. Сообщения копятся в `notification_queue`, утренний flush через scheduler.

12. **WebApp хост `127.0.0.1:8443`**. Caddy на 443 наружу. НЕ менять на 0.0.0.0 — обходит TLS+логирование.

13. **Schedulers маркеры в `settings`** таблице (переживают рестарт). Используй `_run_job_safe(job, marker, func)`.

14. **`datetime.utcnow()` deprecated в Python 3.12.** Используй `datetime.now(timezone.utc).replace(tzinfo=None)` для сохранения naive UTC семантики. Все callsites в кодбазе уже исправлены 14.05.2026.

15. **Phase 0 web-rewrite (Hugo landing + Next.js portal + FastAPI):** скелеты в `landing/`, `web/`, `api/`. Бот и Mini App не трогаются. См. [web-rewrite-rfc.md](web-rewrite-rfc.md).

---

## Закрытый долг (история — НЕ переделывать)

### Уроки сессии 21.05.2026 (для будущего меня)

Серия мелких UX-багов которые попали в прод — стоит учиться:

1. **`btn_back` несуществующий ключ** (PR_A → fix). Я использовал `t("btn_back", lang)` в `src/handlers/ai_chat.py`, ключа в локалях нет — пользователь увидел «btn_back» вместо текста. **Урок:** перед использованием i18n-ключа — grep что он есть в `src/locales/*.json`. Если ключа нет — добавить ВО ВСЕ три (ru/uz/en), потом использовать. Лучше: добавить в i18n.py runtime warning при miss.

2. **«30 дней оценок» — необоснованное и ложное ограничение** (PR_A → fix). Я по умолчанию поставил `days=30` в `get_grade_history_for_student_all` без оценки ёмкости. Claude Haiku 4.5 = 200K context, год оценок = ~3K токенов, не было причин ограничивать. И главное — текст welcome обещал юзеру именно это, то есть бот **врал**. **Урок:** не указывать конкретные числа в UX-текстах если они не следуют из жёсткого ограничения. Лучше «знаю всю историю» чем «знаю последние N дней» с искусственным N.

3. **`cell_reference` race condition** (инцидент с спамом ночью). Два writer'а с разной семантикой cell_reference — это не «забыл», это архитектурный долг известный по RFC. Но защиты от него (content-based identity) не было — PR #43 закрыл правильно. **Урок:** если RFC говорит «когда-нибудь сделаем», но текущий код может иметь race — добавить **defensive check** сразу, не ждать full refactor.

4. **Morning flush без дедупа** (PR #49). Очередь могла содержать дубли из-за бага в writer, но flush этого не учитывал. **Урок:** при любом «накопить и потом раздать» — дедуп на стороне consumer'а как defense in depth, даже если producer вроде бы не должен дублировать.

Общий паттерн ошибок: **необоснованный optimism про правильность собственного кода + leakage внутренних чисел/деталей в UX**. В будущем — проверять синхронность ключей, не вшивать произвольные лимиты в UI-тексты, добавлять defensive checks параллельно с архитектурными планами.

**Дополнительный урок (PR навигации, ночь 21.05):** При изменении handler'ов / auth / keyboards — холодный mental run по всем ролям × состояниям ПЕРЕД commit. За день сделал 6 PR навигации (#50, #56-60), каждый закрывал жалобу но ломал другой path. Юзер написал «полная каша». Финальный role-toggle PR #60 свёл всё в один coherent flow. Урок зафиксирован в memory `feedback_holistic_check_before_pr.md`. Telegram API quirks тоже всплыли: `KeyboardButton.web_app` НЕ передаёт signed initData (только `InlineKeyboardButton.web_app`) — это нюанс легко наступить, поймал на дашборде 401.

**21.05.2026 — третья (ночная) сессия — навигация для родителей и админа:**
- ✅ **PR_F (#50): conversation-first для родителей.** Reply-keyboard `{💬 Чат, ⚙️ Меню}` + AI-чат default для авторизованного с детьми. Inline user_panel остался для empty state / head без детей. Suggested prompts только в welcome (не повторяются в ответах AI). Onboarding 3 экрана → 1. Эмодзи срезаны в suggested.
- ✅ **PR_G (#58): admin UI cleanup.** Admin panel 8 → 5 кнопок. «📊 Статистика» удалена (дубль title). «💰 Тарифы / 🎁 Промокоды / 📢 Рассылка» → submenu `ap_settings`. Tier 2: admin-as-parent toggle.
- ✅ **PR #57 admin-stuck hotfix.** /start для admin → admin welcome (не AI). Чистит ai_chat_mode state как escape.
- ✅ **PR #59 dashboard 401 hotfix.** `KeyboardButton.web_app` → `InlineKeyboardButton.web_app` (Telegram API quirk). Дашборд переехал в Меню. Admin-блокировщик в AI handler удалён (Tier 2 теперь работает).
- ✅ **PR #60 role-toggle (финальный cleanup).** Видимый toggle через reply-keyboard:
  - Admin-mode: `{🛠 Управление, 👨 Я родитель}`
  - Parent-mode (не-admin): `{💬 Чат, ⚙️ Меню}`
  - Parent-mode (admin): `{💬 Чат, ⚙️ Меню}` + `{🛠 Управление}` (вторая строка)
  - Не-admin никогда не видит admin-кнопок. Все навигационные handler'ы в `src/handlers/navigation.py` с role check.
  - `_build_reply_keyboard(lang, mode, is_admin)` — единая фабрика keyboard'ов.

**21.05.2026 — вторая половина сессии:**
- ✅ **Этап 4 RFC MONOSOURCE_GRADES** (PR #47, `feat/monosource-switch`). Monitor читает «Все оценки!A1:ZZ50» вместо «Сегодня!A1:B50», парсит колонку сегодняшней даты через `_parse_master_sheet_for_date`. Это закрывает архитектурный source race condition'а (два writer'а с разными форматами cell_reference). Hourly `history_importer` оставлен как backup для листов «Неделя!» и «Четверти!». Latency evidence из логов 20.05: 6 мин между [NEW GRADE] в today и +1 в master (включая 1ч интервал importer; реальная master latency меньше). Shadow run (PR #45, ~10 мин) подтвердил `match=N today_only=0 master_only=0`. После переключения первый цикл прода (03:02:51 → 03:02:58) — чисто, без NEW GRADE / PENDING / failures.
- ✅ **Shadow run MONOSOURCE_GRADES** (PR #45 + PR #46 hotfix). Observability перед переключением: monitor читал оба листа, логировал `[SHADOW] match=N today_only=K master_only=M` + `[SHADOW_DIVERGENCE]` построчно. Hotfix #46: нормализация обеих сторон через `sanitize_cell` (иначе заголовок «21 мая чт» давал false-positive divergence).
- ✅ **Очередь для групповых уведомлений** (PR #44, `feat/group-notification-queue`). Таблица `group_notification_queue(chat_id, message_thread_id, message, created_at)` + `queue_group_notification` / `get_and_clear_queued_group_notifications` / `get_all_queued_group_targets` хелперы в `src/db/notifications.py`. `_send_to_groups_for_student` в тихие часы пишет в queue, утренний flush в 07:00 (`_flush_quiet_hours_queue` в schedulers.py) сливает накопленное параллельно с личной morning-сводкой. inline_markup НЕ сохраняем — callback'ы устаревают за ночь.
- ✅ **Content-based identity в monitor** (PR #43, `refactor/monitor-content-based-identity`). Заменили `get_existing_grade(cell_ref)` / `update_grade(cell_ref)` / `_pending_grades[(student, cell_ref)]` на content-based варианты: `get_existing_grade_by_content(student, subject, date)`, `update_grade_by_content(student, subject, date, ...)`, `_pending_grades[(student, subject, grade_date)]`. `cell_reference` стал debug-metadata. Это **правильный фикс** root cause инцидента 21.05 (PR #42 был defensive symptom-fix).

**21.05.2026 — первая половина сессии:**
- ✅ **Инцидент ночного спама в групповой чат** (PR #42, `fix/monitor-cellref-cross-domain-dedup`).
  Корень: `monitor_engine` и `history_importer` пишут одну логическую оценку с разным `cell_reference`
  (`"Сегодня!Алгебра:2026-05-21"` vs `"Все оценки!JC7"`). `get_existing_grade()` ищет только по
  `cell_reference` → промах → `_check_pending_confirmation` подтверждает → уведомление улетает →
  `INSERT` падает по `UNIQUE(student, subject, grade_date, raw_text)` → цикл повторяется
  каждые 5 минут. Группы НЕ уважали тихие часы → весь спам уходил в семейный чат
  (14 уведомлений за ночь до фикса).
  Фикс:
  - `grade_exists_by_content(student, subject, grade_date, raw_text)` в `src/db/grades.py` (тот же
    ключ, что `UNIQUE` constraint).
  - `monitor` использует её как fallback после `get_existing_grade()` → лог `[CROSS-DOMAIN DEDUP]`.
  - `_send_to_groups_for_student` уважает `is_quiet_hours()` (defense in depth).
  - +2 regression test (`test_grade_exists_by_content_basic`, `test_monitor_skips_grade_already_written_by_history_importer`).
  Hotfix на проде до мерджа: `UPDATE grade_history SET cell_reference = 'Сегодня!{subject}:{date}'`
  для 2 сегодняшних записей — остановил спам в течение 1 polling cycle.
- ✅ **`telegram_first_name` в `parents`** для приветствий «Здравствуйте, {имя}!» (PR между #41 и #42).
  До этого приветствие использовало `fio` (часто формальное ФИО или admin-заданное «User»).
  Миграция: idempotent `ALTER TABLE`. Обновление имени — на каждом `/start` (юзер может менять имя
  в Telegram). Fallback: `telegram_first_name` → `fio.split()[0]` → `'друг'`. Использовано в
  `auth_success`, `auth_not_linked_contact`, `onboard_step1`, webapp `/api/dashboard*`.

**14.05.2026 (одна сессия):**
- ✅ Этап 1A–1C RFC grade_date: NOT NULL + UNIQUE по содержимому. 846 рядов мигрированы, 3 коллизии схлопнулись.
- ✅ `database_manager.py` split (1789 → 655, –63%) на 12 доменных модулей в `src/db/`.
- ✅ `register_next_step_handler` → `user_states` миграция, все 11 callsites через `state_flows.py`.
- ✅ Multi-grade `2/5` в `sanitize_cell` + двухфазное pending подтверждение.
- ✅ CI Python 3.10 → 3.12.
- ✅ Бэкап БД systemd-таймером (ежедневно 03:30 TST + ротация 7 дней).
- ✅ Шум network warnings в логах (retry → debug, WARN только на 2+).
- ✅ systemd `StartLimitIntervalSec` warning исправлен (перенесён в `[Unit]`).
- ✅ AI-инсайт в дашборде (`compute_dashboard_insight` + кэш).
- ✅ WebApp кнопка напрямую в user panel (когда `has_kids`).
- ✅ `datetime.utcnow()` → timezone-aware (16 callsites).
- ✅ `/api/dashboard` ETag для 304 Not Modified.

См. подробнее: [rfc-grades-source-of-truth.md](rfc-grades-source-of-truth.md), [web-rewrite-rfc.md](web-rewrite-rfc.md).

---

## Открытый долг

### AI roadmap (после AI-first redesign 21.05.2026)

> ⚠️ **Статусы сверены с КОДОМ 2026-06-29** — бОльшая часть Tier 1/2 УЖЕ в проде
> (раздел раньше числил их «planned» и вводил в заблуждение). ✅ done · 🟡 частично · ⏳ planned.

**Tier 1 — knowledge expansion:** ✅ **ГОТОВ**
- ✅ **PR_E1: FAQ в system prompt** — `_CHAT_SYSTEM_PROMPTS` (analytics_engine.py) содержит блок «ФАКТЫ О БОТЕ» (что делает, тихие часы, команды, как добавить ребёнка, инвайты, подписка/цены, WebApp, языки, тайминги) на ru/uz/en.
- ✅ **PR_E2: Tool use для динамики** — `src/ai_tools.py`: `get_subscription_status` / `get_family_members` / `get_family_pricing` + диспетчер + tool_use→tool_result loop (`answer_parent_question`, `MAX_TOOL_ITERATIONS`). family_id резолвится server-side (нельзя подменить).
- Решение НЕ RAG (в силе): документация компактная (~15-20K токенов), Haiku 200K context — vector DB не оправдана; имеет смысл только при >100K или per-school KB (после контракта).

**Tier 2 — UX и автоматизация:** в основном ГОТОВ
- 🟡 **Streaming** — для Telegram СДЕЛАНО (PR_H4: `stream_callback` + throttled edit в `handlers/ai_chat.py`); SSE для WebApp — нет (webapp заморожен правилом web-rewrite «не трогать»).
- 🟡 **Proactive AI alerts** — `detect_anomalies` (analytics_engine) + «Летний режим» weekly-нэджи есть; отдельного ежедневного anomaly→push ещё нет. **B2B killer feature** для школы.
- ✅ **👍/👎 feedback** — `_build_feedback_markup` + `fb:`-callbacks + `save_feedback` (таблица `ai_chat_feedback`).
- ⏳ **UI history rendering в WebApp** — backend `GET /api/chat/history` есть, `dashboard.html` не рендерит; **блокировано** правилом «webapp/app.py не трогать».
- ✅ **Bot conversation memory** — `handlers/ai_chat.py` подключает `get_recent_family_chat_history` → `answer_parent_question(prev_messages=...)`.

**Tier 3 — после школьного контракта (август-сентябрь 2026):** ⏳ всё planned
- **Voice input** — Telegram voice → Whisper → текст → AI → ответ голосом. UZ-родители выиграют.
- **School-side dashboard + multi-tenancy** — `schools` table, RBAC (admin → teacher → parent), teacher view (класс/поток overview), white-label опционально.
- **RAG для per-school knowledge** — если школа даст свой учебный план / FAQ — тогда vector DB оправдана.

**Архитектурные:**
- `handlers/subscription.py` 1318 строк — split отложен сознательно. Платёжные сервисы (CLICK/PAYME) не подключены (владелец выдаёт подписки вручную через `/grant_sub`). Возвращаться когда платежи активны.

**Этапы RFC grade_date:**
- ~~Этап 4 `MONOSOURCE_GRADES`~~ — ✅ closed 21.05.2026 PR #47.
- **Этап 5:** удалить `_pending_grades` (двухфазное подтверждение). Не раньше 28.05.2026. «Все оценки!» более стабильный лист (учитель не редактирует напрямую), pending-механика возможно избыточна. Решать на основе production-наблюдений: если не было [PENDING] событий за неделю → можно удалять.

**Эксплуатация:**
- SSH-хардненинг VPS (отключить root login + password auth). Высокий риск удалённо.
- Off-site бэкапы (S3/Backblaze sync). Нужны creds.
- `_rate_limit_store` / `_panel_cache` в памяти — допустимо для single-instance. Redis для multi-instance — далеко в будущем.

---

## Ресурсы

- **Прод-логи:** `journalctl -u gradesentinel-bot|gradesentinel-webapp|gradesentinel-backup` на VPS
- **Прод-БД:** `/var/lib/gradesentinel/sentinel.db`, бэкапы в `/var/backups/gradesentinel/`
- **Прод-проект:** `/opt/gradesentinel/`, venv в `/opt/gradesentinel/venv/`
- **Secrets:** `/etc/gradesentinel/bot.env`, `/etc/gradesentinel/credentials.json` (НЕ коммитить)
- **GitHub Secrets:** BOT_TOKEN, ADMIN_ID, ADMIN_GROUP_ID, ANTHROPIC_API_KEY, WEBAPP_URL, GOOGLE_SHEETS_CREDENTIALS, CLICK_PROVIDER_TOKEN, PAYME_PROVIDER_TOKEN, SENTRY_DSN
- **Self-hosted runner:** `vps-prod` на VPS под user `deploy`, sudoers в [deploy/deploy-sudoers](../deploy/deploy-sudoers)
- **Дашборд URL:** `https://grades.railtech.uz/webapp`

---

## How to resume на другом устройстве

1. `git clone git@github.com:nzulkaynarov/GradeSentinel_bot.git` (или `git pull` если уже есть)
2. Открой Claude Code в директории проекта
3. Скажи: `Claude, прочитай CLAUDE.md и Docs/CONTEXT.md, потом git log --oneline -20`
4. Если нужен SSH-доступ к проду: убедись что твой публичный ключ есть в `/root/.ssh/authorized_keys` на VPS. Полные инструкции в memory `reference_prod_ssh.md` (локально, не в репо).
5. Тесты локально: `pip install -r requirements.txt` + `BOT_TOKEN=test ADMIN_ID=0 ADMIN_GROUP_ID=0 pytest tests/ -v`
6. Если хочешь полный memory snapshot — синкни `~/.claude/projects/-Users-{username}-Downloads-IT-projects-GradeSentinel-bot/memory/` через `rsync` с исходного устройства.

**Что НЕ переезжает с памятью:** task list сессии (пустой на новом устройстве — нормально, Claude построит при необходимости), история диалога (обычно session-scoped).
