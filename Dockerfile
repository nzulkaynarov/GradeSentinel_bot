FROM python:3.10-slim

# Не пишем .pyc и не буферизуем stdout (нужно для логов в docker logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код и создаём data/config директории
COPY . .
RUN mkdir -p /app/data /app/config

# NOTE: Раньше здесь был `USER bot` для запуска из-под непривилегированного юзера.
# Но named volume `sentinel_data` от прошлых деплоев был root-owned, и bot user
# не мог писать в /app/data (SQLite, heartbeat) — контейнер крашился по кругу.
# Возвращаем root до тех пор, пока не появится entrypoint с gosu+chown на старте.

# Healthcheck читает /app/data/.heartbeat — main thread пишет в этот файл каждые 30 сек.
# Если mtime > 180 сек, считаем что polling завис → Docker перезапустит (restart: always).
# Никаких HTTP-запросов наружу (квоты, лишние round-trip'ы, зависимость от внешней сети).
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
    CMD python3 -c "import os, sys, time; p='/app/data/.heartbeat'; sys.exit(0 if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < 180 else 1)"

# Команда запуска
CMD ["python", "-m", "src.main"]
