import type { Employer, Project } from './models.js';
import { fold } from './models.js';
import type { ScoreConfig } from './config.js';

const round = (value: number) => Math.round((value + Number.EPSILON) * 100) / 100;
export function scoreEmployer(owner: Employer, config: ScoreConfig, now = new Date()) {
  const day = (date: Date) => Math.floor(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate()) / 86400000);
  const age = owner.member_since ? Math.max(day(now) - day(owner.member_since), 0) : 0;
  const values = {
    payment_verified: owner.payment_verified ? config.payment_verified : 0,
    deposit_made: owner.deposit_made ? config.deposit_made : 0,
    profile_completed: owner.profile_completed ? config.profile_completed : 0,
    reviews: config.reviews_max * Math.min(Math.log1p(Math.max(owner.review_count, 0)) / Math.log1p(config.reviews_cap), 1),
    member_age: config.member_age_max * Math.min(Math.log1p(age) / Math.log1p(config.member_age_max_days), 1),
    identity_verified: owner.identity_verified ? config.identity_verified : 0,
    phone_verified: owner.phone_verified ? config.phone_verified : 0,
    email_verified: owner.email_verified ? config.email_verified : 0,
    rating: config.rating_max * (Math.min(Math.max(owner.rating, 0), 5) / 5),
  };
  const rounded = Object.fromEntries(Object.entries(values).map(([key, value]) => [key, round(value)])) as typeof values;
  return { ...rounded, member_age_days: age, total: round(Object.values(values).reduce((a, b) => a + b, 0)) };
}

export function compileFilter(words: string[], skills: string[]): (project: Project) => boolean {
  const terms = words.map(fold);
  const wanted = new Set(skills.map(fold));
  return project => (!terms.length && !wanted.size)
    || terms.some(word => fold(`${project.title}\n${project.description}`).includes(word))
    || project.skills.some(skill => wanted.has(fold(skill)));
}
export const isStale = (project: Project, now = Date.now()): boolean => project.submitted_at !== null && now - project.submitted_at.getTime() >= 3600000;
