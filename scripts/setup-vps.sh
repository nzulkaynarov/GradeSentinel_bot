#!/bin/bash
# ===========================================
# Первоначальная настройка VPS для GradeSentinel
# Запустить один раз на новом сервере:
#   curl -sSL <url> | bash
#   или: bash scripts/setup-vps.sh
# ===========================================
set -e

echo "=== Установка Docker ==="
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo "Docker установлен. Перелогиньтесь для применения группы docker."
fi

echo "=== Установка Docker Compose ==="
if ! command -v docker compose &> /dev/null; then
    sudo apt-get update
    sudo apt-get install -y docker-compose-plugin
fi

echo "=== Клонирование репозитория ==="
if [ ! -d ~/GradeSentinel_bot ]; then
    git clone https://github.com/nzulkaynarov/GradeSentinel_bot.git ~/GradeSentinel_bot
else
    echo "Репозиторий уже существует."
fi

echo "=== Создание .env ==="
ENV_FILE=~/.env.gradesentinel
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << 'ENVEOF'
BOT_TOKEN=
ADMIN_ID=
ADMIN_GROUP_ID=
ANTHROPIC_API_KEY=
WEBAPP_URL=
WEBAPP_PORT=8443
ENVEOF
    echo "⚠️  Заполните $ENV_FILE своими значениями!"
    echo "    nano $ENV_FILE"
else
    echo "$ENV_FILE уже существует."
fi

echo "=== Создание папки config ==="
mkdir -p ~/GradeSentinel_bot/config
if [ ! -f ~/GradeSentinel_bot/config/credentials.json ]; then
    echo "⚠️  Скопируйте credentials.json в ~/GradeSentinel_bot/config/"
fi

echo ""
echo "=== Готово! ==="
echo ""
echo "Осталось:"
echo "  1. Заполнить ~/.env.gradesentinel"
echo "  2. Скопировать config/credentials.json"
echo "  3. cd ~/GradeSentinel_bot && cp ~/.env.gradesentinel .env && docker compose up -d --build"
echo ""
echo "Для CI/CD добавьте в GitHub Secrets:"
echo "  VPS_HOST     — IP-адрес сервера"
echo "  VPS_USER     — имя пользователя (обычно root или ubuntu)"
echo "  VPS_SSH_KEY  — приватный SSH-ключ (ssh-keygen -t ed25519)"
echo "  VPS_PORT     — порт SSH (по умолчанию 22)"
