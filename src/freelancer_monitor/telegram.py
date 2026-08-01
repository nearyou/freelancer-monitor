from __future__ import annotations

import json
import time
from datetime import timezone
from html import escape
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from zoneinfo import ZoneInfo

from .models import Project


JAPAN_TIMEZONE = ZoneInfo("Asia/Tokyo")


def format_project_message(project: Project) -> str:
    employer = project.employer
    member_since = (
        employer.member_since.strftime("%b %d, %Y").replace(" 0", " ")
        if employer.member_since
        else "Unknown"
    )
    posted_at = "Unknown"
    if project.submitted_at:
        submitted_at = project.submitted_at
        if submitted_at.tzinfo is None:
            submitted_at = submitted_at.replace(tzinfo=timezone.utc)
        submitted_jst = submitted_at.astimezone(JAPAN_TIMEZONE)
        posted_at = (
            f"{submitted_jst.strftime('%b')} {submitted_jst.day}, "
            f"{submitted_jst.strftime('%Y %H:%M JST')}"
        )
    project_type = "Hourly rate" if project.hourly else "Fixed price"
    listing_types = " • ".join(project.listing_types) or "Standard"
    if project.budget_min is None and project.budget_max is None:
        budget = "Unknown"
    elif project.budget_min == project.budget_max:
        budget = f"{project.budget_min:g}"
    else:
        minimum = "?" if project.budget_min is None else f"{project.budget_min:g}"
        maximum = "?" if project.budget_max is None else f"{project.budget_max:g}"
        budget = f"{minimum}–{maximum}"
    if budget != "Unknown":
        budget = f"{budget} {project.currency_code}".strip()
        if project.hourly:
            budget += " / hour"
    rating = int(min(max(employer.rating, 0.0), 5.0) + 0.5)
    status = lambda value: "✅" if value else "⛔"
    return (
        f"<blockquote><b>{escape(project.title)}</b></blockquote>\n"
        f"<b>Listing type:</b> {escape(listing_types)}\n"
        f"<b>Payment Type:</b> {project_type}\n"
        f"<b>Budget:</b> {escape(budget)}\n"
        f"<b>Posted:</b> {escape(posted_at)}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏠 {escape(employer.country or 'Unknown')}\t ⭐ {rating}\t 💬 {employer.review_count}\t 📅 {escape(member_since)}\n"
        f"{status(employer.identity_verified)} Identity verified\n"
        f"{status(employer.payment_verified)} Payment verified\n"
        f"{status(employer.deposit_made)} Deposit made"

    )


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        timeout: int = 30,
        send_interval_seconds: float = 3.1,
        max_rate_limit_retries: int = 5,
    ):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout
        self.send_interval_seconds = max(send_interval_seconds, 3.0)
        self.max_rate_limit_retries = max_rate_limit_retries
        self._last_send_attempt_at = 0.0

    def _wait_for_send_slot(self) -> None:
        remaining = self.send_interval_seconds - (
            time.monotonic() - self._last_send_attempt_at
        )
        if remaining > 0:
            time.sleep(remaining)

    def get_updates(
        self, *, offset: int | None = None, timeout_seconds: int = 2
    ) -> list[dict]:
        parameters: dict[str, str | int] = {
            "timeout": timeout_seconds,
            "allowed_updates": json.dumps(["message"]),
        }
        if offset is not None:
            parameters["offset"] = offset
        request = Request(
            f"https://api.telegram.org/bot{self.bot_token}/getUpdates",
            data=urlencode(parameters).encode("utf-8"),
            method="POST",
        )
        with urlopen(
            request, timeout=max(self.timeout, timeout_seconds + 5)
        ) as response:
            payload = json.load(response)
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API rejected getUpdates: {payload}")
        result = payload.get("result", [])
        return result if isinstance(result, list) else []

    def send_message(
        self,
        text: str,
        *,
        button_url: str | None = None,
        button_text: str = "View on Freelancer",
    ) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        parameters = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
        if button_url:
            parameters["reply_markup"] = json.dumps(
                {
                    "inline_keyboard": [
                        [{"text": button_text, "url": button_url}]
                    ]
                }
            )
        body = urlencode(parameters).encode("utf-8")
        request = Request(url, data=body, method="POST")
        for attempt in range(self.max_rate_limit_retries + 1):
            self._wait_for_send_slot()
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
            except HTTPError as exc:
                self._last_send_attempt_at = time.monotonic()
                if exc.code != 429 or attempt >= self.max_rate_limit_retries:
                    raise
                try:
                    error_payload = json.loads(exc.read().decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    error_payload = {}
                retry_after = error_payload.get("parameters", {}).get("retry_after")
                if retry_after is None:
                    retry_after = exc.headers.get("Retry-After", self.send_interval_seconds)
                time.sleep(max(float(retry_after), self.send_interval_seconds))
                continue
            self._last_send_attempt_at = time.monotonic()
            if not payload.get("ok"):
                raise RuntimeError(f"Telegram API rejected the message: {payload}")
            return
