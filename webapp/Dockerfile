FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY webapp/requirements.txt /app/webapp/requirements.txt
RUN pip install --no-cache-dir -r /app/webapp/requirements.txt

# Непривилегированный пользователь
RUN useradd --create-home --shell /bin/bash bot

# Копируем код
COPY webapp/ /app/webapp/
COPY src/ /app/src/
RUN mkdir -p /app/data /app/config && chown -R bot:bot /app

USER bot

WORKDIR /app/webapp

EXPOSE 8443

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://localhost:8443/health || exit 1

CMD ["python", "app.py"]
