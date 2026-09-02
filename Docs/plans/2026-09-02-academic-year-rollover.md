# Rollover учебного года — инцидент 2026-09-02, RFC и бенчмарк

> Статус: **Фаза 1 реализована** (PR `fix/academic-year-rollover`, миграция `0004_student_academic_year`).
> Фазы 2–3 — backlog, см. ниже.

## 1. Инцидент

**Симптом.** 2 сентября 2026 в 00:01 по Ташкенту монитор нашёл в таблице ученика
(id=1) колонку «2 сентября» с оценкой «5» по физкультуре, положил в pending, через
цикл подтвердил, записал `grade_history` (id 8087, `grade_date=2026-09-02`) и в
07:00 (после тихих часов) разослал родителю и в семейную группу как новую оценку.
Оценка — прошлогодняя (02.09.2025). Без фикса это повторялось бы **каждый учебный
день**: колонка «3 сентября», «4 сентября» и т.д. прошлогоднего листа совпадала бы
с «сегодня».

**Корневая причина.** В шапке листа «Все оценки!» даты без года («2 сентября»).
`_parse_russian_date` восстанавливал год от текущей даты (сентябрь–декабрь →
`now.year`, январь–август → `now.year-1`). Пока таблица и «сейчас» в одном учебном
году — это работает. Но школа выдаёт **новую ссылку каждый год**, а старая остаётся
привязанной к ученику до тех пор, пока родитель её не сменит. С 1 сентября старая
таблица «переезжает» на год вперёд целиком: все её колонки начинают читаться как
текущий учебный год. В системе не было понятия «учебный год таблицы».

**Почему не поймали раньше.** Тест `test_academic_year_boundary.py` проверял
границу 31 авг/1 сен для *актуальной* таблицы (и был прав), но не сценарий
«таблица прошлого года всё ещё привязана». Летом (июнь–август) old-sheet баг
невидим: колонок для летних дат в листе нет.

**Ручная мера (runbook).** До деплоя фикса удалить ложные записи монитора за
текущий год:

```sql
-- на DB-VPS: sudo -u postgres psql gradesentinel
SELECT id, student_id, subject, raw_text, grade_date, cell_reference
  FROM grade_history WHERE grade_date >= date '2026-09-01';
DELETE FROM grade_history
 WHERE grade_date >= date '2026-09-01'
   AND cell_reference LIKE 'Все оценки!2026-%';   -- формат cell_reference монитора
```

Важно сделать это **до** миграции 0004: backfill `academic_year` берёт
`max(grade_date)` ученика, ложная запись 2026-09-02 дала бы `academic_year=2026`
для старой таблицы. (Если миграция уже прошла с ложной записью — вручную
`UPDATE students SET academic_year = 2025 WHERE id = <id>`.)

## 2. Бенчмарк: как аналоги проводят год

Ресерч 2026-09-02 (агент, источники внизу). Ключевое: **все зрелые продукты держат
учебный год как first-class сущность**, а не выводят его из дат.

| Продукт | Модель года | Перепривязка родителя | Нэдж в начале года | Архив |
|---|---|---|---|---|
| eMaktab / Kundalik (UZ) | Явный объект «учебный год», админ переводит классы | Не описано публично | Не найдено | Да, через перевод классов |
| Kundelik.kz (KZ) | «Отчётные периоды» (год → четверти) | Ручная, связи пересоздаются при переводе | Не найдено | Архив с restore |
| Дневник.ру / eljur (RU) | Селектор года + четверти в UI | Массовый перевод классов, миграция ученика | Не найдено | Read-only архив по годам |
| ClassDojo | Год = новый класс-сущность | **Автоинвайт родителю** на новый класс | Да | Старые классы архивируются автоматически |
| PowerSchool | Год как измерение отчёта | Ежегодная re-enrollment | Да (регистрация) | Отдельная кнопка Grade History |
| Schoology / Canvas | Курс = год, auto-archive read-only | Rollover-операция | Да | Archived tab |
| Google Classroom | Класс = год, авто-архив летом | Наследуется один раз | Косвенно | Есть |

Паттерны, которые берём (ранжировано):
1. **Явный `academic_year`** на таблице ученика, не выводимый из дат при каждом чтении.
2. **Stale-детект источника** в начале года — не рассылать события из таблицы прошлого года.
3. **Явный flow «перевод на новый год»** для родителя (у нас уже есть «🔗 Сменить ссылку», PR #88).
4. **Нэдж в начале года** (ClassDojo/PowerSchool): «начался учебный год — обновите ссылку».
5. **Разводить текущий год и архив** в `/grades`, дашборде, AI (четверти помечены годом).
6. **Старую ссылку не удалять** — заморозить как read-only источник истории.
7. **Не наследовать привязку автоматически** — новая ссылка требует подтверждения (уже так: confirm с заголовком таблицы).
8. **Эвристика-предохранитель**: «сегодняшние» оценки 1:1 = ровно год назад → это эхо, не новые.

## 3. Дизайн

### Фаза 1 — сделано (этот PR)

**Данные.** `students.academic_year integer NULL` (миграция 0004) — год **начала**
учебного года таблицы (2025 = 2025/26). Backfill из `max(grade_date)` ученика
(сен–дек → year, янв–авг → year-1), без оценок — текущий учебный год.
NULL = «не определён» (новая привязка).

**Парсер** (`history_importer.py`):
- `_parse_russian_day_month()` → (day, month); `_parse_russian_date(..., academic_year=)`
  восстанавливает год из учебного года: `year_for_month(month, ay)`; `now`-fallback
  оставлен только для `academic_year=None`.
- `current_academic_year(today)`, `is_sheet_stale(ay, today)`.
- `infer_sheet_academic_year(months_with_grades, today)` — по **колонкам с оценками**
  (шапка нового листа может быть заполнена датами на год вперёд): есть оценки в
  янв–июн → `year-1`; иначе с августа → `year`, до августа → `year-1`.
- `import_history_for_student` — если `academic_year` NULL: читает master, выводит,
  пишет `set_student_academic_year`, импортирует с этим годом (master читается один раз).

**Монитор** (`monitor_engine.py`):
- `_handle_stale_sheet(student, today)` до fetch-фазы: stale → Sheets не читаем,
  `[STALE_SHEET]` в лог раз в день, нэдж семье (`_maybe_nudge_relink`: маркер
  `relink_nudge:{sid}` в `settings`, интервал `RELINK_NUDGE_INTERVAL_DAYS=7`, только
  вне тихих часов — иначе кнопка «🔗 Сменить ссылку» потеряется в очереди; кнопка
  только тем, кто `can_manage_family`), алерт админу раз в учебный год
  (`stale_admin_alert:{sid}`).
- `_parse_master_sheet_for_date(..., academic_year=)` — второй рубеж.
- `_looks_like_last_year_echo` — третий рубеж для `academic_year IS NULL`.
- `check_for_quarter_changes` и `_maybe_sync_all_grades` пропускают stale.

**Привязка.** `update_student_spreadsheet` сбрасывает `academic_year=NULL`
(новая таблица → importer переопределит). `add_student` — NULL по умолчанию.

**i18n.** `relink_nudge_new_year`, `alert_sheet_stale` в ru/uz/en.
`NotificationType.RELINK_NUDGE`.

### Фаза 2 — backlog (следующий PR)

- **`quarter_grades.academic_year`** — сейчас ключ `(student, subject, quarter)`:
  четверти нового года **перезапишут** прошлогодние при первом импорте новой
  таблицы (~ноябрь). Нужна миграция + ключ `(student, academic_year, subject,
  quarter)` + `/grades`/дашборд/AI-tools показывают текущий год по умолчанию.
- **Селектор года** в дашборде и `/grades` («2025/26 · 2026/27»); AI-контекст
  должен явно знать текущий учебный год и не смешивать годы в «динамике».
- **Scheduler-job 1 сентября** («с новым учебным годом» + чек ссылок) — сейчас
  нэдж идёт из монитора при первом stale-цикле; отдельный job даст праздничное
  сообщение всем и не зависит от подписки.
- **Admin `/status`**: список stale-учеников и дата последнего нэджа.
- **Летний режим** (`schedulers.py`, 01.06–25.08) — вывести даты в `settings`,
  синхронизировать с `ACADEMIC_YEAR_START_MONTH`.

### Фаза 3 — продуктовое

- Onboarding-подсказка при добавлении ребёнка в августе/сентябре: «это таблица
  нового учебного года?» (используем `infer_sheet_academic_year` как валидацию с
  явным предупреждением, если лист выглядит прошлогодним).
- Замороженные (stale) таблицы показывать в семье с меткой «архив 2025/26».
- RFC GradeSource: `academic_year` становится атрибутом источника, а не студента.

## 4. Тесты

`tests/test_academic_year_rollover.py` — 13 тестов: чистые функции (границы,
парсер с явным годом, регрессия инцидента на «2 сентября», инференс по
содержимому включая заполненную наперёд шапку), БД (сброс года при relink,
инференс+персист в импортере), монитор (stale не опрашивается + нэдж с кнопкой +
маркеры + алерт, тихие часы, возобновление после relink, echo-guard).

## 5. Источники бенчмарка

- eMaktab: «Перевод классов в следующий учебный год» — https://help.emaktab.uz/hc/ru/articles/360003703019
- Kundelik.kz: «Как начать учебный год?» — https://portal.kundelik.kz/ru/v2/articles/417
- Дневник.ру: массовый перевод классов — https://support.dnevnik.ru/94-95-100--massovyj-perevod-klassov/
- eljur: перевод учебного года (PDF) — https://eljur.ru/pdf/instr/instr_eljur_admin_next_year.pdf
- ClassDojo: Get Set Up for a New School Year — https://help.classdojo.com/hc/en-us/articles/37302665004685
- ClassDojo: Archive or Unarchive a Class — https://help.classdojo.com/hc/en-us/articles/202793905
- PowerSchool Student/Parent Portal guide — https://goshen1.powerschool.com/public/help/how_to/ps9x_student_parent_portal_user_guide.pdf
- Schoology rollover — https://help.digital.scholastic.com/hc/en-us/articles/4402700577165
- Google Classroom guardian summaries — https://support.google.com/edu/classroom/answer/6386354
