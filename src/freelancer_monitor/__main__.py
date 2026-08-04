from __future__ import annotations

import argparse
import logging
import sys
import time

from .tls import configure_system_trust

configure_system_trust()

from .config import Settings
from .commands import TelegramCommandProcessor
from .freelancer import FreelancerClient
from .service import Monitor
from .state import SeenProjectStore
from .telegram import TelegramClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor and score new Freelancer projects")
    parser.add_argument("--once", action="store_true", help="poll once and exit")
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
            f"Configuration OK: threshold={settings.score_threshold:g}, "
            f"maximum={settings.score.maximum:g}, filters={len(settings.search_words)} words/"
            f"{len(settings.skills)} skills"
        )
        return 0

    store = SeenProjectStore(settings.state_db_path)
    imported = store.initialize_preferences(
        skills=settings.skills,
        search_words=settings.search_words,
        threshold=settings.score_threshold,
    )
    if imported:
        logging.info("Imported initial filters and threshold into SQLite")
    stored_threshold = store.get_threshold()
    if stored_threshold > settings.score.maximum:
        store.set_threshold(settings.score.maximum)
        logging.warning(
            "Reduced stored threshold from %g to the new maximum score %g",
            stored_threshold,
            settings.score.maximum,
        )
    telegram = TelegramClient(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        send_interval_seconds=settings.telegram_send_interval_seconds,
    )
    freelancer = FreelancerClient(
        settings.freelancer_api_url,
        detail_request_delay_seconds=settings.detail_request_delay_seconds,
    )
    monitor = Monitor(
        settings,
        freelancer,
        telegram,
        store,
    )
    commands = TelegramCommandProcessor(
        telegram,
        store,
        authorized_chat_id=settings.telegram_chat_id,
        maximum_score=settings.score.maximum,
    )
    try:
        if args.once:
            try:
                processed, notified = monitor.run_once()
                logging.info("Poll complete: processed=%d notified=%d", processed, notified)
                return 0
            except Exception:
                logging.exception("Poll failed")
                return 1

        next_project_poll = 0.0
        while True:
            try:
                commands.poll_once(timeout_seconds=2)
            except Exception:
                logging.exception("Telegram command poll failed; the bot will retry")
                time.sleep(2)
            if time.monotonic() >= next_project_poll:
                try:
                    processed, notified = monitor.run_once()
                    logging.info(
                        "Poll complete: processed=%d notified=%d", processed, notified
                    )
                except Exception:
                    logging.exception("Project poll failed; the monitor will retry")
                next_project_poll = time.monotonic() + settings.poll_interval_seconds
    except KeyboardInterrupt:
        logging.info("Monitor stopped")
        return 0
    finally:
        freelancer.close()
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
