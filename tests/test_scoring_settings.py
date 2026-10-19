from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, mock

from freelancer_monitor.commands import TelegramCommandProcessor
from freelancer_monitor.config import ScoreConfig
from freelancer_monitor.models import Employer, Project
from freelancer_monitor.scoring import score_employer
from freelancer_monitor.service import Monitor
from freelancer_monitor.state import SeenProjectStore


class ScoringSettingsTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.db"
        self.store = SeenProjectStore(self.path)
        self.addCleanup(self.store.close)
        self.store.initialize_preferences(skills=(), search_words=())
        self.telegram = mock.Mock()
        self.commands = TelegramCommandProcessor(
            self.telegram, self.store, authorized_chat_id="123"
        )

    def test_every_setting_is_editable_and_survives_reinitialization(self):
        self.commands.handle_command("/set_threshold 0")
        for name, default in asdict(ScoreConfig()).items():
            value = default + 1
            with self.subTest(setting=name):
                response = self.commands.handle_command(f"/set_score {name} {value}")
                self.assertIn(f"Set <code>{name}</code>", response)
                self.assertEqual(getattr(self.store.get_scoring_settings()[0], name), value)
                self.assertIn(f"<code>{name}</code>: {value}", self.commands.handle_command("/show_score"))
        saved = self.store.get_scoring_settings()
        other = SeenProjectStore(self.path)
        self.addCleanup(other.close)
        self.assertFalse(other.initialize_preferences(skills=(), search_words=()))
        self.assertEqual(other.get_scoring_settings(), saved)

    def test_invalid_commands_leave_settings_unchanged(self):
        original = self.store.get_scoring_settings()
        commands = [
            "/set_score", "/set_score payment_verified", "/set_score unknown 10",
            "/set_score payment_verified -1", "/set_score payment_verified nan",
            "/set_score payment_verified inf", "/set_score payment_verified abc",
            "/set_score reviews_cap 0", "/set_score reviews_cap 1.5",
            "/set_score member_age_max_days -2", "/set_score member_age_max_days 2.5",
            "/set_threshold nan", "/set_threshold inf", "/set_threshold -1",
            "/set_threshold 66", "/set_threshold abc",
        ]
        for command in commands:
            with self.subTest(command=command):
                response = self.commands.handle_command(command)
                self.assertTrue(response)
                self.assertEqual(self.store.get_scoring_settings(), original)

    def test_threshold_tracks_maximum_and_weight_reduction_is_safe(self):
        self.assertIn("below threshold", self.commands.handle_command("/set_score payment_verified 0"))
        self.assertEqual(self.store.get_scoring_settings(), (ScoreConfig(), 65))
        self.commands.handle_command("/set_threshold 45")
        self.commands.handle_command("/set_score payment_verified 0")
        self.assertIn("between 0 and 45", self.commands.handle_command("/set_threshold 46"))
        self.commands.handle_command("/set_score identity_verified 30")
        self.commands.handle_command("/set_threshold 75")
        self.assertEqual(self.store.get_threshold(), 75)
        self.assertIn("75 maximum", self.commands.handle_command("/show_threshold"))

    def test_existing_database_gains_defaults_without_resetting_threshold(self):
        # Simulate the previous schema: a threshold exists but weights lived in env.
        with self.store.connection:
            self.store.connection.execute("DELETE FROM runtime_settings WHERE key = 'scoring_config'")
        self.store.set_setting("score_threshold", "80")
        self.assertFalse(self.store.initialize_preferences(skills=(), search_words=()))
        self.assertEqual(self.store.get_scoring_settings(), (ScoreConfig(), 80))
        self.assertIn("Threshold exceeds maximum", self.commands.handle_command("/show_score"))
        self.commands.handle_command("/set_threshold 60")
        self.assertEqual(self.store.get_threshold(), 60)

    def test_other_chats_cannot_change_scoring(self):
        self.telegram.get_updates.return_value = [
            {"update_id": 1, "message": {"chat": {"id": 999}, "text": "/set_score identity_verified 30"}},
            {"update_id": 2, "message": {"chat": {"id": 123}, "text": "/set_score identity_verified 10"}},
        ]
        self.assertEqual(self.commands.poll_once(timeout_seconds=0), 1)
        self.assertEqual(self.store.get_scoring_settings()[0].identity_verified, 10)
        self.assertEqual(self.store.pending_notification_count(), 1)
        self.telegram.send_message.assert_not_called()

    def test_poll_uses_one_snapshot_and_observes_changes_on_next_poll(self):
        self.store.set_threshold(20)
        freelancer = mock.Mock()
        freelancer.resolve_skill_ids.return_value = ()
        project = Project(id=1, title="Test", description="", url="https://example.test/1",
                          employer=Employer(payment_verified=True))
        freelancer.fetch_active_projects.return_value = [project, replace(project, id=2)]
        monitor = Monitor(SimpleNamespace(page_limit=20, filter_match_mode="any"), freelancer, self.store)
        other = SeenProjectStore(self.path)
        self.addCleanup(other.close)

        def change_during_poll(employer, config):
            other.set_score_setting("payment_verified", "0")
            return score_employer(employer, config)

        with mock.patch("freelancer_monitor.service.score_employer", side_effect=change_during_poll):
            self.assertEqual(monitor.run_once(), (2, 2))
        freelancer.fetch_active_projects.return_value = [replace(project, id=3)]
        self.assertEqual(monitor.run_once(), (1, 0))
        self.assertEqual(self.store.pending_notification_count(), 2)
        self.assertEqual(self.store.next_notification().score, 20)

    def test_missing_optional_verification_is_deferred_only_when_enabled(self):
        self.store.set_threshold(0)
        freelancer = mock.Mock()
        freelancer.resolve_skill_ids.return_value = ()
        project = Project(id=1, title="Test", description="", url="https://example.test/1")
        freelancer.fetch_active_projects.return_value = [project]
        monitor = Monitor(SimpleNamespace(page_limit=20, filter_match_mode="any"), freelancer, self.store)
        self.assertEqual(monitor.run_once(), (1, 1))
        for index, name in enumerate(("identity_verified", "phone_verified", "email_verified"), 2):
            with self.subTest(setting=name):
                self.store.set_score_setting(name, "10")
                freelancer.fetch_active_projects.return_value = [replace(project, id=index)]
                with self.assertLogs("freelancer_monitor.service", level="WARNING"):
                    self.assertEqual(monitor.run_once(), (1, 0))
                self.assertFalse(self.store.has_seen(index))
                self.assertEqual(self.store.deferred_project_ids([index]), {index})
                self.store.set_score_setting(name, "0")


class ConfigurableScoreTests(TestCase):
    def test_optional_weights_and_custom_caps_change_score(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        config = ScoreConfig(identity_verified=8, phone_verified=4, email_verified=2,
                             rating_max=10, reviews_cap=10, member_age_max_days=30)
        employer = Employer(payment_verified=True, deposit_made=True, profile_completed=True,
                            review_count=10, member_since=now - timedelta(days=30),
                            identity_verified=True, phone_verified=True, email_verified=True, rating=2.5)
        score = score_employer(employer, config, now=now)
        self.assertEqual(score.total, 84)
        self.assertEqual((score.identity_verified, score.phone_verified, score.email_verified, score.rating),
                         (8, 4, 2, 5))
        self.assertEqual(config.maximum, 89)
        self.assertEqual(score_employer(replace(employer, rating=8), config, now=now).total, 89)
        self.assertEqual(score_employer(replace(employer, rating=-1), config, now=now).rating, 0)

    def test_nonfinite_combined_weights_are_rejected(self):
        config = ScoreConfig().updated("payment_verified", "1e308")
        with self.assertRaises(ValueError):
            config.updated("deposit_made", "1e308")
