from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, mock

from freelancer_monitor.config import ScoreConfig
from freelancer_monitor.models import Project
from freelancer_monitor.notifications import NotificationSender
from freelancer_monitor.service import Monitor
from freelancer_monitor.state import SeenProjectStore


class BatchHistoryTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.db"
        self.store = SeenProjectStore(self.path)
        self.addCleanup(self.store.close)
        self.store.initialize_preferences(skills=(), search_words=(), threshold=0)
        self.freelancer = mock.Mock()
        self.freelancer.resolve_skill_ids.return_value = ()
        settings = SimpleNamespace(
            page_limit=2, filter_match_mode="any", score=ScoreConfig(20, 15, 5, 15, 10, 50, 100)
        )
        self.monitor = Monitor(settings, self.freelancer, self.store)
        self.telegram = mock.Mock()
        self.sender = NotificationSender(self.store, self.telegram)

    def poll(self, *ids):
        self.freelancer.fetch_active_projects.return_value = [
            Project(i, f"Project {i}", "", f"https://example/{i}") for i in ids
        ]
        return self.monitor.run_once()

    def delivered(self):
        while self.sender.send_next():
            pass

    def history(self):
        return {row[0] for row in self.store.connection.execute("SELECT project_id FROM seen_projects")}

    def test_overlapping_batches_only_process_new_ids_and_forget_old_ones(self):
        self.assertEqual(self.poll(1, 2), (2, 2))
        self.delivered()
        self.assertEqual(self.poll(2, 3), (1, 1))
        self.assertEqual(self.history(), {2, 3})
        self.delivered()
        self.assertEqual(self.poll(2, 3), (0, 0))
        self.assertEqual(self.telegram.send_message.call_count, 3)
        # A project that left the previous batch is intentionally eligible again.
        self.assertEqual(self.poll(1, 3), (1, 1))
        self.assertEqual(self.history(), {1, 3})

    def test_pending_alert_survives_leaving_batch_and_retry_after_restart(self):
        self.assertEqual(self.poll(1), (1, 1))
        self.telegram.send_message.side_effect = OSError("offline")
        with mock.patch("freelancer_monitor.state.time.time", return_value=100):
            self.assertTrue(self.sender.send_next())
        self.assertEqual(self.poll(2), (1, 1))
        self.assertEqual(self.history(), {1, 2})
        self.assertEqual(self.poll(1, 2), (0, 0))
        self.poll(2)
        reopened = SeenProjectStore(self.path)
        try:
            sender = NotificationSender(reopened, mock.Mock())
            with mock.patch("freelancer_monitor.state.time.time", return_value=130):
                self.assertTrue(sender.send_next())
                self.assertTrue(sender.send_next())
            self.assertEqual(reopened.pending_notification_count(), 0)
        finally:
            reopened.close()
        self.assertEqual(self.poll(2), (0, 0))
        self.assertEqual(self.history(), {2})

    def test_api_failure_keeps_previous_batch_but_valid_empty_batch_clears_it(self):
        self.poll(1, 2)
        self.delivered()
        self.freelancer.fetch_active_projects.side_effect = OSError("API unavailable")
        with self.assertRaises(OSError):
            self.monitor.run_once()
        self.assertEqual(self.history(), {1, 2})
        self.freelancer.fetch_active_projects.side_effect = None
        self.assertEqual(self.poll(), (0, 0))
        self.assertEqual(self.history(), set())

    def test_partial_processing_failure_does_not_discard_previous_batch(self):
        self.poll(1, 2)
        self.delivered()
        with mock.patch.object(self.store, "enqueue_project", side_effect=RuntimeError("disk error")):
            with self.assertRaises(RuntimeError):
                self.poll(3)
        self.assertEqual(self.history(), {1, 2})

    def test_existing_history_and_off_feed_failures_are_trimmed_without_changing_preferences(self):
        self.store.mark_seen_many([(i, False, 0) for i in range(1200)])
        self.store.record_project_failure(1500, "Missing owner")
        self.store.record_project_failure(1501, "Missing owner")
        self.store.enqueue_reply("Saved reply", 20)
        self.store.retain_project_batch([1199, 1501])
        self.assertEqual(self.history(), {1199})
        failures = self.store.connection.execute("SELECT project_id FROM project_failures").fetchall()
        self.assertEqual(failures, [(1501,)])
        self.assertEqual(self.store.get_threshold(), 0)
        self.assertEqual(self.store.get_telegram_update_offset(), 21)
        self.assertEqual(self.store.pending_notification_count(), 1)

    def test_latest_batch_deduplication_survives_restart(self):
        self.poll(1, 2)
        self.delivered()
        self.poll(2, 3)
        self.delivered()
        reopened = SeenProjectStore(self.path)
        try:
            self.assertEqual(reopened.seen_project_ids([1, 2, 3]), {2, 3})
        finally:
            reopened.close()

    def test_history_stays_bounded_over_many_polls(self):
        self.store.set_threshold(65)  # These projects are processed without alerts.
        for i in range(30):
            self.poll(i, i + 1)
            self.assertEqual(self.history(), {i, i + 1})
