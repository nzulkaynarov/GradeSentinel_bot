# Graph Report - GradeSentinel_bot  (2026-09-06)

## Corpus Check
- 248 files · ~202,876 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 3761 nodes · 8297 edges · 173 communities (142 shown, 31 thin omitted)
- Extraction: 90% EXTRACTED · 10% INFERRED · 0% AMBIGUOUS · INFERRED: 847 edges (avg confidence: 0.85)
- Token cost: 413,344 input · 26,000 output

## Community Hubs (Navigation)
- Хендлеры и доступ к семье
- Chart.js вендор: ядро
- Chart.js вендор: события
- Промокоды и платежи
- AI-аналитика Anthropic
- Mini App: фронтенд-логика
- WebApp API и учебный год
- Планировщик и выборки оценок
- Chart.js вендор: отрисовка
- Авторизация и роли
- Chart.js вендор: геометрия
- БД: настройки и уведомления
- Состояния пользователя и i18n
- Chart.js вендор: плагины
- Фасад БД и seed
- Семейные группы
- Периферия #16
- Периферия #17
- Периферия #18
- Периферия #19
- Периферия #20
- Периферия #21
- Периферия #22
- Периферия #23
- Периферия #24
- Периферия #25
- Периферия #26
- Периферия #27
- Периферия #28
- Периферия #29
- Периферия #30
- Периферия #31
- Периферия #32
- Периферия #33
- Периферия #34
- Периферия #35
- Периферия #36
- Периферия #37
- Периферия #38
- Периферия #39
- Периферия #40
- Периферия #41
- Периферия #42
- Периферия #43
- Периферия #44
- Периферия #45
- Периферия #46
- Периферия #47
- Периферия #48
- Периферия #49
- Периферия #50
- Периферия #51
- Периферия #52
- Периферия #53
- Периферия #54
- Периферия #55
- Периферия #56
- Периферия #57
- Периферия #58
- Периферия #59
- Периферия #60
- Периферия #61
- Периферия #62
- Периферия #63
- Периферия #64
- Периферия #65
- Периферия #66
- Периферия #67
- Периферия #68
- Периферия #69
- Периферия #70
- Периферия #71
- Периферия #72
- Периферия #73
- Периферия #74
- Периферия #75
- Периферия #76
- Периферия #77
- Периферия #78
- Периферия #79
- Периферия #80
- Периферия #81
- Периферия #82
- Периферия #83
- Периферия #84
- Периферия #85
- Периферия #86
- Периферия #87
- Периферия #88
- Периферия #89
- Периферия #90
- Периферия #91
- Периферия #92
- Периферия #93
- Периферия #94
- Периферия #95
- Периферия #96
- Периферия #97
- Периферия #98
- Периферия #99
- Периферия #100
- Периферия #101
- Периферия #102
- Периферия #103
- Периферия #104
- Периферия #105
- Периферия #106
- Периферия #107
- Периферия #108
- Периферия #109
- Периферия #110
- Периферия #111
- Периферия #112
- Периферия #113
- Периферия #114
- Периферия #115
- Периферия #116
- Периферия #117
- Периферия #118
- Периферия #119
- Периферия #120
- Периферия #121
- Периферия #122
- Периферия #123
- Периферия #124
- Периферия #125
- Периферия #126
- Периферия #127
- Периферия #128
- Периферия #129
- Периферия #130
- Периферия #131
- Периферия #132
- Периферия #133
- Периферия #134
- Периферия #135
- Периферия #136
- Периферия #137
- Периферия #138
- Периферия #139
- Периферия #140
- Периферия #141
- Периферия #142
- Периферия #143
- Периферия #144
- Периферия #145
- Периферия #146
- Периферия #148
- Периферия #149
- Периферия #150
- Периферия #151
- Периферия #152
- Периферия #153
- Периферия #154
- Периферия #155
- Периферия #156
- Периферия #157
- Периферия #158
- Периферия #159
- Периферия #160
- Периферия #161
- Периферия #162
- Периферия #163
- Периферия #165
- Периферия #166
- Периферия #167
- Периферия #168
- Периферия #170

## God Nodes (most connected - your core abstractions)
1. `t()` - 180 edges
2. `get_db_connection()` - 149 edges
3. `get_user_lang()` - 138 edges
4. `an()` - 61 edges
5. `ns()` - 55 edges
6. `get_parent_role()` - 50 edges
7. `s()` - 42 edges
8. `_check_for_new_grades_impl()` - 41 edges
9. `o()` - 40 edges
10. `a()` - 38 edges

## Surprising Connections (you probably didn't know these)
- `Cutover rollback plan (live sentinel.db read-only, revert main)` --semantically_similar_to--> `Atomic staged release switch with auto-rollback (B19)`  [INFERRED] [semantically similar]
  Docs/cutover-runbook-2026-06-29.md → .github/workflows/deploy.yml
- `Client-side ru/uz/en locale dictionary` --semantically_similar_to--> `Russian landing i18n bundle (base)`  [INFERRED] [semantically similar]
  frontend/index.html → landing/i18n/ru.yaml
- `HTML parse_mode only, never Markdown` --conceptually_related_to--> `GradeSentinel project context (CLAUDE.md)`  [INFERRED]
  .claude/skills/gs-i18n/SKILL.md → CLAUDE.md
- `Tashkent +5h date arithmetic port (silent wrong-day risk)` --semantically_similar_to--> `Asia/Tashkent server TZ vs naive-UTC storage (today = naive UTC + 5h)`  [INFERRED] [semantically similar]
  Docs/migration-sqlite-to-postgres-estimate-2026-06-29.md → .claude/skills/gs-prod-ops/SKILL.md
- `SESSION-HANDOFF docs as portable cross-machine memory` --semantically_similar_to--> `CONTEXT snapshot for a new Claude session`  [INFERRED] [semantically similar]
  .claude/skills/gs-session-start/SKILL.md → Docs/CONTEXT.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Gated atomic deploy pipeline (tests gate to auto-rollback)** — _github_workflows_tests_pytest, _github_workflows_deploy_test_gate, _github_workflows_deploy_deploy_to_vps, _github_workflows_deploy_atomic_release_switch, _claude_skills_gs_pr_skill_merge_equals_deploy, _claude_skills_gs_prod_ops_skill_read_only_prod_rule [EXTRACTED 1.00]
- **SQLite to PostgreSQL migration program (estimate, cutover, ongoing schema policy)** — docs_migration_sqlite_to_postgres_estimate_2026_06_29_migration_estimate, docs_cutover_runbook_2026_06_29_cutover_runbook, docs_migration_sqlite_to_postgres_estimate_2026_06_29_alembic_baseline_replacement, _claude_skills_gs_migration_skill_alembic_schema_policy, docs_migration_sqlite_to_postgres_estimate_2026_06_29_aborted_transaction_trap, docs_migration_sqlite_to_postgres_estimate_2026_06_29_tashkent_date_arithmetic [INFERRED 0.90]
- **Academic year rollover loop (stale sheet detect, relink nudge, seasonal check, open quarter debt)** — docs_maintenance_stale_sheet_rollover, docs_maintenance_seasonal_checklist, _claude_skills_gs_session_start_skill_domain_calendar_check, _claude_skills_gs_prod_ops_skill_log_tag_dictionary, docs_maintenance_quarter_grades_academic_year_gap, _claude_skills_gs_i18n_skill_quiet_hours_text_rule [INFERRED 0.85]
- **Academic-Year Rollover Defense Stack** — docs_plans_2026_09_02_academic_year_rollover_students_academic_year, docs_plans_2026_09_02_academic_year_rollover_stale_sheet_gate, docs_plans_2026_09_02_academic_year_rollover_infer_sheet_year, docs_plans_2026_09_02_academic_year_rollover_reconcile_self_healing, docs_plans_2026_09_02_academic_year_rollover_relink_nudge [EXTRACTED 1.00]
- **Notification Atomicity: Outbox vs Narrowed Window** — docs_plans_2026_07_13_technical_audit_and_refactor_plan_b8_write_then_notify, docs_plans_2026_07_13_tech_debt_and_modularization_tz_pr_f1_outbox, docs_plans_2026_07_13_tech_debt_closure_spec_td1_no_outbox_decision, docs_plans_2026_07_13_tech_debt_closure_spec_notify_fail_tag [EXTRACTED 1.00]
- **GradeSource Ports & Adapters Stack** — docs_rfc_grade_source_integration_gradeevent_dto, docs_rfc_grade_source_integration_gradesource_port, docs_rfc_grade_source_integration_google_sheets_source, docs_rfc_grade_source_integration_emaktab_source, docs_rfc_grade_source_integration_source_registry, docs_rfc_grade_source_integration_source_type_config [EXTRACTED 1.00]
- **Hugo landing render pipeline** — landing_layouts__default_baseof, landing_layouts_partials_head, landing_layouts_partials_nav, landing_layouts_partials_footer, landing_layouts_index [EXTRACTED 1.00]
- **Trilingual landing i18n contract** — landing_i18n_ru, landing_i18n_en, landing_i18n_uz, landing_locale_switcher [INFERRED 0.85]
- **Static frontend → Hugo landing migration (Phase 1)** — frontend_index, frontend_instructions, landing_content_index, landing_content_docs_index, landing_layouts_index [EXTRACTED 1.00]

## Communities (173 total, 31 thin omitted)

### Community 0 - "Хендлеры и доступ к семье"
Cohesion: 0.03
Nodes (111): is_head_of_any_family(), Устанавливает язык пользователя., Является ли пользователь head'ом хотя бы одной семьи., Привязывает telegram_id к родителю по номеру телефона., Обновляет Telegram first_name. Используется в приветствиях вместо `fio`. Юзер…, set_user_lang(), update_parent_first_name(), update_parent_telegram_id() (+103 more)

### Community 1 - "Chart.js вендор: ядро"
Cohesion: 0.03
Nodes (36): Ae(), afterUpdate(), be(), buildTicks(), _calculateBarIndexPixels(), d(), destroy(), determineDataLimits() (+28 more)

### Community 2 - "Chart.js вендор: события"
Cohesion: 0.05
Nodes (26): an(), ct(), f(), fs(), ge(), generateLabels(), gs(), ke() (+18 more)

### Community 3 - "Промокоды и платежи"
Cohesion: 0.04
Nodes (80): pre_checkout_query_handler, delete_promo_code(), get_promo_code(), list_promo_codes(), Any, Промокоды для скидок и подарочных подписок. Этот модуль — первый physical…, Возвращает все промокоды для admin panel., Удаляет промокод. True если удалили. (+72 more)

### Community 4 - "AI-аналитика Anthropic"
Cohesion: 0.06
Nodes (79): AIAnalyticsError, analyze_student_grades(), Exception, Поднимается, когда Anthropic API недоступен или вернул ошибку. Отличается от…, Анализирует оценки студента за последние N дней через Claude API. Возвращает…, can_manage_family(), Может ли пользователь управлять семьёй (admin OR head этой семьи). Единственный…, create_promo_code() (+71 more)

### Community 5 - "Mini App: фронтенд-логика"
Cohesion: 0.07
Nodes (63): API_HEADERS, _appendChatMessage(), applyTranslations(), askAiAboutSubject(), _attachFeedbackToNode(), boot(), _CHAT_PROMPT_KEYS, _clearChatHistory() (+55 more)

### Community 6 - "WebApp API и учебный год"
Cohesion: 0.06
Nodes (69): route, get_student_academic_year(), get_students_for_parent(), Учебный год (год начала, 2025 = 2025/26) привязанной таблицы ученика. None —…, Студенты видимые пользователю. UNION двух источников — для устранения…, get_grade_history_for_student_all(), get_quarter_grades(), Учебный год для операций с четвертными. Явно переданный побеждает; иначе берём… (+61 more)

### Community 7 - "Планировщик и выборки оценок"
Cohesion: 0.06
Nodes (66): get_overnight_grades_for_student(), get_today_grades_for_student(), get_yesterday_grades_for_student(), Any, Все оценки студента за сегодня (по Ташкенту, UTC+5), по одной (свежей) на…, Оценки, добавленные за ночь (22:00 вчера → сейчас по Ташкенту), по одной…, Все оценки студента за вчера (по Ташкенту, UTC+5)., cleanup_expired_invites() (+58 more)

### Community 8 - "Chart.js вендор: отрисовка"
Cohesion: 0.07
Nodes (15): addBox(), afterDraw(), afterEvent(), Ee(), ki(), Le(), oa(), Oi() (+7 more)

### Community 9 - "Авторизация и роли"
Cohesion: 0.06
Nodes (59): get_parent_id_by_telegram(), get_parent_role(), is_member_of_family(), Возвращает роль ('admin' / 'senior' / 'head') или None., Член семьи (через family_links)., Возвращает internal parent ID по telegram_id., cancel_subscription(), extend_subscription() (+51 more)

### Community 10 - "Chart.js вендор: геометрия"
Cohesion: 0.06
Nodes (15): at(), Bi(), _calculateBarValuePixels(), Ci(), Fi(), getBasePixel(), go(), ii() (+7 more)

### Community 11 - "БД: настройки и уведомления"
Cohesion: 0.05
Nodes (57): has_today_grades_for_parent(), Есть ли сегодня хоть одна оценка у детей родителя (по Ташкенту, UTC+5)., get_notify_mode(), is_head_of_family(), Режим уведомлений: 'instant' (по умолчанию) или 'summary_only'., Устанавливает режим уведомлений ('instant' или 'summary_only')., Является ли пользователь head'ом конкретной семьи., set_notify_mode() (+49 more)

### Community 12 - "Состояния пользователя и i18n"
Cohesion: 0.08
Nodes (56): get_user_lang(), Возвращает язык пользователя (ru/uz/en). По умолчанию 'ru'., Сохраняет состояние пользователя в БД (upsert по user_id)., set_user_state(), admin_help(), callback_ap_back(), callback_ap_broadcast(), callback_ap_cancel_sub() (+48 more)

### Community 13 - "Chart.js вендор: плагины"
Cohesion: 0.06
Nodes (33): b(), beforeDatasetDraw(), beforeDatasetsDraw(), ce(), da(), de, dt(), ea() (+25 more)

### Community 14 - "Фасад БД и seed"
Cohesion: 0.05
Nodes (48): Локальный seed тестовых данных для разработки. Приведено к текущему API фасада…, Подключение к БД + инициализация схемы + re-export всего CRUD-слоя. Миграция…, clear_chat_history(), clear_family_chat_history(), get_feedback_for_message(), get_message_owner(), get_recent_chat_history(), get_recent_family_chat_history() (+40 more)

### Community 15 - "Семейные группы"
Cohesion: 0.08
Nodes (42): get_family_for_group(), get_groups_for_family(), get_groups_for_student(), link_group_to_family(), Any, Семейные групповые чаты (бот добавлен в Telegram-чат семьи). Один chat_id →…, Привязывает Telegram-группу к семье. True если создана, False если chat_id уже…, Возвращает {'family_id', 'family_name', 'message_thread_id'} для chat_id или… (+34 more)

### Community 16 - "Периферия #16"
Cohesion: 0.07
Nodes (48): get_parents_for_student(), Telegram_id всех родителей привязанных к ученику через семью. Используется…, get_setting(), Возвращает значение настройки по ключу., Устанавливает настройку (upsert по key)., set_setting(), _cell_avg_grade(), _cell_raw_text() (+40 more)

### Community 17 - "Периферия #17"
Cohesion: 0.04
Nodes (47): app_js(), html(), fixture, parametrize, Каркас Mini App: четыре вкладки и нижняя навигация. Разметка дашборда тестами…, Средний балл на «Сегодня» подписан числом оценок и периодом. Балл без размера…, «Лучший» и «слабее всего» лежат в «Предметах», а не в ленте дня., Урезанный сервером срез не выдаётся за полную историю. (+39 more)

### Community 18 - "Периферия #18"
Cohesion: 0.07
Nodes (23): a(), aa(), afterDatasetsUpdate(), ai(), bo, cn(), et(), g() (+15 more)

### Community 19 - "Периферия #19"
Cohesion: 0.07
Nodes (35): ao(), average(), beforeDraw(), dataset(), draw(), getCenterPoint(), getRange(), hi() (+27 more)

### Community 20 - "Периферия #20"
Cohesion: 0.07
Nodes (15): As(), bt, color(), Ft(), getMaxOverflow(), It(), kt(), mt() (+7 more)

### Community 21 - "Периферия #21"
Cohesion: 0.07
Nodes (13): beforeLayout(), buildLookupTable(), En, Fo(), _generate(), getDecimalForValue(), _getTimestampsForTable(), init() (+5 more)

### Community 22 - "Периферия #22"
Cohesion: 0.08
Nodes (14): ca(), Do(), eo(), Fn(), getLabelAndValue(), getLabelForValue(), n(), ne() (+6 more)

### Community 23 - "Периферия #23"
Cohesion: 0.07
Nodes (40): get_last_menu_id(), get_support_user_id(), get_user_state(), Any, Персистентное состояние пользователя — переживает рестарт бота. Три независимые…, Возвращает ID последнего сообщения меню для пользователя., Обновляет ID последнего сообщения меню (upsert по user_id)., Возвращает текущее состояние пользователя или None. (+32 more)

### Community 24 - "Периферия #24"
Cohesion: 0.06
Nodes (41): Legacy static dashboard prototype, Hardcoded demo children and subjects, localStorage dark-mode toggle (gs-theme), Legacy static landing page, Client-side ru/uz/en locale dictionary, Legacy static instructions page, Russian user documentation (10 sections), Quiet hours 22:00-07:00 Tashkent (non-disableable) (+33 more)

### Community 25 - "Периферия #25"
Cohesion: 0.08
Nodes (37): dispatch_tool(), _format_family_members(), _format_family_pricing(), _format_subscription_status(), _labels(), Any, Tool definitions + server-side dispatcher для AI-чата (PR_E2). AI вызывает…, Выполняет tool по имени и возвращает строковый результат для Anthropic.… (+29 more)

### Community 26 - "Периферия #26"
Cohesion: 0.08
Nodes (38): _format_grades_context(), Компактное представление оценок для prompt'а. По убыванию даты. NAV-001…, Сегодняшняя дата (Tashkent TZ, UTC+5) в ISO формате — для prompt'а., _tashkent_today_str(), _capturing_client(), _flatten_content(), Tests for /api/chat endpoint — AI assistant с контекстом ученика. Реальные API-…, Дата вынесена в ПЕРВЫЙ system-блок (высокая заметность) — при большом grade-… (+30 more)

### Community 27 - "Периферия #27"
Cohesion: 0.08
Nodes (38): _compute_added_grades(), Multiset diff: какие оценки появились в new которых не было в old. «2» → «2/5»…, _make_sheet(), fixture, Регрессия: двухфазное подтверждение оценок + multi-grade в monitor_engine.…, Главный кейс из реальной жалобы: «2» в БД, в таблице теперь «2/5». Бот должен…, Если учитель УБРАЛ оценку из ячейки — родителю не пишем (не сюрприз). Но БД…, Создаёт активную семью с подпиской и одним учеником. (+30 more)

### Community 28 - "Периферия #28"
Cohesion: 0.06
Nodes (32): callable, answer_parent_question(), _extract_text_from_response(), Достаёт первый text-блок из Anthropic response. Defensive — толерантен к mock-…, B15: приводит историю к валидному для Anthropic Messages API виду. Anthropic…, Отвечает на вопрос родителя про оценки ученика с контекстом из БД. Multi-turn…, _sanitize_conversation(), Если Claude вернул text сразу (без tool_use) — возвращаем текст. (+24 more)

### Community 29 - "Периферия #29"
Cohesion: 0.08
Nodes (36): _parse_russian_date(), Парсит русскую дату вида '2 сентября', '14 март Сб', '1 октября' и т.д.…, Учебный год определяется по Ташкенту (UTC+5), а не по локальному/UTC времени…, Сервер: 31 авг 23:00 UTC → Ташкент: 1 сен 04:00 (уже новый уч.год).…, Демонстрация бага: если бы год считался по серверному UTC (31 авг, month=8),…, Сервер: 31 дек 23:00 UTC → Ташкент: 1 янв 04:00 следующего года. Декабрь =…, Во время весны «3 мая» — текущий календарный год, не следующий., Осенью «3 мая» — весна СЛЕДУЮЩЕГО календарного года того же уч.года. (+28 more)

### Community 30 - "Периферия #30"
Cohesion: 0.07
Nodes (36): add_student(), delete_parent_from_family(), delete_student_from_family(), get_active_spreadsheets_with_subscription(), get_all_families(), get_child_count(), get_families_for_student(), get_family_members() (+28 more)

### Community 31 - "Периферия #31"
Cohesion: 0.10
Nodes (34): Message, get_families_for_head(), Семьи где пользователь — head (по families.head_id, не через family_links)., Удаляет привязку группы. True если удалили, False если не было., unlink_group(), parse_topic_link(), Утилиты для работы с групповыми чатами Telegram. Чистая логика без зависимости…, Извлекает message_thread_id из ссылки Telegram. None если не распарсилось.… (+26 more)

### Community 32 - "Периферия #32"
Cohesion: 0.10
Nodes (33): family(), _grades_on(), fixture, Самовосстановление учебного года (разбор прода 2026-09-03). PR #116 ввёл…, Инцидент 2026-09-03: backfill проставил 2026 прошлогодней таблице. Лист…, Исправленный год лежит в БД, а не в памяти: следующий цикл его видит., Год ошибочно занижен (лист привязан в августе, когда оценок ещё не было). Пауза…, Второй цикл в тот же день лист не читает — квота Sheets не тратится, и… (+25 more)

### Community 33 - "Периферия #33"
Cohesion: 0.10
Nodes (16): bn, dn(), e(), ei(), gi(), je(), mi(), on() (+8 more)

### Community 34 - "Периферия #34"
Cohesion: 0.08
Nodes (30): Оценки за прошлый класс можно посмотреть, и они подписаны своим классом. Оценки…, Старые оценки уезжают в архив — год всё равно доступен для выбора., Май 2026 — это учебный год 2025/26, а не 2026/27., Год, в котором оценок ещё нет, остаётся в списке — из снимка привязки., Оценки внутри учебного года: осенние в year, весенние в year+1., Май 2026 принадлежит году, начавшемуся в сентябре 2025., История выпускника почти вся лежит в архиве — без него отчёт пуст., Сценарий выпускника: сводка по годам с классом каждого года. (+22 more)

### Community 35 - "Периферия #35"
Cohesion: 0.06
Nodes (17): client(), fixture, parametrize, Tests for AI chat feedback — 👍/👎 (PR_H3). Покрываем: - save_chat_message теперь…, ON DELETE CASCADE: при clear_chat_history feedback тоже чистится (FK constraint…, Создаёт user+seed для теста endpoint: ученик с подпиской + assistant msg., Чужой msg_id → 404 (не 403 чтобы не утечка ownership info)., После PR_H3 /api/chat в success-ответе содержит message_id (для UI feedback). (+9 more)

### Community 36 - "Периферия #36"
Cohesion: 0.11
Nodes (28): _env_int(), Централизованные константы конфигурации. Все «магические числа» собраны здесь,…, Читает int из ENV с fallback на default. Невалидное значение → default +…, _format_grade_message(), Форматирует уведомление об оценках ученика: детальное для одной оценки, батч-…, format_batched_notification(), format_grade_change_notification(), format_grade_notification() (+20 more)

### Community 37 - "Периферия #37"
Cohesion: 0.11
Nodes (29): get_families_for_user(), Все семьи к которым относится пользователь. UNION двух источников: 1.…, _ask_ai(), _build_feedback_markup(), _build_retry_markup(), _enter_chat_mode(), handle_ai_deeplink(), _is_ai_chat_state() (+21 more)

### Community 38 - "Периферия #38"
Cohesion: 0.07
Nodes (27): client(), fixture, parametrize, Smoke-test PDF экспорта дашборда (Dashboard refresh). Покрываем: -…, RFC 7230: HTTP headers должны быть ASCII. gunicorn отвергает Content-…, type=subject + subject=X → PDF только с этим предметом., type=teacher_talk → backend filters только problem subjects., POST /pdf/send → backend генерит PDF и шлёт через bot.send_document. Tests что… (+19 more)

### Community 39 - "Периферия #39"
Cohesion: 0.13
Nodes (18): Enum, is_quiet_hours(), True для интервала [QUIET_HOURS_START..24) ∪ [0..QUIET_HOURS_END)., Unified notification layer (2026-05-22). Раньше каждый scheduler/handler сам…, Тихие часы — единое место принятия решения «отложить или слать сейчас». Раньше…, True если уведомление этого типа должно копиться в очередь во время тихих часов…, should_defer(), Sender — единая точка отправки уведомлений. API: sender.send(tg_id, text,… (+10 more)

### Community 40 - "Периферия #40"
Cohesion: 0.11
Nodes (27): detect_anomalies(), generate_proactive_alert(), Возвращает список аномалий для ученика (для каждой будет alert). MVP: один тип…, Генерит короткий текст alert'а через Claude. Безопасно деградирует (None при…, parametrize, Tests for proactive AI alerts (PR_H5). Покрываем: - detect_anomalies: серия ≤3…, Тройка тоже считается «низкой» — это сигнал для четвёрочника., 2 низкие оценки — не серия (порог 3+). (+19 more)

### Community 41 - "Периферия #41"
Cohesion: 0.15
Nodes (28): Table, build_dashboard_pdf(), _ensure_font(), _full_history_table(), _grade_date_str(), _hero_table(), _localize(), _make_footer_callback() (+20 more)

### Community 42 - "Периферия #42"
Cohesion: 0.08
Nodes (27): client(), _grades(), fixture, Устойчивость веб-приложения (аудит 2026-09-03). Три места, где HTTP-поток…, Через cooldown попытка повторяется — сбой не вечен., Имя бота не меняется — переменная окружения снимает вызов вовсе., Отчёт на тысячи строк не должен уносить оба воркера по памяти., Усечение подписано числами, а не спрятано. (+19 more)

### Community 43 - "Периферия #43"
Cohesion: 0.11
Nodes (27): _col_letter(), _import_from_sheet(), infer_sheet_academic_year(), _parse_all_grades_sheet(), _parse_russian_day_month(), Any, datetime, Импорт исторических оценок из листа "Все оценки". Структура листа: - Строка 1:… (+19 more)

### Community 44 - "Периферия #44"
Cohesion: 0.08
Nodes (26): family_with_old_sheet(), fixture, parametrize, Rollover учебного года (инцидент 2026-09-02). Даты в шапке «Все оценки!» без…, Новая привязка (academic_year NULL) → importer выводит год по листу, пишет в…, Патчит «сегодня» монитора на 2026-09-02 10:00 Ташкента (05:00 UTC)., Прошлогодняя таблица: оценки не читаются и не рассылаются, семья получает…, Ночью (тихие часы) нэдж не шлём и маркер не ставим — уйдёт днём с кнопкой. (+18 more)

### Community 45 - "Периферия #45"
Cohesion: 0.07
Nodes (26): dom, dom.iterable, esnext, next-env.d.ts, .next/types/**/*.ts, node_modules, **/*.ts, **/*.tsx (+18 more)

### Community 46 - "Периферия #46"
Cohesion: 0.13
Nodes (25): _parse_master_sheet_for_date(), Pure-функция (для тестов): находит колонку с `target_date` в шапке (row 2) и…, Читает «Все оценки!» и возвращает [(subject, raw_grade)] для сегодняшней даты…, read_master_sheet_today_grades(), Лист получен, шапка непустая, но ни одна колонка не распозналась как дата →…, Если хотя бы одна колонка — валидная дата (просто не сегодня), НЕ спамим., test_master_sheet_no_warn_when_dates_parse(), test_master_sheet_warns_when_no_dates_parse() (+17 more)

### Community 47 - "Периферия #47"
Cohesion: 0.14
Nodes (25): _current_academic_year(), _insert_grade(), _load_migration(), Backfill миграции 0004 (students.academic_year) на реальном PostgreSQL. Схему…, Свежая оценка в grade_history перевешивает старую в архиве и наоборот., Ученик без единой оценки — текущий учебный год (единственный разумный fallback)., WHERE academic_year IS NULL — повторный прогон не перетирает уже проставленное., Второй прогон на тех же данных даёт тот же результат. (+17 more)

### Community 48 - "Периферия #48"
Cohesion: 0.15
Nodes (24): _make_family(), _make_parent(), _make_payment_message(), _now_naive_utc(), _payment_rows(), fixture, Payment flow: атомарность и устойчивость (PR-B). Покрывает: B1 —…, Плательщик без строки parents: платёж всё равно записан (paid_by=NULL),… (+16 more)

### Community 49 - "Периферия #49"
Cohesion: 0.16
Nodes (21): Anthropic, _get_client(), Singleton Anthropic-клиент для AI-аналитики. Выделено из…, _insight_cache_key(), Кэш AI-инсайтов в таблице settings (dashboard insight + year insight). Выделено…, Возвращает кэшированный insight если он не протух (TTL 6h)., Сохраняет insight с timestamp., _read_insight_cache() (+13 more)

### Community 50 - "Периферия #50"
Cohesion: 0.11
Nodes (24): fresh_db(), fixture, Тесты для compute_dashboard_insight — кэш 6h и graceful degradation., Если Claude таймаутит — НЕ падаем, возвращаем None (кэш не пишем)., Claude иногда возвращает ответ в кавычках — чистим., ru и en кэшируются независимо., Helper для конструирования summary с None-safe arithmetic., Если Claude вернул мета-описание (markdown headers, "I can assist") —… (+16 more)

### Community 51 - "Периферия #51"
Cohesion: 0.14
Nodes (24): fixture, Security PR-C / S2: валидация Telegram WebApp initData. Закрывает пробел инфра-…, auth_date старше 24ч → подпись валидна, но initData протух → отказ., auth_date в пределах суток — принимается (граница TTL)., auth_date заметно в будущем (за пределами clock-skew) → отказ., Небольшой дрейф часов (auth_date чуть в будущем) допускается., initData без auth_date после hash-проверки → отказ (не бессрочный доступ)., Собирает валидный (или намеренно битый) initData-query-string.… (+16 more)

### Community 52 - "Периферия #52"
Cohesion: 0.15
Nodes (20): _drop_removed_grades(), Убирает из истории сегодняшние оценки, которых больше нет в листе. Учитель…, _insert(), Мелкая правда дашборда и стёртые учителем оценки (аудит 2026-09-03). • счётчик…, Ключевая защита: цикл вызывает проверку только когда в колонке что-то есть.…, Фронт должен знать оба числа, чтобы подписать «100 из 525»., Урезанный срез подписан обоими числами. Раньше это была строка «100 / 525» в…, Учитель стёр ячейку — оценка уходит и из истории. (+12 more)

### Community 53 - "Периферия #53"
Cohesion: 0.12
Nodes (22): parametrize, Regression тесты для FAQ-блока в _CHAT_SYSTEM_PROMPTS. PR_E1 расширил system…, Каждая команда из reference списка упомянута в каждом языке. Если добавляется…, Цены НЕ должны быть захардкожены — они мутируются через /set_prices. Если…, Click, Payme, Telegram Stars — все 3 способа упомянуты., Safeguard «не угадывай про подписку/семью/цены — попроси открыть меню». Без…, Размер system prompt должен быть в разумных пределах. Если кто-то раздул prompt…, Базовая инструкция «отвечай по оценкам» осталась после расширения FAQ. (+14 more)

### Community 54 - "Периферия #54"
Cohesion: 0.10
Nodes (20): _quarters_raw(), Четвертные оценки живут внутри учебного года (Фаза 2 RFC rollover). До миграции…, После смены ссылки год ученика ещё NULL — читаем текущий, а не прошлый., Существующие строки получают год привязанной таблицы ученика. Воспроизводим…, Ограничение пересобрано: дубль внутри одного года по-прежнему невозможен., Флаг паузы в bootstrap строится по academic_year из этого запроса., Сценарий ноября: та же четверть по тому же предмету, но другой год., Учитель исправил четвертную — обновляем, а не плодим строку. (+12 more)

### Community 55 - "Периферия #55"
Cohesion: 0.13
Nodes (21): BaseException, Any, Централизованный репортинг ошибок. Цель: одна точка для всех `except Exception…, Лениво инициализирует Sentry. Возвращает True если активен., Логирует ошибку и отправляет в Sentry (если настроен). scope: короткий…, Не-fatal предупреждение (нет exception, но что-то странное). Уйдёт в Sentry как…, report(), _try_init_sentry() (+13 more)

### Community 56 - "Периферия #56"
Cohesion: 0.13
Nodes (21): date_cls, backfill(), col_letter_to_index_0based(), load_headers_for_student(), main(), Одноразовый backfill `grade_history.grade_date` для существующих записей. Этап…, Главная функция. headers_by_student — для каждого student_id шапки его листов.…, Несколько образцов для отчёта — посмотреть руками что бы поставили. (+13 more)

### Community 57 - "Периферия #57"
Cohesion: 0.17
Nodes (22): archive_old_grades(), Переносит оценки старше N дней из grade_history в grade_history_archive. days…, _counts(), _insert_live(), Цикл «архивация ↔ реимпорт» и потеря истории в дашборде (аудит 2026-09-03). Что…, Ключевая регрессия: оценка из архива больше не считается новой., Защита от переусердствования: незнакомая оценка по-прежнему импортируется., Один и тот же день продублирован в листе (наблюдалось в проде) — импортируется… (+14 more)

### Community 58 - "Периферия #58"
Cohesion: 0.13
Nodes (22): NAV-010: трекать AI fail/success по scheduler job'у. После _AI_FAIL_THRESHOLD…, _track_ai_outcome(), fresh_settings(), fixture, NAV-010: tracking AI scheduler failures + admin alert. Покрываем: - success…, Разные job names — независимые счётчики., Если _bot не задан (тестовая среда без бота) — _track_ai_outcome не падает., Без ADMIN_ID env — не шлём (но и не падаем). (+14 more)

### Community 59 - "Периферия #59"
Cohesion: 0.12
Nodes (22): _make_message(), Тесты для PR_H1: bot ai_chat handler использует conversation history. Webapp…, Порядок: save user → call AI → save assistant. Гарантирует что юзер не теряет…, AI вернул None → ai_chat_error отправлен → assistant НЕ сохраняется., AI raise → assistant НЕ сохраняется. Тот же контракт что и при None., NAV-007 retry: skip_save_user=True пропускает save user, но assistant…, NAV-007: при AI fail отправляется error с inline retry-кнопкой., Первый вопрос родителя — пустая история, prev_messages=[]. (+14 more)

### Community 60 - "Периферия #60"
Cohesion: 0.13
Nodes (22): Tests for dashboard radical refactor (NAV → analytical): -…, Если в БД есть quarter=5 (год) — используем как есть., Если нет q=5 — прогноз из avg 1-4ч., Предметы с year_value < 4 — сверху., Нет предметов — top/worst = None., test_by_subject_trend_skips_when_too_few_data(), test_kpis_empty_subjects(), test_kpis_with_full_data() (+14 more)

### Community 61 - "Периферия #61"
Cohesion: 0.11
Nodes (22): _g(), Дашборд показывает то, что подписано (аудит 2026-09-03). Жалоба владельца: «в…, Одна двойка больше не включает статус «есть на что обратить внимание», пока KPI…, Симметрично: одна пятёрка не делает предмет «лучшим»., Один порог на весь дашборд — иначе подписи противоречат друг другу., Поле отдавалось «на пару дней» с 21 мая и не читалось фронтом., Регрессия: psycopg отдаёт объекты, Flask превратил бы их в HTTP-date., Именно то, что уйдёт клиенту: json.dumps не должен ломать формат. (+14 more)

### Community 62 - "Периферия #62"
Cohesion: 0.12
Nodes (22): _family_with_student(), _group_queue_size(), _queue_group(), Три находки аудита 2026-07-13, дожившие до сентября. B-H1 Групповая очередь не…, Повторный запуск в тот же день не рассылает второй раз., Порядок задаёт запрос, а не планировщик., История чата хранится по паре (родитель, семья): берём общую семью, а не просто…, Общей семьи нет — выбор всё равно детерминирован. (+14 more)

### Community 63 - "Периферия #63"
Cohesion: 0.11
Nodes (14): ConnectionPool, Mapping, Smoke-проверка слоя подключения src/db/pg.py против реального PostgreSQL.…, _configure(), _dsn(), _get_pool(), Слой подключения к PostgreSQL (psycopg v3 + пул соединений). Замена SQLite-…, Применяется пулом к каждому новому соединению. (+6 more)

### Community 64 - "Периферия #64"
Cohesion: 0.12
Nodes (21): seed(), add_parent(), get_greeting_name(), get_parent_by_phone(), get_parent_by_telegram(), _normalize_phone(), Any, Авторизация и профиль пользователя. API: - Создание/lookup родителей:… (+13 more)

### Community 65 - "Периферия #65"
Cohesion: 0.10
Nodes (19): Единая точка входа к соединению с БД. Миграция SQLite → PostgreSQL…, create_invite(), get_invite(), Any, Инвайт-ссылки для добавления родителей в семью. Семантика: - Одноразовые…, Создаёт инвайт-ссылку для семьи. Возвращает invite_code., Возвращает данные инвайта, если он валиден (не использован, не истёк)., Помечает инвайт как использованный. True если был свободный + валидный. (+11 more)

### Community 66 - "Периферия #66"
Cohesion: 0.19
Nodes (21): _g(), _make_year_grades(), Tests for compute_year_report — end-of-year dashboard view. Pure-функция в…, Физика — 4 оценки со средним 2.75 — должна попасть в problem., Хронологически (по grade_date asc) идут 5,5,5,5,5,5 в марте-мае., < 6 оценок — growth не считаем (статистически не валидно)., «н» (отсутствие) не должно влиять на средний., Имитация: сентябрь-май, разные предметы, есть рост. (+13 more)

### Community 67 - "Периферия #67"
Cohesion: 0.13
Nodes (21): B13 Academic Year From Server TZ, Not Tashkent, Academic Year Rollover — Incident, RFC, Benchmark, Benchmark of Peer Diaries' Year Rollover (eMaktab, ClassDojo, PowerSchool…), Calendar Bomb: Time as Hidden Input, Unescaped display_name in HTML Notifications (systemic defect), Incident 2026-09-02: Last Year's Grades Resent as New, infer_sheet_academic_year (year from graded columns), Phase 2: quarter_grades Keyed by academic_year (+13 more)

### Community 68 - "Периферия #68"
Cohesion: 0.18
Nodes (20): init_sender(), Создать (или пересоздать) глобальный Sender. Вызывается из main() после init…, _grade_row(), fixture, PR-F1: persistent outbox для уведомлений об оценках. Проблема (до PR-F1):…, К student_with_parent добавляем семейный групповой чат., Изолируем in-memory состояние monitor'а между тестами., Активная семья с подпиской, один родитель (telegram_id), один ученик с… (+12 more)

### Community 70 - "Периферия #70"
Cohesion: 0.16
Nodes (18): _parse_piece(), Парсит один сегмент. None — не оценка и не спец-слово (мусор)., Парсит содержимое ячейки в список оценок. Возвращает list[(grade_value,…, Backward-compatible одиночная оценка. Возвращает: - (5.0, "5") для одиночной…, sanitize_cell(), sanitize_grade(), parametrize, Тесты для sanitize_grade / sanitize_cell — критичные функции в polling-цикле. (+10 more)

### Community 71 - "Периферия #71"
Cohesion: 0.11
Nodes (18): _backfill_family_links(), _ensure_admin(), init_db(), Регистрирует супер-админа из ADMIN_ID (идемпотентно). Порт SQLite `INSERT OR…, Идемпотентный data-repair: семьям с head_id без строки в family_links — создать…, Готовит БД к работе: применяет миграции Alembic (создаёт/обновляет схему),…, apply_migrations(), Применение миграций Alembic программно (старт бота + тест-харнес). Заменяет… (+10 more)

### Community 72 - "Периферия #72"
Cohesion: 0.11
Nodes (19): eslint, eslint-config-next, postcss, tailwindcss, @tailwindcss/postcss, @types/node, @types/react, @types/react-dom (+11 more)

### Community 73 - "Периферия #73"
Cohesion: 0.18
Nodes (16): _extract_retry_after(), _http_code(), Exception, Утилиты для безопасной работы с Telegram Bot API. send_with_retry — обёртка…, Парсит retry_after из ApiTelegramException pyTelegramBotAPI., Возвращает HTTP-код ошибки если он есть, иначе None., Выполняет Telegram API вызов с обработкой 429 RetryAfter. Возвращает (success,…, send_with_retry() (+8 more)

### Community 74 - "Периферия #74"
Cohesion: 0.23
Nodes (7): _grade(), Helper: формирует grade dict в формате get_grade_history_for_student_all., TestComputeSummary, _avg(), compute_summary(), Среднее арифметическое или None для пустого списка., Вычисляет hero-метрики дашборда: средний балл, дельта, тренд, статус,…

### Community 75 - "Периферия #75"
Cohesion: 0.15
Nodes (16): import_history_for_student(), Импортирует оценки студента из обоих листов: «Все оценки» (master со 2 сент) +…, _make_sheet(), fixture, Этап 3 RFC: write-path пишет grade_date явно. monitor_engine берёт дату из…, После 1C grade_date NOT NULL. add_grade без kwarg должен дефолтить на…, Эмулирует «Все оценки!» с одной колонкой для сегодняшней даты., monitor подтверждает новую оценку → INSERT с grade_date = tashkent_today. (+8 more)

### Community 76 - "Периферия #76"
Cohesion: 0.20
Nodes (17): _make_family(), _make_parent(), _payment_rows(), fixture, Security PR-C / S1: IDOR при применении промокода. До фикса…, Регрессия: член своей семьи применяет промо как раньше., Админ обходит проверку членства (как в платёжных путях)., N+1-й ввод промокода за окно отклоняется без обработки кода. (+9 more)

### Community 77 - "Периферия #77"
Cohesion: 0.15
Nodes (17): Local pytest without DATABASE_URL silently skips DB tests, Merge to main = production deploy (owner merges), app-VPS host (bot, webapp, Caddy, multi-tenant with railtech-b2b), DB-VPS host (PostgreSQL 17 over WireGuard, pg_dump + rclone), gs-prod-ops skill (safe production access), Deploy to VPS workflow, Multi-tenant Caddy conf.d fragment (never overwrite master Caddyfile), Per-release venv built from requirements.lock with --require-hashes (B18) (+9 more)

### Community 78 - "Периферия #78"
Cohesion: 0.14
Nodes (10): GradeSentinel FastAPI service. Phase 2+: auth (Phone OTP), dashboard endpoints,…, lifespan(), FastAPI entrypoint. Запуск локально: uvicorn api.main:app --reload --port 8444…, health(), HealthResponse, Health endpoint — для Caddy/мониторинга и smoke-теста OpenAPI., Smoke-тест /health — гарантия что FastAPI app поднимается и OpenAPI собирается., BaseModel (+2 more)

### Community 79 - "Периферия #79"
Cohesion: 0.12
Nodes (17): class-variance-authority, clsx, next, next-themes, react, react-hook-form, tailwindcss-animate, @tremor/react (+9 more)

### Community 80 - "Периферия #80"
Cohesion: 0.12
Nodes (16): fixture, B12: DB-запись display_name и failure-счётчик вынесены из fetch-воркеров.…, get_sheet_data вернул None → _record_student_failure сработал в…, После сломанного цикла успешный сбрасывает счётчик неудач., Изолируем in-memory состояние monitor'а между тестами., Активная семья с подпиской и учеником БЕЗ display_name (NULL)., `_fetch_student_sheet` — только сеть. Никаких update_student_display_name из…, Если display_name уже есть — воркер не дёргает даже get_spreadsheet_title. (+8 more)

### Community 81 - "Периферия #81"
Cohesion: 0.17
Nodes (16): Log tag grep dictionary ([NEW GRADE], [STALE_SHEET], [PENDING], ...), gs-session-start skill (context restoration), SESSION-HANDOFF docs as portable cross-machine memory, cell_reference is metadata, not identity (content-based identity), Family access control helpers (_check_family_access, can_manage_family), GradeSentinel project context (CLAUDE.md), RFC MONOSOURCE_GRADES (single master sheet as grade source), Reply-keyboard navigation and admin/parent role toggle (+8 more)

### Community 82 - "Периферия #82"
Cohesion: 0.14
Nodes (16): docker-compose.test.yml (PG 17 tmpfs test harness), db Service (postgres:17, data in tmpfs), tests Service (gradesentinel-tests:py312 against db), Tech Debt + Modularization TZ (2026-07-13), PR-F1 Persistent Notification Outbox (notified_at flag), PR-F2 Non-Destructive Queue + Per-Recipient Idempotency, PR-M1 Extract src/ai/ Prompts and Client, Tech Debt Closure Spec — Wave TD (2026-07-13) (+8 more)

### Community 83 - "Периферия #83"
Cohesion: 0.13
Nodes (16): PR-M2 subscription.py → Package Split, Re-Export Backward-Compatibility Invariant, webapp/schedulers Audit + Modularization Plan (2026-07-13), A-H1 `fams[0]` Non-Deterministic Primary Family, A-H2 PDF/Claude Calls Block gunicorn Workers, A-H3 `_authorize_student_access` Untested (always mocked), webapp Blueprints + create_app Factory Plan, schedulers/ Package with Declarative Job Table (+8 more)

### Community 84 - "Периферия #84"
Cohesion: 0.18
Nodes (15): _get_holiday_periods(), Список периодов каникул [[start,end], ...] из settings (или дефолт)., «Летний режим» этап 1: слабейший предмет + календарь каникул. Lock-парити…, Сидит оценку с УНИКАЛЬНОЙ датой (UNIQUE на student+subject+date+raw_text)., Предмет с 1 двойкой (count<3) — шум, игнор; берём предмет с ≥3 оценок., Если не каникулы — джоба выходит сразу, без обращения к students., _seed(), test_holiday_bad_json_falls_back_to_default() (+7 more)

### Community 85 - "Периферия #85"
Cohesion: 0.13
Nodes (15): client(), fixture, Тесты для /api/chat/history и /api/chat/clear endpoints (PR_H2). Endpoint'ы…, POST /api/chat/clear/<id> → удаляет all сообщения для (tg_id, family_id)., Clear от чужого tg_id НЕ удаляет нашу историю (auth scope)., Семья с подпиской + ученик + 4 сообщения в чате (2 turn'а)., GET /api/chat/history/<id> → JSON {messages: [...]} chronologically., Если в чате не было сообщений — возвращаем messages: []. (+7 more)

### Community 86 - "Периферия #86"
Cohesion: 0.13
Nodes (15): client(), fixture, ETag для /api/dashboard — 304 Not Modified при unchanged watermark. Проверяем:…, Один и тот же student с разными ?days получает разные ETag., Если клиент шлёт устаревший ETag — получает 200 (не 304)., Создаёт активную семью с одним учеником и одной оценкой., Первый запрос → 200 + заголовок ETag., Повторный запрос с актуальным If-None-Match → 304, пустое тело. (+7 more)

### Community 87 - "Периферия #87"
Cohesion: 0.12
Nodes (7): fixture, Unit-тесты unified Sender (notifications/sender.py)., Sender, инициализированный с MagicMock-bot. send_message успешен., Admin alerts и daily summaries отправляются даже в тихие часы: либо сами в…, sender_with_bot(), test_should_defer_grade_events(), test_should_NOT_defer_admin_and_summaries()

### Community 88 - "Периферия #88"
Cohesion: 0.17
Nodes (12): _fake_call(), _filter_func(), Тесты для пакета PR-J (UX-nav-cleanup). Покрывают: - B17: метки reply-keyboard…, В ai_chat_mode метка «Оценки»/«Меню» НЕ должна матчиться ai_chat-хендлером…, Та же метка ДОЛЖНА матчиться handle_menu_buttons (куда она проваливается)., test_b17_ai_chat_filter_skips_menu_labels(), test_b17_menu_handler_catches_label(), test_callback_add_child_answers_callback() (+4 more)

### Community 89 - "Периферия #89"
Cohesion: 0.16
Nodes (15): Hard Paywall on Core = Antipattern, Monetization Rebuild (free tier, trial, school year unit), Telegram API Modernization Research and Plan, CloudStorage for last_seen Cross-Device, isVersionAtLeast Gate for Every SDK Call, Do Not Gamify Grades (effects only for effort), Native AI Streaming via sendMessageDraft, Star Subscriptions via create_invoice_link (subscription_period) (+7 more)

### Community 90 - "Периферия #90"
Cohesion: 0.20
Nodes (13): _gc(), is_rate_limited(), Per-user rate limiter для Telegram bot handlers. Thread-safe (под…, Очищает записи неактивных пользователей. Вызывается под локом., True, если пользователь превысил лимит (RATE_LIMIT_MAX за RATE_LIMIT_WINDOW…, Полный сброс — для тестов., reset(), Тесты per-user rate limiter'а. Импортируем напрямую src.rate_limiter — он не… (+5 more)

### Community 91 - "Периферия #91"
Cohesion: 0.19
Nodes (14): _check_marker(), _last_run_key(), Записывает маркер в БД и в кэш. Вызывается после успешного выполнения job'а., Запускает job под локом с проверкой, что задача ещё не выполнена сегодня.…, True, если задача с таким маркером УЖЕ выполнялась. Сначала смотрим в память;…, _run_job_safe(), _set_marker(), Тест маркеров scheduler-задач: один и тот же маркер не выполнится дважды. (+6 more)

### Community 92 - "Периферия #92"
Cohesion: 0.13
Nodes (11): Tests for AI conversation history (PR_D R6). Multi-turn чат: user задаёт вопрос…, Сохранили 3 сообщения — получили в хронологическом порядке., Разные ученики — разные ветки беседы., Разные родители — изолированные истории даже про одного ребёнка., Получаем последние N сообщений когда их больше limit., Sanity: только 'user' и 'assistant' allowed., test_history_isolated_by_parent(), test_history_isolated_by_student() (+3 more)

### Community 93 - "Периферия #93"
Cohesion: 0.21
Nodes (14): Тесты авторизации в БД-слое — после критических фиксов от 2026-04. Покрывают: -…, Создаёт типичную конфигурацию: админ, глава, член, посторонний., Обычный член (не глава, не админ) не может управлять семьёй., Несуществующая семья → доступа нет ни у кого, кроме админа., Дважды использовать один инвайт нельзя (защита от гонки)., _setup_family_with_users(), test_admin_can_manage_any_family(), test_head_can_manage_own_family() (+6 more)

### Community 94 - "Периферия #94"
Cohesion: 0.13
Nodes (14): fixture, Очередь групповых уведомлений для тихих часов. Появилась после инцидента…, queue_group_notification → get_and_clear возвращает в порядке вставки., Один chat_id с разными thread'ами — изолированные очереди., get_all_queued_group_targets возвращает distinct пары., Создаёт семью с групповым чатом и одним учеником., В тихие часы _send_to_groups_for_student пишет в queue, не вызывает bot.send.…, Вне тихих часов — отправка через Sender → bot.send_message, очередь не… (+6 more)

### Community 95 - "Периферия #95"
Cohesion: 0.20
Nodes (12): _archive_count(), _insert_archive(), Чистка дублей в grade_history_archive (миграция 0005) на живом PostgreSQL.…, Пишем в обход UNIQUE-индекса нельзя, поэтому сеем дубли там, где индекс не…, Барьер стоит: вторая такая же строка с непустой датой не вставится., Legacy-записи (grade_date IS NULL, 242 штуки на проде) частичный UNIQUE не…, Разные оценки одного предмета в один день (пересдача «3» и «5») — не дубли, обе…, Повторный прогон ничего больше не удаляет. (+4 more)

### Community 96 - "Периферия #96"
Cohesion: 0.18
Nodes (14): RFC: Web Portal, Admin Panel and Public Site, Admin Login Rate-Limit, Lockout and Audit Log, Additive Auth Tables (auth_otp, auth_refresh_tokens, auth_admin_log), Caddy /api/* Carve-Out (legacy Flask vs FastAPI), Hugo Static Landing + Docs (3 languages), Next.js Route Groups (portal) / (admin) in One Process, Phone + OTP-via-Bot → Password → JWT Auth, Single-Domain Path-Based Topology (grades.railtech.uz) (+6 more)

### Community 98 - "Периферия #98"
Cohesion: 0.19
Nodes (13): Grade Monitor Design v2.0 (2026-03-03), cell_reference as Dedup Identity, Hybrid Dirty-Data Sanitization, 5-Minute Polling of «Сегодня» Sheet, B12 DB Pool max_size < Fetch Workers, Manual Runbook Step a Migration Depends On = Antipattern, _reconcile_academic_year Self-Healing (Phase 1.5), RFC: Single Source of Truth and Stable grade_date (+5 more)

### Community 99 - "Периферия #99"
Cohesion: 0.21
Nodes (13): Business Audit and Growth Plan (2026-07-13), eMaktab/Kundalik State Monopoly Competitor, eMaktab Partner API as TAM Option, Zero Product Analytics (Blocker #1), Private-School Wedge Two-Beat Strategy, Google Sheets Source = TAM Ceiling, RFC: GradeSource Integration Abstraction, Abstraction Only Where Replacement Is Foreseen (+5 more)

### Community 100 - "Периферия #100"
Cohesion: 0.18
Nodes (12): get_last_alert_at(), datetime, Proactive AI alerts dedup (PR_H5). Хранит лог отправленных alert'ов чтобы не…, Логирует факт отправки alert'а. Возвращает row id., True если за последние `hours` часов уже отправлялся alert этого типа.…, Возвращает timestamp последнего alert'а указанного типа, или None. psycopg…, save_alert(), was_alerted_recently() (+4 more)

### Community 101 - "Периферия #101"
Cohesion: 0.23
Nodes (12): _dedup_preserve_order(), Удаляет точные дубли сообщений с сохранением порядка первого появления. Defense…, Регрессия: morning flush больше не отправляет 14 копий одной оценки. Defense in…, Если 'b' встречается раньше 'a' в финальном списке — первая позиция., Воспроизведение конкретного инцидента: 14 копий двух разных уведомлений. До…, Сообщения с HTML — обычные строки, сравнение exact match., test_dedup_empty(), test_dedup_handles_html_messages() (+4 more)

### Community 102 - "Периферия #102"
Cohesion: 0.15
Nodes (11): Регрессия: глава семьи должен видеть детей даже если он не залинкован явно…, Контроль: обычный член семьи (через family_links) тоже видит детей., Контроль: посторонний не видит чужих детей., Если указать family_id — фильтр работает корректно., get_families_for_user должен видеть семью через head_id даже без family_links.…, Контроль безопасности: посторонний не видит чужих семей., test_filter_by_family_id(), test_head_sees_family_via_head_id_only() (+3 more)

### Community 103 - "Периферия #103"
Cohesion: 0.26
Nodes (12): _date_label_today_ru(), _date_label_yesterday_ru(), Регрессия: дубли в grade_history из-за race между monitor (двухфазное…, Контр-проверка: вчерашние даты ОБЯЗАНЫ импортироваться — monitor их уже не…, В «Все оценки» две колонки с одинаковой датой (data quality issue у учителя) —…, «13 мая» — строка, которую парсер дат поймёт как сегодняшнюю в TZ Ташкент., ГЛАВНЫЙ race-тест. До фикса проваливался: history-sync вставлял запись из «Все…, _seed_student() (+4 more)

### Community 104 - "Периферия #104"
Cohesion: 0.15
Nodes (5): addElements(), beforeUpdate(), configure(), initialize(), os()

### Community 105 - "Периферия #105"
Cohesion: 0.17
Nodes (10): Регрессия пост-миграции sqlite→PostgreSQL: потребители дат. psycopg отдаёт…, get_family_subscription отдаёт subscription_end как datetime (PG) — тул…, cmd_subscription (handlers/subscription.py) при активной подписке рендерит дату…, _send_family_manage_menu (handlers/family.py) для админа при активной подписке…, pdf_export: строка без grade_date, но с datetime в date_added, вперемешку со…, test_cmd_subscription_active_sub_pg_datetime(), test_family_manage_menu_active_sub_pg_datetime(), test_pdf_export_grade_date_none_datetime_added() (+2 more)

### Community 107 - "Периферия #107"
Cohesion: 0.18
Nodes (11): Dashboard 401 «Invalid hash» Troubleshooting, Three Features Plan: AI, WebApp, i18n (2026-03-15), Localized BUTTON_ACTIONS Text Routing, Claude AI Grade Analytics Feature, i18n Translation Engine (ru/uz/en), Telegram initData HMAC Validation, Telegram Mini App Grade Dashboard, Handler Registration Order Invariant (+3 more)

### Community 108 - "Периферия #108"
Cohesion: 0.22
Nodes (10): gs-i18n skill (user-facing text rules), HTML parse_mode only, never Markdown, Locale sync invariant (ru/uz/en, identical placeholders), Domain decomposition for parallel subagents (core/subs/AI/family/webapp/deploy), gs-subagent-brief skill (mandatory subagent context block), Subagent context block (PG %s, sync bot, i18n x3, Docker tests), Development Guide (current, 2026-09), post-edit-check hook (compile .py, validate locale JSON) (+2 more)

### Community 109 - "Периферия #109"
Cohesion: 0.22
Nodes (10): Quiet-hours queued notifications lose inline buttons, Domain calendar check (academic year, summer mode, quarter boundary), Quiet hours 22:00-07:00 Tashkent with notification queues, Google Sheets link validator (parse spreadsheetId, probe metadata), Scheduler jobs with per-job locks and DB markers, Maintenance & Deployment Guide (current, 2026-09), Open debt: quarter_grades lacks academic_year (overwrite risk ~November), Production scheduler job calendar (Tashkent times) (+2 more)

### Community 110 - "Периферия #110"
Cohesion: 0.22
Nodes (10): Alembic-only schema policy (manual op.execute, no target_metadata), DEFAULT semantics for pre-existing rows in backfill, gs-migration skill (schema changes via Alembic), Asia/Tashkent server TZ vs naive-UTC storage (today = naive UTC + 5h), Replace in-code PRAGMA migrations with a single Alembic baseline, BIGINT required for Telegram ids (PG INTEGER is 32-bit), Interval passed as bind parameter trap (f'-{days} days'), SQLite to PostgreSQL migration estimate (2026-06-29) (+2 more)

### Community 111 - "Периферия #111"
Cohesion: 0.22
Nodes (10): Bare-Metal Deployment Guide (deploy/README.md), Heartbeat File + systemd Timer Watchdog, Narrow Passwordless sudo for deploy User, Off-Site pg_dump via rclone crypt Remote, Backup Without a Tested Restore Does Not Count, B3 Backup Chain Dead After PG Migration, Web Rewrite Status and Action Items, «Do Not Touch main.py/monitor/webapp» Freeze Lifted (+2 more)

### Community 112 - "Периферия #112"
Cohesion: 0.22
Nodes (10): Session Handoff 2026-07-13, PostgreSQL/psycopg v3 Conventions (%s, objects not strings), Stacked PR Base-Branch Trap, Technical Audit and Refactor Plan (2026-07-13), Atomic Symlink-Swap Deploy with Rollback, B1 successful_payment Non-Atomic (money taken, no subscription), B2 Deploy Not Gated on Tests, B4 `[:10]` Slicing on PG datetime Objects (+2 more)

### Community 113 - "Периферия #113"
Cohesion: 0.20
Nodes (10): get_active_spreadsheets(), Список {student_id, fio, spreadsheet_id, display_name} для опроса Sheets., Вставляет или обновляет четвертную оценку. True если значение изменилось.…, upsert_quarter_grade(), import_history_for_all_students(), import_quarters_for_student(), Импортирует четвертные оценки из листа "Четверти". Структура листа: - Строка 1:…, Импорт истории для всех студентов из листа «Все оценки». Если force=False… (+2 more)

### Community 114 - "Периферия #114"
Cohesion: 0.20
Nodes (10): start_weekly_scheduler(), _heartbeat_loop(), main(), Раз в N секунд touch'ит файл data/.heartbeat. Docker healthcheck смотрит mtime…, Запускает Telegram бота в режиме polling. Обёртка вокруг bot.polling с…, Регистрирует команды бота в меню Telegram (кнопка / в чате). Разные scope для…, _register_bot_commands(), start_bot() (+2 more)

### Community 115 - "Периферия #115"
Cohesion: 0.27
Nodes (6): test_by_subject_includes_last_grade_and_trend(), test_by_subject_trend_flat_for_stable(), Тесты pure-функций агрегации метрик дашборда из webapp/app.py. Проверяем…, TestComputeBySubject, compute_by_subject(), Разбивка по предметам, отсортированная по среднему DESC. Dashboard refactor:…

### Community 116 - "Периферия #116"
Cohesion: 0.29
Nodes (9): _load(), Тесты синхронности локалей: ru/uz/en должны иметь одинаковые ключи. CLAUDE.md…, Все три локали должны иметь идентичный набор ключей., Пустые строки в локали — обычно недоделанный перевод., Если в ru есть {placeholder}, в uz/en для того же ключа должен быть тот же…, test_all_locales_load_as_valid_json(), test_format_placeholders_match_across_locales(), test_locale_keys_in_sync() (+1 more)

### Community 117 - "Периферия #117"
Cohesion: 0.22
Nodes (8): _msg(), Регрессии для прод-стабильности (июнь 2026). Покрывает три бага, найденных при…, Каждый job, вызываемый _run_job_safe в _scheduler_loop, обязан иметь лок.…, Текст, совпадающий с label кнопки, не должен матчиться в группе., get_sheets_service кэшируется в thread-local, не в глобальном singleton., test_every_scheduled_job_has_a_lock(), test_matches_label_ignores_group_messages(), test_sheets_service_is_thread_local()

### Community 118 - "Периферия #118"
Cohesion: 0.27
Nodes (9): Этап 2 RFC: read-path смотрит на grade_date вместо date_added. Главный…, Запись с date_added в марте, но grade_date в мае — должна попасть в период «за…, Запись с grade_date=сегодня, но date_added на год назад — должна попасть в…, Webapp группирует по grade_date если он есть, fallback на date_added., _seed_grade(), test_compute_trend_by_day_uses_grade_date(), test_get_history_uses_grade_date_for_period(), test_today_grades_use_grade_date() (+1 more)

### Community 119 - "Периферия #119"
Cohesion: 0.27
Nodes (9): _load(), parametrize, Проверка что webapp/static/locales/{ru,uz,en}.json синхронны по ключам., Все три файла должны иметь одинаковый набор ключей., Ни в одном переводе не должно быть пустых строк (typo при копи-пасте)., Если в ru.json есть {placeholder} — он должен быть в других языках., test_locales_have_same_keys(), test_locales_no_empty_values() (+1 more)

### Community 120 - "Периферия #120"
Cohesion: 0.20
Nodes (9): name, private, scripts, build, dev, lint, start, typecheck (+1 more)

### Community 121 - "Периферия #121"
Cohesion: 0.22
Nodes (9): gs-incident skill (symptom to PR), Incident workflow: timeline, read-only DB evidence, root cause, regression test, Never stack PRs on a feature branch (GitHub merges into parent), gs-pr skill (branch, tests, PR), Read-only-by-default production rule (no manual edits, no manual restarts), Atomic staged release switch with auto-rollback (B19), Heartbeat file watchdog (mtime > 180s = restart), Cutover rollback plan (live sentinel.db read-only, revert main) (+1 more)

### Community 122 - "Периферия #122"
Cohesion: 0.28
Nodes (9): WebApp initData HMAC validation quirks (URL-decoded values, signature included), Architecture doc (historical, 2026-04-30), error_reporter single catch point with optional Sentry, Subscription and Telegram Payments flow (server-controlled invoice_payload), Audit & Refactor changelog 2026-04-30, Core entity model: Family, Parent, Student, Snapshot, Project overview / detailed specification (historical), Commercial model: 3 subscription tiers tied to family (+1 more)

### Community 123 - "Периферия #123"
Cohesion: 0.33
Nodes (9): Brand Teal #35819B, Design Tool Artboard Export (Desktop-HD-Copy-9), Diamond Frame (Rotated Square Polygon), Frontend Asset Bundle (frontend/assets), Outline Logo Mark (Group 3), RailTech Brand Identity, R/T Monogram Glyph, Solid Logo Variant (logo.svg) (+1 more)

### Community 124 - "Периферия #124"
Cohesion: 0.36
Nodes (8): Регресс: летом (нет свежих оценок) weekly_reports НЕ должен слать ложный admin-…, Лето: 0 свежих оценок → ученик пропущен, AI не зовётся, трекинга нет., Есть свежие данные, но AI вернул None → это реальный fail, трекаем., _seed_recent(), test_no_recent_data_does_not_track_or_call_ai(), test_with_data_and_ai_ok_tracks_success(), test_with_data_but_ai_fail_does_track_failure(), _wire()

### Community 126 - "Периферия #126"
Cohesion: 0.36
Nodes (8): Chart Axes / Grid Motif, Frontend Assets Bundle, GradeSentinel Brand Identity, GradeSentinel Logo Mark (SVG), Magnifier / Watchfulness Motif, Sketch Export Artifact (Group 2 / Desktop HD Copy 9), Teal Diamond Badge (#35819B), White Stroked Line Glyph

### Community 127 - "Периферия #127"
Cohesion: 0.39
Nodes (6): _grade_count(), Смена ссылки на таблицу у существующего ученика сохраняет историю. Сентябрьский…, _seed_grade(), _spreadsheet_of(), test_relink_preserves_history(), test_relink_without_display_name_keeps_old()

### Community 128 - "Периферия #128"
Cohesion: 0.29
Nodes (6): docSections, io, tocLinks, toggle, topbar, topbarNav

### Community 129 - "Периферия #129"
Cohesion: 0.33
Nodes (6): get_plans_from_db(), Any, Key-value store: `settings` таблица. Хранит как простые пары (например,…, Возвращает тарифы из БД или None если не заданы / невалидный JSON., Сохраняет тарифы в БД (JSON)., save_plans_to_db()

### Community 130 - "Периферия #130"
Cohesion: 0.29
Nodes (7): callback_open_admin_as_parent(), callback_open_admin_panel(), callback_start_lang(), callback_query_handler, Выбор языка при первом /start — сохраняем и показываем авторизацию., Inline-кнопка из admin welcome → открывает admin panel., «👨 Я родитель» — admin переключается в parent-режим про своих детей. Tier 2…

### Community 131 - "Периферия #131"
Cohesion: 0.33
Nodes (6): Тесты архивирования старых оценок и чистки БД., Чистка истёкших инвайтов не трогает активные., Вставляет оценку с указанной давностью (через прямой UPDATE date_added)., _seed_old_grade(), test_archive_moves_old_grades(), test_cleanup_invites_no_active()

### Community 132 - "Периферия #132"
Cohesion: 0.29
Nodes (3): Тесты config — fallback на default при невалидных env., is_quiet_hours читает QUIET_HOURS_START/END из config., test_quiet_hours_config_used_in_helper()

### Community 133 - "Периферия #133"
Cohesion: 0.29
Nodes (3): Тесты создания/использования промокодов — закрывают регрессию SQL-injection., Если кто-то передал нечисловое значение в expires_days — функция возвращает…, test_promo_sql_injection_attempt_is_safe()

### Community 134 - "Периферия #134"
Cohesion: 0.43
Nodes (3): TestComputeTrendByDay, compute_trend_by_day(), Группирует оценки по дням, возвращает [{date, avg, count}] за весь период. Дни…

### Community 135 - "Периферия #135"
Cohesion: 0.47
Nodes (4): DEBIAN_FRONTEND, fail(), log(), install.sh script

### Community 136 - "Периферия #136"
Cohesion: 0.53
Nodes (6): Project Brand Mark, Browser Tab Identity Asset, Frontend Static Assets, Favicon Icon, Teal Diamond Badge Shape, White Line-Art Emblem

### Community 137 - "Периферия #137"
Cohesion: 0.47
Nodes (6): Browser Tab Icon Usage, Teal Diamond Badge Mark, GradeSentinel Brand Identity, Hugo Landing Site (landing/), Landing Favicon (32x32 PNG), White Line-Art Emblem Inside Badge

### Community 138 - "Периферия #138"
Cohesion: 0.60
Nodes (4): Alembic environment for GradeSentinel (PostgreSQL via psycopg v3). URL берётся…, run_migrations_offline(), run_migrations_online(), _url()

### Community 145 - "Периферия #145"
Cohesion: 0.50
Nodes (4): generate_weekly_summary(), Используется планировщиком воскресной рассылки — глотаем API-ошибки, чтобы один…, Публичная точка входа для планировщика (`schedulers._send_weekly_ai_reports`).…, send_weekly_reports()

### Community 146 - "Периферия #146"
Cohesion: 0.67
Nodes (3): Color, _grade_color(), Цвет для среднего балла — соответствует webapp UI.

## Ambiguous Edges - Review These
- `gs-i18n skill (user-facing text rules)` → `Draft broadcast: GradeSentinel v2.0 announcement`  [AMBIGUOUS]
  Docs/draft_broadcast_v2.md · relation: conceptually_related_to
- `cell_reference is metadata, not identity (content-based identity)` → `Aborted-transaction trap (PG poisons transaction after IntegrityError)`  [AMBIGUOUS]
  Docs/migration-sqlite-to-postgres-estimate-2026-06-29.md · relation: conceptually_related_to
- `Subscription gate on monitoring and AI` → `anthropic SDK (>=0.50,<1.0)`  [AMBIGUOUS]
  landing/content/docs/_index.md · relation: conceptually_related_to
- `White Line-Art Emblem` → `Project Brand Mark`  [AMBIGUOUS]
  frontend/assets/favicon.png · relation: semantically_similar_to
- `Outline Logo Mark (Group 3)` → `RailTech Brand Identity`  [AMBIGUOUS]
  frontend/assets/logo-outline.svg · relation: conceptually_related_to
- `R/T Monogram Glyph` → `RailTech Brand Identity`  [AMBIGUOUS]
  frontend/assets/logo-outline.svg · relation: conceptually_related_to
- `White Stroked Line Glyph` → `Chart Axes / Grid Motif`  [AMBIGUOUS]
  frontend/assets/logo.svg · relation: conceptually_related_to
- `White Line-Art Emblem Inside Badge` → `GradeSentinel Brand Identity`  [AMBIGUOUS]
  landing/static/favicon.png · relation: semantically_similar_to

## Knowledge Gaps
- **137 isolated node(s):** `post-edit-check.sh script`, `gradesentinel-api`, `gradesentinel-db-backup.sh script`, `DEBIAN_FRONTEND`, `offsite-backup.sh script` (+132 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **31 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **What is the exact relationship between `gs-i18n skill (user-facing text rules)` and `Draft broadcast: GradeSentinel v2.0 announcement`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `cell_reference is metadata, not identity (content-based identity)` and `Aborted-transaction trap (PG poisons transaction after IntegrityError)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `Subscription gate on monitoring and AI` and `anthropic SDK (>=0.50,<1.0)`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `White Line-Art Emblem` and `Project Brand Mark`?**
  _Edge tagged AMBIGUOUS (relation: semantically_similar_to) - confidence is low._
- **What is the exact relationship between `Outline Logo Mark (Group 3)` and `RailTech Brand Identity`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `R/T Monogram Glyph` and `RailTech Brand Identity`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._
- **What is the exact relationship between `White Stroked Line Glyph` and `Chart Axes / Grid Motif`?**
  _Edge tagged AMBIGUOUS (relation: conceptually_related_to) - confidence is low._