"""Подписки и платежи.

API:
- Subscription state: get_family_subscription, is_subscription_active,
  has_any_active_subscription
- Mutate: extend_subscription, cancel_subscription, record_payment
- Expiry tracking (для scheduler'а уведомлений за 7д / 1д / 0д):
  get_families_expiring_in_days, get_families_expired_today

`record_payment` пишет в payments — это «append-only audit log» для всех
прошедших Telegram Payments транзакций (charge_id, amount, plan, months).
`extend_subscription` отдельно от `record_payment` для случаев
admin /grant_sub (бесплатное продление без транзакции).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from src.db.connection import conn_or_new, get_db_connection

logger = logging.getLogger(__name__)


def get_family_subscription(family_id: int) -> Optional[Dict[str, Any]]:
    """Возвращает {'subscription_end': ...} или None если семьи нет."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT subscription_end FROM families WHERE id = %s',
            (family_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {'subscription_end': row['subscription_end']}


def extend_subscription(family_id: int, months: int = 1, conn=None):
    """Продлевает подписку на N месяцев. Если текущая ещё активна — прибавляем
    к её концу; если истекла/NULL — считаем от now. Если семьи нет — silent no-op.

    Единый аддитивный UPDATE (без предварительного SELECT-ветвления) —
    защита от гонки двух одновременных «первых» оплат: под row-lock второй
    UPDATE перечитывает уже закоммиченный subscription_end и прибавляет к нему,
    а не слепо перезаписывает от now (иначе семья получила бы 1 месяц за 2 оплаты).

    GREATEST(COALESCE(subscription_end, now), now):
      • активна (end>now)   → база = end   → продлеваем от конца;
      • истекла (end<=now)  → база = now   → продлеваем от now;
      • NULL (первая)       → база = now   → продлеваем от now.

    `conn` — опционально: если передан, работаем в его транзакции (для
    атомарности с record_payment). Иначе — своё соединение.
    """
    with conn_or_new(conn) as c:
        cursor = c.cursor()
        cursor.execute('''
            UPDATE families SET subscription_end =
                GREATEST(
                    COALESCE(subscription_end, (now() at time zone 'utc')),
                    (now() at time zone 'utc')
                ) + %s * interval '1 month'
            WHERE id = %s
        ''', (months, family_id))


def record_payment(family_id: int, paid_by_parent_id: Optional[int], amount: int,
                   currency: str, plan: str, months: int,
                   telegram_charge_id: str = None,
                   provider_charge_id: str = None, conn=None):
    """Записывает платёж в audit-log таблицу payments.

    `paid_by_parent_id` может быть None (плательщик без строки parents) —
    после миграции 0002 колонка paid_by nullable, аудит платежа не теряется.
    `conn` — опционально: см. extend_subscription (атомарность денежного пути).
    Может бросить UniqueViolation при дубле telegram_payment_charge_id
    (идемпотентность: повторная доставка successful_payment).
    """
    with conn_or_new(conn) as c:
        cursor = c.cursor()
        cursor.execute('''
            INSERT INTO payments (family_id, paid_by, amount, currency, plan, months,
                                  telegram_payment_charge_id, provider_payment_charge_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ''', (family_id, paid_by_parent_id, amount, currency, plan, months,
              telegram_charge_id, provider_charge_id))


def is_subscription_active(family_id: int) -> bool:
    """True если подписка семьи активна (subscription_end > now)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT subscription_end FROM families WHERE id = %s',
            (family_id,),
        )
        row = cursor.fetchone()
        if not row or not row['subscription_end']:
            return False
        cursor.execute(
            "SELECT %s > (now() at time zone 'utc') as active",
            (row['subscription_end'],),
        )
        # PG возвращает python bool (True/False), а не 1/0 — сравнение '== 1'
        # всегда было бы False. Сравниваем с True.
        return cursor.fetchone()['active'] is True


def has_any_active_subscription(telegram_id: int) -> bool:
    """Есть ли у пользователя хотя бы одна семья с активной подпиской.
    Использует get_families_for_user из families-домена."""
    # Lazy import — get_families_for_user пока в database_manager (families
    # модуль ещё не вынесен). После вынесения families.py заменить на
    # `from src.db.families import get_families_for_user`.
    from src.database_manager import get_families_for_user
    families = get_families_for_user(telegram_id)
    return any(is_subscription_active(f['id']) for f in families)


def cancel_subscription(family_id: int) -> bool:
    """Аннулирует подписку: subscription_end = now. True если семья существовала."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE families SET subscription_end = (now() at time zone 'utc') WHERE id = %s",
            (family_id,),
        )
        return cursor.rowcount > 0


def get_families_expiring_in_days(days: int) -> List[Dict[str, Any]]:
    """Семьи, чья подписка истекает ровно через N дней. Для warning'ов 7д/1д."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT f.id as family_id, f.family_name, f.subscription_end
            FROM families f
            WHERE f.subscription_end IS NOT NULL
              AND f.subscription_end > (now() at time zone 'utc')
              AND f.subscription_end <= (now() at time zone 'utc') + %s * interval '1 day'
        ''', (days + 1,))
        target_date = (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=days)).date()
        results = []
        for row in cursor.fetchall():
            # psycopg отдаёт timestamp-колонку как datetime-объект (не строку) —
            # работаем напрямую, без fromisoformat.
            subscription_end = row['subscription_end']
            if subscription_end is None:
                continue
            end_date = subscription_end.date()
            if end_date == target_date:
                results.append(dict(row))
        return results


def get_families_expired_today() -> List[Dict[str, Any]]:
    """Семьи, чья подписка истекла сегодня (по Ташкенту, UTC+5)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT f.id as family_id, f.family_name, f.subscription_end
            FROM families f
            WHERE f.subscription_end IS NOT NULL
              AND (f.subscription_end + interval '5 hours')::date = ((now() at time zone 'utc') + interval '5 hours')::date
              AND f.subscription_end <= (now() at time zone 'utc')
        ''')
        return [dict(row) for row in cursor.fetchall()]


__all__ = [
    "get_family_subscription",
    "extend_subscription",
    "record_payment",
    "is_subscription_active",
    "has_any_active_subscription",
    "cancel_subscription",
    "get_families_expiring_in_days",
    "get_families_expired_today",
]
