"""Тихие часы — единое место принятия решения «отложить или слать сейчас».

Раньше каждый sender сам делал `if is_quiet_hours(): queue else: send`.
Из-за этого половина schedulers игнорировала тихие часы (бот alive,
вечерняя сводка, weekly digest) — теоретически они в активные часы,
но при сбое scheduler'а могли стрелять в 4 утра.

`should_defer(NotificationType)` — единая policy:
- Grade events — defer (родитель спит)
- Admin alerts — НЕ defer (нужно срочно)
- Daily summaries — НЕ defer (они и так в активном окне, плюс агрегированы)
- Payment / support — НЕ defer (синхронный flow)
"""
from src.notifications.types import NotificationType
from src.notification_helpers import is_quiet_hours as _is_quiet_hours

# Реэкспорт (источник истины — notification_helpers.py)
is_quiet_hours = _is_quiet_hours


# Типы, которые ОТКЛАДЫВАЕМ в тихие часы. Всё остальное — шлём сразу
# (включая daily summaries — они и так запускаются в active hours,
# но если scheduler сбился, лучше отправить чем потерять).
_DEFER_IN_QUIET = {
    NotificationType.GRADE_INSTANT,
    NotificationType.GRADE_GROUP,
    NotificationType.QUARTER_GRADE,
    NotificationType.PROACTIVE_ALERT,
}


def should_defer(ntype: NotificationType) -> bool:
    """True если уведомление этого типа должно копиться в очередь
    во время тихих часов (вместо мгновенной отправки)."""
    return ntype in _DEFER_IN_QUIET
