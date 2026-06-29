#!/usr/bin/env bash
# Cutover helper: переключение GradeSentinel SQLite → PostgreSQL.
# Запускать НА app-VPS (176.101.56.141) в окне обслуживания, как root.
#
# Предусловия:
#   1) Новый код уже задеплоен в /opt/gradesentinel (merge ветки в main → runner:
#      rsync кода + pip install psycopg/alembic + bot.env с DATABASE_URL).
#   2) Роль gradesentinel в PG имеет пароль; БД gradesentinel доступна по WireGuard.
#   3) DATABASE_URL передан в окружении (или прочитается из bot.env).
#
# Откат: бот не стартует / smoke упал → вернуть прошлый main (CI передеплоит код
#        на SQLite, bot.env без DATABASE_URL) ИЛИ снять DATABASE_URL и рестарт.
#        Боевой sentinel.db не изменялся (бэкап в шаге 2) — данные целы.
set -euo pipefail

APP=/opt/gradesentinel
SQLITE="${SQLITE_PATH:-/var/lib/gradesentinel/sentinel.db}"

# DATABASE_URL: из env или из bot.env
if [[ -z "${DATABASE_URL:-}" ]]; then
  DATABASE_URL="$(grep -E '^DATABASE_URL=' /etc/gradesentinel/bot.env | cut -d= -f2-)"
fi
: "${DATABASE_URL:?DATABASE_URL не задан (env или /etc/gradesentinel/bot.env)}"
export DATABASE_URL

cd "$APP"

echo "[1/6] stop bot + webapp (начало простоя)"
systemctl stop gradesentinel-bot.service gradesentinel-webapp.service

echo "[2/6] backup живого sqlite (откат)"
cp -av "$SQLITE" "${SQLITE}.pre-pg-$(date +%Y%m%d-%H%M%S)"

echo "[3/6] схема на PG (alembic upgrade head)"
sudo -u gradesentinel env DATABASE_URL="$DATABASE_URL" \
  "$APP/venv/bin/alembic" -c "$APP/alembic.ini" upgrade head

echo "[4/6] перенос данных (ETL, --truncate = свежая загрузка)"
sudo -u gradesentinel env SQLITE_PATH="$SQLITE" DATABASE_URL="$DATABASE_URL" \
  "$APP/venv/bin/python" "$APP/scripts/migrate_sqlite_to_pg.py" --truncate

echo "[5/6] start bot + webapp"
systemctl start gradesentinel-bot.service gradesentinel-webapp.service

echo "[6/6] smoke"
sleep 8
systemctl is-active gradesentinel-bot.service gradesentinel-webapp.service
for i in 1 2 3 4 5; do
  if curl -fsS http://127.0.0.1:8443/health >/dev/null; then echo "webapp /health OK"; break; fi
  sleep 2
done
echo "CUTOVER DONE — бот на PostgreSQL. Проверь вручную: /start, оценки, подписка."
