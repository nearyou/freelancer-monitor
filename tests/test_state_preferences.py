from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from freelancer_monitor.state import SeenProjectStore


class PreferenceStoreTests(TestCase):
    def test_initial_import_happens_only_once_and_database_is_authoritative(self):
        with TemporaryDirectory() as directory:
            store = SeenProjectStore(Path(directory) / "state.db")
            try:
                self.assertTrue(
                    store.initialize_preferences(
                        skills=("Python",),
                        search_words=("automation",),
                        threshold=65,
                    )
                )
                self.assertFalse(
                    store.initialize_preferences(
                        skills=("Ignored",), search_words=("ignored",), threshold=10
                    )
                )
                self.assertEqual(store.list_terms("skill"), ("Python",))
                self.assertEqual(store.list_terms("search_word"), ("automation",))
                self.assertEqual(store.get_threshold(), 65)

                self.assertTrue(store.add_term("skill", "Web Scraping"))
                self.assertFalse(store.add_term("skill", "web scraping"))
                self.assertTrue(store.delete_term("skill", "WEB SCRAPING"))
                store.set_threshold(72.5)
                self.assertEqual(store.get_threshold(), 72.5)

                store.mark_seen(101, notified=True, score=75)
                store.mark_seen(102, notified=False, score=20)
                store.mark_seen_many(
                    [(project_id, False, 0) for project_id in range(1_000, 2_005)]
                )
                self.assertEqual(
                    store.seen_project_ids(list(range(900, 2_100))),
                    set(range(1_000, 2_005)),
                )
                self.assertEqual(store.clear_history(), 1_007)
                self.assertFalse(store.has_seen(101))
                self.assertFalse(store.has_seen(102))
                self.assertEqual(store.list_terms("skill"), ("Python",))
                self.assertEqual(store.list_terms("search_word"), ("automation",))
                self.assertEqual(store.get_threshold(), 72.5)
            finally:
                store.close()
