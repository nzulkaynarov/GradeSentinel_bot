#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Off-site sync локальных бэкапов БД в облако через rclone.
#
# ЗАЧЕМ: локальные бэкапы лежат на том же VPS (/var/backups/gradesentinel/,
# ротация 7 дней). Гибель/потеря VPS = потеря и БД, и бэкапов. Этот скрипт
# зеркалит локальную папку в облачное хранилище → данные переживают смерть VPS.
#
# ⚠️ PRIVACY: в БД — PII реальных родителей/детей (телефоны, имена, оценки).
# КРАЙНЕ рекомендуется rclone CRYPT remote (client-side шифрование) поверх
# B2/S3 — тогда провайдер хранит только зашифрованные блобы.
#
# КОНФИГУРИРУЕТСЯ ВРУЧНУЮ (provision, НЕ в репо — см. deploy/README.md):
#   /etc/gradesentinel/offsite-backup.env   RCLONE_REMOTE=secret:gradesentinel-db
#   /etc/gradesentinel/rclone.conf          rclone remotes (желательно crypt)
#
# Пока не сконфигурирован — МЯГКИЙ skip (exit 0), чтобы systemd не слал алерты
# об упавшем юните до того, как владелец заведёт бакет.
# ─────────────────────────────────────────────────────────────────────────────
set -eu

ENV_FILE=/etc/gradesentinel/offsite-backup.env
RCLONE_CONF=/etc/gradesentinel/rclone.conf
BACKUP_DIR=/var/backups/gradesentinel

if [ ! -f "$ENV_FILE" ]; then
    echo "off-site backup не сконфигурирован ($ENV_FILE отсутствует) — skip"
    exit 0
fi

# shellcheck disable=SC1090
. "$ENV_FILE"

if [ -z "${RCLONE_REMOTE:-}" ]; then
    echo "RCLONE_REMOTE не задан в $ENV_FILE — skip"
    exit 0
fi

if ! command -v rclone >/dev/null 2>&1; then
    echo "rclone не установлен — skip (установка: см. deploy/README.md)"
    exit 0
fi

if [ ! -f "$RCLONE_CONF" ]; then
    echo "$RCLONE_CONF отсутствует — skip"
    exit 0
fi

# sync зеркалит локальную папку (которая уже ротируется 7 дней) в облако:
# off-site хранит то же 7-дневное окно. Если нужна более длинная история —
# настрой lifecycle/versioning на стороне бакета (см. README).
rclone --config "$RCLONE_CONF" sync "$BACKUP_DIR" "$RCLONE_REMOTE" \
    --include "sentinel-*.db.gz" \
    --transfers 2 --retries 3 --low-level-retries 5 \
    --stats-one-line

echo "off-site sync OK → ${RCLONE_REMOTE} ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
