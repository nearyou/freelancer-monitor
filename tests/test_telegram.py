import io
import json
from datetime import datetime, timezone
from unittest import TestCase, mock
from urllib.error import HTTPError
from urllib.parse import parse_qs

from freelancer_monitor.models import Employer, Project
from freelancer_monitor.commands import BOT_COMMANDS
from freelancer_monitor.telegram import TelegramClient, format_project_message


class TelegramMessageTests(TestCase):
    def test_registers_all_commands_with_descriptions_in_configured_chat(self):
        client = TelegramClient("token", "-123")
        with mock.patch("freelancer_monitor.telegram.urlopen", return_value=io.BytesIO(b'{"ok": true, "result": true}')) as request:
            client.set_commands(BOT_COMMANDS)
        sent = request.call_args.args[0]
        self.assertTrue(sent.full_url.endswith("/setMyCommands"))
        parameters = parse_qs(sent.data.decode("utf-8"))
        self.assertEqual(json.loads(parameters["scope"][0]), {"type": "chat", "chat_id": -123})
        commands = json.loads(parameters["commands"][0])
        self.assertEqual({item["command"] for item in commands}, {
            "show_skills", "add_skill", "delete_skill", "show_search_words", "add_search_word",
            "delete_search_word", "show_score", "show_threshold", "set_threshold",
            "set_payment_verified", "set_deposit_made", "set_profile_completed",
            "set_identity_verified", "set_phone_verified", "set_email_verified", "set_rating_max",
            "set_reviews_max", "set_reviews_cap", "set_member_age_max", "set_member_age_max_days",
            "clear_history", "help", "start",
        })
        for command in commands:
            self.assertRegex(command["command"], r"^[a-z_]{1,32}$")
            self.assertTrue(1 <= len(command["description"]) <= 256)

    def test_rejected_menu_registration_raises(self):
        client = TelegramClient("token", "123")
        with mock.patch("freelancer_monitor.telegram.urlopen", return_value=io.BytesIO(b'{"ok": false}')):
            with self.assertRaisesRegex(RuntimeError, "command menu"):
                client.set_commands(BOT_COMMANDS)

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
        self.assertIn("✅ Profile completed", message)
        self.assertIn("✅ Identity verified", message)
        self.assertIn("⛔ Phone verified", message)
        self.assertIn("✅ Email verified", message)
        self.assertIn(
            "<code>✅ Identity verified  ✅ Email verified</code>\n"
            "<code>✅ Payment verified   ✅ Profile completed</code>\n"
            "<code>⛔ Deposit made       ⛔ Phone verified</code>",
            message,
        )
        self.assertNotIn("<pre>", message)
        self.assertEqual(message.count("<code>"), 3)
        self.assertEqual(message.count("</code>"), 3)
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
        ) as mocked_open, mock.patch("freelancer_monitor.telegram.time.sleep") as mocked_sleep, \
             mock.patch("freelancer_monitor.telegram.time.monotonic", return_value=100):
            client.send_message("message")

        self.assertEqual(mocked_open.call_count, 2)
        self.assertTrue(any(call.args[0] >= 4 for call in mocked_sleep.call_args_list))

    def test_exhausted_429_cooldown_applies_to_the_next_message(self):
        clock = [100.0]
        attempts = []
        error = HTTPError("https://example.test", 429, "Too Many Requests", {},
                          io.BytesIO(json.dumps({"parameters": {"retry_after": 120}}).encode()))

        def request(*args, **kwargs):
            attempts.append(clock[0])
            if len(attempts) == 1:
                raise error
            return io.BytesIO(b'{"ok": true}')

        def sleep(seconds):
            clock[0] += seconds

        client = TelegramClient("token", "chat", max_rate_limit_retries=0)
        with mock.patch("freelancer_monitor.telegram.urlopen", side_effect=request), \
             mock.patch("freelancer_monitor.telegram.time.monotonic", side_effect=lambda: clock[0]), \
             mock.patch("freelancer_monitor.telegram.time.sleep", side_effect=sleep):
            with self.assertRaises(HTTPError):
                client.send_message("First")
            client.send_message("Second")
        self.assertGreaterEqual(attempts[1] - attempts[0], 120)

    def test_shutdown_interrupts_rate_limit_wait(self):
        stop = mock.Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = True
        client = TelegramClient("token", "chat", stop_event=stop)
        client._last_send_attempt_at = 100
        with mock.patch("freelancer_monitor.telegram.time.monotonic", return_value=100), \
             mock.patch("freelancer_monitor.telegram.urlopen") as request:
            with self.assertRaises(InterruptedError):
                client.send_message("Message")
        request.assert_not_called()
        self.assertAlmostEqual(stop.wait.call_args.args[0], 3.1)
