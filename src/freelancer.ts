import { fold, type Project } from './models.js';
import { getJson, type Fetcher } from './http.js';

type RecordValue = Record<string, unknown>;
const object = (value: unknown): RecordValue => value !== null && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {};
function first(value: unknown, ...paths: string[]): unknown {
  for (const path of paths) {
    let current = value;
    for (const key of path.split('.')) current = object(current)[key];
    if (current !== undefined && current !== null) return current;
  }
  return undefined;
}
export function verification(value: unknown): boolean | null {
  if (typeof value === 'boolean') return value;
  const text = String(value).trim().toLowerCase();
  if (['1', 'true', 'yes', 'verified'].includes(text)) return true;
  if (['0', 'false', 'no', 'unverified'].includes(text)) return false;
  return null;
}
const number = (value: unknown, fallback = 0): number => {
  if (value === null || value === undefined || value === '') return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
};
function date(value: unknown): Date | null {
  if (value === undefined || value === null || value === '') return null;
  let input = String(value);
  if (/^\d{4}-\d\d-\d\d[T ]\d\d:\d\d(?::\d\d(?:\.\d+)?)?$/.test(input)) input += 'Z';
  const result = typeof value === 'number' || /^\d+$/.test(input) ? new Date(Number(value) * 1000) : new Date(input);
  return Number.isFinite(result.getTime()) ? result : null;
}
function listingTypes(raw: RecordValue): string[] {
  const upgrades = object(raw.upgrades);
  const enabled = (...values: unknown[]) => values.some(value => typeof value === 'object' && value !== null ? Object.keys(value).length > 0 : verification(value) === true || (typeof value === 'number' && value > 0));
  return [
    ['Featured', enabled(raw.featured, upgrades.featured)], ['Sealed', enabled(upgrades.sealed)],
    ['NDA', enabled(upgrades.NDA, upgrades.nda, raw.nda_details)], ['Urgent', enabled(raw.urgent, upgrades.urgent)],
    ['Recruiter', enabled(raw.recruiter, upgrades.recruiter, upgrades.unpaid_recruiter)],
    ['IP Agreement', enabled(raw.ip_contract, upgrades.ip_contract)],
  ].filter(([, yes]) => yes).map(([label]) => String(label));
}

export function parseProjects(payload: unknown): Project[] {
  const body = object(payload);
  if (body.status !== undefined && body.status !== 'success') throw new Error('Freelancer returned an unsuccessful response.');
  const result = object(body.result ?? body);
  if (!Array.isArray(result.projects)) throw new Error('Freelancer response has no projects list.');
  const users = Array.isArray(result.users)
    ? Object.fromEntries(result.users.map(user => [String(object(user).id), user])) : object(result.users);
  return result.projects.flatMap(entry => {
    const raw = object(entry);
    if (raw.id === undefined) return [];
    const id = number(raw.id, NaN);
    if (!Number.isSafeInteger(id) || id <= 0) throw new Error('Invalid project ID.');
    const ownerId = first(raw, 'owner_id', 'owner.id');
    const inline = object(raw.owner_info);
    const owner = Object.keys(inline).length ? inline : object(users[String(ownerId)]);
    const status = object(owner.status);
    const reputation = object(first(owner, 'employer_reputation.entire_history', 'employer_reputation', 'reputation.entire_history'));
    const rating = number(first(reputation, 'overall', 'rating'), -1);
    const rawReviews = first(reputation, 'reviews', 'review_count', 'count');
    const reviews = rawReviews === undefined && rating === 0 ? 0 : number(rawReviews, -1);
    const payment = verification(status.payment_verified);
    const deposit = verification(status.deposit_made);
    const profile = verification(first(status, 'profile_complete', 'profile_completed'));
    const memberSince = date(first(owner, 'registration_date', 'member_since', 'created_at'));
    const budget = object(raw.budget);
    const slug = String(raw.seo_url ?? '').replace(/^\/+|\/+$/g, '');
    return [{
      id, title: String(raw.title ?? 'Untitled project'), description: String(first(raw, 'description', 'preview_description') ?? ''),
      url: `https://www.freelancer.com/projects/${slug || id}`,
      owner_id: ownerId === undefined ? null : number(ownerId),
      skills: (Array.isArray(raw.jobs) ? raw.jobs : []).map(job => String(first(job, 'name', 'seo_url') ?? '').trim()).filter(Boolean),
      listing_types: listingTypes(raw), currency_code: String(object(raw.currency).code ?? ''),
      budget_min: budget.minimum == null ? null : number(budget.minimum),
      budget_max: budget.maximum == null ? null : number(budget.maximum),
      hourly: String(raw.type).toLowerCase() === 'hourly',
      submitted_at: date(first(raw, 'submitdate', 'time_submitted', 'time_updated')),
      employer_data_complete: payment !== null && deposit !== null && profile !== null && rating >= 0 && rating <= 5 && reviews >= 0 && Number.isInteger(reviews) && memberSince !== null,
      employer: {
        country: String(first(owner, 'location.country.name', 'location.country.code', 'country.name', 'country.code') ?? ''),
        payment_verified: payment === true, deposit_made: deposit === true, profile_completed: profile === true,
        rating: Math.max(rating, 0), review_count: Math.trunc(Math.max(reviews, 0)), member_since: memberSince,
        identity_verified: verification(status.identity_verified), phone_verified: verification(status.phone_verified), email_verified: verification(status.email_verified),
      },
    }];
  });
}

export interface ProjectSource {
  resolveSkills(names: string[], signal?: AbortSignal): Promise<number[]>;
  fetchProjects(limit: number, terms: string[], skills: number[], languages: string[], signal?: AbortSignal): Promise<Project[]>;
}

export class FreelancerClient implements ProjectSource {
  private cachedNames = '';
  private cachedIds: number[] = [];
  constructor(private readonly apiUrl: string, private readonly request: Fetcher = fetch) {}
  async resolveSkills(names: string[], signal?: AbortSignal): Promise<number[]> {
    const cleaned = [...new Set(names.map(s => s.trim()).filter(Boolean))];
    const key = JSON.stringify(cleaned);
    if (key === this.cachedNames) return [...this.cachedIds];
    if (!cleaned.length) { this.cachedNames = key; this.cachedIds = []; return []; }
    const base = new URL(this.apiUrl); base.search = ''; base.pathname = base.pathname.replace(/\/?$/, '/');
    const url = new URL('../../jobs/', base);
    for (const name of cleaned) url.searchParams.append('job_names[]', name);
    const payload = object(await getJson(url, signal, this.request));
    if (payload.status !== undefined && payload.status !== 'success') throw new Error('Skill lookup failed.');
    const result = payload.result;
    if (!Array.isArray(result)) throw new Error('Skill lookup returned an invalid catalog.');
    const ids = new Map(result.map(job => [fold(String(object(job).name ?? '')), number(object(job).id, NaN)]));
    this.cachedIds = cleaned.map(name => ids.get(fold(name))).filter((id): id is number => id !== undefined && Number.isSafeInteger(id));
    const missing = cleaned.filter(name => !ids.has(fold(name)));
    if (missing.length) console.warn('Unrecognized registered skills:', missing.join(', '));
    this.cachedNames = key;
    return [...this.cachedIds];
  }
  async fetchProjects(limit: number, terms: string[] = [], skills: number[] = [], languages: string[] = [], signal?: AbortSignal): Promise<Project[]> {
    const url = new URL(this.apiUrl);
    const params = url.searchParams;
    for (const flag of ['full_description', 'job_details', 'local_details', 'location_details', 'upgrade_details', 'owner_info', 'compact', 'new_errors', 'new_pools', 'or_search_query']) params.set(flag, 'true');
    params.set('webapp', '1'); params.set('limit', String(limit)); params.set('sort_field', 'submitdate');
    params.delete('reverse_sort');
    if (terms.length) params.set('query', [...new Set(terms)].join(' '));
    for (const [key, values] of [['jobs[]', skills], ['languages[]', languages]] as const) {
      if (values.length) { params.delete(key); for (const value of new Set(values.map(String))) params.append(key, value); }
    }
    return parseProjects(await getJson(url, signal, this.request));
  }
}
