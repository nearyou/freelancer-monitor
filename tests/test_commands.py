from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, mock

from freelancer_monitor.commands import TelegramCommandProcessor
from freelancer_monitor.state import SeenProjectStore


class FakeTelegram:
    def __init__(self, updates):
        self.updates = updates
        self.messages = []

    def get_updates(self, *, offset=None, timeout_seconds=2):
        return self.updates

    def set_commands(self, commands):
        self.commands = commands

    def send_message(self, text, **kwargs):
        self.messages.append(text)


class TelegramCommandTests(TestCase):
    def test_menu_failure_retries_without_blocking_commands_and_stops_after_success(self):
        telegram = mock.Mock()
        telegram.get_updates.return_value = []
        telegram.set_commands.side_effect = [OSError("offline"), None]
        with TemporaryDirectory() as directory:
            store = SeenProjectStore(Path(directory) / "state.db")
            try:
                store.initialize_preferences(skills=(), search_words=())
                processor = TelegramCommandProcessor(telegram, store, authorized_chat_id="123")
                with mock.patch("freelancer_monitor.commands.time.monotonic", return_value=100):
                    with self.assertLogs("freelancer_monitor.commands", level="WARNING"):
                        processor.poll_once()
                with mock.patch("freelancer_monitor.commands.time.monotonic", return_value=159):
                    processor.poll_once()
                self.assertEqual(telegram.set_commands.call_count, 1)
                with mock.patch("freelancer_monitor.commands.time.monotonic", return_value=160):
                    processor.poll_once()
                    processor.poll_once()
                self.assertEqual(telegram.set_commands.call_count, 2)
                self.assertEqual(telegram.get_updates.call_count, 4)
            finally:
                store.close()

    def test_commands_manage_database_preferences_and_ignore_other_chats(self):
        updates = [
            {"update_id": 10, "message": {"chat": {"id": 123}, "text": "/add_skill Go"}},
            {
                "update_id": 11,
                "message": {"chat": {"id": 123}, "text": "/add_search_word API integration"},
            },
            {
                "update_id": 12,
                "message": {"chat": {"id": 123}, "text": "/set_threshold 52.5"},
            },
            {
                "update_id": 13,
                "message": {"chat": {"id": 999}, "text": "/delete_skill Go"},
            },
        ]
        telegram = FakeTelegram(updates)
        with TemporaryDirectory() as directory:
            store = SeenProjectStore(Path(directory) / "state.db")
            try:
                store.initialize_preferences(skills=(), search_words=(), threshold=65)
                processor = TelegramCommandProcessor(
                    telegram,
                    store,
                    authorized_chat_id="123",
                )
                self.assertEqual(processor.poll_once(timeout_seconds=0), 3)
                self.assertEqual(store.list_terms("skill"), ("Go",))
                self.assertEqual(store.list_terms("search_word"), ("API integration",))
                self.assertEqual(store.get_threshold(), 52.5)
                self.assertEqual(store.get_telegram_update_offset(), 14)
                self.assertEqual(telegram.messages, [])
                self.assertEqual(store.pending_notification_count(), 3)
                self.assertIn("Added", store.next_notification().message)
            finally:
                store.close()

    def test_show_delete_validation_and_help_commands(self):
        telegram = FakeTelegram([])
        with TemporaryDirectory() as directory:
            store = SeenProjectStore(Path(directory) / "state.db")
            try:
                store.initialize_preferences(
                    skills=("Python",), search_words=("bot",), threshold=60
                )
                processor = TelegramCommandProcessor(
                    telegram, store, authorized_chat_id="123"
                )
                self.assertIn("Python", processor.handle_command("/show_skills"))
                self.assertIn("bot", processor.handle_command("/show_search_words"))
                self.assertIn("Deleted", processor.handle_command("/delete_skill python"))
                self.assertIn("between", processor.handle_command("/set_threshold 101"))
                store.mark_seen(42, notified=True, score=80)
                self.assertIn("1", processor.handle_command("/clear_history"))
                self.assertFalse(store.has_seen(42))
                self.assertEqual(store.get_threshold(), 60)
                self.assertEqual(store.list_terms("search_word"), ("bot",))
                self.assertIn("Filter commands", processor.handle_command("/help"))
            finally:
                store.close()
