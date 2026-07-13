# Session handoff — 2026-07-13 (для продолжения на другой машине)

> Свежей сессии Claude Code: **прочитай этот файл первым**, затем документы по ссылкам ниже. Память
> Claude (`~/.claude/`) машинно-локальна и НЕ переносится — весь durable-контекст здесь и в `Docs/`.

## Где всё сейчас

- **`main` @ `a47e7ee`** (origin), всё смержено, **521 тест зелёный**, прод задеплоен и здоров.
- **Branch protection ВКЛЮЧЕНА:** required check = **`pytest`** (имя job'а, не «Tests»!), force-push/удаление запрещены, enforce_admins=false, ревью не требуется (соло-репо).
- **Прод:** app-VPS `176.101.56.141` (bot+webapp+heartbeat active), DB-VPS PostgreSQL `170.168.6.209`/внутр.`10.0.0.2`. Миграция схемы head = **0003_grade_notified_outbox**. Деплой push→main (gated на pytest, staged с авто-откатом).
- **Масштаб (реальность):** 2 семьи (владельца+тест), 3 родителя, 2 ученика, 923 оценки, 0 внешней выручки, платежи Click/Payme НЕ подключены (нужно юрлицо). Pre-PMF.

## Что сделано в этой сессии (хронология)

1. **Изучение проекта** + локальная память (10 файлов, машинно-локальны).
2. **Технический аудит** (`Docs/plans/2026-07-13-technical-audit-and-refactor-plan.md`) → 9 PR (#95-107) исправлений: `[:10]`-краши datetime, атомарность платежей + миграция 0002, IDOR промокода + initData TTL, CI-гейт + атомарный деплой + lock, реальный off-site pg_dump, пул БД, ташкентский год, AI-чат устойчивость, UX/мёртвый код. **Все в проде.** По пути чинил инцидент деплоя (sudoers на VPS) и стековую ловушку GitHub.
3. **Фикс даты в AI-чате** (#106): today в первом system-блоке (Haiku терял низко-заметную дату). В проде.
4. **Бизнес-аудит** (`Docs/plans/2026-07-13-business-audit-and-growth-plan.md`): pre-PMF, конкурент eMaktab PRO (60k сум/год за TG-уведомления, GradeSentinel 6× дороже), Google Sheets = потолок TAM, стратегия «клин через частные школы», пересборка монетизации (free-tier/trial/годовой/winback — сейчас всего этого нет или фиктивно).
5. **Техдолг + модуляризация** (`Docs/plans/2026-07-13-tech-debt-and-modularization-tz.md`) → 6 PR (#108-114): notification outbox (write-then-notify потеря закрыта, миграция 0003), недеструктивная очередь + идемпотентность, раскол god-файлов (subscription.py 1491→пакет из 7; analytics_engine 1131→772 + пакет src/ai/; main.py 1031→485 + panel.py). **Все в проде.** Плюс фикс красного main (тест с `?`-плейсхолдерами из #107).
6. **RFC GradeSource** + **аудит webapp/schedulers** + снят блокер «не трогать webapp» (см. ниже).

## Открытые направления (НЕ сделано — для продолжения)

### Реальные баги в проде (из аудита webapp/schedulers, `Docs/plans/2026-07-13-webapp-schedulers-audit.md`)
Приоритет — эти баги живут в проде прямо сейчас:
- **schedulers B-H1:** групповая очередь застревает навсегда у семей с уведомлениями только в групповой чат (early-return `schedulers.py:303-306` + group_notification_queue не чистится по TTL).
- **schedulers B-H2/H3:** weekly AI loop в `handlers/analytics.py:89-100` — на серверной TZ (не Ташкент) + без персистентного маркера (дубли всем при рестарте в окне Вс 19:00). Это фактически второй незрелый планировщик.
- **webapp A-H1:** `fams[0]` недетерминирован при мульти-семейном ученике (чат-история путается).
- **webapp A-H3:** `_authorize_student_access` (security-критичный) БЕЗ тестов — только мокается.
- Предложенная волна **PR-W1..W4** (сначала баги + auth-тесты, потом модуляризация Blueprints/пакет) — в том же документе.

### RFC GradeSource (`Docs/rfc-grade-source-integration.md`)
Абстракция источника оценок под eMaktab API / B2B-школы. Ядро уже source-agnostic на ~70%. План RFC-1..4 (RFC-4=eMaktab гейтится доступом к их партнёрскому API `api.emaktab.uz/partners`). Не реализовано — ждёт решения owner. Действие из бизнес-плана: написать в eMaktab об условиях API.

### Бизнес (из бизнес-аудита, Фаза 0 первой)
Аналитика событий (её НЕТ вообще — блокер №1), честный лендинг + захват лидов, юрлицо для платежей, согласие родителей на данные ребёнка. Затем монетизационные рельсы, затем валидация спроса в частных школах.

## Указатель документов (все в git после этого коммита)

- `Docs/plans/2026-07-13-SESSION-HANDOFF.md` — этот файл
- `Docs/plans/2026-07-13-technical-audit-and-refactor-plan.md` — тех-аудит + 9 PR (сделано)
- `Docs/plans/2026-07-13-tech-debt-and-modularization-tz.md` — ТЗ техдолга + 6 PR (сделано)
- `Docs/plans/2026-07-13-business-audit-and-growth-plan.md` — бизнес-аудит + стратегия
- `Docs/plans/2026-07-13-webapp-schedulers-audit.md` — аудит + план след. волны (PR-W1..W4, НЕ сделано)
- `Docs/rfc-grade-source-integration.md` — RFC источников данных (НЕ сделано)
- `Docs/web-rewrite-status.md` — обновлён: блокер «не трогать webapp/main/monitor» СНЯТ

## Ключевые операционные факты (чтобы не наступить на грабли)

- **PG / psycopg v3:** плейсхолдеры `%s` (не `?`!), `get_db_connection()` коммитит сам, `conn_or_new(conn)` для атомарности нескольких операций, Row — Mapping, PG отдаёт datetime/date/bool ОБЪЕКТЫ (не строки — используй `to_date_str()`).
- **Бот синхронный**, i18n 3 локали синхронны (sync-тесты), новый ключ — во все три.
- **Стековые PR:** база должна быть `main`, иначе GitHub сливает в родительскую ветку, а не в main (ловушка, наступал дважды).
- **Тесты:** `docker compose -f docker-compose.test.yml run --rm -e BOT_TOKEN='12345:ci-test' -e ADMIN_GROUP_ID='0' tests pytest -q`. После смены миграции — `docker compose ... down -v` (том Postgres персистит).
- **Не пушить/мержить в main без явной команды owner** (прод-деплой). Ветка + PR + зелёный pytest + ревью owner.

## Как продолжить на другой машине

```bash
git clone <repo> && cd GradeSentinel_bot   # или git pull если уже склонирован
git checkout main && git pull
# прочитать этот handoff + нужные документы из Docs/
# при желании: docker compose -f docker-compose.test.yml run --rm ... pytest -q  (нужен Docker)
```
Свежей сессии Claude: контекста памяти не будет — начни с чтения `Docs/plans/2026-07-13-SESSION-HANDOFF.md`.
