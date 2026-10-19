from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Employer:
    country: str = ""
    payment_verified: bool = False
    deposit_made: bool = False
    profile_completed: bool = False
    rating: float = 0.0
    review_count: int = 0
    member_since: datetime | None = None
    identity_verified: bool | None = None
    phone_verified: bool | None = None
    email_verified: bool | None = None


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
    employer_data_complete: bool = True


@dataclass(frozen=True)
class ScoreBreakdown:
    total: float
    payment_verified: float
    deposit_made: float
    profile_completed: float
    reviews: float
    member_age: float
    member_age_days: float
    identity_verified: float = 0
    phone_verified: float = 0
    email_verified: float = 0
    rating: float = 0
