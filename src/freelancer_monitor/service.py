from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .config import Settings
from .filters import compile_project_filter
from .freelancer import FreelancerClient
from .scoring import score_employer
from .state import SeenProjectStore
from .telegram import format_project_message

logger = logging.getLogger(__name__)
MAX_PROJECT_AGE = timedelta(hours=1)


def is_project_stale(project, *, now: datetime | None = None) -> bool:
    """Return true when a known submission time is at least one hour old."""
    if project.submitted_at is None:
        return False
    submitted_at = project.submitted_at
    if submitted_at.tzinfo is None:
        submitted_at = submitted_at.replace(tzinfo=timezone.utc)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    return current_time - submitted_at >= MAX_PROJECT_AGE


class Monitor:
    def __init__(
        self,
        settings: Settings,
        freelancer: FreelancerClient,
        store: SeenProjectStore,
    ):
        self.settings = settings
        self.freelancer = freelancer
        self.store = store

    def run_once(self) -> tuple[int, int]:
        search_words = self.store.list_terms("search_word")
        skills = self.store.list_terms("skill")
        skill_ids = self.freelancer.resolve_skill_ids(skills)
        projects = self.freelancer.fetch_active_projects(
            self.settings.page_limit,
            search_terms=search_words + skills,
            skill_ids=skill_ids,
            languages=getattr(self.settings, "freelancer_languages", ()),
        )
        score_config, threshold = self.store.get_scoring_settings()
        project_filter = compile_project_filter(
            search_words=search_words,
            wanted_skills=skills,
            mode=self.settings.filter_match_mode,
        )
        sorted_projects = sorted(projects, key=lambda item: item.id)
        project_ids = [project.id for project in sorted_projects]
        seen_ids = self.store.seen_project_ids(project_ids)
        deferred_ids = self.store.deferred_project_ids(project_ids)
        unseen_projects = [
            project
            for project in sorted_projects
            if project.id not in seen_ids and project.id not in deferred_ids
        ]
        matched_projects = []
        filter_misses = []
        poll_time = datetime.now(timezone.utc)
        for project in unseen_projects:
            if is_project_stale(project, now=poll_time):
                filter_misses.append((project.id, False, 0.0))
                logger.info("Skipped project %s (posted at least 1 hour ago)", project.id)
            elif project_filter.matches(project):
                matched_projects.append(project)
            else:
                filter_misses.append((project.id, False, 0.0))
                logger.info("Skipped project %s (filter mismatch)", project.id)
        self.store.mark_seen_many(filter_misses)

        queued = 0
        processed = len(unseen_projects)
        for project in matched_projects:
            missing_enabled_verification = any(
                getattr(score_config, field) > 0 and getattr(project.employer, field) is None
                for field in ("identity_verified", "phone_verified", "email_verified")
            )
            if not project.employer_data_complete or missing_enabled_verification:
                attempts, retry_seconds = self.store.record_project_failure(
                    project.id, "API response is missing required employer trust data"
                )
                logger.warning(
                    "Deferred project %s for %s seconds: incomplete API employer data "
                    "(attempt %s)",
                    project.id,
                    retry_seconds,
                    attempts,
                )
                continue
            score = score_employer(project.employer, score_config)
            should_notify = score.total >= threshold
            if should_notify:
                message = format_project_message(project)
                if self.store.enqueue_project(project.id, message, project.url, score.total):
                    queued += 1
                    logger.info("Queued project %s with score %.2f", project.id, score.total)
            else:
                logger.info(
                    "Skipped project %s (matches=%s score=%.2f threshold=%.2f)",
                    project.id,
                    True,
                    score.total,
                    threshold,
                )
                self.store.mark_seen(project.id, notified=False, score=score.total)
        self.store.retain_project_batch(project_ids)
        return processed, queued
