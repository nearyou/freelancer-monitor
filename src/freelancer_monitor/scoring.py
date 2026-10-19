from __future__ import annotations

import math
from datetime import datetime, timezone

from .config import ScoreConfig
from .models import Employer, ScoreBreakdown


def score_employer(
    employer: Employer,
    config: ScoreConfig,
    *,
    now: datetime | None = None,
) -> ScoreBreakdown:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    payment = config.payment_verified if employer.payment_verified else 0.0
    deposit = config.deposit_made if employer.deposit_made else 0.0
    profile = config.profile_completed if employer.profile_completed else 0.0

    review_count = max(employer.review_count, 0)
    reviews = config.reviews_max * min(
        math.log1p(review_count) / math.log1p(config.reviews_cap), 1.0
    )

    member_age_days = 0.0
    if employer.member_since is not None:
        joined = employer.member_since
        if joined.tzinfo is None:
            joined = joined.replace(tzinfo=timezone.utc)
        member_age_days = float(max((now.date() - joined.date()).days, 0))
    member_age = config.member_age_max * min(
        math.log1p(member_age_days) / math.log1p(config.member_age_max_days), 1.0
    )

    identity = config.identity_verified if employer.identity_verified else 0.0
    phone = config.phone_verified if employer.phone_verified else 0.0
    email = config.email_verified if employer.email_verified else 0.0
    rating = config.rating_max * (min(max(employer.rating, 0.0), 5.0) / 5.0)
    values = (payment, deposit, profile, reviews, member_age, identity, phone, email, rating)
    return ScoreBreakdown(
        total=round(sum(values), 2),
        payment_verified=round(payment, 2),
        deposit_made=round(deposit, 2),
        profile_completed=round(profile, 2),
        reviews=round(reviews, 2),
        member_age=round(member_age, 2),
        member_age_days=round(member_age_days, 2),
        identity_verified=round(identity, 2),
        phone_verified=round(phone, 2),
        email_verified=round(email, 2),
        rating=round(rating, 2),
    )
