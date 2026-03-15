FROM python:3.10-slim

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy webapp and src (for DB access)
COPY webapp/ ./webapp/
COPY src/ ./src/

WORKDIR /app/webapp

EXPOSE 8443

CMD ["python", "app.py"]
