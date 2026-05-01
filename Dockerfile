FROM python:3.10-slim

# Не пишем .pyc и не буферизуем stdout (нужно для логов в docker logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# gosu — правильный drop-privileges (signals, без su-quirks)
RUN apt-get update && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код и создаём data/config директории
COPY . .
RUN mkdir -p /app/data /app/config

# Непривилегированный пользователь
RUN useradd --create-home --shell /bin/bash bot

# Entrypoint: стартуем от root, chown'им volume-mounted директории, потом
# gosu роняем привилегии на bot. Решает проблему с root-owned named volume.
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Healthcheck читает /app/data/.heartbeat — main thread пишет в этот файл каждые 30 сек.
# Если mtime > 180 сек, считаем что polling завис → Docker перезапустит (restart: always).
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --retries=3 \
    CMD python3 -c "import os, sys, time; p='/app/data/.heartbeat'; sys.exit(0 if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < 180 else 1)"

# Команда запуска (запускается через entrypoint от bot user)
CMD ["python", "-m", "src.main"]
