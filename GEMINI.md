# 🚀 Project Antigravity: GradeSentinel

> **Статус:** Инициализация (Проектирование архитектуры)
> **Версия:** 2.0 (Docker-based)
> **Девиз:** "Оценки прилетают быстрее, чем их ставят."

---

## 🛠 Технологический стек "Antigravity"
- **Containerization:** Docker + Docker Compose
- **Backend:** Python 3.10 (Slim Image)
- **Database:** SQLite (Relational, Many-to-Many)
- **API:** Google Sheets API v4 + Telegram Bot API
- **Deployment:** Raspberry Pi 3B (Home Server)

---

## 🛰 Архитектурная карта (Roadmap)

### Этап 1: Фундамент (Completed) ✅
- [x] Итоговое ТЗ (MD)
- [x] Структура репозитория и `.gitignore`
- [x] `README.md` для GitHub
- [x] Схема базы данных (SQLAlchemy/SQL)

### Этап 2: Модуль "Центр Управления" (Bot) 🤖
- [x] Реализация `auth_handler` (верификация по номеру телефона)
- [x] Админ-команды для создания "Семьи" и привязки "Родитель-Ребенок"
- [x] Расширенное управление семьями (Inline-кнопки, удаление, редактирование)
- [x] Валидатор ссылок на Google Таблицы

### Этап 3: Двигатель "Antigravity" (Monitor) ⚙️
- [x] Логика Snapshot-сравнения (нахождение разницы в ячейках)
- [x] Защита от потери сообщений (сохранение отчетов в истории)
- [x] Обработка ошибок сети Google API (Exponential Backoff)
- [x] Система красивых уведомлений (HTML + Emoji)

### Этап 4: Развертывание 🐳
- [x] Написание Dockerfile
- [x] Настройка автоматического перезапуска (restart: always)
- [x] Тестирование развертывания (Локально в Docker)

---

## 🌳 Стратегия ветвления (Workflow)
- **`develop`**: Основная ветка разработки. Тестирование на Mac.
- **`main`**: Продакшн ветка. Пуш в эту ветку автоматически деплоит бота на Raspberry Pi.

---

## 💾 Справочник команд (Cheatsheet)

### Работа с Docker на Raspberry Pi
```bash
# Сборка и запуск в фоне
docker-compose up -d --build

# Просмотр логов в реальном времени
docker-compose logs -f


# Остановка проекта
docker-compose down