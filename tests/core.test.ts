import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, writeFileSync, mkdtempSync, rmSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { DEFAULT_SCORE, SCORE_KEYS, loadDotenv, maximum, settingsFromEnv, updateScore } from '../src/config.js';
import { compileFilter, isStale, scoreEmployer } from '../src/scoring.js';
import { defaultEmployer } from '../src/models.js';
import { parseProjects, FreelancerClient, verification } from '../src/freelancer.js';
import { formatProject } from '../src/telegram.js';
import { getJson, retryAfter, type Fetcher } from '../src/http.js';
import { fixture, project, settings } from './helpers.js';

test('environment defaults preserve polling and rate limits; legacy values ignored', () => {
  assert.equal(settings().pollInterval, 15); assert.equal(settings().sendInterval, 3.1);
  const actual = settingsFromEnv({ TELEGRAM_BOT_TOKEN: 't', TELEGRAM_CHAT_ID: '1', SCORE_THRESHOLD: 'garbage', SKILLS: 'ignored', FILTER_MATCH_MODE: 'invalid' });
  assert.equal('skills' in actual, false);
  for (const [key, value] of [['POLL_INTERVAL_SECONDS', '4'], ['TELEGRAM_SEND_INTERVAL_SECONDS', '2.9'], ['FREELANCER_PAGE_LIMIT', 'NaN'], ['FREELANCER_PAGE_LIMIT', '1.5']]) {
    assert.throws(() => settingsFromEnv({ TELEGRAM_BOT_TOKEN: 't', TELEGRAM_CHAT_ID: '1', [key!]: value }));
  }
  assert.throws(() => settingsFromEnv({}));
});
test('dotenv preserves existing process values and handles quoted URL values', t => {
  const dir = mkdtempSync(join(tmpdir(), 'env-test-')); t.after(() => rmSync(dir, { recursive: true, force: true }));
  const path = join(dir, '.env');
  writeFileSync(path, '# comment\nTELEGRAM_BOT_TOKEN="file-token"\nFREELANCER_API_URL="https://example.test/?x=1&y=2"\n');
  const env = { TELEGRAM_BOT_TOKEN: 'existing' } as NodeJS.ProcessEnv;
  loadDotenv(path, env);
  assert.equal(env.TELEGRAM_BOT_TOKEN, 'existing'); assert.equal(env.FREELANCER_API_URL, 'https://example.test/?x=1&y=2');
});
test('all score settings validate; caps do not affect maximum', () => {
  assert.equal(maximum(DEFAULT_SCORE), 65);
  for (const key of SCORE_KEYS) assert.equal(updateScore(DEFAULT_SCORE, key, '50')[key], 50);
  for (const value of ['NaN', 'Infinity', '-1', '', '0x10']) assert.throws(() => updateScore(DEFAULT_SCORE, 'payment_verified', value));
  for (const value of ['0', '1.5', '1e100']) assert.throws(() => updateScore(DEFAULT_SCORE, 'reviews_cap', value));
  assert.throws(() => updateScore(DEFAULT_SCORE, '__proto__', '1'));
  assert.equal(maximum(updateScore(DEFAULT_SCORE, 'reviews_cap', '10')), 65);
  assert.throws(() => updateScore({ ...DEFAULT_SCORE, payment_verified: 1e308 }, 'deposit_made', '1e308'));
});
test('score curve matches 25 captured Python results', () => {
  const expected = JSON.parse(readFileSync('tests/fixtures/python-parity.json', 'utf8'));
  const now = new Date(expected.now);
  for (const row of expected.scores) {
    const owner = { ...defaultEmployer(), payment_verified: true, deposit_made: true, rating: 4.8, review_count: row.reviews, member_since: new Date(now.getTime() - row.age * 86400000) };
    assert.equal(scoreEmployer(owner, DEFAULT_SCORE, now).total, row.score);
  }
});
test('optional weights, custom caps, future join dates and rating bounds', () => {
  const config = { ...DEFAULT_SCORE, identity_verified: 8, phone_verified: 4, email_verified: 2, rating_max: 10, reviews_cap: 10, member_age_max_days: 30 };
  const now = new Date('2026-01-31T00:00:00Z');
  const owner = { ...defaultEmployer(), payment_verified: true, deposit_made: true, profile_completed: true, review_count: 10, member_since: new Date('2026-01-01T00:00:00Z'), identity_verified: true, phone_verified: true, email_verified: true, rating: 2.5 };
  assert.equal(scoreEmployer(owner, config, now).total, 84);
  assert.equal(scoreEmployer({ ...owner, rating: 8 }, config, now).total, 89);
  assert.equal(scoreEmployer({ ...owner, rating: -1 }, config, now).rating, 0);
  assert.equal(scoreEmployer({ ...owner, member_since: new Date('2030-01-01') }, config, now).member_age_days, 0);
});
test('filter OR behavior, case folding and empty groups', () => {
  for (const [words, skills, expected] of [
    [['telegram'], ['Rust'], true], [['missing'], ['python'], true], [[], [], true],
    [['missing'], ['Rust'], false], [[], ['Rust'], false], [['missing'], [], false],
  ] as [string[], string[], boolean][]) assert.equal(compileFilter(words, skills)(project()), expected);
});
test('one-hour cutoff includes boundary and permits unknown timestamps', () => {
  const now = Date.now();
  assert.equal(isStale(project(1, { submitted_at: new Date(now - 3600000) }), now), true);
  assert.equal(isStale(project(1, { submitted_at: new Date(now - 3599999) }), now), false);
  assert.equal(isStale(project(), now), false);
});
test('compact API fixture and messages match Python output exactly', () => {
  const projects = parseProjects(fixture());
  const expected = JSON.parse(readFileSync('tests/fixtures/python-parity.json', 'utf8'));
  assert.deepEqual(projects.map(formatProject), expected.messages);
  assert.equal(projects.length, 2);
  assert.equal(projects[0]!.employer.country, 'Japan');
  assert.equal(projects[0]!.employer.review_count, 0);
  assert.equal(projects[1]!.employer.review_count, 8);
  assert.ok(projects.every(p => p.employer_data_complete));
});
test('missing trust fields and malformed reputation defer instead of granting trust', () => {
  for (const key of ['status', 'registration_date', 'reputation']) {
    const payload = fixture(); delete payload.result.projects[0].owner_info[key];
    assert.equal(parseProjects(payload)[0]!.employer_data_complete, false);
  }
  for (const value of [null, 'invalid', NaN, Infinity, -1, 6]) {
    const payload = fixture(); payload.result.projects[0].owner_info.reputation.entire_history.overall = value;
    assert.equal(parseProjects(payload)[0]!.employer_data_complete, false);
  }
  const payload = fixture(); delete payload.result.projects[1].owner_info.reputation.entire_history.reviews;
  assert.equal(parseProjects(payload)[1]!.employer_data_complete, false);
});
test('verification states preserve false and unknown; payment and deposit are not swapped', () => {
  assert.equal(verification('false'), false); assert.equal(verification('verified'), true); assert.equal(verification('invalid'), null);
  const payload = fixture(); const status = payload.result.projects[0].owner_info.status;
  status.payment_verified = false; status.deposit_made = true; delete status.identity_verified;
  const parsed = parseProjects(payload)[0]!;
  assert.equal(parsed.employer.payment_verified, false); assert.equal(parsed.employer.deposit_made, true);
  assert.equal(parsed.employer.identity_verified, null); assert.equal(parsed.employer_data_complete, true);
});
test('legacy users and inline owner precedence', () => {
  const payload = fixture(); const raw = payload.result.projects[0]; raw.owner_id = 7;
  payload.result.users = { '7': raw.owner_info }; delete raw.owner_info;
  assert.equal(parseProjects(payload)[0]!.employer.country, 'Japan');
  payload.result.users = [{ id: 7, ...payload.result.users['7'] }];
  assert.equal(parseProjects(payload)[0]!.employer.country, 'Japan');
  raw.owner_info = { country: { name: 'Inline' } };
  assert.equal(parseProjects(payload)[0]!.employer.country, 'Inline');
});
test('API errors and malformed feeds do not masquerade as empty batches', () => {
  for (const payload of [{ status: 'error' }, { result: {} }, { result: { projects: null } }, []]) assert.throws(() => parseProjects(payload));
  assert.deepEqual(parseProjects({ result: { projects: [] } }), []);
});
test('query filters preserved and runtime overrides sent once', async () => {
  const urls: URL[] = [];
  const request: Fetcher = async input => { urls.push(new URL(String(input))); return Response.json({ result: { projects: [] } }); };
  const client = new FreelancerClient('https://example.test/api/projects/0.1/projects/active?jobs[]=3&jobs[]=9&languages[]=en&max_price=1500&owner_info=false&reverse_sort=true', request);
  await client.fetchProjects(20);
  assert.deepEqual(urls[0]!.searchParams.getAll('jobs[]'), ['3', '9']);
  await client.fetchProjects(50, ['Python', 'Python', 'API'], [13, 13], ['ja']);
  const params = urls[1]!.searchParams;
  assert.deepEqual(params.getAll('jobs[]'), ['13']); assert.equal(params.get('query'), 'Python API');
  assert.equal(params.get('limit'), '50'); assert.equal(params.get('max_price'), '1500');
  assert.equal(params.get('owner_info'), 'true'); assert.equal(params.has('reverse_sort'), false);
});
test('skills resolved in one request and cached until changed', async () => {
  let calls = 0;
  const request: Fetcher = async input => {
    calls++; const url = new URL(String(input));
    assert.equal(url.pathname, '/api/projects/0.1/jobs/');
    assert.ok(url.searchParams.has('job_names[]'));
    return Response.json({ result: [{ id: 13, name: 'Python' }] });
  };
  const client = new FreelancerClient('https://example.test/api/projects/0.1/projects/active?limit=20', request);
  assert.deepEqual(await client.resolveSkills(['python']), [13]); await client.resolveSkills(['python']); assert.equal(calls, 1);
  assert.deepEqual(await client.resolveSkills([]), []);
  await client.resolveSkills(['Python']); assert.equal(calls, 2);
});
test('HTTP retries temporary failures and respects Retry-After', async () => {
  let calls = 0; const waits: number[] = [];
  const request: Fetcher = async () => ++calls === 1 ? new Response('', { status: 429, headers: { 'retry-after': '2' } }) : Response.json({ ok: true });
  assert.deepEqual(await getJson(new URL('https://example.test'), undefined, request, async ms => { waits.push(ms); }), { ok: true });
  assert.deepEqual(waits, [2000]);
  assert.equal(retryAfter('invalid', 500), 500);
  let failures = 0;
  await assert.rejects(getJson(new URL('https://example.test'), undefined, async () => { failures++; return new Response('', { status: 503 }); }, async () => {}));
  assert.equal(failures, 4);
});
