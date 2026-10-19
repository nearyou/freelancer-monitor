import io
import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from types import SimpleNamespace
from unittest import TestCase, mock

from freelancer_monitor.commands import TelegramCommandProcessor
from freelancer_monitor.models import Project
from freelancer_monitor.notifications import NotificationSender
from freelancer_monitor.service import Monitor
from freelancer_monitor.state import SeenProjectStore
from freelancer_monitor.telegram import TelegramClient


class NotificationQueueTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "state.db"
        self.store = SeenProjectStore(self.path)
        self.addCleanup(self.store.close)

    def test_queue_survives_restart_and_tracks_delivery_separately(self):
        self.assertTrue(self.store.enqueue_project(1, "Alert", "https://example/1", 65))
        self.assertFalse(self.store.enqueue_project(1, "Duplicate", "https://example/1", 65))
        self.assertEqual(self.store.connection.execute("SELECT notified FROM seen_projects").fetchone(), (0,))
        reopened = SeenProjectStore(self.path)
        try:
            telegram = mock.Mock()
            sender = NotificationSender(reopened, telegram)
            self.assertTrue(sender.send_next())
            telegram.send_message.assert_called_once_with("Alert", button_url="https://example/1")
            self.assertEqual(reopened.pending_notification_count(), 0)
            self.assertEqual(reopened.connection.execute("SELECT notified FROM seen_projects").fetchone(), (1,))
            self.assertFalse(reopened.enqueue_project(1, "Duplicate", "https://example/1", 65))
        finally:
            reopened.close()

    def test_enqueue_and_seen_marker_roll_back_together(self):
        self.store.connection.execute(
            "CREATE TRIGGER fail_seen BEFORE INSERT ON seen_projects "
            "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.enqueue_project(1, "Alert", "https://example/1", 65)
        self.assertEqual(self.store.pending_notification_count(), 0)
        self.assertFalse(self.store.has_seen(1))

    def test_failure_does_not_block_other_alerts_and_retry_needs_no_feed(self):
        for i in (1, 2):
            self.store.enqueue_project(i, f"Alert {i}", f"https://example/{i}", 65)
        telegram = mock.Mock()
        telegram.send_message.side_effect = [OSError("connection failed"), None, None]
        sender = NotificationSender(self.store, telegram)
        with mock.patch("freelancer_monitor.state.time.time", return_value=100):
            self.assertTrue(sender.send_next())
            self.assertTrue(sender.send_next())
            self.assertFalse(sender.send_next())
        self.assertEqual(self.store.pending_notification_count(), 1)
        with mock.patch("freelancer_monitor.state.time.time", return_value=130):
            self.assertTrue(sender.send_next())
        self.assertEqual(self.store.pending_notification_count(), 0)
        self.assertEqual([call.args[0] for call in telegram.send_message.call_args_list], ["Alert 1", "Alert 2", "Alert 1"])

    def test_clear_history_keeps_pending_alerts_without_duplicates(self):
        self.store.enqueue_project(1, "Alert", "https://example/1", 65)
        self.store.mark_seen(2, notified=True, score=65)
        self.assertEqual(self.store.clear_history(), 1)
        self.assertTrue(self.store.has_seen(1))
        self.assertFalse(self.store.enqueue_project(1, "Duplicate", "https://example/1", 65))
        self.assertEqual(self.store.pending_notification_count(), 1)

    def test_replies_have_priority_and_acknowledgement_is_persisted(self):
        self.store.enqueue_project(1, "Project", "https://example/1", 65)
        self.store.enqueue_reply("Reply", 10)
        self.store.enqueue_reply("Duplicate reply", 10)
        self.assertEqual(self.store.pending_notification_count(), 2)
        self.assertEqual(self.store.get_telegram_update_offset(), 11)
        notification = self.store.next_notification()
        self.assertEqual(notification.message, "Reply")
        self.assertIsNone(notification.project_id)
        self.store.complete_notification(notification)
        self.assertEqual(self.store.next_notification().project_id, 1)

    def test_replies_and_alerts_share_the_same_send_rate(self):
        self.store.enqueue_project(1, "Project", "https://example/1", 65)
        self.store.enqueue_reply("Reply", 10)
        clock = [100.0]
        sent_at = []

        def sleep(seconds):
            clock[0] += seconds

        def send(*args, **kwargs):
            sent_at.append(clock[0])
            return io.BytesIO(json.dumps({"ok": True}).encode())

        client = TelegramClient("token", "chat", send_interval_seconds=3.1)
        with mock.patch("freelancer_monitor.telegram.time.monotonic", side_effect=lambda: clock[0]), \
             mock.patch("freelancer_monitor.telegram.time.sleep", side_effect=sleep), \
             mock.patch("freelancer_monitor.telegram.urlopen", side_effect=send):
            sender = NotificationSender(self.store, client)
            self.assertTrue(sender.send_next())
            self.assertTrue(sender.send_next())
        self.assertAlmostEqual(sent_at[1] - sent_at[0], 3.1)

    def test_shutdown_keeps_unsent_notification_ready(self):
        self.store.enqueue_project(1, "Alert", "https://example/1", 65)
        stop = Event()
        stop.set()
        client = TelegramClient("token", "chat", stop_event=stop)
        with mock.patch("freelancer_monitor.telegram.urlopen") as send:
            self.assertFalse(NotificationSender(self.store, client).send_next())
        send.assert_not_called()
        self.assertEqual(self.store.next_notification().attempts, 0)

    def test_slow_sender_does_not_block_polling_or_commands(self):
        self.store.initialize_preferences(skills=(), search_words=(), threshold=0)
        self.store.enqueue_project(1, "Old alert", "https://example/1", 65)
        started = Event()
        release = Event()
        errors = []

        def blocked_send(*args, **kwargs):
            started.set()
            if not release.wait(5):
                raise RuntimeError("test sender timed out")

        def send_in_thread():
            store = SeenProjectStore(self.path)
            try:
                NotificationSender(store, SimpleNamespace(send_message=blocked_send)).send_next()
            except Exception as exc:
                errors.append(exc)
            finally:
                store.close()

        thread = Thread(target=send_in_thread)
        thread.start()
        try:
            self.assertTrue(started.wait(3))
            freelancer = mock.Mock()
            freelancer.resolve_skill_ids.return_value = ()
            freelancer.fetch_active_projects.return_value = [Project(2, "New", "", "https://example/2")]
            settings = SimpleNamespace(page_limit=50, filter_match_mode="any")
            monitor = Monitor(settings, freelancer, self.store)
            self.assertEqual(monitor.run_once(), (1, 1))
            self.assertEqual(monitor.run_once(), (0, 0))
            telegram = mock.Mock()
            telegram.get_updates.return_value = [{"update_id": 10, "message": {"chat": {"id": 123}, "text": "/add_skill Python"}}]
            commands = TelegramCommandProcessor(telegram, self.store, authorized_chat_id="123")
            self.assertEqual(commands.poll_once(), 1)
            self.assertEqual(self.store.list_terms("skill"), ("Python",))
            telegram.send_message.assert_not_called()
            self.assertEqual(self.store.pending_notification_count(), 3)
        finally:
            release.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
