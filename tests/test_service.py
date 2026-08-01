from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from freelancer_monitor.config import ScoreConfig
from freelancer_monitor.freelancer import EmployerPageParseError
from freelancer_monitor.models import Employer, Project
from freelancer_monitor.service import Monitor
from freelancer_monitor.state import SeenProjectStore


class _Freelancer:
    def __init__(self, projects):
        self.projects = projects
        self.enriched_ids = []

    def fetch_active_projects(self, _limit):
        return self.projects

    def enrich_from_public_page(self, project):
        self.enriched_ids.append(project.id)
        if project.id == 1:
            raise EmployerPageParseError("Public page has no client-information section")
        return project


class _Telegram:
    def __init__(self):
        self.messages = []

    def send_message(self, message, *, button_url):
        self.messages.append((message, button_url))


class MonitorResilienceTests(TestCase):
    def test_one_unparseable_page_does_not_abort_other_projects(self):
        projects = [
            Project(id=1, title="Unavailable", description="", url="https://example/1"),
            Project(
                id=2,
                title="Valid",
                description="",
                url="https://example/2",
                employer=Employer(
                    identity_verified=True,
                    payment_verified=True,
                    member_since=datetime(2020, 1, 1, tzinfo=timezone.utc),
                ),
            ),
        ]
        freelancer = _Freelancer(projects)
        telegram = _Telegram()
        settings = SimpleNamespace(
            page_limit=50,
            filter_match_mode="any",
            score=ScoreConfig(20, 20, 15, 15, 10, 50, 100),
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
                self.assertEqual(freelancer.enriched_ids, [1, 2])
            finally:
                store.close()
