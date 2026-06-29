#!/bin/bash
# Суточный бэкап боевой БД GradeSentinel (PostgreSQL на DB-VPS).
# ОТДЕЛЬНЫЙ скрипт — НЕ трогает общий railtech-db-backup.sh (это инфра B2B).
# Custom-формат (pg_restore-able), 14-дневная ротация. pg_dump бежит как postgres
# (peer auth), root перенаправляет в root-only каталог → дамп остаётся root:600.
#
# Установка на DB-VPS (10.0.0.2), однократно как root:
#   install -m 0755 deploy/gradesentinel-db-backup.sh /usr/local/bin/gradesentinel-db-backup.sh
#   install -m 0644 deploy/gradesentinel-db-backup.cron /etc/cron.d/gradesentinel-db-backup
#   /usr/local/bin/gradesentinel-db-backup.sh   # прогнать раз для проверки
set -euo pipefail
BACKUP_DIR=/var/backups/railtech-db   # тот же root-only каталог; дампы префиксованы именем БД
DB=gradesentinel
RETENTION_DAYS=14
TS=$(date +%Y-%m-%d_%H%M%S)
OUT="$BACKUP_DIR/${DB}_${TS}.dump"
umask 077
sudo -u postgres pg_dump -Fc --no-owner --no-privileges -d "$DB" > "$OUT"
find "$BACKUP_DIR" -name "${DB}_*.dump" -type f -mtime +"$RETENTION_DAYS" -delete
echo "$(date -Is) OK $OUT ($(du -h "$OUT" | cut -f1))"
