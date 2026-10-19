from __future__ import annotations

import argparse
import logging
import sys
from threading import Event, Thread

from .tls import configure_system_trust

configure_system_trust()

from .config import Settings
from .commands import TelegramCommandProcessor
from .freelancer import FreelancerClient
from .notifications import NotificationSender
from .runtime import notification_worker, project_worker
from .service import Monitor
from .state import SeenProjectStore
from .telegram import TelegramClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor and score new Freelancer projects")
    parser.add_argument("--once", action="store_true", help="poll once, send ready queued messages, and exit")
    parser.add_argument(
        "--check-config", action="store_true", help="validate .env without calling external services"
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        settings = Settings.from_env()
    except (ValueError, OSError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.check_config:
        print(
            "Environment configuration OK. Owner scoring and thresholds are stored in SQLite; "
            "use /show_score in Telegram to view them."
        )
        return 0

    store = SeenProjectStore(settings.state_db_path)
    imported = store.initialize_preferences(
        skills=settings.skills,
        search_words=settings.search_words,
    )
    if imported:
        logging.info("Initialized filters and scoring preferences in SQLite")
    score_config, stored_threshold = store.get_scoring_settings()
    if stored_threshold > score_config.maximum:
        logging.warning(
            "Stored threshold %g exceeds maximum %g; use /show_score and /set_threshold in Telegram",
            stored_threshold,
            score_config.maximum,
        )
    stop = Event()
    workers: list[Thread] = []
    telegram = TelegramClient(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        send_interval_seconds=settings.telegram_send_interval_seconds,
        stop_event=stop,
    )
    commands = TelegramCommandProcessor(
        telegram,
        store,
        authorized_chat_id=settings.telegram_chat_id,
    )
    try:
        if args.once:
            freelancer = FreelancerClient(settings.freelancer_api_url)
            try:
                processed, queued = Monitor(settings, freelancer, store).run_once()
                logging.info("Poll complete: processed=%d queued=%d", processed, queued)
                sender = NotificationSender(store, telegram)
                while sender.send_next():
                    pass
                pending = store.pending_notification_count()
                if pending:
                    logging.warning("%d notifications remain queued for a later run", pending)
                return 1 if pending else 0
            except Exception:
                logging.exception("Poll failed")
                return 1
            finally:
                freelancer.close()

        for name, target in (("project-poller", project_worker), ("notification-sender", notification_worker)):
            worker = Thread(name=name, target=target, args=(settings, stop), daemon=True)
            worker.start()
            workers.append(worker)
        while not stop.is_set():
            try:
                commands.poll_once(timeout_seconds=2)
            except Exception:
                logging.exception("Telegram command poll failed; the bot will retry")
                stop.wait(2)
        return 1  # A worker failed to initialize and requested shutdown.
    except KeyboardInterrupt:
        logging.info("Monitor stopped")
        return 0
    finally:
        stop.set()
        for worker in workers:
            worker.join(timeout=5)
            if worker.is_alive():
                logging.warning("%s is still finishing a request; unsent alerts remain in SQLite", worker.name)
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
