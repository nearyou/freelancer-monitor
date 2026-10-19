from __future__ import annotations

import logging
import time
from threading import Event

from .config import Settings
from .freelancer import FreelancerClient
from .notifications import NotificationSender
from .service import Monitor
from .state import SeenProjectStore
from .telegram import TelegramClient

logger = logging.getLogger(__name__)


def run_poll_loop(monitor: Monitor, interval: float, stop: Event) -> None:
    """Schedule from poll starts, skipping missed ticks rather than catching up."""
    next_poll = time.monotonic()
    while not stop.is_set():
        try:
            processed, queued = monitor.run_once()
            logger.info("Poll complete: processed=%d queued=%d", processed, queued)
        except Exception:
            logger.exception("Project poll failed; the monitor will retry")
        next_poll += interval
        now = time.monotonic()
        if next_poll <= now:
            next_poll = now + interval
        stop.wait(next_poll - now)


def project_worker(settings: Settings, stop: Event) -> None:
    store = None
    freelancer = None
    try:
        # SQLite connections and the HTTP session belong to this thread only.
        store = SeenProjectStore(settings.state_db_path)
        freelancer = FreelancerClient(settings.freelancer_api_url)
        run_poll_loop(Monitor(settings, freelancer, store), settings.poll_interval_seconds, stop)
    except Exception:
        logger.exception("Project worker could not start")
        stop.set()
    finally:
        if freelancer is not None:
            freelancer.close()
        if store is not None:
            store.close()


def notification_worker(settings: Settings, stop: Event) -> None:
    store = None
    try:
        store = SeenProjectStore(settings.state_db_path)
        telegram = TelegramClient(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            send_interval_seconds=settings.telegram_send_interval_seconds,
            stop_event=stop,
        )
        NotificationSender(store, telegram).run(stop)
    except Exception:
        logger.exception("Notification worker could not start")
        stop.set()
    finally:
        if store is not None:
            store.close()
