import io
import json
from datetime import datetime, timezone
from unittest import TestCase, mock
from urllib.error import HTTPError
from urllib.parse import parse_qs

from freelancer_monitor.models import Employer, Project
from freelancer_monitor.telegram import TelegramClient, format_project_message


class TelegramMessageTests(TestCase):
    def test_message_contains_only_requested_project_fields(self):
        project = Project(
            id=42,
            title="Build <Python> bot",
            description="SECRET OVERVIEW MUST NOT APPEAR",
            url="https://www.freelancer.com/projects/42",
            skills=("Python", "API"),
            listing_types=("Featured", "NDA", "Urgent"),
            budget_min=100,
            budget_max=500,
            submitted_at=datetime(2026, 8, 1, 3, 45, tzinfo=timezone.utc),
            employer=Employer(
                country="United States",
                payment_verified=True,
                deposit_made=False,
                profile_completed=True,
                rating=4.8,
                review_count=12,
                member_since=datetime(2020, 5, 3, tzinfo=timezone.utc),
                identity_verified=True,
                phone_verified=False,
                email_verified=True,
            ),
        )

        message = format_project_message(project)

        self.assertIn(
            "<blockquote><b>Build &lt;Python&gt; bot</b></blockquote>", message
        )
        self.assertIn("United States", message)
        self.assertIn("⭐ 5", message)
        self.assertNotIn("4.8", message)
        self.assertIn("12", message)
        self.assertIn("May 3, 2020", message)
        self.assertIn("<b>Payment Type:</b> Fixed price", message)
        self.assertIn("<b>Listing type:</b> Featured • NDA • Urgent", message)
        self.assertIn("<b>Budget:</b> 100–500", message)
        self.assertIn("✅ Payment verified", message)
        self.assertIn("⛔ Deposit made", message)
        self.assertIn("✅ Profile Completed", message)
        self.assertIn("✅ Identity verified", message)
        self.assertIn("⛔ Phone verified", message)
        self.assertIn("✅ Email verified", message)
        self.assertLess(message.index("Deposit made"), message.index("Profile Completed"))
        self.assertNotIn("https://www.freelancer.com/projects/42", message)
        self.assertNotIn("Open project", message)
        self.assertIn("<b>Posted:</b> Aug 1, 2026 12:45 JST", message)
        self.assertNotIn("SECRET OVERVIEW", message)
        self.assertNotIn("Python, API", message)
        self.assertNotIn("Trust score", message)

    def test_hourly_project_shows_hourly_type_and_rate(self):
        project = Project(
            id=43,
            title="Hourly work",
            description="",
            url="https://www.freelancer.com/projects/43",
            hourly=True,
            currency_code="USD",
            budget_min=15,
            budget_max=25,
        )

        message = format_project_message(project)

        self.assertIn("<b>Payment Type:</b> Hourly rate", message)
        self.assertIn("<b>Listing type:</b> Standard", message)
        self.assertIn("<b>Budget:</b> 15–25 USD / hour", message)
        self.assertIn("❔ Identity verified", message)
        self.assertIn("❔ Phone verified", message)
        self.assertIn("❔ Email verified", message)

    def test_project_url_is_sent_as_inline_button(self):
        success = io.BytesIO(json.dumps({"ok": True}).encode("utf-8"))
        client = TelegramClient("token", "chat", send_interval_seconds=3.1)

        with mock.patch(
            "freelancer_monitor.telegram.urlopen", return_value=success
        ) as mocked_open:
            client.send_message(
                "message", button_url="https://www.freelancer.com/projects/42"
            )

        request = mocked_open.call_args.args[0]
        parameters = parse_qs(request.data.decode("utf-8"))
        markup = json.loads(parameters["reply_markup"][0])
        button = markup["inline_keyboard"][0][0]
        self.assertEqual(button["text"], "View on Freelancer")
        self.assertEqual(button["url"], "https://www.freelancer.com/projects/42")

    def test_rate_limit_response_waits_and_retries(self):
        error_body = io.BytesIO(
            json.dumps(
                {"ok": False, "parameters": {"retry_after": 4}}
            ).encode("utf-8")
        )
        rate_limit_error = HTTPError(
            "https://api.telegram.org/test",
            429,
            "Too Many Requests",
            {},
            error_body,
        )
        success = io.BytesIO(json.dumps({"ok": True}).encode("utf-8"))
        client = TelegramClient("token", "chat", send_interval_seconds=3.1)

        with mock.patch(
            "freelancer_monitor.telegram.urlopen",
            side_effect=[rate_limit_error, success],
        ) as mocked_open, mock.patch("freelancer_monitor.telegram.time.sleep") as mocked_sleep:
            client.send_message("message")

        self.assertEqual(mocked_open.call_count, 2)
        self.assertTrue(any(call.args[0] >= 4 for call in mocked_sleep.call_args_list))
