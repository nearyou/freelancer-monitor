from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Employer:
    country: str = ""
    identity_verified: bool = False
    payment_verified: bool = False
    deposit_made: bool = False
    rating: float = 0.0
    review_count: int = 0
    member_since: datetime | None = None


@dataclass(frozen=True)
class Project:
    id: int
    title: str
    description: str
    url: str
    owner_id: int | None = None
    skills: tuple[str, ...] = ()
    listing_types: tuple[str, ...] = ()
    currency_code: str = ""
    budget_min: float | None = None
    budget_max: float | None = None
    hourly: bool = False
    submitted_at: datetime | None = None
    employer: Employer = field(default_factory=Employer)


@dataclass(frozen=True)
class ScoreBreakdown:
    total: float
    identity_verified: float
    payment_verified: float
    deposit_made: float
    reviews: float
    member_age: float
    member_age_days: float
