FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

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

# Healthcheck без curl — стандартный python вместо apt-get install. Дёргаем
# /health на localhost; если Flask не отвечает 3 раза подряд → restart.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python3 -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8443/health', timeout=3).status == 200 else 1)"

CMD ["python", "app.py"]
