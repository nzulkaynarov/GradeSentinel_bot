---
name: gs-pr
description: "Ветки, коммиты и PR в GradeSentinel: база main (ловушка стековых PR), стиль сообщений, прогон тестов в Docker, branch protection, кто мержит. Использовать перед любым коммитом/PR."
---

# /gs-pr — ветка → тесты → PR

- **Ветка от `main`:** `git checkout -b <type>/<slug> main`. Типы: `fix/`, `feat/`, `refactor/`, `docs/`,
  `chore/`. **Никогда** не базировать PR на другой feature-ветке: GitHub сольёт его в родительскую ветку,
  а не в main (наступали дважды). Несколько PR — каждый от main, мержить по очереди с rebase.
- **Перед push:** `make test` (Docker + PG; после новой миграции — `make test-reset`). Локальный
  `pytest` без `DATABASE_URL` молча скипает БД-тесты — не доказательство.
- **Коммит:** русский, `type(scope): что` в первой строке, тело — почему/что именно (списком). Трейлеры
  из системного промпта (`Co-Authored-By`, `Claude-Session`). Без `--no-verify`, без amend опубликованного.
- **PR:** `gh pr create --base main`; тело: Инцидент/Контекст → Что сделано → Тесты (число passed) →
  **Runbook** (если есть миграция или ручные шаги на проде) → Backlog. Ссылка на документ в `Docs/plans/`.
- **Branch protection main:** required check — job **`pytest`** (не «Tests»). Force-push запрещён.
- **Merge = деплой на прод.** Мержит владелец; Claude — только по явной команде в этой сессии.
- После merge: удалить ветку, `make prod-status`, `make prod-schema`, обновить handoff/память.
