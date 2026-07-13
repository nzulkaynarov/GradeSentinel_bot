"""payment hardening: идемпотентность charge_id + nullable paid_by

Закрывает пакет PR-B «Payment flow: атомарность и устойчивость»:
  • UNIQUE(payments.telegram_payment_charge_id) — идемпотентность:
    повторная доставка Telegram successful_payment (тот же charge_id) не
    должна двоить подписку (INSERT упадёт UniqueViolation → ловим и скипаем).
    В PG UNIQUE допускает несколько NULL — существующие NULL-строки
    (admin_grant / promo / card-переводы без Telegram-charge) не мешают.
  • payments.paid_by → NULLABLE — плательщик без строки parents реален
    (напр. новый Telegram-пользователь оплатил, но ещё не зарегистрирован).
    Раньше NOT NULL → INSERT падал NotNullViolation и аудит платежа терялся,
    хотя деньги уже списаны. Теперь пишем NULL и алертим админа.

env.py не имеет target_metadata → миграция написана вручную (op.* операции).

Revision ID: 0002_payment_hardening
Revises: 0001_baseline
Create Date: 2026-07-13
"""
from alembic import op

revision = "0002_payment_hardening"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

_UQ_NAME = "uq_payments_telegram_charge_id"


def upgrade():
    op.alter_column("payments", "paid_by", nullable=True)
    op.create_unique_constraint(
        _UQ_NAME, "payments", ["telegram_payment_charge_id"]
    )


def downgrade():
    op.drop_constraint(_UQ_NAME, "payments", type_="unique")
    # NB: вернуть NOT NULL можно только если нет NULL-строк paid_by.
    op.alter_column("payments", "paid_by", nullable=False)
