FROM python:3.10-slim

# Не пишем .pyc и не буферизуем stdout (нужно для логов в docker logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Создаём непривилегированного пользователя
RUN useradd --create-home --shell /bin/bash bot

# Копируем код и создаём data/config директории до chown
COPY . .
RUN mkdir -p /app/data /app/config && chown -R bot:bot /app

USER bot

# Healthcheck читает /app/data/.heartbeat — main thread пишет в этот файл каждые 30 сек.
# Если mtime > 180 сек, считаем что polling завис → Docker перезапустит (restart: always).
# Никаких HTTP-запросов наружу (квоты, лишние round-trip'ы, зависимость от внешней сети).
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
    CMD python3 -c "import os, sys, time; p='/app/data/.heartbeat'; sys.exit(0 if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < 180 else 1)"

# Команда запуска
CMD ["python", "-m", "src.main"]
