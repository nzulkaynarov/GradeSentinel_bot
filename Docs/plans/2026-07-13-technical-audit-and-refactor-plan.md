# Технический аудит и план исправлений / рефакторинга — 2026-07-13

---
## СТАТУС ИСПОЛНЕНИЯ (обновлено 2026-07-13, Opus-агенты)

Все PR реализованы в изолированных worktree, коммиты **локальные, НЕ запушены, main не тронут**.
Базовый main: `814143a`. Ветки/статусы:

| PR | Ветка | HEAD | База | Тесты | Статус |
|----|-------|------|------|-------|--------|
| PR-A | `fix/pg-date-consumers-followup` | 70255b4 | main | 446 ✅ | Готов к ревью |
| PR-B | `fix/payment-flow-atomicity` | ef1b89b | PR-A | 456 ✅ | Готов (миграция 0002) |
| PR-C | `fix/security-idor-initdata-ttl` | fb421ee | PR-B | 471 ✅ | Готов |
| PR-J | `fix/ux-nav-cleanup` | 9de6b5c | PR-C | 483 ✅ | Готов (вершина стека A→B→C→J) |
| PR-D | `chore/ci-gate-atomic-deploy-lock` | b83f39b | main | YAML/bash ✅ | Готов (см. ручные шаги) |
| PR-E | `fix/offsite-pg-backup` | 4e211a7 | main | static ✅ | Готов (нужен test-restore) |
| PR-G | `chore/db-pool-sizing` | bc6439e | main | 448 ✅ | Готов |
| PR-H | `fix/tashkent-academic-year` | 0cab672 | main | 452 ✅ | Готов |
| PR-I | `fix/ai-chat-resilience` | 82a5ca2 | PR-A | 454 ✅ | Готов |

**Не запускались (требуют решения владельца):**
- **PR-F** (атомарность уведомлений B8/B9/B10/B11/B14) — дизайн-выбор inline-send vs persistent-outbox
  в инцидент-опасной зоне; нужен RFC/решение владельца перед реализацией.
- **PR-K** (раскол файлов-гигантов) — отложен: конфликтовал бы со всеми pending-ветками; делать после merge.
- **PR-L** (синхронизация CLAUDE.md/Docs) — отложен: делать после того, как решено, что мержится.

**Порядок merge (рекомендация):** PR-D + PR-E первыми (инфра, снимают системный риск) → стек
PR-A → PR-B → PR-C → PR-J (линейный, конфликтов внутри нет) → независимые PR-G, PR-H, PR-I (ребейз на
свежий main).

**Известные merge-точки (тривиальные):**
- PR-G и PR-H обе трогают `monitor_engine.py` (G переписал fetch-фазу, H добавил `context=` в call-site) — мелкий конфликт.
- PR-A и PR-I обе трогают `ai_chat.py` (A: sort-key `to_date_str` — I основан на A, конфликта нет).
- PR-C правил тест PR-B (`test_payment_flow.py`, добавил `link_parent_to_family`) — within-stack, ок.

**Требует проверки на проде ПЕРЕД merge PR-B:** нет ли уже задвоенных non-NULL `telegram_payment_charge_id`
в `payments` — иначе `create_unique_constraint` в миграции 0002 упадёт на upgrade
(`SELECT telegram_payment_charge_id, count(*) FROM payments WHERE telegram_payment_charge_id IS NOT NULL GROUP BY 1 HAVING count(*)>1`).

**Ручные шаги владельца (вне кода):** PR-D — branch protection «require Tests» + установить новый
`deploy-sudoers` на VPS ДО merge (иначе первый деплой по новой схеме упрётся в старые sudo-правила);
PR-E — настроить rclone remote на DB-VPS + провести test-restore + вычистить осиротевшие SQLite-юниты
на app-VPS. Полные списки — в отчётах и деталях пакетов ниже.
---


**Метод:** 6 параллельных аудиторов (Opus) по измерениям: ядро/handlers, мониторинг/уведомления,
слой БД после PG-миграции, безопасность, подписки+AI, тесты/CI/deploy. Находки сведены,
дедуплицированы, критичные перепроверены чтением кода.

**Контекст-константы (для всех исполнителей — не нарушать):** PostgreSQL 17 / psycopg v3, плейсхолдеры
`%s`; `get_db_connection()` — context manager (commit на выходе, rollback при exception); Row —
Mapping (`row['c']` и `row[0]`); PG отдаёт **datetime/date/bool/float объекты, не строки**; бот
СИНХРОННЫЙ (pyTelegramBotAPI polling); i18n — 3 локали синхронны (`t()` + sync-тесты); push в main =
авто-деплой на прод; `webapp/app.py` не трогать без явного разрешения (правило web-rewrite).

---

## Сводная таблица находок

| # | Severity | Область | Файл | Суть | Статус проверки |
|---|----------|---------|------|------|-----------------|
| B1 | 🔴 CRITICAL | payments | subscription.py:604-655 | `successful_payment` неатомарен + без try/except: деньги списаны → подписка не продлена; `parent_id=None`→IntegrityError; нет длины payload; нет refund | confirmed |
| B2 | 🔴 CRITICAL | infra | deploy.yml / tests.yml | Деплой НЕ гейтится на тесты — идут параллельно; красные тесты не блокируют выкатку | confirmed |
| B3 | 🔴 CRITICAL | infra | gradesentinel-backup.service / offsite-backup.sh | Бэкап-цепочка мертва после PG: юнит бэкапит несуществующий SQLite, offsite синкает пустую маску; реальные pg_dump только на DB-VPS (нет off-site) | confirmed |
| B4 | 🟠 HIGH | db/UX | subscription.py:108,693,954,1330; family.py:96 | `[:10]` на datetime→TypeError: краш экрана `/subscription`, `/grant_sub`, `/cancel_sub`, меню семьи у любого платящего | confirmed (прочитано) |
| B5 | 🟠 HIGH | db/UX | webapp/pdf_export.py:365,458-462 | Тот же `[:10]`/смешанные sort-key типы — PDF-экспорт ломается на строках с `grade_date IS NULL` | plausible |
| B6 | 🟠 HIGH | payments | subscription.py:893-915, promo.py:76-84 | Promo over-redemption: `extend_subscription` до `use_promo_code`, возврат игнорируется; race при max_uses=1; нет per-family дедупа | confirmed |
| B7 | 🟠 HIGH | payments | payments.py:38-66 | `extend_subscription` else-ветка (первая подписка из NULL) — слепая перезапись, не аддитивна: 2 оплаты → 1 месяц | plausible |
| B8 | 🟠 HIGH | reliability | monitor_engine.py:294-500 | Write-then-notify неатомарен: grade в БД, но exception в фазе send → батч уведомлений теряется навсегда (след. цикл видит «не изменилось») | plausible |
| B9 | 🟠 HIGH | reliability | schedulers.py:288,390 | Деструктивное чтение очереди тихих часов: `get_and_clear` коммитит до отправки; сбой отправки → потеря (групповая очередь не реконструируется) | plausible |
| B10 | 🟠 HIGH | reliability | schedulers.py:179-198,268,409,607 | Нет per-recipient идемпотентности в evening/morning/weekly: частичный сбой → маркер не ставится → двойная рассылка части родителей | plausible |
| S1 | 🟠 HIGH | security | subscription.py:881-890 | IDOR: `callback_promo_apply` не проверяет членство в семье → продление чужой семьи crafted callback | confirmed |
| S2 | 🟡 MEDIUM | security | webapp/app.py:79-110 | initData без проверки `auth_date` → бессрочный replay доступа к детской PII при утечке ссылки | confirmed |
| B11 | 🟡 MEDIUM | db | grades.py:106-118 | `update_grade_by_content` без `raw_text` в WHERE → при мульти-строках на день UNIQUE-abort → потеря батча (связь с B8) | plausible |
| B12 | 🟡 MEDIUM | db | config.py:35 vs pg.py:117 | Пул max_size=5 < 8 fetch-воркеров, которые ходят в БД (display_name/failure) → PoolTimeout под нагрузкой | plausible |
| B13 | 🟡 MEDIUM | reliability | history_importer.py:50,88 | Год учебного периода из `datetime.now()` (локальное TZ сервера, не Ташкент) → на границе месяца/года дата парсится в ±1 год → тихий пропуск оценки; `CURRENT_YEAR` мёртв | plausible |
| B14 | 🟡 MEDIUM | reliability | schedulers.py:286-375 | Morning-flush: при наличии overnight-оценок не-оценочные queued (proactive) молча отбрасываются | plausible |
| B15 | 🟡 MEDIUM | ai | analytics_engine.py:751-788 | messages_array может дать 2 user подряд (orphan) или leading-assistant → Anthropic 400 → устойчивый отказ чата до clear_history | plausible |
| B16 | 🟡 MEDIUM | ai | ai_chat.py:230-258 | Нет rate-limit на AI-вопросы → неограниченный расход токенов (до 6 вызовов/вопрос, контекст пересылается целиком, без prompt caching) | confirmed |
| B17 | 🟡 MEDIUM | ux | main.py:878 vs ai_chat.py:230 | `handle_menu_buttons` регистрируется после ai_chat → кнопки `📈 Оценки`/`📱 Меню` из `get_main_menu` уходят в AI как вопрос | plausible |
| B18 | 🟡 MEDIUM | infra | requirements.txt | Зависимости диапазонами без lock; деплой ставит свежий `pip install` → в прод приезжает непроверенный minor (особенно `anthropic<1.0`) | confirmed |
| B19 | 🟡 MEDIUM | infra | deploy.yml:53-57 | Неатомарный rsync `--delete` в живой каталог; прерывание → полусостояние → рестарт-луп без отката | confirmed |
| B20 | 🟡 MEDIUM | ai | analytics_engine.py:470 | `_CHAT_MAX_TOKENS=600` молча обрезает «подробный разбор» (stop_reason=max_tokens без индикации), обрезок сохраняется в историю | confirmed |
| B21 | 🟡 MEDIUM | ai | analytics_engine.py:470-492 | Family-scoped контекст режется до 600 суммарно по всем детям → у семьи с 4-5 детьми старые оценки части детей теряются | plausible |
| —  | 🟢 LOW | разное | — | Спиннеры без answer_callback_query (family.py:219,529); мёртвые хендлеры (main.py:671,683,695); сырой int() без _parse_int_args (family.py:725,752); admin-reply не-текстом→«None» (communication.py:148); race на BUTTON_ACTIONS при смене языка (settings.py:40); group_cancel двойной answer (group.py:123); DEFAULT_PLANS мутируется (subscription.py:1112); ФИО в логах (history_importer.py:411); rate-limit на promo/deeplink (S3); singleton Anthropic без лока; вестигиальные SQLite ENV/комментарии в systemd; кэш инсайтов без инвалидации/чистки; `?` в backfill_grade_date.py:160; seed_db.py сломан | mixed |

**Опровергнутые гипотезы (в план НЕ идут):** A1:ZZ50 не ограничивает число детей (строки=предметы);
`src/db/*` отмигрирован чисто (type-mismatch только у потребителей-хендлеров и pdf_export);
aborted-transaction каскада нет (write-path на ON CONFLICT); AI prompt-injection не даёт кросс-семейную
утечку (tools zero-arg, family_id server-side); кросс-семейная утечка контекста в ai_pick_fam не найдена;
grade_value real→float (не Decimal, round работает).

---

## План: PR-пакеты для Opus-агентов

Пакеты упорядочены по приоритету. Каждый — один PR, одна ветка, изолированные файлы (минимум пересечений
для параллельного исполнения). Внутри пакета — задачи, критерии приёмки, обязательные тесты. Каждый пакет
пишется как задание отдельному Opus-агенту: включать контекст-константы (шапка) в промпт.

### Волна 0 — прод-инциденты (последовательно, каждый = отдельный PR, ревью владельцем перед merge)

#### PR-A · «PG date consumers: остатки после #93» [B4, B5, +LOW `?`/ai_chat sort]
- **Файлы:** `src/handlers/subscription.py` (108,693,954,1330), `src/handlers/family.py:96`,
  `webapp/pdf_export.py` (365,458-462), `src/handlers/ai_chat.py:325`, `scripts/backfill_grade_date.py:160`.
- **Задачи:** заменить все `[:10]`-срезы и инлайн `(...)[:10]` на `to_date_str()` (`src/utils.py`, уже
  есть) или общий `_grade_date_str`; в pdf_export вынести единый нормализатор для ячеек И sort-key;
  `?`→`%s` в backfill.
- **⚠️ webapp/pdf_export.py входит в правило «webapp не трогать» — получить явное ОК владельца перед PR-A,
  либо вынести B5 в отдельный PR-A2 под его контролем.**
- **Тесты (обязательно):** расширить `tests/test_pg_date_consumers.py` — покрыть `cmd_subscription` при
  активной подписке, `/grant_sub`-листинг, `/cancel_sub`, `family.py` меню (мокнуть `bot`, проверить,
  что не падает и дата отрендерена). Для pdf — строка с `grade_date=None`.
- **Приёмка:** экран `/subscription` у семьи с активной подпиской рендерится без TypeError; PDF с
  archived-оценкой (grade_date NULL) генерится; вся суита зелёная.

#### PR-B · «Payment flow: атомарность и устойчивость» [B1, B6, B7]
- **Файлы:** `src/handlers/subscription.py` (successful_payment 604-655, `_apply_promo_to_family`
  893-915), `src/db/payments.py` (`extend_subscription` 38-66), `src/db/promo.py`.
- **Задачи:** (1) B1 — обернуть парсинг+`record_payment`+`extend_subscription` в одну транзакцию;
  length-check payload (3 части, int-парсинг в try); guard `parent_id is None` (fallback — создать/найти
  parent или зафиксировать платёж с корректной ссылкой); при любом исключении — уведомить админа; для
  XTR добавить путь `refundStarPayment`. Рассмотреть UNIQUE на `payments.telegram_payment_charge_id` для
  идемпотентности (миграция — см. PR-G). (2) B6/B7 — `use_promo_code` guard ДО `extend_subscription`,
  проверять возврат; `extend_subscription` — единый аддитивный UPDATE `COALESCE(subscription_end, now())`
  без SELECT-ветвления.
- **Тесты:** новый `tests/test_payment_flow.py` — successful_payment→начисление (мок bot); parent_id=None
  не теряет деньги молча; promo-редемпшн начисляет ровно раз; конкурентное применение max_uses=1 (симуляция
  через два вызова); первая подписка из NULL при двух вызовах аддитивна.
- **Приёмка:** нет пути «деньги списаны, подписка не продлена, админ не уведомлён»; промокод нельзя
  отредемить сверх max_uses; повторная доставка charge_id не двоит подписку.
- **Зависимость:** UNIQUE-констрейнт — через миграцию из PR-G (или включить сюда одну ревизию).

#### PR-C · «Security: IDOR промокода + initData TTL» [S1, S2, S3-часть]
- **Файлы:** `src/handlers/subscription.py:881-890` (+ `_apply_promo_to_family` guard), `webapp/app.py:79-110`,
  `src/handlers/state_flows.py`/`subscription.py` (rate-limit ввода промо).
- **Задачи:** S1 — добавить `_check_user_can_pay_for_family`/`is_member_of_family` в начале
  `callback_promo_apply` (тот же гейт, что у sub_pay_). S2 — после проверки hash сверять `auth_date`
  (TTL 24ч, `raise ValueError("initData expired")`). S3 — `is_rate_limited(user_id)` в `_process_promo_code`
  и перед диспатчем deeplink в `send_welcome`.
- **⚠️ webapp/app.py — правило «не трогать»: S2 согласовать с владельцем отдельно** (можно вынести в PR-C2).
- **Тесты:** IDOR — применение промо к чужой family_id отклоняется; `validate_init_data` — happy path,
  неверный hash, отсутствие hash, просроченный auth_date (это заодно закрывает пробел H2 из инфра-аудита —
  HMAC вообще без тестов).
- **Приёмка:** crafted `sub_promo_apply_<чужой fid>` не продлевает чужую подписку; протухший initData → 401.

#### PR-D · «CI/CD: гейт деплоя на тесты + атомарный деплой + lock» [B2, B18, B19]
- **Файлы:** `.github/workflows/deploy.yml`, `tests.yml`, `requirements.txt` (+ `requirements.lock`),
  `deploy/*.sh`.
- **Задачи:** B2 — deploy на `workflow_run: {workflows:[Tests], types:[completed]}` с проверкой
  `conclusion==success` (или один pipeline `needs: pytest`) + branch protection (ручной шаг владельца).
  B19 — rsync в `/opt/gradesentinel.new` → `pip install` → smoke → атомарный symlink swap; хранить прошлый
  release для отката. B18 — `pip-compile`→`requirements.lock` с хешами, деплой строго из lock, CI на тех же
  пинах.
- **Тесты:** N/A (инфра) — проверка на тестовом push в ветку; workflow lint.
- **Приёмка:** красные тесты блокируют деплой; прерванный rsync не роняет прод (симулировать); прод==CI по
  версиям зависимостей.

#### PR-E · «Backup: реальный off-site pg_dump» [B3]
- **Файлы:** `deploy/gradesentinel-backup.service` (переписать под `pg_dump -Fc` или снять таймер),
  `deploy/offsite-backup.sh` (маска `*.dump` из каталога PG-дампов), `deploy/gradesentinel-db-backup.sh`,
  вестигиальные `DATABASE_PATH` из `*.service` (L1).
- **Задачи:** off-site rclone должен синкать реальные `pg_dump`-дампы (перенести на DB-VPS с указанием на
  `/var/backups/railtech-db/gradesentinel_*.dump`, либо тянуть дампы на app-VPS перед синком); удалить/переписать
  мёртвый SQLite-бэкап; провести **test-restore** (обязательный шаг в PR-описании).
- **Приёмка:** свежий `pg_dump` уходит в облако ежедневно; выполнен и задокументирован restore из off-site копии.

### Волна 1 — надёжность value-потока (после волны 0)

#### PR-F · «Мониторинг: атомарность уведомлений» [B8, B9, B10, B11, B14]
- **Файлы:** `src/monitor_engine.py`, `src/schedulers.py`, `src/db/grades.py`, `src/db/notifications.py`.
- **Задачи:** B8 — слать уведомление сразу после успешной записи по каждому студенту (внутри пер-студенческого
  блока), ИЛИ persistent-outbox (флаг `notified` в grade_history / отдельная таблица) с добивкой на след.
  цикле. B11 — добавить id-строки в WHERE `update_grade_by_content` (get_existing уже читает LIMIT 1). B9 —
  удалять из очереди только после подтверждённой отправки (per-message) либо помечать «в обработке». B10 —
  per-recipient маркер/чек-поинт в evening/morning/weekly. B14 — всегда дошлывать не-реконструируемые
  queued_messages.
- **Тесты:** kill между записью grade и send → уведомление не теряется/дошлётся; сбой send после
  get_and_clear не теряет очередь; частичный сбой рассылки не двоит; morning со смешанной очередью
  (оценка+proactive) не теряет proactive; update_grade при мульти-строках не падает UNIQUE.
- **Приёмка:** нет пути «оценка записана, родитель не уведомлён и не будет»; нет двойных рассылок.
- **Примечание:** это самая деликатная зона (инциденты 21.05). Дизайн outbox согласовать с владельцем перед
  реализацией — возможно отдельным RFC.

#### PR-G · «БД: пул, миграция констрейнтов, атомарность промо/платежей» [B12, +миграция для B1/B6]
- **Файлы:** `src/config.py`/`src/db/pg.py` (пул), `migrations/versions/` (новая ревизия
  `down_revision="0001_baseline"`), опц. `migrations/env.py` (`target_metadata` если решим включить
  autogenerate).
- **Задачи:** B12 — `DB_POOL_MAX >= FETCH_WORKERS + запас` (напр. 12), ИЛИ вынести запись display_name из
  fetch-воркеров в последовательную фазу. Миграция: UNIQUE `payments.telegram_payment_charge_id` (для B1),
  опц. таблица `promo_redemptions(code, family_id UNIQUE)` (для B6 per-family дедупа).
- **Тесты:** миграция upgrade/downgrade применяется на чистой тестовой БД; пул не таймаутит при 8 воркерах +
  main + scheduler (интеграционный).
- **Приёмка:** нет PoolTimeout под нагрузкой; charge_id уникален на уровне БД.

#### PR-H · «Дата учебного года по Ташкенту» [B13]
- **Файлы:** `src/history_importer.py` (36-99).
- **Задачи:** считать «сейчас» через ташкентскую логику (как `_tashkent_today_date`), удалить мёртвый
  `CURRENT_YEAR`; алерт/лог когда лист получен, но ни одна колонка-дата не распозналась при непустой шапке.
- **Тесты:** дата на границе 31 дек/1 янв и 31 авг/1 сен парсится в корректный учебный год; неизвестный
  формат даты логируется.

### Волна 2 — AI и UX-полировка

#### PR-I · «AI-чат: устойчивость и бюджет» [B15, B16, B20, B21]
- **Файлы:** `src/handlers/ai_chat.py`, `src/analytics_engine.py`, `src/ai_tools.py`.
- **Задачи:** B15 — санитайзить messages_array (отбрасывать ведущие assistant, схлопывать хвостовой
  orphan-user), ИЛИ сохранять user-сообщение только после успешного ответа. B16 — `is_rate_limited` в
  `_on_chat_message`; добавить `cache_control` на system+grade-контекст (prompt caching). B20 — поднять
  лимит/пометка при `stop_reason=='max_tokens'`. B21 — cap контекста на ребёнка, а не суммарный 600.
- **Тесты:** orphan-user в истории не ломает следующий вопрос; rate-limit срабатывает; family с 5 детьми не
  теряет данные ребёнка. Заодно закрыть пробел: `state_flows`/`navigation` вообще без тестов.
- **Примечание:** обновить память [[ai-features]] по факту (модель haiku-4-5, prompt caching).

#### PR-J · «UX: навигация, спиннеры, мёртвый код» [B17, LOW-набор]
- **Файлы:** `src/main.py` (порядок регистрации / мёртвые хендлеры 671,683,695), `src/handlers/family.py`
  (219,529 answer_callback_query; 725,752 _parse_int_args), `src/handlers/communication.py:148`,
  `src/handlers/settings.py:40` (атомарная замена BUTTON_ACTIONS), `src/handlers/group.py:123`,
  `src/handlers/subscription.py:1112` (deepcopy DEFAULT_PLANS), `src/history_importer.py:411` (PII в логах),
  `seed_db.py` (fix/удалить), `src/notifications/types.py` (комментарии).
- **Задачи:** B17 — зарегистрировать `handle_menu_buttons` до ai_chat ИЛИ исключать `m.text in BUTTON_ACTIONS`
  в ai_chat-хендлере. Плюс весь LOW-набор.
- **Тесты:** кнопка `📈 Оценки` в ai_chat_mode открывает оценки, а не уходит в AI; add_child/add_member не
  зависают спиннером.

### Волна 3 — рефакторинг (после стабилизации, необязательно параллельно)

#### PR-K · «Раскол файлов-гигантов» [B22 tech-debt]
- `subscription.py` 1342 (отложено до активации платежей — делать вместе с PR-B/подключением Click/Payme),
  `main.py` 1056 (роутинг → подмодули), `analytics_engine.py` 1018 (промпты → отдельный модуль),
  `schedulers.py` 912. **Только после того как payment/monitor тесты дадут страховку.** Не блокирует ничего.

#### PR-L · «Синхронизация документации» [doc-drift]
- `CLAUDE.md`: убрать SQLite-реликты (§8 `?`, PRAGMA, BEGIN IMMEDIATE), обновить line-counts, закрыть
  «долг» register_next_step_handler, дополнить список scheduler-джобов, добавить в структуру api/frontend/web/
  landing/notifications/. Пометить Docs/{Terms_of_reference,Project_overview,ARCHITECTURE,API_INTEGRATIONS}.md
  как исторические. Дешёвый, независимый PR.

---

## Порядок и зависимости

```
Волна 0 (прод-инциденты, ПОСЛЕДОВАТЕЛЬНО, ревью владельцем):
  PR-A (date consumers)  ─┐
  PR-B (payment flow) ────┼─ PR-B требует UNIQUE из PR-G (или своя мини-миграция)
  PR-C (security) ────────┤
  PR-D (CI/CD gate) ──────┤  ← сделать РАНО: защищает все следующие merge
  PR-E (backup) ──────────┘  ← независим, можно первым

Волна 1 (надёжность):
  PR-F (monitor atomicity) ← согласовать дизайн outbox (RFC)
  PR-G (pool + migrations) ← даёт констрейнты для PR-B
  PR-H (tashkent year)

Волна 2 (AI/UX):
  PR-I (ai chat)   ← параллельно
  PR-J (ux/low)    ← параллельно

Волна 3 (рефакторинг, необязательно):
  PR-K (split), PR-L (docs)
```

**Рекомендация по первому шагу:** PR-D (гейт CI) + PR-E (backup) — самые дешёвые и снимают
системный риск для всех последующих изменений. Затем PR-A и PR-B (ломают платящих прямо сейчас).

## Как запускать Opus-агентов по этому плану

Каждому агенту в промпт: (1) шапку контекст-констант; (2) один PR-пакет целиком; (3) требование —
сначала прочитать затронутые файлы и `tests/` рядом, потом писать; (4) обязательные тесты из пакета +
`docker compose -f docker-compose.test.yml run --rm tests` зелёный перед завершением; (5) НЕ коммитить/пушить
без явной команды владельца (авто-деплой на прод); (6) вернуть diff-сводку + список добавленных тестов.
Пакеты волны 0 — по одному агенту последовательно (пересечения по subscription.py). Волны 1-2 — параллельно
(файлы почти не пересекаются). Детали дисциплины — память [[multiagent-playbook]].
