from __future__ import annotations

import os
from dataclasses import dataclass
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
    payment_verified: float
    deposit_made: float
    profile_completed: float
    reviews_max: float
    member_age_max: float
    reviews_cap: int
    member_age_max_days: int

    @property
    def maximum(self) -> float:
        return (
            self.payment_verified
            + self.deposit_made
            + self.profile_completed
            + self.reviews_max
            + self.member_age_max
        )


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    telegram_chat_id: str
    telegram_send_interval_seconds: float
    freelancer_api_url: str
    page_limit: int
    detail_request_delay_seconds: float
    freelancer_languages: tuple[str, ...]
    search_words: tuple[str, ...]
    skills: tuple[str, ...]
    filter_match_mode: str
    score_threshold: float
    score: ScoreConfig
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

        match_mode = os.getenv("FILTER_MATCH_MODE", "any").strip().lower()
        if match_mode not in {"any", "all"}:
            raise ValueError("FILTER_MATCH_MODE must be 'any' or 'all'")

        score = ScoreConfig(
            payment_verified=_float("SCORE_PAYMENT_VERIFIED", 20),
            deposit_made=_float("SCORE_DEPOSIT_MADE", 15),
            profile_completed=_float("SCORE_PROFILE_COMPLETED", 5),
            reviews_max=_float("SCORE_REVIEWS_MAX", 15),
            member_age_max=_float("SCORE_MEMBER_AGE_MAX", 10),
            reviews_cap=_int("SCORE_REVIEWS_CAP", 50, 1),
            member_age_max_days=_int("SCORE_MEMBER_AGE_MAX_DAYS", 100, 1),
        )
        threshold = _float("SCORE_THRESHOLD", 65)
        if threshold > score.maximum:
            raise ValueError(
                f"SCORE_THRESHOLD ({threshold}) cannot exceed maximum score ({score.maximum})"
            )

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
            detail_request_delay_seconds=_float("DETAIL_REQUEST_DELAY_SECONDS", 1.0),
            freelancer_languages=_csv("FREELANCER_LANGUAGES"),
            search_words=_csv("SEARCH_WORDS"),
            skills=_csv("SKILLS"),
            filter_match_mode=match_mode,
            score_threshold=threshold,
            score=score,
            poll_interval_seconds=_int("POLL_INTERVAL_SECONDS", 60, 5),
            state_db_path=Path(os.getenv("STATE_DB_PATH", "freelancer_monitor.db")),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        )
