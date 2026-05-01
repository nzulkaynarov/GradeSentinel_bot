"""Утилиты для работы с групповыми чатами Telegram.

Чистая логика без зависимости от telebot — чтобы тесты в CI работали без
установленного pyTelegramBotAPI.
"""
import re
from typing import Optional

# Парсер ссылок Telegram на сообщение в теме:
#   https://t.me/c/<internal_chat_id>/<topic_id>/<msg_id>      (приватные)
#   https://t.me/c/<internal_chat_id>/<topic_id>               (саму тему)
#   https://t.me/<username>/<topic_id>/<msg_id>                 (публичные)
#   https://t.me/<username>/<topic_id>                          (саму тему)
# topic_id = message_thread_id (= id первого сообщения темы).
_TOPIC_LINK_RE = re.compile(
    r'https?://t\.me/(?:c/)?(?P<chat>[A-Za-z0-9_]+)/(?P<a>\d+)(?:/(?P<b>\d+))?(?:[/?#].*)?$'
)


def parse_topic_link(link: str) -> Optional[int]:
    """Извлекает message_thread_id из ссылки Telegram. None если не распарсилось.

    Telegram шлёт ссылки вида .../<topic_id>/<message_id>, но иногда
    .../<message_id> (если ссылка на сообщение в General). Мы берём
    первый числовой сегмент — для тематической ссылки это всегда topic_id.
    """
    if not link:
        return None
    m = _TOPIC_LINK_RE.match(link.strip())
    if not m:
        return None
    try:
        return int(m.group('a'))
    except (ValueError, TypeError):
        return None
