from __future__ import annotations

import logging
from threading import Event

from .state import SeenProjectStore
from .telegram import TelegramClient

logger = logging.getLogger(__name__)


class NotificationSender:
    """The sole consumer of the persistent outgoing Telegram queue."""

    def __init__(self, store: SeenProjectStore, telegram: TelegramClient):
        self.store = store
        self.telegram = telegram

    def send_next(self) -> bool:
        notification = self.store.next_notification()
        if notification is None:
            return False
        try:
            self.telegram.send_message(notification.message, button_url=notification.button_url)
        except InterruptedError:
            # Shutdown during a rate-limit wait: keep this alert ready for restart.
            return False
        except (OSError, RuntimeError, ValueError) as exc:
            delay = self.store.retry_notification(notification, str(exc))
            logger.warning("Notification %s will retry in %s seconds: %s", notification.id, delay, exc)
        else:
            self.store.complete_notification(notification)
            logger.info("Delivered notification %s (project=%s)", notification.id, notification.project_id)
        return True

    def run(self, stop: Event) -> None:
        while not stop.is_set():
            try:
                if not self.send_next():
                    stop.wait(0.5)
            except Exception:
                logger.exception("Notification queue failed; the sender will retry")
                stop.wait(5)
