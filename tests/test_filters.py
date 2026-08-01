from unittest import TestCase

from freelancer_monitor.filters import project_matches
from freelancer_monitor.models import Project


class ProjectFilterTests(TestCase):
    def setUp(self):
        self.project = Project(
            id=1,
            title="Build a Python bot",
            description="Telegram automation needed",
            url="https://example.test/1",
            skills=("Python", "API"),
        )

    def test_any_matches_word_or_skill(self):
        self.assertTrue(
            project_matches(
                self.project,
                search_words=("telegram",),
                wanted_skills=("Rust",),
                mode="any",
            )
        )

    def test_all_requires_both_enabled_filter_groups(self):
        self.assertFalse(
            project_matches(
                self.project,
                search_words=("telegram",),
                wanted_skills=("Rust",),
                mode="all",
            )
        )

    def test_no_filters_matches_every_project(self):
        self.assertTrue(
            project_matches(self.project, search_words=(), wanted_skills=(), mode="all")
        )

