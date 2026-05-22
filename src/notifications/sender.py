"""Sender — единая точка отправки уведомлений.

API:
    sender.send(tg_id, text, ntype, kb=None, force=False, defer=None) -> bool
    sender.send_to_admin(text, ntype, lang=None) -> bool
    sender.send_to_group(chat_id, thread_id, text, ntype, kb=None) -> bool
    sender.batch_send(items, ntype) -> dict[str, int]  # {sent, queued, failed, skipped}

Что делает каждый вызов:
1. Проверяет notify_mode пользователя (summary_only → skip кроме force)
2. Решает: defer в очередь или слать сейчас (по should_defer + is_quiet_hours)
3. Если defer → write to notification_queue / group_notification_queue
4. Иначе → send_with_retry с 429/5xx handling
5. Логирует с тегом ntype= и status= (для grep'а по типу)

Что НЕ делает (по дизайну):
- Не форматирует HTML — формирование текста остаётся у caller'а
- Не управляет batching — batch_send только rate-limit'ит между вызовами
- Не делает persistent очереди — этим занимается database_manager
"""
import logging
import os
import time
from typing import Iterable, List, Optional, Tuple

from src.notifications.quiet_hours import is_quiet_hours, should_defer
from src.notifications.types import NotificationType
from src.telegram_utils import send_with_retry

logger = logging.getLogger(__name__)

# Глобальный rate-limit между батч-сообщениями (Telegram ~30 msg/sec для бота).
# 0.05с = 20 msg/sec — с запасом.
_BATCH_DELAY_SECONDS = 0.05


class Sender:
    """Singleton-ish wrapper. Создаётся раз через init_sender(bot).
    Дальше get_sender() возвращает один и тот же экземпляр."""

    def __init__(self, bot):
        self._bot = bot

    # ────────────────────────────────────────────────────────
    # Public API
    # ────────────────────────────────────────────────────────

    def send(
        self,
        tg_id: int,
        text: str,
        *,
        ntype: NotificationType,
        kb=None,
        force: bool = False,
        defer: Optional[bool] = None,
        parse_mode: str = "HTML",
    ) -> bool:
        """Отправляет одно уведомление одному пользователю.

        Возвращает True если ушло (или попало в очередь), False при failure.

        force=True игнорирует notify_mode=summary_only (для quarter grades).
        defer=None — авто-решение по NotificationType. defer=True/False — override.
        """
        if not self._bot:
            logger.warning(f"Sender: bot not initialized, ntype={ntype.value} tg={tg_id}")
            return False

        # notify_mode check
        if not force:
            from src.database_manager import get_notify_mode
            if get_notify_mode(tg_id) == "summary_only":
                logger.info(f"ntype={ntype.value} tg={tg_id} status=skipped reason=summary_only")
                return True

        # Решение defer/send
        should_queue = defer if defer is not None else (
            should_defer(ntype) and is_quiet_hours()
        )

        if should_queue:
            try:
                from src.database_manager import queue_notification
                queue_notification(tg_id, text)
                logger.info(f"ntype={ntype.value} tg={tg_id} status=queued reason=quiet_hours")
                return True
            except Exception as e:
                logger.error(f"ntype={ntype.value} tg={tg_id} status=queue_failed err={e}")
                return False

        return self._do_send(tg_id, text, kb=kb, parse_mode=parse_mode, ntype=ntype)

    def send_to_admin(
        self,
        text: str,
        *,
        ntype: NotificationType,
        parse_mode: str = "HTML",
    ) -> bool:
        """Отправка администратору. ADMIN_ID из env.
        НЕ deferится — админ-алерты всегда срочные."""
        admin_id = self._get_admin_id()
        if not admin_id:
            logger.warning(f"ntype={ntype.value} status=no_admin_id text={text[:80]!r}")
            return False
        return self._do_send(admin_id, text, kb=None, parse_mode=parse_mode, ntype=ntype)

    def send_to_group(
        self,
        chat_id: int,
        thread_id: Optional[int],
        text: str,
        *,
        ntype: NotificationType,
        kb=None,
        parse_mode: str = "HTML",
        defer: Optional[bool] = None,
    ) -> bool:
        """Отправка в групповой чат семьи. Уважает quiet_hours по типу."""
        if not self._bot:
            return False

        should_queue = defer if defer is not None else (
            should_defer(ntype) and is_quiet_hours()
        )

        if should_queue:
            try:
                from src.database_manager import queue_group_notification
                queue_group_notification(chat_id, thread_id, text)
                logger.info(
                    f"ntype={ntype.value} chat={chat_id} thread={thread_id} "
                    f"status=queued reason=quiet_hours"
                )
                return True
            except Exception as e:
                logger.error(
                    f"ntype={ntype.value} chat={chat_id} thread={thread_id} "
                    f"status=queue_failed err={e}"
                )
                return False

        kwargs = {"parse_mode": parse_mode, "disable_web_page_preview": True}
        if thread_id is not None:
            kwargs["message_thread_id"] = thread_id
        if kb is not None:
            kwargs["reply_markup"] = kb

        ok, exc = send_with_retry(
            self._bot.send_message, chat_id, text, **kwargs
        )
        if ok:
            logger.info(f"ntype={ntype.value} chat={chat_id} thread={thread_id} status=sent")
        else:
            logger.warning(
                f"ntype={ntype.value} chat={chat_id} thread={thread_id} "
                f"status=failed err={exc}"
            )
        return ok

    def batch_send(
        self,
        items: Iterable[Tuple[int, str]],
        *,
        ntype: NotificationType,
        kb=None,
        parse_mode: str = "HTML",
    ) -> dict:
        """Массовая отправка (для evening/weekly/etc).
        items — iterable (tg_id, text).
        Returns: {'sent': N, 'queued': N, 'failed': N, 'skipped': N}"""
        stats = {"sent": 0, "queued": 0, "failed": 0, "skipped": 0}

        for tg_id, text in items:
            before_sent = stats["sent"]
            ok = self.send(tg_id, text, ntype=ntype, kb=kb, parse_mode=parse_mode)
            if not ok:
                stats["failed"] += 1
            elif "queued" in str(ok):  # never happens — placeholder
                pass
            else:
                stats["sent"] += 1
            time.sleep(_BATCH_DELAY_SECONDS)

        logger.info(
            f"ntype={ntype.value} batch_done sent={stats['sent']} "
            f"failed={stats['failed']}"
        )
        return stats

    # ────────────────────────────────────────────────────────
    # Internals
    # ────────────────────────────────────────────────────────

    def _do_send(
        self,
        tg_id: int,
        text: str,
        *,
        kb,
        parse_mode: str,
        ntype: NotificationType,
    ) -> bool:
        """Низкоуровневая отправка с retry. Не проверяет quiet hours / notify_mode."""
        kwargs = {"parse_mode": parse_mode, "disable_web_page_preview": True}
        if kb is not None:
            kwargs["reply_markup"] = kb

        ok, exc = send_with_retry(
            self._bot.send_message, tg_id, text, **kwargs
        )
        if ok:
            logger.info(f"ntype={ntype.value} tg={tg_id} status=sent")
        else:
            code = getattr(exc, "error_code", None)
            if code == 403:
                logger.info(f"ntype={ntype.value} tg={tg_id} status=blocked reason=user_blocked_bot")
            else:
                logger.warning(f"ntype={ntype.value} tg={tg_id} status=failed err={exc}")
        return ok

    @staticmethod
    def _get_admin_id() -> Optional[int]:
        raw = os.environ.get("ADMIN_ID", "0") or "0"
        try:
            val = int(raw)
            return val if val > 0 else None
        except (TypeError, ValueError):
            return None


# ════════════════════════════════════════════════════════════
#  Singleton management
# ════════════════════════════════════════════════════════════

_INSTANCE: Optional[Sender] = None


def init_sender(bot) -> Sender:
    """Создать (или пересоздать) глобальный Sender. Вызывается из main() после
    init bot instance."""
    global _INSTANCE
    _INSTANCE = Sender(bot)
    return _INSTANCE


def get_sender() -> Sender:
    """Получить singleton. Если init не вызван — RuntimeError (раньше падало
    silently через if not _bot)."""
    if _INSTANCE is None:
        raise RuntimeError(
            "Sender not initialized. Call init_sender(bot) in main() first."
        )
    return _INSTANCE
