from __future__ import annotations

import math
import os
from dataclasses import dataclass, fields, replace
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Load a simple .env file without overwriting real environment variables."""
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _csv(name: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, "").split(",") if item.strip())


def _int(name: str, default: int, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _float(name: str, default: float, minimum: float = 0.0) -> float:
    value = float(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


@dataclass(frozen=True)
class ScoreConfig:
    payment_verified: float = 20
    deposit_made: float = 15
    profile_completed: float = 5
    reviews_max: float = 15
    member_age_max: float = 10
    reviews_cap: int = 50
    member_age_max_days: int = 100
    identity_verified: float = 0
    phone_verified: float = 0
    email_verified: float = 0
    rating_max: float = 0

    @property
    def maximum(self) -> float:
        return (
            self.payment_verified
            + self.deposit_made
            + self.profile_completed
            + self.reviews_max
            + self.member_age_max
            + self.identity_verified
            + self.phone_verified
            + self.email_verified
            + self.rating_max
        )

    def updated(self, name: str, value: str) -> "ScoreConfig":
        if name not in {field.name for field in fields(self)}:
            raise ValueError("Unknown score setting. Use /show_score to see valid names.")
        try:
            number = float(value)
        except ValueError:
            raise ValueError("Score settings must be finite numbers.") from None
        if not math.isfinite(number):
            raise ValueError("Score settings must be finite numbers.")
        if name in {"reviews_cap", "member_age_max_days"}:
            if number < 1 or not number.is_integer():
                raise ValueError(f"{name} must be a whole number of at least 1.")
            number = int(number)
        elif number < 0:
            raise ValueError("Score weights must be zero or greater. Use 0 to disable a signal.")
        updated = replace(self, **{name: number})
        if not math.isfinite(updated.maximum):
            raise ValueError("The combined maximum score is too large.")
        return updated


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_send_interval_seconds: float
    freelancer_api_url: str
    page_limit: int
    freelancer_languages: tuple[str, ...]
    poll_interval_seconds: int
    state_db_path: Path
    log_level: str

    @classmethod
    def from_env(cls, *, require_telegram: bool = True) -> "Settings":
        load_dotenv()
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        if require_telegram and (not token or not chat_id):
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")

        return cls(
            telegram_bot_token=token,
            telegram_chat_id=chat_id,
            telegram_send_interval_seconds=_float(
                "TELEGRAM_SEND_INTERVAL_SECONDS", 3.1, 3.0
            ),
            freelancer_api_url=os.getenv(
                "FREELANCER_API_URL",
                "https://www.freelancer.com/api/projects/0.1/projects/active/",
            ).strip(),
            page_limit=_int("FREELANCER_PAGE_LIMIT", 50, 1),
            freelancer_languages=_csv("FREELANCER_LANGUAGES"),
            poll_interval_seconds=_int("POLL_INTERVAL_SECONDS", 15, 5),
            state_db_path=Path(os.getenv("STATE_DB_PATH", "freelancer_monitor.db")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
