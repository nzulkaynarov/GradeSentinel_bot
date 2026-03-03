FROM python:3.10-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Ensure data and config directories exist
RUN mkdir -p /app/data /app/config

# Command to run the application
CMD ["python", "-m", "src.main"]
