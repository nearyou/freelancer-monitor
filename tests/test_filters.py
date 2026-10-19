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

    def test_matches_word_without_skill_match(self):
        self.assertTrue(
            project_matches(
                self.project,
                search_words=("telegram",),
                wanted_skills=("Rust",),
            )
        )

    def test_matches_skill_without_word_match(self):
        self.assertTrue(
            project_matches(
                self.project,
                search_words=("unrelated",),
                wanted_skills=("python",),
            )
        )

    def test_no_filters_matches_every_project(self):
        self.assertTrue(
            project_matches(self.project, search_words=(), wanted_skills=())
        )

    def test_rejects_when_no_enabled_filter_matches(self):
        for words, skills in [(('unrelated',), ('Rust',)), (('unrelated',), ()), ((), ('Rust',))]:
            with self.subTest(words=words, skills=skills):
                self.assertFalse(project_matches(self.project, search_words=words, wanted_skills=skills))

