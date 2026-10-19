from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest import TestCase, mock

from freelancer_monitor.__main__ import main
from freelancer_monitor.models import Project
from freelancer_monitor.state import SeenProjectStore


class MainTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = SimpleNamespace(
            state_db_path=Path(self.directory.name) / "state.db",
            log_level="ERROR", filter_match_mode="any",
            telegram_bot_token="test-token", telegram_chat_id="123",
            telegram_send_interval_seconds=3.1,
            freelancer_api_url="https://example.test/api", page_limit=20,
            poll_interval_seconds=15,
        )
        store = SeenProjectStore(self.settings.state_db_path)
        try:
            store.initialize_preferences(skills=(), search_words=(), threshold=0)
        finally:
            store.close()

    def test_once_delivers_pending_notifications_from_previous_run(self):
        store = SeenProjectStore(self.settings.state_db_path)
        store.enqueue_project(1, "Saved alert", "https://example/1", 65)
        store.close()
        with mock.patch("sys.argv", ["monitor", "--once"]), \
             mock.patch("freelancer_monitor.__main__.Settings.from_env", return_value=self.settings), \
             mock.patch("freelancer_monitor.__main__.FreelancerClient") as freelancer, \
             mock.patch("freelancer_monitor.__main__.TelegramClient") as telegram:
            freelancer.return_value.resolve_skill_ids.return_value = ()
            freelancer.return_value.fetch_active_projects.return_value = []
            self.assertEqual(main(), 0)
            telegram.return_value.send_message.assert_called_once_with("Saved alert", button_url="https://example/1")
            freelancer.return_value.close.assert_called_once()
        store = SeenProjectStore(self.settings.state_db_path)
        try:
            self.assertEqual(store.pending_notification_count(), 0)
        finally:
            store.close()

    def test_once_failed_delivery_remains_queued_and_returns_failure(self):
        with mock.patch("sys.argv", ["monitor", "--once"]), \
             mock.patch("freelancer_monitor.__main__.Settings.from_env", return_value=self.settings), \
             mock.patch("freelancer_monitor.__main__.FreelancerClient") as freelancer, \
             mock.patch("freelancer_monitor.__main__.TelegramClient") as telegram:
            freelancer.return_value.resolve_skill_ids.return_value = ()
            freelancer.return_value.fetch_active_projects.return_value = [Project(1, "Project", "", "https://example/1")]
            telegram.return_value.send_message.side_effect = OSError("offline")
            self.assertEqual(main(), 1)
        store = SeenProjectStore(self.settings.state_db_path)
        try:
            self.assertEqual(store.pending_notification_count(), 1)
        finally:
            store.close()

    def test_continuous_mode_wires_workers_and_stops_on_interrupt(self):
        sent = Event()
        closed = Event()
        client = mock.Mock()
        client.resolve_skill_ids.return_value = ()
        client.fetch_active_projects.return_value = [Project(1, "Project", "", "https://example/1")]
        client.close.side_effect = closed.set

        def command_poll(**kwargs):
            sent.wait(3)
            raise KeyboardInterrupt

        with mock.patch("sys.argv", ["monitor"]), \
             mock.patch("freelancer_monitor.__main__.Settings.from_env", return_value=self.settings), \
             mock.patch("freelancer_monitor.__main__.TelegramClient") as commands_client, \
             mock.patch("freelancer_monitor.runtime.FreelancerClient", return_value=client), \
             mock.patch("freelancer_monitor.runtime.TelegramClient") as sender_client:
            commands_client.return_value.get_updates.side_effect = command_poll
            sender_client.return_value.send_message.side_effect = lambda *args, **kwargs: sent.set()
            self.assertEqual(main(), 0)
            self.assertTrue(sent.is_set(), "worker never delivered its queued alert")
            self.assertTrue(closed.is_set())
            self.assertEqual(sender_client.call_args.kwargs["send_interval_seconds"], 3.1)
            commands_client.return_value.send_message.assert_not_called()
            sender_client.return_value.send_message.assert_called_once()
