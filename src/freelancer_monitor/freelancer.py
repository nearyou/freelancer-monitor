from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import Employer, Project

logger = logging.getLogger(__name__)


class FreelancerAPIError(RuntimeError):
    """The API returned an error or an unexpected response structure."""


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    return str(value).strip().lower() in {"1", "true", "yes", "verified"}


def _verification(value: Any) -> bool | None:
    """Keep absent or malformed verification states distinct from false."""
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "verified"}:
        return True
    if normalized in {"0", "false", "no", "unverified"}:
        return False
    return None


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
    text = str(value).strip()
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if text.isdigit():
            return datetime.fromtimestamp(int(text), tz=timezone.utc)
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, OSError, OverflowError):
        logger.debug("Could not parse date value %r", value)
        return None


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
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
        session: requests.Session | None = None,
    ):
        parts = urlsplit(api_url)
        self.api_url = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))
        self._query_parameters = parse_qs(parts.query, keep_blank_values=True)
        self.timeout = timeout
        self._resolved_skill_names: tuple[str, ...] | None = None
        self._resolved_skill_ids: tuple[int, ...] = ()
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
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            }
        )
        return session

    def resolve_skill_ids(self, skill_names: tuple[str, ...]) -> tuple[int, ...]:
        """Resolve all registered Freelancer skill names in one catalog request."""
        normalized_names = tuple(
            dict.fromkeys(name.strip() for name in skill_names if name.strip())
        )
        if normalized_names == self._resolved_skill_names:
            return self._resolved_skill_ids
        if not normalized_names:
            self._resolved_skill_names = normalized_names
            self._resolved_skill_ids = ()
            return ()

        response = self.session.get(
            urljoin(self.api_url + "/", "../../jobs/"),
            params={"job_names[]": list(normalized_names)},
            headers={"Accept": "application/json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        result = payload.get("result", payload)
        jobs = result if isinstance(result, list) else []
        ids_by_name = {
            str(job.get("name", "")).strip().casefold(): int(job["id"])
            for job in jobs
            if isinstance(job, dict) and job.get("id") is not None
        }
        resolved = tuple(
            ids_by_name[name.casefold()]
            for name in normalized_names
            if name.casefold() in ids_by_name
        )
        missing = [name for name in normalized_names if name.casefold() not in ids_by_name]
        if missing:
            logger.warning("Freelancer did not recognize registered skills: %s", ", ".join(missing))
        self._resolved_skill_names = normalized_names
        self._resolved_skill_ids = resolved
        return resolved

    def fetch_active_projects(
        self,
        limit: int = 50,
        *,
        search_terms: tuple[str, ...] = (),
        skill_ids: tuple[int, ...] = (),
        languages: tuple[str, ...] = (),
    ) -> list[Project]:
        parameters = {
            **self._query_parameters,
            "limit": limit,
            "full_description": "true",
            "job_details": "true",
            "local_details": "true",
            "location_details": "true",
            "upgrade_details": "true",
            "owner_info": "true",
            "webapp": "1",
            "compact": "true",
            "new_errors": "true",
            "new_pools": "true",
            "sort_field": "submitdate",
            "or_search_query": "true",
        }
        # Freelancer applies all repeated jobs/languages in one server-side
        # search. This prevents relevant projects from being crowded out of a
        # small, unfiltered global page before the bot sees them.
        if search_terms:
            parameters["query"] = " ".join(dict.fromkeys(search_terms))
        if skill_ids:
            parameters["jobs[]"] = list(dict.fromkeys(skill_ids))
        if languages:
            parameters["languages[]"] = list(dict.fromkeys(languages))
        response = self.session.get(
            self.api_url,
            params=parameters,
            headers={"Accept": "application/json"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        return parse_projects_response(response.json())

    def close(self) -> None:
        self.session.close()


def parse_projects_response(payload: dict[str, Any]) -> list[Project]:
    if not isinstance(payload, dict) or payload.get("status", "success") != "success":
        raise FreelancerAPIError("Freelancer returned an unsuccessful project response")
    result = payload.get("result", payload)
    if not isinstance(result, dict) or not isinstance(result.get("projects"), list):
        raise FreelancerAPIError("Freelancer response has no projects list")
    raw_projects = result["projects"]
    raw_users = result.get("users", {})
    if isinstance(raw_users, list):
        users = {str(user.get("id")): user for user in raw_users if isinstance(user, dict)}
    elif isinstance(raw_users, dict):
        users = {str(key): value for key, value in raw_users.items()}
    else:
        users = {}

    projects: list[Project] = []
    for raw in raw_projects:
        if not isinstance(raw, dict) or "id" not in raw:
            continue
        owner_id = _first(raw, "owner_id", "owner.id")
        # Anonymous active-project responses embed trust data in owner_info;
        # older/authenticated responses may instead include a users map.
        user = raw.get("owner_info")
        if not isinstance(user, dict) or not user:
            user = users.get(str(owner_id), {})
        if not isinstance(user, dict):
            user = {}
        status = user.get("status", {})
        reputation = _first(
            user,
            "employer_reputation.entire_history",
            "employer_reputation",
            "reputation.entire_history",
            default={},
        )
        if not isinstance(reputation, dict):
            reputation = {}

        rating = _number(_first(reputation, "overall", "rating"), default=-1)
        # compact=true omits zero-count review fields for unrated employers.
        raw_reviews = _first(reputation, "reviews", "review_count", "count")
        reviews = 0 if raw_reviews is None and rating == 0 else _number(raw_reviews, default=-1)
        payment = _verification(_first(status, "payment_verified"))
        deposit = _verification(_first(status, "deposit_made"))
        profile = _verification(_first(status, "profile_complete", "profile_completed"))
        member_since = _datetime(_first(user, "registration_date", "member_since", "created_at"))
        employer_data_complete = (
            payment is not None and deposit is not None and profile is not None
            and 0 <= rating <= 5 and reviews >= 0 and reviews == int(reviews)
            and member_since is not None
        )
        employer = Employer(
            country=str(
                _first(
                    user,
                    "location.country.name",
                    "location.country.code",
                    "country.name",
                    "country.code",
                    default="",
                )
            ),
            payment_verified=payment is True,
            deposit_made=deposit is True,
            profile_completed=profile is True,
            rating=max(rating, 0),
            review_count=int(max(reviews, 0)),
            member_since=member_since,
            identity_verified=_verification(_first(status, "identity_verified")),
            phone_verified=_verification(_first(status, "phone_verified")),
            email_verified=_verification(_first(status, "email_verified")),
        )

        jobs = raw.get("jobs") or []
        skills = tuple(
            str(_first(job, "name", "seo_url", default="")).strip()
            for job in jobs
            if isinstance(job, dict) and _first(job, "name", "seo_url", default="")
        )
        budget = raw.get("budget", {}) if isinstance(raw.get("budget"), dict) else {}
        currency = raw.get("currency", {}) if isinstance(raw.get("currency"), dict) else {}
        project_id = int(raw["id"])
        seo_url = str(raw.get("seo_url") or "").strip("/")
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
                budget_min=_number(budget["minimum"]) if budget.get("minimum") is not None else None,
                budget_max=_number(budget["maximum"]) if budget.get("maximum") is not None else None,
                hourly=str(raw.get("type", "")).lower() == "hourly",
                submitted_at=_datetime(_first(raw, "submitdate", "time_submitted", "time_updated")),
                employer=employer,
                employer_data_complete=employer_data_complete,
            )
        )
    return projects
