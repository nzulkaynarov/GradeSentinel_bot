# 📘 Detailed Project Specification: GradeSentinel (Antigravity)

Этот документ содержит исчерпывающее техническое описание системы GradeSentinel.

---

## 1. Концептуальная логика
Проект представляет собой **автономный мониторинговый сервис**, работающий внутри Docker-контейнера. Сервис обеспечивает мост между закрытой экосистемой Google Sheets и персональным пространством пользователя в Telegram.

### Основные сущности:
1. **Семья (Family):** Центральный узел. Объединяет баланс оплаты, список детей и список родителей.
2. **Родитель (Parent):** Физическое лицо, авторизованное через номер телефона. Получает уведомления.
3. **Ученик (Student):** Профиль ребенка, к которому привязан конкретный Spreadsheet ID.
4. **Снимок (Snapshot):** Текущий слепок оценок, хранящийся в БД для вычисления дельты (изменений).

---

## 2. Архитектура Базы Данных (ER-Model)

Для обеспечения связи **Many-to-Many** используется реляционная схема SQLite:

### Таблицы:
* **`parents`**: `id`, `fio`, `phone` (unique), `telegram_id` (unique), `role` (`admin`, `senior`), `lang` (ru/uz/en).
* **`students`**: `id`, `fio`, `spreadsheet_id`, `display_name`, `last_snapshot` (JSON).
* **`families`**: `id`, `family_name`, `head_id` (FK→parents), `subscription_end` (TIMESTAMP — дата окончания подписки).
* **`family_links`**: `family_id`, `parent_id`, `student_id` (связующая M2M таблица).
* **`grade_history`**: `id`, `student_id`, `subject`, `raw_text`, `grade_value`, `cell_reference`, `date_added`. Кэш оценок — `/grades` читает отсюда вместо live Google API.
* **`quarter_grades`**: `id`, `student_id`, `subject`, `quarter`, `grade_value`, `date_added`. Четвертные оценки.
* **`notification_queue`**: `id`, `telegram_id`, `message`, `created_at`. Очередь тихих часов (22:00–07:00).
* **`family_invites`**: `id`, `family_id`, `invite_code` (unique), `created_by`, `used_by`, `expires_at`, `is_used`. Одноразовые инвайт-ссылки (48ч).
* **`payments`**: `id`, `family_id`, `paid_by`, `amount`, `currency`, `plan`, `months`, `telegram_payment_charge_id`, `provider_payment_charge_id`, `created_at`. История платежей через Telegram Payments API (Click/Payme).
* **`user_states`**: `user_id`, `state`, `data`, `updated_at`. Временные состояния (выбор языка, pending invite).

### Индексы (8 шт):
* `idx_grade_history_student_date` — (student_id, date_added)
* `idx_grade_history_student_cell` — (student_id, cell_reference)
* `idx_family_links_parent` — (parent_id)
* `idx_family_links_student` — (student_id)
* `idx_family_links_family` — (family_id)
* `idx_parents_telegram` — (telegram_id)
* `idx_notification_queue_tg` — (telegram_id)
* `idx_quarter_grades_student` — (student_id)

---

## 3. Функциональные модули

### 3.1. Telegram Bot (Interface)
* **Auth-Flow:** Использование `ReplyKeyboardMarkup` с параметром `request_contact=True`.
* **Dynamic Menu:** "Умная" клавиатура, собирающаяся на лету в зависимости от ролей пользователя (Admin, Head одной/нескольких семей, Senior).
* **Admin-Panel:** Управление семьями, назначение глав, добавление/удаление членов и детей.
* **Family Head:** Контекстное управление своими семьями с поддержкой выбора, если человек руководит несколькими.

### 3.2. Google Integration (Data Source)
* **Auth:** Использование `google-auth` через Service Account.
* **Polling:** Чтение диапазонов ячеек. Оптимизация через чтение только нужных листов (напр. "Сегодня").

### 3.3. Monitor Engine (Core)
Алгоритм обнаружения оценки:
1. Получение данных из таблицы ($Current$).
2. Запрос данных из БД за прошлую итерацию ($Previous$).
3. Сравнение массивов. 
4. Если $Current \neq Previous$, вычленение координат измененной ячейки и извлечение названия предмета из заголовка строки/столбца.

---

## 4. Спецификация Docker



### Dockerfile:
* **Base Image:** `python:3.10-slim` (минимизация объема).
* **Workdir:** `/app`
* **Volumes:** * `/app/data` — для сохранения `sentinel.db`.
    * `/app/config` — для `credentials.json` и `settings.yaml`.

### 3.4 Модуль коммуникации (Communication)
* **Обратная связь (Поддержка):** Любой авторизованный пользователь может нажать "💬 Поддержка" и отправить сообщение (текст/медиа), которое пересылается в закрытую группу администраторов (задается через `ADMIN_GROUP_ID` в `.env`). Ответ на это сообщение в админ-группе (Reply) бот доставит обратно пользователю.
* **Рассылка (Broadcast):** Супер-администратор обладает кнопкой "📢 Рассылка", позволяющей отправить массовое уведомление/анонс всем зарегистрированным пользователям из БД.

## 4. Среда развертывания (Docker)
* **Restart Policy:** `always` (автозапуск при сбое Raspberry Pi).
* **Environment:** Передача `BOT_TOKEN` через файл `.env`.

---

## 5. Требования к безопасности
1. **Защита данных:** Исключение доступа к боту по `username` (только по проверенному номеру телефона).
2. **Лимиты API:** Соблюдение интервала опроса (не чаще 1 раза в 100 секунд) во избежание блокировки Service Account со стороны Google.
3. **Изоляция:** БД не должна иметь внешних портов (только локальный доступ внутри контейнера).

---

### 3.4 Модуль инвайт-ссылок (Invite)
* **Генерация:** Глава семьи нажимает "Пригласить родственника" → бот создаёт одноразовую ссылку `t.me/bot?start=inv_<code>` (48ч).
* **Активация:** Родственник переходит по ссылке → если уже в системе — привязывается к семье. Если новый — авторизация через контакт → привязка.
* **Безопасность:** Ссылка одноразовая, с expiry. Нет самостоятельной регистрации без инвайта от главы.

### 3.5 Модуль подписок и платежей (Subscription)
* **Telegram Payments API:** Интеграция с Click/Payme через `PAYMENT_PROVIDER_TOKEN`.
* **Тарифы:** 1 мес (29 900 UZS), 3 мес (79 900), 12 мес (249 900). Конфигурируются в `subscription.py:PLANS`.
* **Flow:** Кнопка "Подписка" → статус → выбор тарифа → выбор семьи → инвойс → оплата → `successful_payment` → `extend_subscription()`.
* **Проверка:** Мониторинг работает только для семей с активной подпиской (`subscription_end > now` или NULL). AI-анализ гейтится `has_any_active_subscription()`.
* **Админ:** `/grant_sub <family_id> <months>` — ручная выдача подписки.

### 3.6 Модуль AI-аналитики (Analytics)
* **Claude API** (Anthropic) для анализа успеваемости за 14 дней.
* **По запросу:** Кнопка "AI-анализ" или `/ai_report`.
* **Автоматический:** Еженедельный отчёт по воскресеньям в 19:00.
* **Премиум:** Требует активной подписки (кроме админа).

### 3.7 Мультиязычность (i18n)
* 3 языка: Русский, O'zbek, English. Файлы: `src/locales/{ru,uz,en}.json`.
* Выбор при первом `/start`. Смена через кнопку "Язык" в меню.

---

## 6. План масштабирования
* **Web-Dashboard (Mini App):** Визуализация оценок через Telegram WebApp (Chart.js). Реализован прототип.
* **Расширение платёжных провайдеров:** Добавление Uzcard, Humo.
* **PostgreSQL:** Миграция при >100 семей для устранения ограничений SQLite.