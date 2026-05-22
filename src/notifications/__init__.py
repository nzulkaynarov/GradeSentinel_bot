"""Unified notification layer (2026-05-22).

Раньше каждый scheduler/handler сам реализовывал `bot.send_message` +
try/except + sleep + опционально quiet_hours queue. Retry на 429/5xx был
только в 2 местах из 13.

Сейчас всё уведомления идут через `Sender` (sender.py):
- единый retry (429 + 5xx + сеть) через telegram_utils.send_with_retry
- единая логика тихих часов (quiet_hours.py)
- structured-логи с тегом NotificationType (types.py)
- защитная отправка админу при критичных сбоях

Что не покрыто:
- broadcast — специфический resumable flow в handlers/communication.py
- successful_payment — атомарный, обёрнут отдельно
- support reply (admin↔user) — отдельный path через support_msg_map
"""
from src.notifications.sender import Sender, get_sender, init_sender
from src.notifications.types import NotificationType
from src.notifications.quiet_hours import is_quiet_hours, should_defer

__all__ = [
    "Sender",
    "get_sender",
    "init_sender",
    "NotificationType",
    "is_quiet_hours",
    "should_defer",
]
