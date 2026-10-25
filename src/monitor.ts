import type { Settings } from './config.js';
import type { ProjectSource } from './freelancer.js';
import { compileFilter, isStale, scoreEmployer } from './scoring.js';
import { Store } from './state.js';
import { formatProject } from './telegram.js';

export class Monitor {
  constructor(private readonly settings: Settings, private readonly source: ProjectSource, private readonly store: Store) {}
  async runOnce(signal?: AbortSignal): Promise<{ processed: number; queued: number }> {
    const words = this.store.terms('search_word');
    const skills = this.store.terms('skill');
    const ids = await this.source.resolveSkills(skills, signal);
    const projects = await this.source.fetchProjects(this.settings.pageLimit, [...words, ...skills], ids, this.settings.languages, signal);
    signal?.throwIfAborted();
    const { config, threshold } = this.store.scoring();
    const matches = compileFilter(words, skills);
    let processed = 0, queued = 0;
    const now = Date.now();
    for (const project of projects.sort((a, b) => a.id - b.id)) {
      if (this.store.hasSeen(project.id) || this.store.deferred(project.id)) continue;
      processed++;
      if (isStale(project, now) || !matches(project)) { this.store.markSeen(project.id, 0); continue; }
      const missing = (['identity_verified', 'phone_verified', 'email_verified'] as const).some(key => config[key] > 0 && project.employer[key] === null);
      if (!project.employer_data_complete || missing) {
        const delay = this.store.failProject(project.id, 'API response is missing required employer trust data');
        console.warn(`Deferred project ${project.id} for ${delay} seconds: incomplete employer data.`); continue;
      }
      const score = scoreEmployer(project.employer, config, new Date(now)).total;
      if (score >= threshold) {
        if (this.store.enqueueProject(project.id, formatProject(project), project.url, score)) queued++;
      } else this.store.markSeen(project.id, score);
    }
    this.store.retainBatch(projects.map(project => project.id));
    return { processed, queued };
  }
}
