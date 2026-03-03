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
- [ ] Реализация `auth_handler` (верификация по номеру телефона)
- [ ] Админ-команды для создания "Семьи" и привязки "Родитель-Ребенок"
- [ ] Валидатор ссылок на Google Таблицы

### Этап 3: Двигатель "Antigravity" (Monitor) ⚙️
- [ ] Логика Snapshot-сравнения (нахождение разницы в ячейках)
- [ ] Обработка ошибок сети и лимитов Google API
- [ ] Система красивых уведомлений (HTML + Emoji)

### Этап 4: Развертывание 🐳
- [x] Написание Dockerfile
- [x] Настройка автоматического перезапуска (restart: always)
- [x] Тестирование развертывания (Локально в Docker)

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