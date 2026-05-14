#!/usr/bin/env bash
# Идемпотентная первоначальная настройка VPS под GradeSentinel.
# Запускать ОДИН РАЗ от root (или через sudo) на чистом Ubuntu 24.04.
# Скрипт безопасен для повторного запуска — все шаги проверяют текущее состояние.
#
# Что делает:
#   1) Устанавливает системные зависимости (Python 3.12, Caddy, UFW, fail2ban, sqlite3)
#   2) Создаёт system-юзера gradesentinel + директории /opt, /var/lib, /etc
#   3) Настраивает swap (2 GB), timezone, unattended-upgrades
#   4) Открывает UFW (22/80/443), активирует fail2ban
#   5) Кладёт Caddyfile, systemd-юниты, sudoers
#   6) НЕ запускает бот — это делает GitHub Actions deploy.yml
#
# Что нужно сделать вручную ДО запуска:
#   - Создать юзера 'deploy' и SSH-ключ (см. deploy/README.md)
#   - Прописать DNS A-запись grades.railtech.uz → IP VPS
#
# Использование:
#   curl -fsSL https://raw.githubusercontent.com/<user>/<repo>/main/deploy/install.sh | sudo bash
#   ИЛИ скопировать репо на VPS и запустить: sudo deploy/install.sh

set -euo pipefail

# ────────────────────────────────────────────────────────────
# Константы
# ────────────────────────────────────────────────────────────
PROJECT="gradesentinel"
SERVICE_USER="${PROJECT}"
DEPLOY_USER="deploy"
APP_DIR="/opt/${PROJECT}"
DATA_DIR="/var/lib/${PROJECT}"
ETC_DIR="/etc/${PROJECT}"
PYTHON_VERSION="3.12"

# Цвета
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[install]${NC} $*"; }
warn() { echo -e "${YELLOW}[warn]${NC} $*"; }
fail() { echo -e "${RED}[fail]${NC} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "Запусти от root: sudo $0"

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
log "Репозиторий: ${REPO_DIR}"

# ────────────────────────────────────────────────────────────
# 1. Apt-зависимости
# ────────────────────────────────────────────────────────────
log "Шаг 1/8: системные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    "python${PYTHON_VERSION}" "python${PYTHON_VERSION}-venv" "python${PYTHON_VERSION}-dev" \
    python3-pip \
    sqlite3 \
    rsync \
    ufw fail2ban unattended-upgrades \
    curl ca-certificates gnupg \
    debian-keyring debian-archive-keyring apt-transport-https

# Caddy — официальный репозиторий
if ! command -v caddy >/dev/null 2>&1; then
    log "  ставим Caddy"
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq
    apt-get install -y -qq caddy
else
    log "  Caddy уже установлен"
fi

# ────────────────────────────────────────────────────────────
# 2. Юзер и директории
# ────────────────────────────────────────────────────────────
log "Шаг 2/8: системный юзер ${SERVICE_USER} и директории"

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --shell /usr/sbin/nologin --home-dir "${APP_DIR}" --create-home "${SERVICE_USER}"
    log "  юзер ${SERVICE_USER} создан"
else
    log "  юзер ${SERVICE_USER} уже есть"
fi

install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 "${APP_DIR}"
install -d -o "${SERVICE_USER}" -g "${SERVICE_USER}" -m 0750 "${DATA_DIR}"
install -d -o root -g "${SERVICE_USER}" -m 0750 "${ETC_DIR}"

# ────────────────────────────────────────────────────────────
# 3. Python venv
# ────────────────────────────────────────────────────────────
log "Шаг 3/8: Python venv в ${APP_DIR}/venv"
if [[ ! -d "${APP_DIR}/venv" ]]; then
    sudo -u "${SERVICE_USER}" "python${PYTHON_VERSION}" -m venv "${APP_DIR}/venv"
    sudo -u "${SERVICE_USER}" "${APP_DIR}/venv/bin/pip" install --quiet --upgrade pip
    log "  venv создан"
else
    log "  venv уже есть"
fi

# ────────────────────────────────────────────────────────────
# 4. Swap (2 GB) — VPS 4 GB ОЗУ, страховка от OOM
# ────────────────────────────────────────────────────────────
log "Шаг 4/8: swap"
if ! swapon --show | grep -q '/swapfile'; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    sysctl -q vm.swappiness=10
    grep -q 'vm.swappiness' /etc/sysctl.d/99-swappiness.conf 2>/dev/null \
        || echo 'vm.swappiness=10' > /etc/sysctl.d/99-swappiness.conf
    log "  swap 2 GB добавлен"
else
    log "  swap уже активен"
fi

# ────────────────────────────────────────────────────────────
# 5. Таймзона + автообновления
# ────────────────────────────────────────────────────────────
log "Шаг 5/8: timezone Asia/Tashkent + unattended-upgrades"
timedatectl set-timezone Asia/Tashkent
systemctl enable --now unattended-upgrades

# ────────────────────────────────────────────────────────────
# 6. UFW + fail2ban
# ────────────────────────────────────────────────────────────
log "Шаг 6/8: UFW + fail2ban"
ufw --force default deny incoming
ufw --force default allow outgoing
ufw allow OpenSSH
ufw allow 80/tcp comment 'Caddy HTTP (ACME)'
ufw allow 443/tcp comment 'Caddy HTTPS'
ufw --force enable
systemctl enable --now fail2ban

# ────────────────────────────────────────────────────────────
# 7. Caddy config
# ────────────────────────────────────────────────────────────
log "Шаг 7/8: Caddyfile → /etc/caddy/Caddyfile"
install -m 0644 "${REPO_DIR}/deploy/Caddyfile" /etc/caddy/Caddyfile
# /var/log/caddy уже создан apt-пакетом caddy; install -d НЕ перевладеет
# существующую директорию, поэтому делаем chown явно. На повторный запуск
# скрипта это идемпотентно.
mkdir -p /var/log/caddy
chown -R caddy:caddy /var/log/caddy
chmod 0755 /var/log/caddy
caddy validate --config /etc/caddy/Caddyfile >/dev/null \
    || fail "Caddyfile невалиден — починить и повторить"
# restart, не reload — гарантирует что caddy перечитает конфиг с нуля
# и подключится к новому log-файлу с правильными правами.
systemctl restart caddy
systemctl enable caddy

# ────────────────────────────────────────────────────────────
# 8. Systemd-юниты + sudoers для деплоя
# ────────────────────────────────────────────────────────────
log "Шаг 8/8: systemd-юниты + sudoers"

install -m 0644 "${REPO_DIR}/deploy/gradesentinel-bot.service" /etc/systemd/system/
install -m 0644 "${REPO_DIR}/deploy/gradesentinel-webapp.service" /etc/systemd/system/
install -m 0644 "${REPO_DIR}/deploy/gradesentinel-heartbeat.service" /etc/systemd/system/
install -m 0644 "${REPO_DIR}/deploy/gradesentinel-heartbeat.timer" /etc/systemd/system/
install -m 0644 "${REPO_DIR}/deploy/gradesentinel-backup.service" /etc/systemd/system/
install -m 0644 "${REPO_DIR}/deploy/gradesentinel-backup.timer" /etc/systemd/system/

# Каталог для бэкапов БД (gradesentinel:gradesentinel 0750).
install -d -o gradesentinel -g gradesentinel -m 0750 /var/backups/gradesentinel

# Sudoers: visudo -c проверяет синтаксис; падаем если невалиден
visudo -cf "${REPO_DIR}/deploy/deploy-sudoers" >/dev/null \
    || fail "deploy-sudoers невалиден — visudo -cf падает"
install -m 0440 -o root -g root "${REPO_DIR}/deploy/deploy-sudoers" /etc/sudoers.d/deploy-runner

systemctl daemon-reload
systemctl enable gradesentinel-heartbeat.timer
systemctl enable gradesentinel-backup.timer
# bot/webapp юниты НЕ enable'ятся здесь — их активирует первый деплой
# (раньше бы пытались стартовать без кода в /opt/gradesentinel и упали).

# ────────────────────────────────────────────────────────────
# Финал
# ────────────────────────────────────────────────────────────
log "Готово."
echo
echo "Дальше:"
echo "  1) Если ${DEPLOY_USER} ещё не создан — создай его и положи свой ssh-ключ"
echo "     (см. deploy/README.md, шаг 'Юзер deploy')."
echo "  2) Зарегистрируй GitHub Actions runner от юзера ${DEPLOY_USER}:"
echo "     https://github.com/<user>/<repo>/settings/actions/runners/new"
echo "  3) В GitHub Secrets обнови WEBAPP_URL = https://grades.railtech.uz"
echo "     и удали WEBAPP_PORT (теперь захардкожен в systemd-юните)."
echo "  4) Мержни PR feature/bare-metal-migration в main —"
echo "     deploy.yml автоматом раскатает код и стартанёт сервисы."
echo "  5) Проверь: curl https://grades.railtech.uz/health"
