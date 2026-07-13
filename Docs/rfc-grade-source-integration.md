# RFC: GradeSource — абстракция источника оценок для внешних интеграций

**Статус:** Draft · **Дата:** 2026-07-13 · **Автор:** Claude (по запросу owner)
**Связано:** `Docs/plans/2026-07-13-business-audit-and-growth-plan.md` (eMaktab API как опцион трансформации TAM),
`Docs/rfc-grades-source-of-truth.md` (grade_date, identity-ключ).

---

## 1. Мотивация

Сегодня GradeSentinel читает оценки **только** из Google Sheets. Это структурное ограничение рынка
(бизнес-аудит 13.07): госшколы Узбекистана обязаны вести оценки в Kundalik/eMaktab, а не в Sheets, поэтому
реальный TAM текущей архитектуры — частные школы, кружки, «параллельные листы» отдельных учителей.

Три сценария, где нужен второй источник (все предвидимы):
1. **Партнёрский API eMaktab** (`api.emaktab.uz/partners`, OAuth2) — если доступ реален, продукт становится
   «лучшим родительским клиентом поверх госдневника» с AI-дифференциацией → TAM 6,87 млн школьников.
2. **B2B частные школы** — у каждой своя LMS/формат таблиц; коннекторы под них.
3. **Другие электронные дневники** региона (Kundelik.kz и т.п.) при географической экспансии.

**Тезис RFC:** источник данных — единственное место в архитектуре, где замена реально предвидится, поэтому
именно здесь оправдана абстракция (ports & adapters). Это НЕ приглашение к «корпоративной архитектуре»
везде — остальной монолит остаётся монолитом.

## 2. Хорошая новость: ядро уже source-agnostic

Аудит связки (13.07) показал: **канонический слой данных уже не зависит от Sheets.**

- **Каноническая модель оценки де-факто существует** — `grade_history` с content-based identity
  `UNIQUE(student_id, subject, grade_date, raw_text)`. `cell_reference` осознанно выведен из identity
  (инцидент 2026-05-21) и является лишь origin-меткой.
- **Все потребители читают только БД** (подтверждено grep = 0 вызовов Sheets): `webapp/app.py`,
  `analytics_engine`/`src/ai`, `src/notifications`, `ai_chat`, дайджесты. Замена источника их не касается.
- **Sheets-специфика сосредоточена в 4 модулях:** `google_sheets.py`, `history_importer.py`,
  fetch-фаза `monitor_engine.py` (`_fetch_student_sheet` + `_parse_master_sheet_for_date`),
  онбординг `handlers/family.py`.
- **Точный шов уже виден в коде:** после `_parse_master_sheet_for_date` монитор работает со списком
  `[(subject, raw_grade)]` и больше не знает про Sheets. **Это и есть готовый контракт порта.**

Вывод: это не переписывание, а вынос уже изолированной логики за интерфейс + одна миграция схемы.

## 3. Дизайн

### 3.1. Канонический DTO — `GradeEvent`

Нормализованная оценка, которую любой адаптер обязан произвести (соответствует кортежу, к которому уже
сходятся monitor и importer — `monitor_engine.py:527-528`, `history_importer.py:190-192`):

```python
@dataclass(frozen=True)
class GradeEvent:
    subject: str
    grade_date: date          # ISO-дата оценки (не строка)
    raw_text: str             # канонический текст («5», «2/5», «н»)
    grade_value: float | None # среднее численных (None для «н»/нечисловых)
    origin_ref: str = ""      # debug-метка источника («Все оценки!2026-05-23:Математика»
                              #   / «emaktab:{grade_id}»); НЕ identity

@dataclass(frozen=True)
class QuarterEvent:
    subject: str
    quarter: int
    grade_value: float | None
    raw_text: str
```

Identity остаётся content-based `(student_id, subject, grade_date, raw_text)` — от источника не зависит.

### 3.2. Порт — интерфейс `GradeSource`

```python
class GradeSource(Protocol):
    source_type: str  # "google_sheets" | "emaktab" | ...

    def fetch_today_grades(self, ref: SourceRef) -> list[GradeEvent]:
        """Оценки за сегодня (Ташкент). Заменяет _fetch_student_sheet + _parse_master_sheet_for_date."""

    def fetch_history(self, ref: SourceRef) -> list[GradeEvent]:
        """Полная история (для hourly-sync/импорта при добавлении ученика). Заменяет import_history."""

    def fetch_quarters(self, ref: SourceRef) -> list[QuarterEvent]:
        """Четвертные. Заменяет check_for_quarter_changes fetch."""

    def display_name(self, ref: SourceRef) -> str | None:
        """Имя ученика от источника (Sheets: заголовок таблицы; API: поле профиля)."""

    def validate(self, ref: SourceRef) -> ValidationResult:
        """Проверка доступности источника при онбординге (Sheets: 403/404; API: auth)."""
```

`SourceRef` — непрозрачный для ядра идентификатор источника: для Sheets `{spreadsheet_id}`, для eMaktab
`{school_id, pupil_id, credential_ref}`. Хранится как `source_type` + JSON `source_config`.

**Anti-corruption layer:** адаптеры — единственное место, где живут форматы внешних систем (русские даты,
шапки листов, JSON-схемы API). Внутрь ядра протекают только `GradeEvent`/`QuarterEvent`.

### 3.3. Адаптеры

- **`GoogleSheetsSource`** (первый, из существующего кода): оборачивает `google_sheets.py` +
  переносит парсеры `history_importer.py` (`MONTH_MAP`, `_parse_russian_date`, `_parse_master_sheet_for_date`,
  `_col_letter`, диапазоны «Все оценки»/«Неделя»/«Четверти»). Логика НЕ меняется — только переезжает под
  интерфейс. `sanitize_cell` («X/Y») остаётся здесь (семантика Sheets-ячейки).
- **`EMaktabSource`** (будущий): OAuth2 к `api.emaktab.uz/partners`, маппинг JSON → `GradeEvent`. **Не нужны**
  парсеры дат/шапок (API отдаёт ISO-даты и структурные записи), `sanitize_cell` (мультиоценки придут
  отдельными объектами — адаптер сам соберёт `raw_text`/`grade_value` в канон), `get_spreadsheet_title`
  (есть поле профиля).

### 3.4. Реестр источников

```python
def get_source(source_type: str) -> GradeSource: ...  # фабрика по students.source_type
```

Монитор больше не импортирует `google_sheets` — берёт адаптер из реестра по типу ученика.

### 3.5. Схема (миграция)

`students`: `spreadsheet_id text NOT NULL` → сохранить nullable для обратной совместимости + добавить:
- `source_type text NOT NULL DEFAULT 'google_sheets'`
- `source_config jsonb` (для Sheets: `{"spreadsheet_id": "..."}` — backfill из старой колонки)

Backfill: `UPDATE students SET source_config = jsonb_build_object('spreadsheet_id', spreadsheet_id),
source_type='google_sheets' WHERE spreadsheet_id IS NOT NULL`. Старую колонку оставить (не DROP) — читатели
мигрируют постепенно. Гейты опроса (`get_active_spreadsheets_*`, `db/families.py:29-53`) → фильтр по
`source_config IS NOT NULL` вместо `spreadsheet_id`.

### 3.6. Онбординг

`handlers/family.py`: вместо жёсткого «вставь Google-URL» → выбор типа источника (пока один — Sheets, UX
не меняется), затем ввод его идентификатора. Для eMaktab позже — форма логина/school-выбора. Relink
обобщается до «сменить источник» (история сохраняется — привязана к `student_id`, не к источнику).

### 3.7. Presentation-ссылка

Кнопка «Открыть таблицу» (`docs.google.com/spreadsheets/d/{id}` в `notification_helpers.py`,
`schedulers.py:361`, `family.py`) → метод адаптера `deep_link(ref) -> str | None` (Sheets — URL таблицы,
eMaktab — ссылка в их приложение, либо None). `get_unnotified_grades` (`db/grades.py:197`) перестаёт
тянуть `spreadsheet_id` ради ссылки.

## 4. План внедрения (фазы, обратная совместимость на каждом шаге)

**Эпик разбит так, чтобы каждый PR был безопасен и тестируем; порядок — снизу вверх.**

- **RFC-1 · Канон + порт (без смены поведения).** Ввести `GradeEvent`/`QuarterEvent`, `GradeSource`
  Protocol, реестр. `GoogleSheetsSource` = обёртка над текущим кодом. Монитор/импортёр переключить на
  `get_source(...).fetch_*` — но источник по-прежнему один, поведение идентично. Тесты: адаптер отдаёт те же
  `GradeEvent`, что старый путь (golden-тест на реальных матрицах). **Средне.**
- **RFC-2 · Схема source_type/source_config + backfill.** Миграция, гейты и онбординг на новую модель;
  `spreadsheet_id` остаётся заполненным для совместимости. **Средне.**
- **RFC-3 · Presentation deep_link через адаптер.** Косметика, много точек. **Мало.**
- **RFC-4 · EMaktabSource (когда/если есть доступ к API).** Новый адаптер, OAuth2, маппинг; онбординг-форма
  eMaktab. Ядро не трогается. **Средне** (зависит от API eMaktab).

**Некритичный путь:** RFC-1..3 можно делать сейчас как чистую подготовку архитектуры (снижает будущий риск,
даёт основание для письма в eMaktab «готовы интегрироваться за N недель»). RFC-4 — только после ответа eMaktab.

## 5. Non-goals

- НЕ вводить абстракции где-либо ещё (платежи, уведомления, БД остаются как есть).
- НЕ строить plugin-систему/микросервисы — реестр из 1-2 адаптеров, статически известных.
- НЕ трогать identity-ключ, outbox, потребителей (они уже source-agnostic).
- НЕ делать неофициальный парсинг eMaktab (юридический/платформенный риск) — только партнёрский API.

## 6. Риски и открытые вопросы

| Риск / вопрос | Заметка |
|---|---|
| Доступ к партнёрскому API eMaktab неизвестен | Написать им (действие из бизнес-плана Фаза 0). RFC-4 гейтится этим. |
| Двухфазный `_pending_grades` для API | У API-оценки стабильный id, но семантика «учитель стёр/исправил» сохраняется → механизм остаётся полезным, source-agnostic. Решить на этапе RFC-4. |
| Rate-limit/квоты у API отличаются от Sheets | Каждый адаптер несёт свою retry/quota-логику (у Sheets уже есть `[GOOGLE_QUOTA]` backoff). |
| Мультиоценки/спец-токены в API | Адаптер обязан собрать тот же канон (`raw_text`/`grade_value`); покрыть golden-тестами. |
| Хранение кредов источника (eMaktab OAuth) | `source_config` с токенами = чувствительные данные; шифровать/выносить в защищённое хранилище (не в открытый jsonb). Решить в RFC-4. |

## 7. Оценка

Канонический слой уже готов на ~70%. Вся Sheets-специфика — в 4 модулях + одна NOT NULL-колонка +
presentation-ссылка. Подготовка (RFC-1..3) — реалистично 3-4 PR силами Opus-агентов с golden-тестами на
эквивалентность. RFC-4 (eMaktab) — отдельно, по факту доступа к API.

**Рекомендация:** одобрить RFC-1..3 как подготовку архитектуры (низкий риск, высокая опциональная ценность),
RFC-4 держать как гейтированный опцион под ответ eMaktab. Реализацию — отдельным ТЗ для мультиагентов после
ревью этого RFC.
