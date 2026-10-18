import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase, mock

from freelancer_monitor.config import ScoreConfig
from freelancer_monitor.freelancer import FreelancerClient
from freelancer_monitor.models import Employer, Project
from freelancer_monitor.service import Monitor, is_project_stale
from freelancer_monitor.state import SeenProjectStore


class _Freelancer:
    def __init__(self, projects):
        self.projects = projects

    def fetch_active_projects(self, _limit, **_filters):
        return self.projects

    def resolve_skill_ids(self, _skills):
        return ()


class _Telegram:
    def __init__(self):
        self.messages = []

    def send_message(self, message, *, button_url):
        self.messages.append((message, button_url))


class MonitorResilienceTests(TestCase):
    def test_api_to_notification_uses_one_request_and_deduplicates(self):
        payload = json.loads(
            (Path(__file__).parent / "fixtures" / "active_projects.json").read_text(encoding="utf-8")
        )
        for project in payload["result"]["projects"]:
            project["submitdate"] = datetime.now(timezone.utc).timestamp()
        session = mock.Mock()
        session.get.return_value.json.return_value = payload
        freelancer = FreelancerClient("https://example.test/api", session=session)
        telegram = _Telegram()
        settings = SimpleNamespace(
            page_limit=50, filter_match_mode="any",
            score=ScoreConfig(20, 15, 5, 15, 10, 50, 100),
        )
        with TemporaryDirectory() as directory:
            store = SeenProjectStore(Path(directory) / "state.db")
            try:
                store.initialize_preferences(skills=(), search_words=(), threshold=0)
                monitor = Monitor(settings, freelancer, telegram, store)
                self.assertEqual(monitor.run_once(), (2, 2))
                self.assertEqual(session.get.call_count, 1)
                self.assertIn("Phone verified", telegram.messages[0][0])
                self.assertEqual(monitor.run_once(), (0, 0))
                self.assertEqual(len(telegram.messages), 2)
                self.assertEqual(session.get.call_count, 2)
            finally:
                freelancer.close()
                store.close()

    def test_notification_failure_defers_only_that_project_and_can_retry(self):
        projects = [Project(i, "Example", "", f"https://example/{i}") for i in (1, 2)]
        freelancer = _Freelancer(projects)
        telegram = mock.Mock()
        telegram.send_message.side_effect = [OSError("connection failed"), None, None]
        settings = SimpleNamespace(
            page_limit=50, filter_match_mode="any",
            score=ScoreConfig(20, 15, 5, 15, 10, 50, 100),
        )
        with TemporaryDirectory() as directory:
            store = SeenProjectStore(Path(directory) / "state.db")
            try:
                store.initialize_preferences(skills=(), search_words=(), threshold=0)
                monitor = Monitor(settings, freelancer, telegram, store)
                self.assertEqual(monitor.run_once(), (2, 1))
                self.assertFalse(store.has_seen(1))
                self.assertTrue(store.has_seen(2))
                self.assertEqual(store.deferred_project_ids([1]), {1})
                self.assertEqual(monitor.run_once(), (0, 0))
                store.clear_project_failure(1)
                self.assertEqual(monitor.run_once(), (1, 1))
                self.assertTrue(store.has_seen(1))
            finally:
                store.close()

    def test_projects_posted_one_hour_or_more_ago_are_stale(self):
        now = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        project = Project(
            id=10,
            title="Boundary",
            description="",
            url="https://example/10",
            submitted_at=now - timedelta(hours=1),
        )

        self.assertTrue(is_project_stale(project, now=now))
        self.assertFalse(
            is_project_stale(
                Project(
                    id=11,
                    title="Fresh",
                    description="",
                    url="https://example/11",
                    submitted_at=now - timedelta(minutes=59, seconds=59),
                ),
                now=now,
            )
        )
        self.assertFalse(
            is_project_stale(
                Project(id=12, title="Unknown", description="", url="https://example/12"),
                now=now,
            )
        )

    def test_stale_project_is_marked_seen_without_notification(self):
        stale_project = Project(
            id=3,
            title="Old project",
            description="",
            url="https://example/3",
            submitted_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        freelancer = _Freelancer([stale_project])
        telegram = _Telegram()
        settings = SimpleNamespace(
            page_limit=50,
            filter_match_mode="any",
            score=ScoreConfig(20, 15, 5, 15, 10, 50, 100),
        )

        with TemporaryDirectory() as directory:
            store = SeenProjectStore(Path(directory) / "state.db")
            try:
                store.initialize_preferences(skills=(), search_words=(), threshold=0)
                monitor = Monitor(settings, freelancer, telegram, store)

                self.assertEqual(monitor.run_once(), (1, 0))
                self.assertTrue(store.has_seen(3))
                self.assertEqual(telegram.messages, [])
            finally:
                store.close()

    def test_missing_api_employer_does_not_abort_other_projects(self):
        projects = [
            Project(id=1, title="Unavailable", description="", url="https://example/1",
                    employer_data_complete=False),
            Project(
                id=2,
                title="Valid",
                description="",
                url="https://example/2",
                employer=Employer(
                    payment_verified=True,
                    profile_completed=True,
                    member_since=datetime(2020, 1, 1, tzinfo=timezone.utc),
                ),
            ),
        ]
        freelancer = _Freelancer(projects)
        telegram = _Telegram()
        settings = SimpleNamespace(
            page_limit=50,
            filter_match_mode="any",
            score=ScoreConfig(20, 15, 5, 15, 10, 50, 100),
        )

        with TemporaryDirectory() as directory:
            store = SeenProjectStore(Path(directory) / "state.db")
            try:
                store.initialize_preferences(skills=(), search_words=(), threshold=0)
                monitor = Monitor(settings, freelancer, telegram, store)

                self.assertEqual(monitor.run_once(), (2, 1))
                self.assertFalse(store.has_seen(1))
                self.assertTrue(store.has_seen(2))
                self.assertEqual(store.deferred_project_ids([1]), {1})
                self.assertEqual(len(telegram.messages), 1)

                self.assertEqual(monitor.run_once(), (0, 0))
            finally:
                store.close()
