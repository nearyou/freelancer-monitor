import os
from unittest import TestCase, mock

from freelancer_monitor.config import Settings


class SettingsTests(TestCase):
    def test_config_does_not_require_freelancer_token(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "telegram-token",
            "TELEGRAM_CHAT_ID": "123",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.telegram_chat_id, "123")
        self.assertEqual(settings.poll_interval_seconds, 15)
        self.assertEqual(settings.telegram_send_interval_seconds, 3.1)

    def test_legacy_score_environment_values_are_ignored(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "telegram-token",
            "TELEGRAM_CHAT_ID": "123",
            "SCORE_THRESHOLD": "101",
            "SCORE_PAYMENT_VERIFIED": "invalid",
            "SCORE_REVIEWS_CAP": "0",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()
        self.assertFalse(hasattr(settings, "score"))
        self.assertFalse(hasattr(settings, "score_threshold"))

    def test_telegram_interval_cannot_exceed_group_rate(self):
        env = {
            "TELEGRAM_BOT_TOKEN": "telegram-token",
            "TELEGRAM_CHAT_ID": "123",
            "TELEGRAM_SEND_INTERVAL_SECONDS": "2.9",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "TELEGRAM_SEND_INTERVAL_SECONDS"):
                Settings.from_env()
