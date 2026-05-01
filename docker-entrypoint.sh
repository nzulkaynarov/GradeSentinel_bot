#!/bin/bash
# Entrypoint для запуска бота от непривилегированного пользователя.
#
# Проблема: named volume `sentinel_data:/app/data` сохраняет owner от прошлых
# контейнеров. Если когда-то контейнер был запущен от root, файлы в /app/data
# и /app/config принадлежат root, и не-root юзер `bot` не может писать туда
# (SQLite db open → EACCES → init_db падает → heartbeat не пишется →
# healthcheck валит контейнер по кругу).
#
# Решение: стартуем от root, chown'им /app/data и /app/config на bot,
# затем drop-в bot user через gosu (passes signals правильно, в отличие от su).
set -euo pipefail

TARGET_USER="bot"
TARGET_UID=$(id -u "$TARGET_USER")
TARGET_GID=$(id -g "$TARGET_USER")

# Гарантируем что директории существуют (на случай первого запуска без volume)
mkdir -p /app/data /app/config

# Чиним права. -h игнорируем symlinks. Делаем только если реально нужно —
# на каждом старте chown'ить десятки тысяч файлов было бы дорого.
for dir in /app/data /app/config; do
    if [ "$(stat -c '%u' "$dir")" != "$TARGET_UID" ]; then
        echo "[entrypoint] Fixing ownership of $dir → $TARGET_USER ($TARGET_UID:$TARGET_GID)"
        chown -R "$TARGET_UID:$TARGET_GID" "$dir"
    fi
done

# Передаём управление bot user'у. exec → этот процесс становится PID 1
# (важно для proper signal handling от Docker).
exec gosu "$TARGET_USER" "$@"
