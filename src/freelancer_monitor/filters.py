from __future__ import annotations

from dataclasses import dataclass

from .models import Project


@dataclass(frozen=True)
class CompiledProjectFilter:
    search_words: tuple[str, ...]
    wanted_skills: frozenset[str]
    mode: str

    def matches(self, project: Project) -> bool:
        word_filter_enabled = bool(self.search_words)
        skill_filter_enabled = bool(self.wanted_skills)

        word_match = False
        if word_filter_enabled:
            text = f"{project.title}\n{project.description}".casefold()
            word_match = any(word in text for word in self.search_words)

        skill_match = False
        if skill_filter_enabled:
            project_skills = {skill.casefold() for skill in project.skills}
            skill_match = not self.wanted_skills.isdisjoint(project_skills)

        if not word_filter_enabled:
            return skill_match if skill_filter_enabled else True
        if not skill_filter_enabled:
            return word_match
        return word_match and skill_match if self.mode == "all" else word_match or skill_match


def compile_project_filter(
    *,
    search_words: tuple[str, ...],
    wanted_skills: tuple[str, ...],
    mode: str = "any",
) -> CompiledProjectFilter:
    return CompiledProjectFilter(
        search_words=tuple(word.casefold() for word in search_words),
        wanted_skills=frozenset(skill.casefold() for skill in wanted_skills),
        mode=mode,
    )


def project_matches(
    project: Project,
    *,
    search_words: tuple[str, ...],
    wanted_skills: tuple[str, ...],
    mode: str = "any",
) -> bool:
    return compile_project_filter(
        search_words=search_words, wanted_skills=wanted_skills, mode=mode
    ).matches(project)
