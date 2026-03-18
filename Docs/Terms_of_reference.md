📄 Техническое Задание: GradeSentinel (v2.0)
GradeSentinel — это масштабируемая система мониторинга школьной успеваемости через Google Таблицы с уведомлениями в Telegram и поддержкой семейных аккаунтов.

1. Цели и задачи
Автоматизация: Исключить ручную проверку оценок родителями.

Оперативность: Доставлять уведомление в течение 5 минут после выставления оценки.

Безопасность: Доступ к данным только после верификации по номеру телефона.

Масштабируемость: Возможность обслуживания множества семей в одном боте.

2. Архитектура системы (Docker-based)
Система упаковывается в Docker-контейнер для обеспечения переносимости между Raspberry Pi и любым облачным сервером (VPS).

Стек технологий:
Язык: Python 3.10+

База данных: SQLite3 (хранится в Docker Volume)

Интерфейс: Telebot (pyTelegramBotAPI)

API: Google Sheets API v4 (Service Account)

3. Модель данных (Database Schema)
Архитектура поддерживает связи "Многие-ко-многим" для гибкого управления семьями.

Families (Семьи): Группирующая сущность, включает подписку и `head_id` (ссылка на Главу семьи).

Parents (Родители): id, fio, phone, telegram_id, role (admin или senior).

Students (Ученики): id, fio, spreadsheet_id (ID файла ученика).

Family_Links: Таблица связей "Многие-ко-многим" (привязка родителей и детей к семьям).

Grade_History: Кэш оценок (subject, raw_text, grade_value, cell_reference, date_added). /grades читает из этой таблицы, а не из Google API.

Quarter_Grades: Четвертные оценки (subject, quarter, grade_value).

Notification_Queue: Очередь уведомлений в тихие часы (22:00–07:00).

Family_Invites: Одноразовые инвайт-ссылки для семей (invite_code, expires_at, is_used, created_by, used_by).

Payments: История платежей через Telegram Payments API (amount, currency, plan, months, telegram_payment_charge_id, provider_payment_charge_id).

User_States: Временные состояния пользователей (pending_lang, pending_invite).

4. Алгоритм работы (User Flow)
4.1. Административный контур (Admin Panel)
Админ (владелец бота) через встроенное меню добавляет Семью и сразу назначает Родителя её Главой.

Глава семьи (Head) получает возможность добавлять в свою семью дополнительных Родственников и Детей через кнопку "🏠 Моя семья".

Админ имеет глобальный доступ ко всем семьям через кнопку "🏠 Семьи".

Система связывает их в одну ячейку "Семья".

4.2. Контур Родителя (Client Side)
Авторизация: При команде /start бот запрашивает контакт (кнопка "Отправить номер").

Верификация: Бот ищет номер в таблице Parents.

Если не найден: Сообщение об отсутствии доступа.

Если найден: Записывается telegram_id родителя, открывается доступ.

Привязка дневника: Если у ребенка в этой семье еще нет ссылки на таблицу, бот запрашивает её у родителя.

Мониторинг: Система начинает цикл проверки.

5. Логика мониторинга и уведомлений
Скрипт раз в 5 минут скачивает данные из вкладок "Сегодня" и "Все оценки".

Сравнивает количество и содержание ячеек с последним "снимком" в БД.

При обнаружении новой записи формирует сообщение:

🔔 Новая оценка!
👨‍🎓 Ученик: Заур
📚 Предмет: Физика
📝 Тип: Контрольная работа
⭐ Оценка: 5

Сообщение отправляется всем родителям, привязанным к данной семье.

6. Структура проекта (Filesystem)

```
/GradeSentinel
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env                        # BOT_TOKEN, ADMIN_ID, PAYMENT_PROVIDER_TOKEN, ANTHROPIC_API_KEY, ...
├── config/
│   └── credentials.json        # Google Service Account Key
├── data/
│   └── sentinel.db             # SQLite база данных (Docker Volume)
└── src/
    ├── main.py                 # Точка входа, /start, /help, роутинг кнопок меню
    ├── bot_instance.py         # Singleton бота
    ├── database_manager.py     # SQL: таблицы, миграции, 8 индексов, все CRUD-операции
    ├── google_sheets.py        # Google Sheets API v4
    ├── monitor_engine.py       # Polling-цикл, snapshot-сравнение, подписка-фильтр
    ├── data_cleaner.py         # Очистка "грязных" оценок из Google Sheets
    ├── analytics_engine.py     # Claude AI — анализ успеваемости
    ├── schedulers.py           # Вечерняя сводка, тихие часы, bot_alive
    ├── ui.py                   # Динамическое меню, send_menu_safe, send_content
    ├── i18n.py                 # Модуль мультиязычности
    ├── utils.py                # Утилиты
    ├── locales/
    │   ├── ru.json             # Русский (169 ключей)
    │   ├── uz.json             # O'zbek
    │   └── en.json             # English
    └── handlers/
        ├── admin.py            # /status, /add_family, /list_families
        ├── family.py           # Управление семьёй, /grades, /manage_family
        ├── communication.py    # Поддержка, рассылка
        ├── analytics.py        # /ai_report, еженедельные AI-отчёты
        ├── settings.py         # Смена языка
        ├── subscription.py     # Подписка, Telegram Payments, /grant_sub
        └── invite.py           # Инвайт-ссылки для семей
```

7. Коммерческая модель

7.1 Подписка (реализовано):
- Telegram Payments API с провайдерами Click / Payme.
- 3 тарифа: 1 мес (29 900 UZS), 3 мес (79 900), 12 мес (249 900).
- Подписка привязана к семье (`subscription_end` в таблице `families`).
- Без подписки: мониторинг не работает, AI-анализ заблокирован.
- Админ может выдать подписку: `/grant_sub <family_id> <months>`.

7.2 Масштабирование:
- Один экземпляр на Raspberry Pi 3B обслуживает до 50–100 семей.
- При >100 семей — миграция на PostgreSQL.