from datetime import datetime, timedelta, timezone
from unittest import TestCase

from freelancer_monitor.config import ScoreConfig
from freelancer_monitor.models import Employer
from freelancer_monitor.scoring import score_employer


CONFIG = ScoreConfig(20, 20, 15, 15, 10, 50, 100)


class ScoreEmployerTests(TestCase):
    def test_fully_trusted_old_employer_scores_80(self):
        employer = Employer(
            identity_verified=True,
            payment_verified=True,
            deposit_made=True,
            rating=5,
            review_count=50,
            member_since=datetime(2015, 1, 1, tzinfo=timezone.utc),
        )
        score = score_employer(
            employer, CONFIG, now=datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(score.total, 80)

    def test_rating_does_not_change_score(self):
        low_rating = Employer(rating=0, review_count=3)
        high_rating = Employer(rating=5, review_count=3)
        self.assertEqual(
            score_employer(low_rating, CONFIG).total,
            score_employer(high_rating, CONFIG).total,
        )

    def test_member_age_is_calculated_by_day_and_capped_at_100_days(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        fifty_days_old = Employer(member_since=now - timedelta(days=50))
        very_old = Employer(member_since=now - timedelta(days=500))
        fifty_day_score = score_employer(fifty_days_old, CONFIG, now=now)
        self.assertEqual(fifty_day_score.member_age_days, 50)
        self.assertAlmostEqual(fifty_day_score.member_age, 8.52, places=2)
        self.assertEqual(score_employer(very_old, CONFIG, now=now).member_age, 10)

    def test_future_join_date_never_subtracts_points(self):
        employer = Employer(member_since=datetime(2030, 1, 1, tzinfo=timezone.utc))
        score = score_employer(
            employer, CONFIG, now=datetime(2026, 1, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(score.member_age, 0)
        self.assertEqual(score.member_age_days, 0)
