#!/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
# Off-site sync PG-дампов GradeSentinel в облако через rclone.
#
# ⚠️ РАБОТАЕТ НА DB-VPS (170.168.6.209 / внутр. 10.0.0.2), НЕ на app-VPS.
# Именно там gradesentinel-db-backup.sh уже пишет боевые pg_dump-дампы в
# /var/backups/railtech-db/gradesentinel_*.dump. Этот скрипт зеркалит их в облако.
#
# ЗАЧЕМ: дампы лежат на том же DB-VPS, где живёт сама БД. Гибель/потеря DB-VPS =
# потеря и БД, и всех дампов (PII семей невосстановима). Этот скрипт выносит
# дампы за пределы DB-VPS → данные переживают смерть VPS.
#
# (До миграции на PostgreSQL этот скрипт крутился на app-VPS и синкал
#  sentinel-*.db.gz из SQLite-бэкапа. После cutover'а 2026-06-29 БД живёт на
#  PostgreSQL DB-VPS — синкаем pg_dump-дампы оттуда. См. deploy/README.md.)
#
# ⚠️ PRIVACY: в дампе — PII реальных родителей/детей (телефоны, имена, оценки).
# КРАЙНЕ рекомендуется rclone CRYPT remote (client-side шифрование) поверх
# B2/S3 — тогда провайдер хранит только зашифрованные блобы.
#
# КОНФИГУРИРУЕТСЯ ВРУЧНУЮ на DB-VPS (provision, НЕ в репо — см. deploy/README.md):
#   /etc/gradesentinel/offsite-backup.env   RCLONE_REMOTE=secret:gradesentinel-db
#   /etc/gradesentinel/rclone.conf          rclone remotes (желательно crypt)
#
# УСТАНОВКА на DB-VPS (однократно, как root):
#   install -m 0755 deploy/offsite-backup.sh /usr/local/bin/gradesentinel-offsite-backup.sh
#   install -m 0644 deploy/gradesentinel-offsite-backup.cron /etc/cron.d/gradesentinel-offsite-backup
#   mkdir -p /etc/gradesentinel && chmod 0700 /etc/gradesentinel
#   /usr/local/bin/gradesentinel-offsite-backup.sh   # прогнать раз для проверки
#
# Пока не сконфигурирован — МЯГКИЙ skip (exit 0), чтобы cron не слал алерты
# об упавшем задании до того, как владелец заведёт бакет.
# ─────────────────────────────────────────────────────────────────────────────
set -eu

ENV_FILE=${OFFSITE_ENV_FILE:-/etc/gradesentinel/offsite-backup.env}
RCLONE_CONF=${OFFSITE_RCLONE_CONF:-/etc/gradesentinel/rclone.conf}
# Каталог pg_dump-дампов — совпадает с BACKUP_DIR в gradesentinel-db-backup.sh.
BACKUP_DIR=${OFFSITE_BACKUP_DIR:-/var/backups/railtech-db}
# Маска дампов — совпадает с именованием "${DB}_${TS}.dump" в gradesentinel-db-backup.sh.
DUMP_GLOB=${OFFSITE_DUMP_GLOB:-gradesentinel_*.dump}

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

if [ ! -d "$BACKUP_DIR" ]; then
    echo "$BACKUP_DIR отсутствует (нет локальных дампов) — skip"
    exit 0
fi

# ⚠️ Защита от уничтожения off-site копии: `rclone sync` удаляет на удалённой
# стороне файлы, которых нет локально. Если локальный pg_dump сломался и каталог
# пуст — sync СНЁС БЫ всю облачную копию. Не даём: при нуле дампов — skip.
count=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name "$DUMP_GLOB" | wc -l)
if [ "$count" -eq 0 ]; then
    echo "нет дампов ($DUMP_GLOB) в $BACKUP_DIR — skip (не трогаем off-site копию)"
    exit 0
fi

# sync зеркалит локальный каталог (ротация 14 дней в gradesentinel-db-backup.sh)
# в облако: off-site хранит то же 14-дневное окно. Нужна более длинная история —
# настрой lifecycle/versioning на стороне бакета (см. README).
rclone --config "$RCLONE_CONF" sync "$BACKUP_DIR" "$RCLONE_REMOTE" \
    --include "$DUMP_GLOB" \
    --transfers 2 --retries 3 --low-level-retries 5 \
    --stats-one-line

echo "off-site sync OK → ${RCLONE_REMOTE} (${count} дампов, $(date -u +%Y-%m-%dT%H:%M:%SZ))"
