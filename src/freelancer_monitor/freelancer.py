from __future__ import annotations

import logging
import re
import time
from dataclasses import replace
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Employer, Project

logger = logging.getLogger(__name__)

_CLIENT_SECTION_START = "<app-project-view-logged-out-client-info"
_CLIENT_SECTION_END = "</app-project-view-logged-out-client-info>"
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
_MEMBER_SINCE_RE = re.compile(
    r"\bMember since\s+([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})\b"
)


def _extract_client_section(html: str) -> str:
    start = html.find(_CLIENT_SECTION_START)
    if start < 0:
        raise EmployerPageParseError("Public project page has no client-information section")
    end = html.find(_CLIENT_SECTION_END, start)
    if end < 0:
        return html[start:]
    return html[start : end + len(_CLIENT_SECTION_END)]


class ProjectEnrichmentError(RuntimeError):
    """Raised when one project's public detail page cannot be enriched safely."""


class EmployerPageParseError(ProjectEnrichmentError):
    """Raised when a public project page does not expose its client information."""


class _EmployerPageParser(HTMLParser):
    """Extract semantic client fields from Freelancer's server-rendered HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scope_depth = 0
        self.scope_started = False
        self.capture: str | None = None
        self.capture_depth = 0
        self.capture_text: list[str] = []
        self.all_text: list[str] = []
        self.icon_label: str | None = None
        self.icon_depth = 0
        self.icon_success = False
        self.icons: dict[str, bool] = {}
        self.rating: float | None = None
        self.reviews: int | None = None

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key.lower(): value or "" for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attributes = self._attrs(attrs)
        if tag == "app-project-view-logged-out-client-info":
            self.scope_started = True
            self.scope_depth = 1
            return
        if not self.scope_depth:
            return
        self.scope_depth += 1

        if self.capture:
            self.capture_depth += 1
        elif tag == "fl-rating":
            self.capture = "rating"
            self.capture_depth = 1
            self.capture_text = []
        elif tag == "fl-review-count":
            self.capture = "reviews"
            self.capture_depth = 1
            self.capture_text = []

        if self.icon_label:
            self.icon_depth += 1
            if attributes.get("data-color", "").lower() == "success":
                self.icon_success = True
        elif tag == "fl-icon" and attributes.get("aria-label"):
            self.icon_label = attributes["aria-label"].strip().casefold()
            self.icon_depth = 1
            self.icon_success = attributes.get("data-color", "").lower() == "success"

    def handle_endtag(self, tag: str) -> None:
        if not self.scope_depth:
            return

        if self.capture:
            self.capture_depth -= 1
            if self.capture_depth == 0:
                text = " ".join(self.capture_text)
                numbers = _NUMBER_RE.findall(text)
                if numbers and self.capture == "rating":
                    self.rating = float(numbers[-1])
                elif numbers and self.capture == "reviews":
                    self.reviews = int(float(numbers[-1]))
                self.capture = None
                self.capture_text = []

        if self.icon_label:
            self.icon_depth -= 1
            if self.icon_depth == 0:
                self.icons[self.icon_label] = self.icon_success
                self.icon_label = None
                self.icon_success = False

        self.scope_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self.scope_depth:
            return
        text = data.strip()
        if text:
            self.all_text.append(text)
            if self.capture:
                self.capture_text.append(text)

    @property
    def text(self) -> str:
        return " ".join(self.all_text)


def parse_employer_page(html: str) -> Employer:
    parser = _EmployerPageParser()
    parser.feed(_extract_client_section(html))
    if not parser.scope_started:
        raise EmployerPageParseError("Public project page has no client-information section")

    member_match = _MEMBER_SINCE_RE.search(parser.text)
    member_since = None
    if member_match:
        for pattern in ("%b %d, %Y", "%B %d, %Y"):
            try:
                member_since = datetime.strptime(member_match.group(1), pattern).replace(
                    tzinfo=timezone.utc
                )
                break
            except ValueError:
                continue

    def verified(*labels: str) -> bool:
        return any(parser.icons.get(label.casefold(), False) for label in labels)

    country = ""
    try:
        about_index = parser.all_text.index("About the client")
        location = " ".join(parser.all_text[about_index + 1].split())
        country = location.rsplit(",", 1)[-1].strip()
    except (ValueError, IndexError):
        pass

    if parser.rating is None or parser.reviews is None or member_since is None:
        raise EmployerPageParseError(
            "Public client section is missing rating, review count, or member-since date"
        )
    return Employer(
        country=country,
        identity_verified=verified("Identity verified", "Profile complete"),
        payment_verified=verified("Payment verified"),
        deposit_made=verified("Deposit made"),
        rating=parser.rating,
        review_count=parser.reviews,
        member_since=member_since,
    )


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return str(value).strip().lower() in {"1", "true", "yes", "verified"}


def _first(mapping: dict[str, Any], *paths: str, default: Any = None) -> Any:
    for path in paths:
        current: Any = mapping
        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                break
            current = current[part]
        else:
            if current is not None:
                return current
    return default


def _datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    text = str(value).strip()
    try:
        if text.isdigit():
            return datetime.fromtimestamp(int(text), tz=timezone.utc)
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, OSError):
        logger.debug("Could not parse date value %r", value)
        return None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _listing_types(raw: dict[str, Any]) -> tuple[str, ...]:
    upgrades = raw.get("upgrades", {})
    if not isinstance(upgrades, dict):
        upgrades = {}

    def enabled(*values: Any) -> bool:
        return any(
            bool(value) if isinstance(value, (dict, list)) else _as_bool(value)
            for value in values
        )

    listing_types: list[str] = []
    checks = (
        ("Featured", enabled(raw.get("featured"), upgrades.get("featured"))),
        ("Sealed", enabled(upgrades.get("sealed"))),
        ("NDA", enabled(upgrades.get("NDA"), upgrades.get("nda"), raw.get("nda_details"))),
        ("Urgent", enabled(raw.get("urgent"), upgrades.get("urgent"))),
        (
            "Recruiter",
            enabled(
                raw.get("recruiter"),
                upgrades.get("recruiter"),
                upgrades.get("unpaid_recruiter"),
            ),
        ),
        ("IP Agreement", enabled(raw.get("ip_contract"), upgrades.get("ip_contract"))),
    )
    for label, is_enabled in checks:
        if is_enabled:
            listing_types.append(label)
    return tuple(listing_types)


class FreelancerClient:
    def __init__(
        self,
        api_url: str,
        timeout: int = 30,
        detail_request_delay_seconds: float = 1.0,
        session: requests.Session | None = None,
    ):
        self.api_url = api_url
        self.timeout = timeout
        self.detail_request_delay_seconds = detail_request_delay_seconds
        self._last_detail_request_at = 0.0
        self._employer_cache: dict[int, Employer] = {}
        self.session = session or self._build_session()

    @staticmethod
    def _build_session() -> requests.Session:
        retry = Retry(
            total=3,
            connect=2,
            read=2,
            status=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=2, pool_maxsize=4)
        session = requests.Session()
        session.mount("https://", adapter)
        session.headers.update(
            {
                "Accept-Encoding": "gzip, deflate",
                "User-Agent": "freelancer-monitor/0.1",
            }
        )
        return session

    def fetch_active_projects(self, limit: int = 50) -> list[Project]:
        self._employer_cache.clear()
        parameters = {
            "limit": limit,
            "full_description": "true",
            "job_details": "true",
            "user_details": "true",
            "user_employer_reputation": "true",
            "sort_field": "time_updated",
            "or_search_query": "true",
        }
        response = self.session.get(
            self.api_url,
            params=parameters,
            headers={"Accept": "application/json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return parse_projects_response(response.json())

    def enrich_from_public_page(self, project: Project) -> Project:
        if project.owner_id is not None and project.owner_id in self._employer_cache:
            return replace(project, employer=self._employer_cache[project.owner_id])
        elapsed = time.monotonic() - self._last_detail_request_at
        remaining = self.detail_request_delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        try:
            try:
                response = self.session.get(
                    project.url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
                html = response.text
            except requests.RequestException as exc:
                raise ProjectEnrichmentError(
                    f"Public project page request failed: {exc}"
                ) from exc
        finally:
            self._last_detail_request_at = time.monotonic()
        employer = parse_employer_page(html)
        if project.owner_id is not None:
            self._employer_cache[project.owner_id] = employer
        return replace(project, employer=employer)

    def close(self) -> None:
        self.session.close()


def parse_projects_response(payload: dict[str, Any]) -> list[Project]:
    result = payload.get("result", payload)
    raw_projects = result.get("projects", []) if isinstance(result, dict) else []
    raw_users = result.get("users", {}) if isinstance(result, dict) else {}
    if isinstance(raw_users, list):
        users = {str(user.get("id")): user for user in raw_users if isinstance(user, dict)}
    else:
        users = {str(key): value for key, value in raw_users.items()}

    projects: list[Project] = []
    for raw in raw_projects:
        if not isinstance(raw, dict) or "id" not in raw:
            continue
        owner_id = _first(raw, "owner_id", "owner.id")
        user = users.get(str(owner_id), {})
        status = user.get("status", {}) if isinstance(user, dict) else {}
        reputation = _first(
            user,
            "employer_reputation.entire_history",
            "employer_reputation",
            "reputation.entire_history",
            default={},
        )
        if not isinstance(reputation, dict):
            reputation = {}

        rating = _number(_first(reputation, "overall", "rating", default=0))
        reviews = int(_number(_first(reputation, "reviews", "review_count", "count", default=0)))
        employer = Employer(
            country=str(
                _first(
                    user,
                    "location.country.name",
                    "location.country.code",
                    "country.name",
                    default="",
                )
            ),
            identity_verified=_as_bool(
                _first(status, "identity_verified", default=_first(user, "identity_verified", default=False))
            ),
            payment_verified=_as_bool(
                _first(status, "payment_verified", default=_first(user, "payment_verified", default=False))
            ),
            deposit_made=_as_bool(
                _first(status, "deposit_made", default=_first(user, "deposit_made", default=False))
            ),
            rating=rating,
            review_count=reviews,
            member_since=_datetime(_first(user, "registration_date", "member_since", "created_at")),
        )

        jobs = raw.get("jobs", [])
        skills = tuple(
            str(_first(job, "name", "seo_url", default="")).strip()
            for job in jobs
            if isinstance(job, dict) and _first(job, "name", "seo_url", default="")
        )
        budget = raw.get("budget", {}) if isinstance(raw.get("budget"), dict) else {}
        currency = raw.get("currency", {}) if isinstance(raw.get("currency"), dict) else {}
        project_id = int(raw["id"])
        seo_url = str(raw.get("seo_url", "")).strip("/")
        url = f"https://www.freelancer.com/projects/{seo_url}" if seo_url else f"https://www.freelancer.com/projects/{project_id}"
        projects.append(
            Project(
                id=project_id,
                title=str(raw.get("title", "Untitled project")),
                description=str(_first(raw, "description", "preview_description", default="")),
                url=url,
                owner_id=int(owner_id) if owner_id is not None else None,
                skills=skills,
                listing_types=_listing_types(raw),
                currency_code=str(_first(currency, "code", default="")),
                budget_min=_number(budget.get("minimum"), default=0.0) if "minimum" in budget else None,
                budget_max=_number(budget.get("maximum"), default=0.0) if "maximum" in budget else None,
                hourly=str(raw.get("type", "")).lower() == "hourly",
                submitted_at=_datetime(_first(raw, "submitdate", "time_submitted", "time_updated")),
                employer=employer,
            )
        )
    return projects
