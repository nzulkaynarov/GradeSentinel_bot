# CONTEXT — snapshot для новой Claude-сессии

Цель: возобновить работу с проектом на другом устройстве без потери контекста.

При старте сессии: `Claude, прочитай CLAUDE.md и Docs/CONTEXT.md`.

**Последнее обновление:** 2026-05-21.

---

## Что это и где

**GradeSentinel** — Telegram-бот мониторинга школьных оценок (Узбекистан) +
Mini App дашборд + admin/landing/portal стек. В продакшене, реальные пользователи.

- **Прод:** Ubuntu 24.04 VPS, bare-metal (без Docker), `grades.railtech.uz` через Caddy.
  IP/детали — в [deploy/README.md](../deploy/README.md), [Docs/web-rewrite-status.md](web-rewrite-status.md).
- **БД:** SQLite + WAL в `/var/lib/gradesentinel/sentinel.db`. Локально `data/sentinel.db`.
- **Деплой:** GitHub Actions self-hosted runner → rsync `/opt/gradesentinel/` → `systemctl restart`. Каждый push в `main` = auto-deploy.
- **Бэкап:** systemd-таймер `gradesentinel-backup.timer` ежедневно 03:30 TST → gzip в `/var/backups/gradesentinel/`, ротация >7 дней через `find -mtime`.

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

**21.05.2026:**
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

**Архитектурные:**
- `handlers/subscription.py` 1318 строк — split отложен сознательно. Платёжные сервисы (CLICK/PAYME) не подключены (владелец выдаёт подписки вручную через `/grant_sub`). Возвращаться когда платежи активны.

**Этапы RFC grade_date — заблокированы на входных от владельца:**
- **Этап 4 `MONOSOURCE_GRADES`:** monitor читает только «Все оценки», 24h shadow mode. Нужно: read-only Sheets share student=2, замер latency «Сегодня» vs «Все оценки», согласие на shadow run. **Дополнительная мотивация после 21.05.2026:** инцидент с cell_reference cross-domain mismatch — это симптом двух конкурирующих writer'ов. PR #42 закрыл симптом defensively (content-key fallback), но архитектурно правильно — оставить ОДИН writer. Пока два writer'а — не доверяй `cell_reference` как identity-ключу.
- **Этап 5:** удалить `_pending_grades` (двухфазное подтверждение). Только после недели стабильной работы этапа 4.

**Уведомления:**
- Очередь для **групповых** уведомлений (сейчас в тихие часы группы просто пропускаются — `_send_to_groups_for_student` early-return). Для четвертных это потенциально нежелательно (большое событие потеряется). Нужна отдельная таблица типа `group_notification_queue` (привязка по `chat_id`, не `telegram_id`) + flush в 07:00.

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
