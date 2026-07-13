"""grade notify outbox: grade_history.notified_at (атомарность уведомлений)

Закрывает PR-F1 «Атомарность уведомлений: outbox вместо write-then-notify».

Проблема: monitor писал оценку в grade_history (add_grade), а батч уведомлений
слал ОТДЕЛЬНЫМИ циклами в конце обработки всех студентов. Любой exception в фазе
отправки → оценки в БД, но уведомление не ушло, а на следующем цикле diff пуст
(old==new) → уведомление терялось НАВСЕГДА.

Решение — persistent outbox прямо на строке оценки:
  • notified_at IS NULL  → уведомление ещё НЕ доставлено (в outbox);
  • notified_at = <ts>   → доставлено (или поставлено в очередь тихих часов —
    это тоже персистентная доставка).
Monitor шлёт сразу после записи по каждому студенту и проставляет notified_at
после подтверждённой доставки; sweeper в начале цикла добивает всё, что осталось
notified_at IS NULL (крах между write и send).

Ключевой инвариант безопасности: **DEFAULT now()** — строка «доставлена» по
умолчанию. В outbox (notified_at IS NULL) попадают ТОЛЬКО оценки, которые
monitor осознанно пишет через `add_grade(notify_pending=True)` (явный INSERT
NULL). history_importer / бэкапы / любые прочие writer'ы, не указывающие
notified_at, получают now() → sweeper их НИКОГДА не рассылает (иначе первый же
импорт истории обернулся бы спамом на весь учебный год). DEFAULT now() также
проставляет метку ВСЕМ существующим строкам при ADD COLUMN — отдельный backfill
не нужен.

Частичный индекс WHERE notified_at IS NULL — sweeper-запрос дешёвый (outbox
почти всегда пуст).

env.py без target_metadata → миграция вручную (op.execute).

Revision ID: 0003_grade_notified_outbox
Revises: 0002_payment_hardening
Create Date: 2026-07-13
"""
from alembic import op

revision = "0003_grade_notified_outbox"
down_revision = "0002_payment_hardening"
branch_labels = None
depends_on = None

_IDX_NAME = "idx_grade_history_unnotified"


def upgrade():
    # DEFAULT now() → существующие строки помечаются доставленными автоматически,
    # и любой будущий INSERT без явного notified_at тоже «доставлен» (safe default).
    # Только monitor.add_grade(notify_pending=True) кладёт NULL → в outbox.
    op.execute("ALTER TABLE grade_history ADD COLUMN notified_at timestamptz DEFAULT now()")
    op.execute(
        f"CREATE INDEX {_IDX_NAME} ON grade_history (date_added) "
        f"WHERE notified_at IS NULL"
    )


def downgrade():
    op.execute(f"DROP INDEX IF EXISTS {_IDX_NAME}")
    op.execute("ALTER TABLE grade_history DROP COLUMN notified_at")
