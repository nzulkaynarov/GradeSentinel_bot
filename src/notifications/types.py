"""Типы уведомлений — для structured-логов и per-type quiet hours policy."""
from enum import Enum


class NotificationType(str, Enum):
    # Instant — реактивные на event
    GRADE_INSTANT = "grade_instant"          # monitor нашёл новую оценку
    GRADE_GROUP = "grade_group"              # та же в групповой чат семьи
    QUARTER_GRADE = "quarter_grade"          # четвертная оценка

    # Scheduled daily
    EVENING_SUMMARY = "evening_summary"      # 19:00
    MORNING_FLUSH = "morning_flush"          # 07:00, накопленное за тихие
    BOT_ALIVE = "bot_alive"                  # 15:00, "бот работает"
    SUBSCRIPTION_EXPIRY = "sub_expiry"       # 10:00, за 7д/1д/0д
    WEEKLY_DIGEST = "weekly_digest"          # Вс 18:00 (бесплатный текстовый)
    PROACTIVE_ALERT = "proactive_alert"      # раз в день 17:00, AI-аномалии
    SUMMER_ACTIVITY = "summer_activity"      # каникулы: еженедельный AI-нэдж родителю

    # Admin/operational
    SHEET_FAILURE = "sheet_failure"          # 5 подряд ошибок чтения листа
    PAYMENT_SUCCESS = "payment_success"      # юзеру после оплаты
    SUPPORT_REPLY = "support_reply"          # admin → user в поддержке
