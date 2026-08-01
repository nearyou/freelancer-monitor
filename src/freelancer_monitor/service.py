from __future__ import annotations

import logging

from .config import Settings
from .filters import compile_project_filter
from .freelancer import FreelancerClient
from .scoring import score_employer
from .state import SeenProjectStore
from .telegram import TelegramClient, format_project_message

logger = logging.getLogger(__name__)


class Monitor:
    def __init__(
        self,
        settings: Settings,
        freelancer: FreelancerClient,
        telegram: TelegramClient,
        store: SeenProjectStore,
    ):
        self.settings = settings
        self.freelancer = freelancer
        self.telegram = telegram
        self.store = store

    def run_once(self) -> tuple[int, int]:
        projects = self.freelancer.fetch_active_projects(self.settings.page_limit)
        search_words = self.store.list_terms("search_word")
        skills = self.store.list_terms("skill")
        threshold = self.store.get_threshold()
        project_filter = compile_project_filter(
            search_words=search_words,
            wanted_skills=skills,
            mode=self.settings.filter_match_mode,
        )
        sorted_projects = sorted(projects, key=lambda item: item.id)
        seen_ids = self.store.seen_project_ids([project.id for project in sorted_projects])
        unseen_projects = [project for project in sorted_projects if project.id not in seen_ids]
        matched_projects = []
        filter_misses = []
        for project in unseen_projects:
            if project_filter.matches(project):
                matched_projects.append(project)
            else:
                filter_misses.append((project.id, False, 0.0))
                logger.info("Skipped project %s (filter mismatch)", project.id)
        self.store.mark_seen_many(filter_misses)

        notified = 0
        processed = len(unseen_projects)
        for project in matched_projects:
            project = self.freelancer.enrich_from_public_page(project)
            score = score_employer(project.employer, self.settings.score)
            should_notify = score.total >= threshold
            if should_notify:
                message = format_project_message(project)
                self.telegram.send_message(message, button_url=project.url)
                notified += 1
                logger.info("Notified project %s with score %.2f", project.id, score.total)
            else:
                logger.info(
                    "Skipped project %s (matches=%s score=%.2f threshold=%.2f)",
                    project.id,
                    True,
                    score.total,
                    threshold,
                )
            self.store.mark_seen(project.id, notified=should_notify, score=score.total)
        return processed, notified
