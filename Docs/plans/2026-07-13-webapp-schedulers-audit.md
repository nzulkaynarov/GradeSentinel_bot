# Аудит webapp/app.py + schedulers.py + план модуляризации (2026-07-13)

Аудит проведён после снятия блокера «не трогать webapp» (owner, 2026-07-13). Оба файла — прод,
зрелость **4/5** каждый: сильная инженерная база, но god-модули со смешением слоёв и точечными багами.

---

## Часть A — webapp/app.py (1348 строк, зрелость 4/5)

**Критичных нет.** Все роуты за `_authorize_student_access`/`_get_authenticated_user`, запросы
параметризованы `%s`, initData валидируется HMAC + TTL. Находки:

| # | Sev | Место | Суть |
|---|-----|-------|------|
| A-H1 | HIGH | app.py:1187/1246/1256 | `fams[0]` при мульти-семейном ученике недетерминирован (`get_families_for_student` без ORDER BY) → чат-история и запись могут попасть в разные семьи. Фикс: `ORDER BY f.id` + явный «primary family». |
| A-H2 | HIGH | app.py:1001-1086, 1213 | PDF (reportlab, полная история) и синхронный вызов Claude блокируют gunicorn-воркер целиком (8 потоков). 8 одновременных «PDF/AI» → дашборд/health не отвечают. Фикс: таймаут на Anthropic, очередь/отдельный пул для PDF. |
| A-H3 | HIGH | тесты | **`_authorize_student_access` не покрыт интеграционными тестами** — во всех тестах замокан. Security-критичный путь (403 чужой student, 402 без подписки, admin-байпас) не проверяется. Фикс: интеграционные тесты ДО рефакторинга auth. |
| A-M1 | MED | app.py:528-539 | `_grade_date_str` fallback берёт `date_added[:10]` в UTC, а SQL — `+5h` Ташкент → legacy-оценка без grade_date бакетируется в разные дни в БД и Python. |
| A-M2 | MED | app.py:261 | `compute_summary` считает период по `datetime.now()` (серверная TZ), остальной код — Ташкент. Границы периода на день неверны на UTC-хосте. |
| A-M3 | MED | api_dashboard | ~8 отдельных round-trip'ов на один `/api/dashboard`, `get_students_for_parent` дважды. Фикс: per-request кэш (flask.g). |
| A-M4/M5 | MED | — | Нет app-level CSP (только Caddy); module-level `init_db` падает при недоступной БД на boot, `/health` не проверяет БД. |
| A-L1..L7 | LOW | — | Мёртвый `trend_by_day` (deprecated), распухший JSON при days=365, дубли inline-импортов, `_get_bot_username` не кэширует None, orphan-запись в чат-истории при сбое AI. |

**Модуляризация webapp/ (Blueprints):** `create_app()` factory + `auth.py` + `services/{aggregation,dates,dashboard,pdf_service,chat_service,etag}.py` + `routes/{dashboard,pdf,chat,legacy}_bp.py`. Порядок: (1) вынести чистые агрегации + `dates.py` (нулевой риск, уже юнит-тестятся; заодно чинит A-M1/M2 — дата аргументом); (2) **сначала тесты на authorize (A-H3), потом** вынос `auth.py`; (3) factory + Blueprints по одному, начиная с legacy/health; (4) flask.g-кэш (A-M3).

---

## Часть B — schedulers.py (984 строки, зрелость 4/5)

**Ключевое открытие:** weekly AI loop живёт в `handlers/analytics.py` отдельно и является фактически
**вторым, менее зрелым планировщиком** — без общего механизма TZ/маркеров/локов. Отсюда H2/H3.

| # | Sev | Место | Суть |
|---|-----|-------|------|
| B-H1 | HIGH | schedulers.py:303-306 | Групповая очередь НЕ флешится, если в это утро пуста ЛИЧНАЯ очередь (early-return `if not tg_ids: return` до группового прохода). + `group_notification_queue` НЕ чистится по TTL (maintenance чистит только личную). Семья с уведомлениями только в групповой чат → сообщения застревают навсегда + рост таблицы. |
| B-H2 | HIGH | analytics.py:91 | Weekly AI loop: `datetime.now()` (серверная TZ) вместо Ташкента → на UTC-хосте рассылка в 19:00 UTC = **полночь по Ташкенту**, не «Вс 19:00». |
| B-H3 | HIGH | analytics.py:89-100 | Weekly AI reports без персистентного маркера (только in-memory `processed_pairs`) → рестарт в окне Вс 19:00-19:05 = **повторная рассылка всех AI-отчётов всем родителям**. |
| B-M1 | MED | schedulers.py:381-412,456 | Morning/group флеш зовёт `_bot.send_message` напрямую, минуя `Sender`/`send_with_retry` → нет 429-backoff, notify_mode, единой политики. Самый массовый джоб — без retry. |
| B-M2 | MED | schedulers.py:818-826, 941-949 | Proactive/summer: дедуп (`save_alert`/`set_setting`) пишется ПОСЛЕ рассылки всем адресатам, без per-recipient чек-пойнта → дубли всем при крахе в середине (PR-F2 закрыл это для evening/morning/weekly, но не для этих двух). |
| B-M3 | MED | schedulers.py:909 | `summer_sent_{student_id}_{week_tag}` — ISO-неделя в КЛЮЧЕ → строка на (ученик×неделя) навсегда, `settings` тихо растёт. Фикс: ключ без недели (значение=week_tag) или чистка в cleanup. |
| B-L1..L4 | LOW | — | Тонкий margin `minute<5`/`sleep(300)` в weekly loop; quarter/cleanup глотают исключения → маркер ставится при провале; delete+mark вне try; dead-ветка в `Sender.batch_send`. |

**Подтверждено ОК:** DST-риска нет (UZ фиксированный UTC+5); тихие часы wrap 22→07 корректны; все 9 джобов
в `_job_locks`; `_run_job_safe` + outer-try не роняют loop; F2-очереди недеструктивны и корректны (кроме B-H1).

**Модуляризация schedulers/ (пакет):** `loop.py` (расписание декларативной таблицей) + `markers.py` +
`locks.py` + `ai_health.py` + `time_utils.py` (единый `_get_local_now`) + `jobs/*.py` (по джобу) +
`common/{recipients,parents,formatting}.py` (дедуп дублирования между джобами). **Ключевое: перенести
weekly AI loop из `analytics.py` в `jobs/weekly_ai.py` под общий механизм** — это by-design устраняет H2/H3.

---

## Предлагаемая следующая волна PR (для Opus-агентов, после ревью owner)

Реальные баги (B-H1/H2/H3, A-H1) стоит починить ДО или ВМЕСТЕ с модуляризацией. Разбивка:

- **PR-W1 (баги надёжности schedulers, приоритет):** B-H1 (групповой флеш вне early-return + TTL-чистка
  group-очереди), B-H2/H3 (weekly AI loop → Ташкент-время + персистентный маркер; заодно перенос под
  `_run_job_safe`), B-M1 (morning флеш через Sender), B-M2 (per-recipient для proactive/summer), B-M3
  (summer marker). Тесты на каждый.
- **PR-W2 (баги webapp):** A-H1 (ORDER BY / primary family), A-M1/M2 (TZ в датах/summary — дата
  аргументом), A-H2 (таймаут Anthropic + async/очередь PDF). + **A-H3: интеграционные тесты
  `_authorize_student_access`** (обязательно перед любым рефактором auth).
- **PR-W3 (модуляризация webapp):** Blueprints по плану части A (после A-H3-тестов).
- **PR-W4 (модуляризация schedulers):** пакет по плану части B (weekly_ai перенос закрывает H2/H3, если
  не сделано в W1).

Порядок: W1 (баги, отдельные фиксы, легко ревьюить) → W2 (webapp баги + auth-тесты) → W3/W4 (модуляризация,
параллельно, файлы не пересекаются). Всё через PR + зелёный `pytest` + ревью owner (прод-деплой).

## Итог
Оба god-файла зрелые (4/5), но каждый скрывает по 3 реальных HIGH-бага надёжности, которые аудит по коду
не показал бы без глубокого чтения. Модуляризация обоснована и безопасна (обратная совместимость через
re-export/factory), но **сначала — реальные баги и тесты на security-критичный authorize**, потом раскол.
